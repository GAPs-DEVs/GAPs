from __future__ import annotations

import argparse
import sys
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import cv2
import numpy as np
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QColor, QCloseEvent, QFontMetrics, QImage, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from ultralytics import YOLO

from camera_calibration import CameraCalibration, load_camera_calibration
from fretboard_edge_refinement import (
    EdgeRefinementResult,
    FretLineTracker,
    StringLineTracker,
    refine_grid_with_edge_lines_diagnostics,
)
from fretboard_mapping import (
    DetectedLine,
    FretboardTracker,
    NoteMarker,
    NoteMarkerTracker,
    SelectedLineTracker,
    StableFretboardGrid,
    build_center_test_note_markers,
    build_fretboard_grid,
    build_note_markers,
    count_detected_classes,
    infer_selected_reference_fret_index,
    suppress_fret_indices_from_grid,
)
from hand_tracking import Fingertip, MediaPipeHandTracker


DEFAULT_MODEL_PATH = Path(__file__).with_name("best06.pt")
DEFAULT_CALIBRATION_PATH = Path(__file__).with_name("camera_calibration.npz")
NOTE_CHOICES = ["없음", "도", "레", "미", "파", "솔", "라", "시", "높은 도"]
POINT_THRESHOLD = 0.15
SELECTION_MAX_DISTANCE = 30.0
TRACKING_CONF_FLOOR = 0.10
TRACKING_CONF_SCALE = 0.55
REACQUIRE_CONF_FLOOR = 0.14
REACQUIRE_CONF_SCALE = 0.80
CLASS_COLORS = {
    "fret": (80, 190, 255),
    "nut": (70, 255, 150),
}
NOTE_MARKER_COLOR = (0, 218, 255)
NOTE_MARKER_MATCH_COLOR = (80, 255, 120)
FINGERTIP_COLOR = (255, 180, 80)
EDGE_GUIDE_STRING_COLOR = (80, 255, 120)
EDGE_GUIDE_INFERRED_STRING_COLOR = (180, 185, 190)
EDGE_GUIDE_FRET_COLOR = (80, 190, 255)
EDGE_GUIDE_INFERRED_FRET_COLOR = (165, 175, 185)
EDGE_GUIDE_INTERSECTION_COLOR = (0, 230, 255)
OPEN_NOTE_MARKER_RADIUS = 7
OPEN_NOTE_MARKER_THICKNESS = 2
FRETTED_NOTE_MARKER_RADIUS = 6


@dataclass
class FrameAnalysis:
    detected_lines: tuple[DetectedLine, ...]
    stable_grid: StableFretboardGrid | None
    note_markers: tuple[NoteMarker, ...]
    fingertip_points: tuple[Fingertip, ...]
    matched_marker_indices: tuple[int, ...]
    class_counts: dict[str, int]
    hand_tracking_available: bool
    tracking_mode: str
    reference_fret_index: int | None = None
    edge_refinement: EdgeRefinementResult | None = None


@dataclass
class InferenceStats:
    fps: float
    inference_ms: float
    total_detections: int
    class_counts: dict[str, int]
    visible_fret_count: int
    marker_count: int
    matched_marker_count: int
    fingertip_count: int
    selected_note: str
    center_test_mode: bool
    hand_tracking_available: bool
    tracking_mode: str
    reference_fret_index: int | None = None
    edge_string_count: int = 0
    edge_fret_count: int = 0
    suppressed_fret_count: int = 0
    stable_grid: StableFretboardGrid | None = None
    edge_refinement: EdgeRefinementResult | None = None


class VideoLabel(QLabel):
    clicked = Signal(float, float)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(float(event.position().x()), float(event.position().y()))
        super().mousePressEvent(event)


def resolve_model_path(model_path: str | None) -> Path:
    if model_path:
        return Path(model_path).expanduser().resolve()
    return DEFAULT_MODEL_PATH.resolve()


def resolve_camera_calibration_path(calibration_path: str | None) -> Path | None:
    if calibration_path:
        return Path(calibration_path).expanduser().resolve()
    if DEFAULT_CALIBRATION_PATH.exists():
        return DEFAULT_CALIBRATION_PATH.resolve()
    return None


def load_yolo_model(model_path: Path) -> YOLO:
    if not model_path.exists():
        raise FileNotFoundError(f"YOLO weight file not found: {model_path}")
    return YOLO(str(model_path))


def format_counts(class_counts: dict[str, int]) -> str:
    if not class_counts:
        return "no detections"
    return ", ".join(f"{name}: {count}" for name, count in sorted(class_counts.items()))


def bgr_to_qimage(frame_bgr: np.ndarray) -> QImage:
    frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    height, width, channels = frame_rgb.shape
    bytes_per_line = channels * width
    return QImage(
        frame_rgb.data,
        width,
        height,
        bytes_per_line,
        QImage.Format.Format_RGB888,
    ).copy()


def point_to_segment_distance(
    point_xy: tuple[float, float],
    start_xy: tuple[float, float],
    end_xy: tuple[float, float],
) -> float:
    point = np.asarray(point_xy, dtype=np.float32)
    start = np.asarray(start_xy, dtype=np.float32)
    end = np.asarray(end_xy, dtype=np.float32)
    segment = end - start
    length_sq = float(np.dot(segment, segment))
    if length_sq <= 1e-6:
        return float(np.linalg.norm(point - start))
    ratio = float(np.dot(point - start, segment) / length_sq)
    ratio = min(1.0, max(0.0, ratio))
    projection = start + segment * ratio
    return float(np.linalg.norm(point - projection))


def find_nearest_line(
    click_xy: tuple[float, float],
    candidate_lines: tuple[DetectedLine, ...],
    max_distance: float = SELECTION_MAX_DISTANCE,
) -> DetectedLine | None:
    nearest_line = None
    nearest_distance = max_distance
    for line in candidate_lines:
        distance = point_to_segment_distance(click_xy, line.p0, line.p1)
        if distance <= nearest_distance:
            nearest_distance = distance
            nearest_line = line
    return nearest_line


def find_nearest_fret_index(
    click_xy: tuple[float, float],
    stable_grid: StableFretboardGrid | None,
    edge_refinement: EdgeRefinementResult | None,
    suppressed_fret_indices: Collection[int] | None = None,
    max_distance: float = SELECTION_MAX_DISTANCE,
) -> int | None:
    if stable_grid is None or not stable_grid.ordered_fret_lines:
        return None

    suppressed_indices = set(suppressed_fret_indices or ())
    candidate_indices = set()
    if edge_refinement is not None:
        candidate_indices = set(edge_refinement.refined_fret_indices) | set(edge_refinement.tracked_fret_indices)
    if not candidate_indices:
        candidate_indices = set(range(len(stable_grid.ordered_fret_lines)))
    candidate_indices = {
        fret_index
        for fret_index in candidate_indices
        if 0 <= fret_index < len(stable_grid.ordered_fret_lines) and fret_index not in suppressed_indices
    }

    nearest_index: int | None = None
    nearest_distance = max_distance
    for fret_index in sorted(candidate_indices):
        fret_line = stable_grid.ordered_fret_lines[fret_index]
        distance = point_to_segment_distance(click_xy, fret_line.p0, fret_line.p1)
        if distance <= nearest_distance:
            nearest_index = fret_index
            nearest_distance = distance
    return nearest_index


def extract_detected_lines(
    result,
    names: dict[int, str],
    point_threshold: float = POINT_THRESHOLD,
) -> tuple[DetectedLine, ...]:
    boxes = result.boxes
    keypoints = result.keypoints
    if boxes is None or keypoints is None or len(boxes) == 0:
        return ()

    classes = boxes.cls.cpu().numpy().astype(int)
    confidences = boxes.conf.cpu().numpy()
    keypoint_xy = keypoints.xy.cpu().numpy()
    keypoint_conf = None
    if getattr(keypoints, "conf", None) is not None:
        keypoint_conf = keypoints.conf.cpu().numpy()

    detected_lines: list[DetectedLine] = []
    for index, (class_index, confidence) in enumerate(zip(classes, confidences)):
        class_name = names.get(class_index, str(class_index))
        if class_name not in CLASS_COLORS or index >= len(keypoint_xy):
            continue

        visible_points: list[tuple[float, float]] = []
        for point_index, point in enumerate(keypoint_xy[index]):
            point_confidence = 1.0
            if keypoint_conf is not None and point_index < len(keypoint_conf[index]):
                point_confidence = float(keypoint_conf[index][point_index])
            if point_confidence < point_threshold:
                continue
            visible_points.append((float(point[0]), float(point[1])))

        if len(visible_points) < 2:
            continue

        detected_lines.append(
            DetectedLine(
                kind=class_name,
                p0=visible_points[0],
                p1=visible_points[1],
                confidence=float(confidence),
            )
        )

    return tuple(detected_lines)


def offset_detected_lines(
    detected_lines: tuple[DetectedLine, ...],
    offset_x: int,
    offset_y: int,
) -> tuple[DetectedLine, ...]:
    return tuple(
        DetectedLine(
            kind=line.kind,
            p0=(line.p0[0] + offset_x, line.p0[1] + offset_y),
            p1=(line.p1[0] + offset_x, line.p1[1] + offset_y),
            confidence=line.confidence,
        )
        for line in detected_lines
    )


def predict_detected_lines(
    model: YOLO,
    frame_bgr: np.ndarray,
    conf_threshold: float,
    image_size: int,
    roi: tuple[int, int, int, int] | None = None,
) -> tuple[DetectedLine, ...]:
    source_frame = frame_bgr
    offset_x = 0
    offset_y = 0
    if roi is not None:
        x0, y0, x1, y1 = roi
        source_frame = frame_bgr[y0:y1, x0:x1]
        offset_x = x0
        offset_y = y0
        if source_frame.size == 0:
            return ()

    result = model.predict(
        source=source_frame,
        conf=conf_threshold,
        imgsz=image_size,
        verbose=False,
    )[0]
    detected_lines = extract_detected_lines(result, model.names)
    if roi is not None and detected_lines:
        detected_lines = offset_detected_lines(detected_lines, offset_x=offset_x, offset_y=offset_y)
    return detected_lines


def draw_detected_lines(
    frame_bgr: np.ndarray,
    detected_lines: tuple[DetectedLine, ...],
) -> np.ndarray:
    annotated = frame_bgr.copy()
    if not detected_lines:
        cv2.putText(
            annotated,
            "Waiting for fretboard detections...",
            (24, 36),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.9,
            (230, 230, 230),
            2,
            cv2.LINE_AA,
        )
        return annotated

    for line in detected_lines:
        color = CLASS_COLORS.get(line.kind, (255, 210, 90))
        p0 = tuple(int(round(value)) for value in line.p0)
        p1 = tuple(int(round(value)) for value in line.p1)
        cv2.line(annotated, p0, p1, color, 3, cv2.LINE_AA)
        cv2.circle(annotated, p0, 6, color, -1, cv2.LINE_AA)
        cv2.circle(annotated, p1, 6, color, -1, cv2.LINE_AA)

    return annotated


def draw_note_markers(
    frame_bgr: np.ndarray,
    note_markers: tuple[NoteMarker, ...],
    matched_marker_indices: tuple[int, ...] = (),
) -> np.ndarray:
    annotated = frame_bgr.copy()
    matched_index_set = set(matched_marker_indices)
    for marker_index, marker in enumerate(note_markers):
        center = tuple(int(round(value)) for value in marker.point_xy)
        color = NOTE_MARKER_MATCH_COLOR if marker_index in matched_index_set else NOTE_MARKER_COLOR
        if marker.is_open:
            cv2.circle(annotated, center, OPEN_NOTE_MARKER_RADIUS, color, OPEN_NOTE_MARKER_THICKNESS, cv2.LINE_AA)
        else:
            cv2.circle(annotated, center, FRETTED_NOTE_MARKER_RADIUS, color, -1, cv2.LINE_AA)
    return annotated


def draw_fingertips(
    frame_bgr: np.ndarray,
    fingertip_points: tuple[Fingertip, ...],
) -> np.ndarray:
    annotated = frame_bgr.copy()
    for fingertip in fingertip_points:
        center = tuple(int(round(value)) for value in fingertip.point_xy)
        cv2.circle(annotated, center, 7, FINGERTIP_COLOR, 2, cv2.LINE_AA)
        cv2.circle(annotated, center, 2, FINGERTIP_COLOR, -1, cv2.LINE_AA)
    return annotated


def draw_reference_quad(
    frame_bgr: np.ndarray,
    stable_grid: StableFretboardGrid | None,
    color: tuple[int, int, int] = (255, 255, 255),
) -> np.ndarray:
    if stable_grid is None or not stable_grid.ordered_fret_lines:
        return frame_bgr

    annotated = frame_bgr.copy()
    nut_line = stable_grid.nut_line
    reference_line = stable_grid.ordered_fret_lines[-1]
    quad_points = [nut_line.p0, nut_line.p1, reference_line.p1, reference_line.p0]
    polygon = np.asarray(
        [[(int(round(x_value)), int(round(y_value))) for x_value, y_value in quad_points]],
        dtype=np.int32,
    )
    cv2.polylines(annotated, polygon, True, color, 2, cv2.LINE_AA)
    return annotated


def draw_edge_guides(
    frame_bgr: np.ndarray,
    stable_grid: StableFretboardGrid | None,
    edge_refinement: EdgeRefinementResult | None = None,
    suppressed_fret_indices: Collection[int] | None = None,
) -> np.ndarray:
    if stable_grid is None or not stable_grid.string_crossings:
        return frame_bgr

    annotated = frame_bgr.copy()
    suppressed_indices = set(suppressed_fret_indices or ())
    fitted_string_indices = set(edge_refinement.fitted_string_indices if edge_refinement is not None else ())
    refined_fret_indices = set(edge_refinement.refined_fret_indices if edge_refinement is not None else ()) - suppressed_indices
    tracked_string_indices = set(edge_refinement.tracked_string_indices if edge_refinement is not None else ())
    tracked_fret_indices = set(edge_refinement.tracked_fret_indices if edge_refinement is not None else ()) - suppressed_indices
    drawable_string_indices = tracked_string_indices or fitted_string_indices
    drawable_fret_indices = tracked_fret_indices or refined_fret_indices

    if edge_refinement is None:
        fret_indices_to_draw = (
            fret_index
            for fret_index in range(len(stable_grid.ordered_fret_lines))
            if fret_index not in suppressed_indices
        )
    else:
        fret_indices_to_draw = (
            fret_index
            for fret_index in sorted(drawable_fret_indices)
            if 0 <= fret_index < len(stable_grid.ordered_fret_lines)
        )
    for fret_index in fret_indices_to_draw:
        line = stable_grid.ordered_fret_lines[fret_index]
        p0 = tuple(int(round(value)) for value in line.p0)
        p1 = tuple(int(round(value)) for value in line.p1)
        is_refined_fret = edge_refinement is None or fret_index in refined_fret_indices
        cv2.line(
            annotated,
            p0,
            p1,
            EDGE_GUIDE_FRET_COLOR if is_refined_fret else EDGE_GUIDE_INFERRED_FRET_COLOR,
            2 if is_refined_fret else 1,
            cv2.LINE_AA,
        )

    for string_index in range(len(stable_grid.string_crossings[0])):
        if edge_refinement is not None and string_index not in drawable_string_indices:
            continue
        points = [
            tuple(int(round(value)) for value in row[string_index])
            for row in stable_grid.string_crossings
            if string_index < len(row)
        ]
        if len(points) >= 2:
            is_detected_string = edge_refinement is None or string_index in fitted_string_indices
            cv2.polylines(
                annotated,
                [np.asarray(points, dtype=np.int32)],
                False,
                EDGE_GUIDE_STRING_COLOR if is_detected_string else EDGE_GUIDE_INFERRED_STRING_COLOR,
                2 if is_detected_string else 1,
                cv2.LINE_AA,
            )

    visible_row_indices = {0, *(fret_index + 1 for fret_index in drawable_fret_indices)}
    for row_index, row in enumerate(stable_grid.string_crossings):
        if edge_refinement is not None and row_index not in visible_row_indices:
            continue
        for string_index, point in enumerate(row):
            if edge_refinement is not None and string_index not in drawable_string_indices:
                continue
            center = tuple(int(round(value)) for value in point)
            if edge_refinement is not None and string_index not in fitted_string_indices:
                cv2.circle(annotated, center, 3, EDGE_GUIDE_INTERSECTION_COLOR, 1, cv2.LINE_AA)
            else:
                cv2.circle(annotated, center, 3, EDGE_GUIDE_INTERSECTION_COLOR, -1, cv2.LINE_AA)

    return annotated


def filter_note_markers_by_edge_frets(
    note_markers: tuple[NoteMarker, ...],
    edge_refinement: EdgeRefinementResult | None,
    edge_supported_notes_only: bool,
    suppressed_fret_indices: Collection[int] | None = None,
) -> tuple[NoteMarker, ...]:
    if not edge_supported_notes_only or edge_refinement is None:
        return note_markers

    suppressed_indices = set(suppressed_fret_indices or ())
    supported_fret_indices = (
        set(edge_refinement.refined_fret_indices) | set(edge_refinement.tracked_fret_indices)
    ) - suppressed_indices
    supported_frets = {fret_index + 1 for fret_index in supported_fret_indices}
    return tuple(
        marker
        for marker in note_markers
        if marker.is_open or marker.fret_index in supported_frets
    )


def reindex_edge_refinement_for_effective_grid(
    edge_refinement: EdgeRefinementResult | None,
    effective_grid: StableFretboardGrid | None,
    original_fret_count: int,
    suppressed_fret_indices: Collection[int] | None,
) -> EdgeRefinementResult | None:
    if edge_refinement is None:
        return None

    suppressed_indices = {
        int(fret_index)
        for fret_index in suppressed_fret_indices or ()
        if 0 <= int(fret_index) < original_fret_count
    }
    if not suppressed_indices:
        return EdgeRefinementResult(
            grid=effective_grid,
            fitted_string_indices=edge_refinement.fitted_string_indices,
            refined_fret_indices=edge_refinement.refined_fret_indices,
            tracked_string_indices=edge_refinement.tracked_string_indices,
            tracked_fret_indices=edge_refinement.tracked_fret_indices,
        )

    index_map: dict[int, int] = {}
    effective_index = 0
    for fret_index in range(original_fret_count):
        if fret_index in suppressed_indices:
            continue
        index_map[fret_index] = effective_index
        effective_index += 1

    return EdgeRefinementResult(
        grid=effective_grid,
        fitted_string_indices=edge_refinement.fitted_string_indices,
        refined_fret_indices=tuple(
            index_map[fret_index]
            for fret_index in edge_refinement.refined_fret_indices
            if fret_index in index_map
        ),
        tracked_string_indices=edge_refinement.tracked_string_indices,
        tracked_fret_indices=tuple(
            index_map[fret_index]
            for fret_index in edge_refinement.tracked_fret_indices
            if fret_index in index_map
        ),
    )


def marker_grid_context(
    stable_grid: StableFretboardGrid | None,
    edge_refinement: EdgeRefinementResult | None,
    suppressed_fret_indices: Collection[int] | None,
) -> tuple[StableFretboardGrid | None, EdgeRefinementResult | None]:
    original_fret_count = len(stable_grid.ordered_fret_lines) if stable_grid is not None else 0
    effective_grid = suppress_fret_indices_from_grid(stable_grid, suppressed_fret_indices)
    effective_edge_refinement = reindex_edge_refinement_for_effective_grid(
        edge_refinement=edge_refinement,
        effective_grid=effective_grid,
        original_fret_count=original_fret_count,
        suppressed_fret_indices=suppressed_fret_indices,
    )
    return effective_grid, effective_edge_refinement


def analyze_yolo_frame(
    frame_bgr: np.ndarray,
    model: YOLO,
    conf_threshold: float,
    image_size: int,
    tracker: FretboardTracker,
    hand_tracker: MediaPipeHandTracker,
    selected_note: str | None,
    reverse_strings: bool,
    center_test_mode: bool = False,
    show_edge_guides: bool = False,
    edge_supported_notes_only: bool = False,
    tracking_mode: str = "yolo_assist",
    string_tracker: StringLineTracker | None = None,
    fret_tracker: FretLineTracker | None = None,
    suppressed_fret_indices: Collection[int] | None = None,
) -> tuple[np.ndarray, FrameAnalysis]:
    detected_lines = predict_detected_lines(
        model=model,
        frame_bgr=frame_bgr,
        conf_threshold=conf_threshold,
        image_size=image_size,
    )
    class_counts = count_detected_classes(detected_lines)
    stable_grid = tracker.update(
        build_fretboard_grid(
            detected_lines,
            prior_grid=tracker.last_grid,
        )
    )
    edge_refinement = refine_grid_with_edge_lines_diagnostics(
        frame_bgr,
        stable_grid,
        string_tracker=string_tracker,
        fret_tracker=fret_tracker,
        suppressed_fret_indices=suppressed_fret_indices,
    )
    stable_grid = edge_refinement.grid
    marker_grid, marker_edge_refinement = marker_grid_context(
        stable_grid,
        edge_refinement,
        suppressed_fret_indices,
    )
    if center_test_mode:
        note_markers = build_center_test_note_markers(marker_grid, selected_note)
    else:
        note_markers = build_note_markers(marker_grid, selected_note, reverse_strings=reverse_strings)
    note_markers = filter_note_markers_by_edge_frets(
        note_markers,
        marker_edge_refinement,
        edge_supported_notes_only,
    )
    hand_tracking = hand_tracker.process(frame_bgr, note_markers)

    annotated = draw_detected_lines(frame_bgr, detected_lines)
    if show_edge_guides:
        annotated = draw_edge_guides(
            annotated,
            stable_grid,
            edge_refinement=edge_refinement,
            suppressed_fret_indices=suppressed_fret_indices,
        )
    annotated = draw_note_markers(
        annotated,
        note_markers,
        matched_marker_indices=hand_tracking.matched_marker_indices,
    )
    annotated = draw_fingertips(annotated, hand_tracking.fingertips)
    analysis = FrameAnalysis(
        detected_lines=detected_lines,
        stable_grid=stable_grid,
        note_markers=note_markers,
        fingertip_points=hand_tracking.fingertips,
        matched_marker_indices=hand_tracking.matched_marker_indices,
        class_counts=class_counts,
        hand_tracking_available=hand_tracking.backend_available,
        tracking_mode=tracking_mode,
        edge_refinement=edge_refinement,
    )
    return annotated, analysis


def analyze_selected_lines_frame(
    frame_bgr: np.ndarray,
    primary_detected_lines: tuple[DetectedLine, ...],
    fallback_detected_lines: tuple[DetectedLine, ...] | None,
    selected_line_tracker: SelectedLineTracker,
    note_marker_tracker: NoteMarkerTracker,
    hand_tracker: MediaPipeHandTracker,
    selected_note: str | None,
    reverse_strings: bool,
    center_test_mode: bool = False,
    show_edge_guides: bool = False,
    edge_supported_notes_only: bool = False,
    string_tracker: StringLineTracker | None = None,
    fret_tracker: FretLineTracker | None = None,
    suppressed_fret_indices: Collection[int] | None = None,
) -> tuple[np.ndarray, FrameAnalysis]:
    reference_fret_index = selected_line_tracker.reference_fret_index
    tracking_result = selected_line_tracker.update(
        primary_detected_lines=primary_detected_lines,
        fallback_detected_lines=fallback_detected_lines,
    )
    class_count_source = fallback_detected_lines if fallback_detected_lines else primary_detected_lines
    if tracking_result.stable_grid is None:
        note_marker_tracker.update(())
        hand_tracking = hand_tracker.process(frame_bgr, ())
        annotated = draw_detected_lines(frame_bgr, class_count_source)
        annotated = draw_fingertips(annotated, hand_tracking.fingertips)
        analysis = FrameAnalysis(
            detected_lines=class_count_source,
            stable_grid=None,
            note_markers=(),
            fingertip_points=hand_tracking.fingertips,
            matched_marker_indices=(),
            class_counts=count_detected_classes(class_count_source),
            hand_tracking_available=hand_tracking.backend_available,
            tracking_mode=tracking_result.tracking_mode,
            reference_fret_index=reference_fret_index,
        )
        return annotated, analysis

    edge_refinement = refine_grid_with_edge_lines_diagnostics(
        frame_bgr,
        tracking_result.stable_grid,
        string_tracker=string_tracker,
        fret_tracker=fret_tracker,
        suppressed_fret_indices=suppressed_fret_indices,
    )
    stable_grid = edge_refinement.grid
    tracked_lines = tracking_result.tracked_lines
    class_counts = count_detected_classes(class_count_source)
    marker_grid, marker_edge_refinement = marker_grid_context(
        stable_grid,
        edge_refinement,
        suppressed_fret_indices,
    )
    if center_test_mode:
        note_markers = build_center_test_note_markers(marker_grid, selected_note)
    else:
        note_markers = build_note_markers(marker_grid, selected_note, reverse_strings=reverse_strings)
    note_markers = filter_note_markers_by_edge_frets(
        note_markers,
        marker_edge_refinement,
        edge_supported_notes_only,
    )
    note_markers = note_marker_tracker.update(note_markers)
    hand_tracking = hand_tracker.process(frame_bgr, note_markers)
    quad_color = (255, 255, 255)
    if tracking_result.tracking_mode == "selected_lines_hold":
        quad_color = (0, 200, 255)
    elif tracking_result.tracking_mode == "selected_lines_reacquired":
        quad_color = (80, 255, 170)
    annotated = draw_detected_lines(frame_bgr, tracked_lines)
    annotated = draw_reference_quad(annotated, stable_grid, color=quad_color)
    if show_edge_guides:
        annotated = draw_edge_guides(
            annotated,
            stable_grid,
            edge_refinement=edge_refinement,
            suppressed_fret_indices=suppressed_fret_indices,
        )
    annotated = draw_note_markers(
        annotated,
        note_markers,
        matched_marker_indices=hand_tracking.matched_marker_indices,
    )
    annotated = draw_fingertips(annotated, hand_tracking.fingertips)
    analysis = FrameAnalysis(
        detected_lines=tracked_lines,
        stable_grid=stable_grid,
        note_markers=note_markers,
        fingertip_points=hand_tracking.fingertips,
        matched_marker_indices=hand_tracking.matched_marker_indices,
        class_counts=class_counts,
        hand_tracking_available=hand_tracking.backend_available,
        tracking_mode=tracking_result.tracking_mode,
        reference_fret_index=reference_fret_index,
        edge_refinement=edge_refinement,
    )
    return annotated, analysis


def enumerate_camera_indices(max_index: int = 5) -> list[int]:
    available: list[int] = []
    for camera_index in range(max_index):
        capture = cv2.VideoCapture(camera_index)
        try:
            if capture.isOpened():
                ok, _ = capture.read()
                if ok:
                    available.append(camera_index)
        finally:
            capture.release()
    return available


class VideoWorker(QThread):
    frame_ready = Signal(QImage, object)
    error_message = Signal(str)

    def __init__(
        self,
        model_path: Path,
        camera_index: int,
        conf_threshold: float,
        image_size: int,
        selected_note: str | None,
        reverse_strings: bool,
        center_test_mode: bool,
        show_edge_guides: bool,
        edge_supported_notes_only: bool,
        camera_calibration: CameraCalibration | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.model_path = model_path
        self.camera_index = camera_index
        self.conf_threshold = conf_threshold
        self.image_size = image_size
        self.selected_note = selected_note
        self.reverse_strings = reverse_strings
        self.center_test_mode = center_test_mode
        self.show_edge_guides = show_edge_guides
        self.edge_supported_notes_only = edge_supported_notes_only
        self.camera_calibration = camera_calibration
        self.tracker = FretboardTracker(max_history=5)
        self.note_marker_tracker = NoteMarkerTracker()
        self.selected_line_tracker = SelectedLineTracker(max_history=5)
        self.string_line_tracker = StringLineTracker()
        self.fret_line_tracker = FretLineTracker()
        self.hand_tracker = MediaPipeHandTracker()
        self.latest_frame_bgr: np.ndarray | None = None
        self.frozen_selection_lines: tuple[DetectedLine, ...] = ()
        self.selection_request = False
        self.selection_active = False
        self.pending_manual_selection: tuple[DetectedLine, DetectedLine, int] | None = None
        self.suppressed_fret_indices: set[int] = set()
        self._running = True

    def stop(self) -> None:
        self._running = False
        self.wait(2000)

    def set_conf_threshold(self, conf_threshold: float) -> None:
        self.conf_threshold = conf_threshold

    def set_image_size(self, image_size: int) -> None:
        self.image_size = image_size

    def set_selected_note(self, selected_note: str | None) -> None:
        self.selected_note = selected_note
        self.note_marker_tracker.clear()

    def set_reverse_strings(self, reverse_strings: bool) -> None:
        self.reverse_strings = reverse_strings
        self.note_marker_tracker.clear()

    def set_center_test_mode(self, center_test_mode: bool) -> None:
        self.center_test_mode = center_test_mode
        self.note_marker_tracker.clear()

    def set_show_edge_guides(self, show_edge_guides: bool) -> None:
        self.show_edge_guides = show_edge_guides

    def set_edge_supported_notes_only(self, edge_supported_notes_only: bool) -> None:
        self.edge_supported_notes_only = edge_supported_notes_only
        self.note_marker_tracker.clear()

    def suppress_fret_index(self, fret_index: int) -> None:
        if fret_index < 0:
            return
        next_indices = set(self.suppressed_fret_indices)
        next_indices.add(int(fret_index))
        self.suppressed_fret_indices = next_indices
        self.fret_line_tracker.clear_indices((int(fret_index),))
        self.note_marker_tracker.clear()

    def clear_suppressed_frets(self) -> None:
        if not self.suppressed_fret_indices:
            return
        cleared_indices = tuple(self.suppressed_fret_indices)
        self.suppressed_fret_indices = set()
        self.fret_line_tracker.clear_indices(cleared_indices)
        self.note_marker_tracker.clear()

    def begin_line_selection(self) -> bool:
        if self.latest_frame_bgr is None:
            return False
        self.note_marker_tracker.clear()
        self.string_line_tracker.clear()
        self.fret_line_tracker.clear()
        self.selection_request = True
        return True

    def cancel_line_selection(self) -> None:
        self.selection_request = False
        self.selection_active = False
        self.frozen_selection_lines = ()
        self.pending_manual_selection = None

    def apply_line_selection(
        self,
        nut_line: DetectedLine,
        reference_fret_line: DetectedLine,
        reference_fret_index: int,
    ) -> None:
        self.pending_manual_selection = (nut_line, reference_fret_line, reference_fret_index)

    def clear_manual_tracking(self) -> None:
        self.selected_line_tracker.clear()
        self.string_line_tracker.clear()
        self.fret_line_tracker.clear()
        self.note_marker_tracker.clear()
        self.cancel_line_selection()

    def run(self) -> None:
        try:
            model = load_yolo_model(self.model_path)
        except Exception as exc:  # pragma: no cover - GUI error path
            self.error_message.emit(str(exc))
            return

        capture = cv2.VideoCapture(self.camera_index)
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

        if not capture.isOpened():
            self.error_message.emit(
                f"Could not open webcam index {self.camera_index}. Check camera permission or another camera index."
            )
            capture.release()
            return

        previous_frame_time = perf_counter()

        while self._running:
            if self.pending_manual_selection is not None:
                nut_line, reference_fret_line, reference_fret_index = self.pending_manual_selection
                self.selected_line_tracker.initialize_from_lines(
                    nut_line,
                    reference_fret_line,
                    reference_fret_index,
                )
                self.pending_manual_selection = None
                self.selection_active = False
                self.frozen_selection_lines = ()

            if self.selection_active:
                self.msleep(30)
                continue

            ok, frame_bgr = capture.read()
            if not ok:
                self.error_message.emit("Failed to read frame from webcam.")
                break

            if self.camera_calibration is not None:
                frame_bgr = self.camera_calibration.undistort_frame(frame_bgr)
            frame_bgr = cv2.flip(frame_bgr, 1)

            self.latest_frame_bgr = frame_bgr.copy()
            selected_note = self.selected_note
            reverse_strings = self.reverse_strings
            center_test_mode = self.center_test_mode
            show_edge_guides = self.show_edge_guides
            edge_supported_notes_only = self.edge_supported_notes_only
            suppressed_fret_indices = frozenset(self.suppressed_fret_indices)

            inference_start = perf_counter()

            if self.selection_request:
                detected_lines = predict_detected_lines(
                    model=model,
                    frame_bgr=frame_bgr,
                    conf_threshold=self.conf_threshold,
                    image_size=self.image_size,
                )
                self.selected_line_tracker.clear()
                class_counts = count_detected_classes(detected_lines)
                stable_grid = self.tracker.update(
                    build_fretboard_grid(
                        detected_lines,
                        prior_grid=self.tracker.last_grid,
                    )
                )
                edge_refinement = refine_grid_with_edge_lines_diagnostics(
                    frame_bgr,
                    stable_grid,
                    string_tracker=self.string_line_tracker,
                    fret_tracker=self.fret_line_tracker,
                    suppressed_fret_indices=suppressed_fret_indices,
                )
                stable_grid = edge_refinement.grid
                marker_grid, marker_edge_refinement = marker_grid_context(
                    stable_grid,
                    edge_refinement,
                    suppressed_fret_indices,
                )
                if center_test_mode:
                    note_markers = build_center_test_note_markers(marker_grid, selected_note)
                else:
                    note_markers = build_note_markers(marker_grid, selected_note, reverse_strings=reverse_strings)
                note_markers = filter_note_markers_by_edge_frets(
                    note_markers,
                    marker_edge_refinement,
                    edge_supported_notes_only,
                )
                note_markers = self.note_marker_tracker.update(note_markers)
                hand_tracking = self.hand_tracker.process(frame_bgr, note_markers)
                annotated = draw_detected_lines(frame_bgr, detected_lines)
                if show_edge_guides:
                    annotated = draw_edge_guides(
                        annotated,
                        stable_grid,
                        edge_refinement=edge_refinement,
                        suppressed_fret_indices=suppressed_fret_indices,
                    )
                annotated = draw_note_markers(
                    annotated,
                    note_markers,
                    matched_marker_indices=hand_tracking.matched_marker_indices,
                )
                annotated = draw_fingertips(annotated, hand_tracking.fingertips)
                analysis = FrameAnalysis(
                    detected_lines=detected_lines,
                    stable_grid=stable_grid,
                    note_markers=note_markers,
                    fingertip_points=hand_tracking.fingertips,
                    matched_marker_indices=hand_tracking.matched_marker_indices,
                    class_counts=class_counts,
                    hand_tracking_available=hand_tracking.backend_available,
                    tracking_mode="selection_ready",
                    edge_refinement=edge_refinement,
                )
                self.selection_request = False
                self.selection_active = True
                self.frozen_selection_lines = analysis.detected_lines
            elif self.selected_line_tracker.is_active:
                roi_detected_lines: tuple[DetectedLine, ...] = ()
                fallback_detected_lines: tuple[DetectedLine, ...] | None = None
                search_roi = self.selected_line_tracker.search_roi(frame_bgr.shape)
                if search_roi is not None:
                    roi_detected_lines = predict_detected_lines(
                        model=model,
                        frame_bgr=frame_bgr,
                        conf_threshold=max(TRACKING_CONF_FLOOR, self.conf_threshold * TRACKING_CONF_SCALE),
                        image_size=self.image_size,
                        roi=search_roi,
                    )

                roi_counts = count_detected_classes(roi_detected_lines)
                if (
                    search_roi is None
                    or self.selected_line_tracker.missed_frames > 0
                    or roi_counts.get("nut", 0) == 0
                    or roi_counts.get("fret", 0) == 0
                ):
                    fallback_detected_lines = predict_detected_lines(
                        model=model,
                        frame_bgr=frame_bgr,
                        conf_threshold=max(REACQUIRE_CONF_FLOOR, self.conf_threshold * REACQUIRE_CONF_SCALE),
                        image_size=self.image_size,
                    )

                annotated, analysis = analyze_selected_lines_frame(
                    frame_bgr=frame_bgr,
                    primary_detected_lines=roi_detected_lines,
                    fallback_detected_lines=fallback_detected_lines,
                    selected_line_tracker=self.selected_line_tracker,
                    note_marker_tracker=self.note_marker_tracker,
                    hand_tracker=self.hand_tracker,
                    selected_note=selected_note,
                    reverse_strings=reverse_strings,
                    center_test_mode=center_test_mode,
                    show_edge_guides=show_edge_guides,
                    edge_supported_notes_only=edge_supported_notes_only,
                    string_tracker=self.string_line_tracker,
                    fret_tracker=self.fret_line_tracker,
                    suppressed_fret_indices=suppressed_fret_indices,
                )
            else:
                detected_lines = predict_detected_lines(
                    model=model,
                    frame_bgr=frame_bgr,
                    conf_threshold=self.conf_threshold,
                    image_size=self.image_size,
                )
                class_counts = count_detected_classes(detected_lines)
                stable_grid = self.tracker.update(
                    build_fretboard_grid(
                        detected_lines,
                        prior_grid=self.tracker.last_grid,
                    )
                )
                edge_refinement = refine_grid_with_edge_lines_diagnostics(
                    frame_bgr,
                    stable_grid,
                    string_tracker=self.string_line_tracker,
                    fret_tracker=self.fret_line_tracker,
                    suppressed_fret_indices=suppressed_fret_indices,
                )
                stable_grid = edge_refinement.grid
                marker_grid, marker_edge_refinement = marker_grid_context(
                    stable_grid,
                    edge_refinement,
                    suppressed_fret_indices,
                )
                if center_test_mode:
                    note_markers = build_center_test_note_markers(marker_grid, selected_note)
                else:
                    note_markers = build_note_markers(marker_grid, selected_note, reverse_strings=reverse_strings)
                note_markers = filter_note_markers_by_edge_frets(
                    note_markers,
                    marker_edge_refinement,
                    edge_supported_notes_only,
                )
                note_markers = self.note_marker_tracker.update(note_markers)
                hand_tracking = self.hand_tracker.process(frame_bgr, note_markers)
                annotated = draw_detected_lines(frame_bgr, detected_lines)
                if show_edge_guides:
                    annotated = draw_edge_guides(
                        annotated,
                        stable_grid,
                        edge_refinement=edge_refinement,
                        suppressed_fret_indices=suppressed_fret_indices,
                    )
                annotated = draw_note_markers(
                    annotated,
                    note_markers,
                    matched_marker_indices=hand_tracking.matched_marker_indices,
                )
                annotated = draw_fingertips(annotated, hand_tracking.fingertips)
                analysis = FrameAnalysis(
                    detected_lines=detected_lines,
                    stable_grid=stable_grid,
                    note_markers=note_markers,
                    fingertip_points=hand_tracking.fingertips,
                    matched_marker_indices=hand_tracking.matched_marker_indices,
                    class_counts=class_counts,
                    hand_tracking_available=hand_tracking.backend_available,
                    tracking_mode="yolo_assist",
                    edge_refinement=edge_refinement,
                )
            inference_ms = (perf_counter() - inference_start) * 1000.0

            now = perf_counter()
            fps = 1.0 / max(now - previous_frame_time, 1e-6)
            previous_frame_time = now

            visible_fret_count = 0
            if analysis.stable_grid is not None:
                suppressed_visible_count = len(
                    {
                        fret_index
                        for fret_index in suppressed_fret_indices
                        if 0 <= fret_index < len(analysis.stable_grid.ordered_fret_lines)
                    }
                )
                visible_fret_count = len(analysis.stable_grid.ordered_fret_lines) - suppressed_visible_count

            stats = InferenceStats(
                fps=fps,
                inference_ms=inference_ms,
                total_detections=sum(analysis.class_counts.values()),
                class_counts=analysis.class_counts,
                visible_fret_count=visible_fret_count,
                marker_count=len(analysis.note_markers),
                matched_marker_count=len(analysis.matched_marker_indices),
                fingertip_count=len(analysis.fingertip_points),
                selected_note=selected_note or "없음",
                center_test_mode=center_test_mode,
                hand_tracking_available=analysis.hand_tracking_available,
                tracking_mode=analysis.tracking_mode,
                reference_fret_index=analysis.reference_fret_index,
                edge_string_count=(
                    len(analysis.edge_refinement.tracked_string_indices)
                    if analysis.edge_refinement is not None
                    else 0
                ),
                edge_fret_count=(
                    len(analysis.edge_refinement.refined_fret_indices)
                    if analysis.edge_refinement is not None
                    else 0
                ),
                suppressed_fret_count=len(suppressed_fret_indices),
                stable_grid=analysis.stable_grid,
                edge_refinement=analysis.edge_refinement,
            )
            self.frame_ready.emit(bgr_to_qimage(annotated), stats)

        capture.release()
        self.hand_tracker.close()


class MainWindow(QMainWindow):
    def __init__(
        self,
        model_path: Path,
        camera_index: int = 0,
        camera_calibration: CameraCalibration | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Guitar Fretboard YOLO Viewer")
        self.resize(1360, 900)

        self.model_path = model_path
        self.camera_calibration = camera_calibration
        self.worker: VideoWorker | None = None
        self.current_image: QImage | None = None
        self.selection_stage = "idle"
        self.selected_nut_line: DetectedLine | None = None
        self.selected_reference_line: DetectedLine | None = None
        self.latest_stable_grid: StableFretboardGrid | None = None
        self.latest_edge_refinement: EdgeRefinementResult | None = None
        self.suppressed_fret_indices: set[int] = set()

        self.video_label = VideoLabel("Press Start to open the webcam.")
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setMinimumSize(960, 540)
        self.video_label.setStyleSheet(
            "background-color: #101418; color: #e8eef2; border: 1px solid #24303a; font-size: 20px;"
        )
        self.video_label.clicked.connect(self.handle_video_click)

        self.model_value = QLabel(str(self.model_path))
        self.model_value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.camera_combo = QComboBox()
        self.refresh_camera_button = QPushButton("Refresh Cameras")
        self.refresh_camera_button.clicked.connect(self.populate_camera_list)

        self.note_combo = QComboBox()
        self.note_combo.addItems(NOTE_CHOICES)
        self.note_combo.currentTextChanged.connect(self.handle_note_change)

        self.reverse_checkbox = QCheckBox("현 방향 반전")
        self.reverse_checkbox.stateChanged.connect(self.handle_reverse_change)
        self.center_test_checkbox = QCheckBox("중앙 테스트 점")
        self.center_test_checkbox.stateChanged.connect(self.handle_center_test_change)
        self.edge_guides_checkbox = QCheckBox("줄/교차점 표시")
        self.edge_guides_checkbox.setChecked(True)
        self.edge_guides_checkbox.stateChanged.connect(self.handle_edge_guides_change)
        self.edge_supported_notes_checkbox = QCheckBox("검출 프렛만 후보")
        self.edge_supported_notes_checkbox.stateChanged.connect(self.handle_edge_supported_notes_change)

        self.reference_fret_spin = QSpinBox()
        self.reference_fret_spin.setRange(1, 24)
        self.reference_fret_spin.setValue(12)
        self.reference_fret_spin.setEnabled(False)
        self.reference_fret_spin.setToolTip("현재 보이는 마지막 프렛을 기준으로 자동 추정됩니다.")

        self.select_lines_button = QPushButton("Select Nut + Last Visible Fret")
        self.clear_tracking_button = QPushButton("Clear Manual Tracking")
        self.delete_fret_button = QPushButton("잘못된 프렛 삭제")
        self.clear_deleted_frets_button = QPushButton("삭제 프렛 초기화")
        self.select_lines_button.clicked.connect(self.begin_line_selection)
        self.clear_tracking_button.clicked.connect(self.clear_manual_tracking)
        self.delete_fret_button.clicked.connect(self.toggle_fret_deletion)
        self.clear_deleted_frets_button.clicked.connect(self.clear_deleted_frets)

        self.conf_slider = QSlider(Qt.Orientation.Horizontal)
        self.conf_slider.setRange(10, 95)
        self.conf_slider.setValue(25)
        self.conf_slider.valueChanged.connect(self.handle_conf_change)
        self.conf_value = QLabel("0.25")

        self.image_size_spin = QSpinBox()
        self.image_size_spin.setRange(320, 1600)
        self.image_size_spin.setSingleStep(32)
        self.image_size_spin.setValue(960)
        self.image_size_spin.valueChanged.connect(self.handle_image_size_change)

        self.start_button = QPushButton("Start")
        self.stop_button = QPushButton("Stop")
        self.browse_button = QPushButton("Choose Model")

        self.start_button.clicked.connect(self.start_detection)
        self.stop_button.clicked.connect(self.stop_detection)
        self.browse_button.clicked.connect(self.choose_model_file)
        self.stop_button.setEnabled(False)

        self.mode_label = QLabel("Mode: YOLO assist")
        self.status_label = QLabel("선택 음: 없음 / 후보 0개 / 정확 운지 0개 / visible frets: 0")
        self.fps_label = QLabel("FPS: -")
        self.inference_label = QLabel("Inference: -")
        self.counts_label = QLabel("Detections: -")
        self.configure_dynamic_labels()

        controls = self._build_controls()
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addWidget(self.video_label, stretch=1)
        layout.addWidget(controls)
        self.setCentralWidget(root)

        self.populate_camera_list(selected=camera_index)

    def configure_dynamic_labels(self) -> None:
        self._set_fixed_label_width(self.conf_value, "0.00", Qt.AlignmentFlag.AlignRight)
        self._set_fixed_label_width(self.fps_label, "FPS: 000.0", Qt.AlignmentFlag.AlignLeft)
        self._set_fixed_label_width(self.inference_label, "Inference: 0000.0 ms", Qt.AlignmentFlag.AlignLeft)
        self._set_fixed_label_width(
            self.counts_label,
            "Detections: 0000 (fret: 000, nut: 000) / edge strings: 6/6 / edge frets: 24/24 / deleted frets: 24 / fingertips: 10",
            Qt.AlignmentFlag.AlignLeft,
        )
        self.status_label.setMinimumWidth(
            self._text_width(
                "선택 음: 높은 도 / 후보 000개 / 정확 운지 000개 / visible frets: 24 / 중앙 테스트 점 / 검출 프렛 후보 / 삭제 프렛 24개"
            )
        )

    def _set_fixed_label_width(
        self,
        label: QLabel,
        sample_text: str,
        alignment: Qt.AlignmentFlag,
    ) -> None:
        label.setFixedWidth(self._text_width(sample_text))
        label.setAlignment(alignment | Qt.AlignmentFlag.AlignVCenter)

    def _text_width(self, sample_text: str, padding: int = 18) -> int:
        metrics = QFontMetrics(self.font())
        return metrics.horizontalAdvance(sample_text) + padding

    def _build_controls(self) -> QWidget:
        controls_box = QGroupBox("Controls")
        layout = QGridLayout(controls_box)

        layout.addWidget(QLabel("Model"), 0, 0)
        layout.addWidget(self.model_value, 0, 1, 1, 4)
        layout.addWidget(self.browse_button, 0, 5)

        layout.addWidget(QLabel("Camera"), 1, 0)
        layout.addWidget(self.camera_combo, 1, 1)
        layout.addWidget(self.refresh_camera_button, 1, 2)

        layout.addWidget(QLabel("Confidence"), 1, 3)
        layout.addWidget(self.conf_slider, 1, 4)
        layout.addWidget(self.conf_value, 1, 5)

        layout.addWidget(QLabel("Selected Note"), 2, 0)
        layout.addWidget(self.note_combo, 2, 1)
        layout.addWidget(self.reverse_checkbox, 2, 2)
        layout.addWidget(self.center_test_checkbox, 2, 3)
        layout.addWidget(self.edge_guides_checkbox, 2, 4)
        layout.addWidget(self.edge_supported_notes_checkbox, 2, 5)
        layout.addWidget(QLabel("Image Size"), 2, 6)
        layout.addWidget(self.image_size_spin, 2, 7)

        layout.addWidget(QLabel("Reference Fret (auto)"), 3, 0)
        layout.addWidget(self.reference_fret_spin, 3, 1)

        tracking_row = QHBoxLayout()
        tracking_row.addWidget(self.select_lines_button)
        tracking_row.addWidget(self.clear_tracking_button)
        tracking_row.addWidget(self.delete_fret_button)
        tracking_row.addWidget(self.clear_deleted_frets_button)
        layout.addLayout(tracking_row, 3, 2, 1, 4)

        button_row = QHBoxLayout()
        button_row.addWidget(self.start_button)
        button_row.addWidget(self.stop_button)
        layout.addLayout(button_row, 3, 6, 1, 2)

        layout.addWidget(QLabel("Mode"), 4, 0)
        layout.addWidget(self.mode_label, 4, 1, 1, 7)
        layout.addWidget(QLabel("Status"), 5, 0)
        layout.addWidget(self.status_label, 5, 1, 1, 7)
        layout.addWidget(self.fps_label, 6, 5)
        layout.addWidget(self.inference_label, 6, 6)
        layout.addWidget(self.counts_label, 6, 7)

        return controls_box

    def selected_note(self) -> str | None:
        note = self.note_combo.currentText()
        return None if note == "없음" else note

    def update_note_status(
        self,
        marker_count: int = 0,
        visible_frets: int = 0,
        matched_count: int = 0,
    ) -> None:
        note_label = self.selected_note() or "없음"
        suffix = " / 중앙 테스트 점" if self.center_test_checkbox.isChecked() else ""
        if self.edge_supported_notes_checkbox.isChecked():
            suffix += " / 검출 프렛 후보"
        if self.suppressed_fret_indices:
            suffix += f" / 삭제 프렛 {len(self.suppressed_fret_indices)}개"
        self.status_label.setText(
            f"선택 음: {note_label} / 후보 {marker_count}개 / 정확 운지 {matched_count}개 / visible frets: {visible_frets}{suffix}"
        )

    def populate_camera_list(self, selected: int | None = None) -> None:
        current_selection = selected
        if current_selection is None and self.camera_combo.currentData() is not None:
            current_selection = int(self.camera_combo.currentData())

        self.camera_combo.clear()
        available_cameras = enumerate_camera_indices()
        if not available_cameras:
            available_cameras = [0]

        for camera_index in available_cameras:
            self.camera_combo.addItem(f"Camera {camera_index}", camera_index)

        if current_selection in available_cameras:
            index = available_cameras.index(current_selection)
            self.camera_combo.setCurrentIndex(index)

    def handle_conf_change(self, value: int) -> None:
        confidence = value / 100.0
        self.conf_value.setText(f"{confidence:.2f}")
        if self.worker is not None:
            self.worker.set_conf_threshold(confidence)

    def handle_image_size_change(self, value: int) -> None:
        if self.worker is not None:
            self.worker.set_image_size(value)

    def handle_note_change(self, _: str) -> None:
        if self.worker is not None:
            self.worker.set_selected_note(self.selected_note())
        else:
            self.update_note_status(marker_count=0, visible_frets=0)

    def handle_reverse_change(self, _: int) -> None:
        if self.worker is not None:
            self.worker.set_reverse_strings(self.reverse_checkbox.isChecked())

    def handle_center_test_change(self, _: int) -> None:
        if self.worker is not None:
            self.worker.set_center_test_mode(self.center_test_checkbox.isChecked())
        else:
            self.update_note_status(marker_count=0, visible_frets=0)

    def handle_edge_guides_change(self, _: int) -> None:
        if self.worker is not None:
            self.worker.set_show_edge_guides(self.edge_guides_checkbox.isChecked())

    def handle_edge_supported_notes_change(self, _: int) -> None:
        if self.worker is not None:
            self.worker.set_edge_supported_notes_only(self.edge_supported_notes_checkbox.isChecked())
        else:
            self.update_note_status(marker_count=0, visible_frets=0)

    def choose_model_file(self) -> None:
        file_name, _ = QFileDialog.getOpenFileName(
            self,
            "Select YOLO Weights",
            str(self.model_path.parent),
            "YOLO weights (*.pt *.onnx *.engine *.pth);;All files (*)",
        )
        if not file_name:
            return

        self.model_path = Path(file_name).expanduser().resolve()
        self.model_value.setText(str(self.model_path))
        self.update_note_status(marker_count=0, visible_frets=0)

        if self.worker is not None:
            self.stop_detection()

    def begin_line_selection(self) -> None:
        if self.worker is None or not self.worker.isRunning():
            QMessageBox.information(self, "Start First", "웹캠을 먼저 시작한 뒤 선 선택을 진행해주세요.")
            return
        if not self.worker.begin_line_selection():
            QMessageBox.warning(self, "No Frame", "현재 프레임이 준비되지 않아 선 선택을 시작할 수 없습니다.")
            return

        self.selection_stage = "await_frame"
        self.selected_nut_line = None
        self.selected_reference_line = None
        self.video_label.setCursor(Qt.CursorShape.CrossCursor)
        self.mode_label.setText("Mode: preparing selectable YOLO lines")

    def clear_manual_tracking(self) -> None:
        self.selection_stage = "idle"
        self.selected_nut_line = None
        self.selected_reference_line = None
        self.delete_fret_button.setText("잘못된 프렛 삭제")
        self.video_label.setCursor(Qt.CursorShape.ArrowCursor)
        if self.worker is not None:
            self.worker.clear_manual_tracking()
        self.mode_label.setText("Mode: YOLO assist")
        self.render_current_frame()

    def toggle_fret_deletion(self) -> None:
        if self.selection_stage == "delete_fret":
            self.selection_stage = "idle"
            self.delete_fret_button.setText("잘못된 프렛 삭제")
            self.video_label.setCursor(Qt.CursorShape.ArrowCursor)
            self.mode_label.setText("Mode: YOLO assist")
            self.render_current_frame()
            return

        if self.worker is None or not self.worker.isRunning():
            QMessageBox.information(self, "Start First", "웹캠을 먼저 시작한 뒤 잘못된 프렛을 선택해주세요.")
            return
        if self.latest_stable_grid is None:
            QMessageBox.warning(self, "No Grid", "아직 프렛보드 기준선이 준비되지 않았습니다.")
            return

        if self.selection_stage in {"select_nut", "select_fret", "await_frame"}:
            self.worker.cancel_line_selection()
            self.selected_nut_line = None
            self.selected_reference_line = None

        self.selection_stage = "delete_fret"
        self.delete_fret_button.setText("삭제 완료")
        self.video_label.setCursor(Qt.CursorShape.CrossCursor)
        self.mode_label.setText("Mode: click a wrong edge fret to delete")
        self.render_current_frame()

    def clear_deleted_frets(self) -> None:
        self.suppressed_fret_indices.clear()
        if self.worker is not None:
            self.worker.clear_suppressed_frets()
        if self.selection_stage == "delete_fret":
            self.mode_label.setText("Mode: click a wrong edge fret to delete")
        else:
            self.mode_label.setText("Mode: deleted fret list cleared")
        self.update_note_status()
        self.render_current_frame()

    def map_label_click_to_image(self, label_x: float, label_y: float) -> tuple[float, float] | None:
        if self.current_image is None:
            return None

        image_width = self.current_image.width()
        image_height = self.current_image.height()
        if image_width <= 0 or image_height <= 0:
            return None

        label_width = self.video_label.width()
        label_height = self.video_label.height()
        scale = min(label_width / image_width, label_height / image_height)
        display_width = image_width * scale
        display_height = image_height * scale
        offset_x = (label_width - display_width) / 2.0
        offset_y = (label_height - display_height) / 2.0

        if label_x < offset_x or label_x > offset_x + display_width:
            return None
        if label_y < offset_y or label_y > offset_y + display_height:
            return None

        image_x = (label_x - offset_x) / scale
        image_y = (label_y - offset_y) / scale
        return (float(image_x), float(image_y))

    def handle_video_click(self, label_x: float, label_y: float) -> None:
        if self.selection_stage == "delete_fret":
            self.handle_fret_deletion_click(label_x, label_y)
            return

        if self.selection_stage not in {"select_nut", "select_fret"} or self.worker is None:
            return

        image_point = self.map_label_click_to_image(label_x, label_y)
        if image_point is None:
            return

        if self.selection_stage == "select_nut":
            candidates = tuple(line for line in self.worker.frozen_selection_lines if line.kind == "nut")
            selected_line = find_nearest_line(image_point, candidates)
            if selected_line is None:
                self.mode_label.setText("Mode: click closer to the nut line")
                return
            self.selected_nut_line = selected_line
            self.selection_stage = "select_fret"
            self.mode_label.setText("Mode: selected nut - click the last visible fret")
        else:
            candidates = tuple(line for line in self.worker.frozen_selection_lines if line.kind == "fret")
            selected_line = find_nearest_line(image_point, candidates)
            if selected_line is None:
                self.mode_label.setText("Mode: click closer to the last visible fret line")
                return
            self.selected_reference_line = selected_line
            if self.selected_nut_line is not None:
                inferred_reference_fret = infer_selected_reference_fret_index(
                    self.selected_nut_line,
                    selected_line,
                    self.worker.frozen_selection_lines,
                )
                self.reference_fret_spin.setValue(inferred_reference_fret)
                self.worker.apply_line_selection(
                    self.selected_nut_line,
                    selected_line,
                    inferred_reference_fret,
                )
            self.selection_stage = "idle"
            self.video_label.setCursor(Qt.CursorShape.ArrowCursor)
            self.mode_label.setText(
                f"Mode: applying manual tracking ({self.reference_fret_spin.value()}f last visible reference)"
            )

        self.render_current_frame()

    def handle_fret_deletion_click(self, label_x: float, label_y: float) -> None:
        if self.worker is None:
            return

        image_point = self.map_label_click_to_image(label_x, label_y)
        if image_point is None:
            return

        fret_index = find_nearest_fret_index(
            image_point,
            self.latest_stable_grid,
            self.latest_edge_refinement,
            suppressed_fret_indices=self.suppressed_fret_indices,
        )
        if fret_index is None:
            self.mode_label.setText("Mode: click closer to an edge fret")
            return

        self.suppressed_fret_indices.add(fret_index)
        self.worker.suppress_fret_index(fret_index)
        self.mode_label.setText(f"Mode: deleted edge fret {fret_index + 1} - click another or Done")
        self.update_note_status()
        self.render_current_frame()

    def decorate_selection_frame(self, image: QImage) -> QImage:
        preview = image.copy()
        painter = QPainter(preview)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if self.selected_nut_line is not None:
            painter.setPen(QPen(QColor(70, 255, 150), 5))
            painter.drawLine(
                int(round(self.selected_nut_line.p0[0])),
                int(round(self.selected_nut_line.p0[1])),
                int(round(self.selected_nut_line.p1[0])),
                int(round(self.selected_nut_line.p1[1])),
            )

        if self.selected_reference_line is not None:
            painter.setPen(QPen(QColor(0, 218, 255), 5))
            painter.drawLine(
                int(round(self.selected_reference_line.p0[0])),
                int(round(self.selected_reference_line.p0[1])),
                int(round(self.selected_reference_line.p1[0])),
                int(round(self.selected_reference_line.p1[1])),
            )

        if self.selection_stage == "select_nut":
            instruction = "Click the YOLO-detected nut line"
        elif self.selection_stage == "select_fret":
            instruction = "Click the YOLO-detected last visible fret line"
        elif self.selection_stage == "delete_fret":
            instruction = "Click a wrong edge fret to delete it"
        else:
            instruction = "Selecting YOLO lines"

        painter.setPen(QPen(QColor(255, 255, 255), 2))
        painter.drawText(24, 36, instruction)
        painter.end()
        return preview

    def render_current_frame(self) -> None:
        if self.current_image is None:
            return

        image = self.current_image
        if self.selection_stage in {"select_nut", "select_fret", "delete_fret"}:
            image = self.decorate_selection_frame(image)

        pixmap = QPixmap.fromImage(image)
        scaled = pixmap.scaled(
            self.video_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.video_label.setPixmap(scaled)

    def start_detection(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            return

        model_path = self.model_path
        if not model_path.exists():
            QMessageBox.critical(self, "Missing Weights", f"Model file does not exist:\n{model_path}")
            return

        camera_index = int(self.camera_combo.currentData() or 0)
        confidence = self.conf_slider.value() / 100.0
        image_size = int(self.image_size_spin.value())

        self.worker = VideoWorker(
            model_path=model_path,
            camera_index=camera_index,
            conf_threshold=confidence,
            image_size=image_size,
            selected_note=self.selected_note(),
            reverse_strings=self.reverse_checkbox.isChecked(),
            center_test_mode=self.center_test_checkbox.isChecked(),
            show_edge_guides=self.edge_guides_checkbox.isChecked(),
            edge_supported_notes_only=self.edge_supported_notes_checkbox.isChecked(),
            camera_calibration=self.camera_calibration,
            parent=self,
        )
        self.worker.frame_ready.connect(self.update_frame)
        self.worker.error_message.connect(self.handle_worker_error)
        self.worker.finished.connect(self.on_worker_finished)
        for fret_index in self.suppressed_fret_indices:
            self.worker.suppress_fret_index(fret_index)

        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.selection_stage = "idle"
        self.delete_fret_button.setText("잘못된 프렛 삭제")
        self.video_label.setCursor(Qt.CursorShape.ArrowCursor)
        self.mode_label.setText("Mode: YOLO assist")
        self.update_note_status(marker_count=0, visible_frets=0)
        self.worker.start()

    def stop_detection(self) -> None:
        if self.worker is None:
            return
        self.mode_label.setText("Mode: stopping")
        self.worker.stop()

    def update_frame(self, image: QImage, stats: InferenceStats) -> None:
        self.current_image = image
        self.latest_stable_grid = stats.stable_grid
        self.latest_edge_refinement = stats.edge_refinement

        if stats.tracking_mode == "selection_ready" and self.selection_stage == "await_frame":
            self.selection_stage = "select_nut"
            self.mode_label.setText("Mode: click the nut line")
        elif self.selection_stage == "idle":
            if stats.tracking_mode == "selected_lines_tracking":
                self.mode_label.setText(
                    f"Mode: YOLO selected-line tracking ({stats.reference_fret_index}f reference)"
                )
            elif stats.tracking_mode == "selected_lines_reacquired":
                self.mode_label.setText(
                    f"Mode: full-frame reacquired ({stats.reference_fret_index}f reference)"
                )
            elif stats.tracking_mode == "selected_lines_hold":
                self.mode_label.setText(
                    f"Mode: hold + reacquire ({stats.reference_fret_index}f reference)"
                )
            elif stats.tracking_mode == "selected_lines_lost":
                self.mode_label.setText("Mode: selected lines lost - select lines again")
            else:
                self.mode_label.setText("Mode: YOLO assist")

        self.render_current_frame()
        suffix = " / 중앙 테스트 점" if stats.center_test_mode else ""
        if self.edge_supported_notes_checkbox.isChecked():
            suffix += " / 검출 프렛 후보"
        if stats.suppressed_fret_count:
            suffix += f" / 삭제 프렛 {stats.suppressed_fret_count}개"
        hand_suffix = ""
        if not stats.hand_tracking_available:
            hand_suffix = " / 손 검출 비활성"
        self.status_label.setText(
            f"선택 음: {stats.selected_note} / 후보 {stats.marker_count}개 / 정확 운지 {stats.matched_marker_count}개 / visible frets: {stats.visible_fret_count}{suffix}{hand_suffix}"
        )
        self.fps_label.setText(f"FPS: {stats.fps:5.1f}")
        self.inference_label.setText(f"Inference: {stats.inference_ms:6.1f} ms")
        self.counts_label.setText(
            f"Detections: {stats.total_detections} ({format_counts(stats.class_counts)})"
            f" / edge strings: {stats.edge_string_count}/6"
            f" / edge frets: {stats.edge_fret_count}/{stats.visible_fret_count}"
            f" / deleted frets: {stats.suppressed_fret_count}"
            f" / fingertips: {stats.fingertip_count}"
        )

    def handle_worker_error(self, message: str) -> None:
        QMessageBox.warning(self, "Webcam / Model Error", message)
        self.mode_label.setText(message)
        self.stop_detection()

    def on_worker_finished(self) -> None:
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.worker = None
        self.selection_stage = "idle"
        self.selected_nut_line = None
        self.selected_reference_line = None
        self.latest_stable_grid = None
        self.latest_edge_refinement = None
        self.delete_fret_button.setText("잘못된 프렛 삭제")
        self.video_label.setCursor(Qt.CursorShape.ArrowCursor)
        if self.video_label.pixmap() is None:
            self.video_label.setText("Press Start to open the webcam.")
        self.mode_label.setText("Mode: idle")
        self.update_note_status(marker_count=0, visible_frets=0)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.stop_detection()
        super().closeEvent(event)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PySide webcam viewer for guitar fretboard YOLO detection")
    parser.add_argument("--model", type=str, default=None, help="Path to the YOLO weight file")
    parser.add_argument("--camera", type=int, default=0, help="Webcam index to open first")
    parser.add_argument(
        "--camera-calibration",
        type=str,
        default=None,
        help="Path to a camera calibration .npz file. If omitted, camera_calibration.npz is auto-loaded when present.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    model_path = resolve_model_path(args.model)
    calibration_path = resolve_camera_calibration_path(args.camera_calibration)
    camera_calibration = None
    if calibration_path is not None:
        camera_calibration = load_camera_calibration(calibration_path)

    app = QApplication(sys.argv)
    window = MainWindow(
        model_path=model_path,
        camera_index=args.camera,
        camera_calibration=camera_calibration,
    )
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
