from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import cv2
import numpy as np
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QColor, QCloseEvent, QImage, QMouseEvent, QPainter, QPen, QPixmap
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
from fretboard_mapping import (
    DetectedLine,
    DetectedStringPoints,
    FretboardTracker,
    NoteMarker,
    NoteMarkerTracker,
    SelectedLineTracker,
    StableFretboardGrid,
    build_center_test_note_markers,
    build_center_test_note_markers_from_string_points,
    build_fretboard_grid,
    build_note_markers,
    build_note_markers_from_string_points,
    count_detected_classes,
    infer_selected_reference_fret_index,
)
from hand_tracking import Fingertip, MediaPipeHandTracker


DEFAULT_MODEL_PATH = Path(__file__).resolve().parent / "models" / "weights" / "best06.pt"
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
OPEN_NOTE_MARKER_RADIUS = 7
OPEN_NOTE_MARKER_THICKNESS = 2
FRETTED_NOTE_MARKER_RADIUS = 6
SOURCE_NONE = "none"
SOURCE_WEBCAM = "webcam"
SOURCE_IMAGE = "image"
SOURCE_VIDEO = "video"


@dataclass
class FrameAnalysis:
    detected_lines: tuple[DetectedLine, ...]
    detected_string_points: tuple[DetectedStringPoints, ...]
    stable_grid: StableFretboardGrid | None
    note_markers: tuple[NoteMarker, ...]
    fingertip_points: tuple[Fingertip, ...]
    matched_marker_indices: tuple[int, ...]
    class_counts: dict[str, int]
    hand_tracking_available: bool
    tracking_mode: str
    guide_source: str
    reference_fret_index: int | None = None


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
    guide_source: str
    source_kind: str
    source_name: str
    paused: bool = False
    frame_index: int = 0
    total_frames: int = 0
    reference_fret_index: int | None = None


@dataclass(frozen=True)
class PredictionOutputs:
    detected_lines: tuple[DetectedLine, ...]
    detected_string_points: tuple[DetectedStringPoints, ...]


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


def format_timecode(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "-"
    total_seconds = int(round(seconds))
    minutes, secs = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


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
                p1=visible_points[-1],
                confidence=float(confidence),
            )
        )

    return tuple(detected_lines)


def extract_detected_string_points(
    result,
    names: dict[int, str],
    point_threshold: float = POINT_THRESHOLD,
    minimum_points: int = 3,
) -> tuple[DetectedStringPoints, ...]:
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

    detected_points: list[DetectedStringPoints] = []
    for index, (class_index, confidence) in enumerate(zip(classes, confidences)):
        class_name = names.get(class_index, str(class_index))
        if class_name not in {"fret", "nut"} or index >= len(keypoint_xy):
            continue

        visible_points: list[tuple[float, float]] = []
        for point_index, point in enumerate(keypoint_xy[index]):
            point_confidence = 1.0
            if keypoint_conf is not None and point_index < len(keypoint_conf[index]):
                point_confidence = float(keypoint_conf[index][point_index])
            if point_confidence < point_threshold:
                continue
            visible_points.append((float(point[0]), float(point[1])))

        if len(visible_points) < minimum_points:
            continue

        detected_points.append(
            DetectedStringPoints(
                kind=class_name,
                points=tuple(visible_points),
                confidence=float(confidence),
            )
        )

    return tuple(detected_points)


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


def offset_detected_string_points(
    detected_string_points: tuple[DetectedStringPoints, ...],
    offset_x: int,
    offset_y: int,
) -> tuple[DetectedStringPoints, ...]:
    return tuple(
        DetectedStringPoints(
            kind=item.kind,
            points=tuple((x_value + offset_x, y_value + offset_y) for x_value, y_value in item.points),
            confidence=item.confidence,
        )
        for item in detected_string_points
    )


def predict_model_outputs(
    model: YOLO,
    frame_bgr: np.ndarray,
    conf_threshold: float,
    image_size: int,
    roi: tuple[int, int, int, int] | None = None,
) -> PredictionOutputs:
    source_frame = frame_bgr
    offset_x = 0
    offset_y = 0
    if roi is not None:
        x0, y0, x1, y1 = roi
        source_frame = frame_bgr[y0:y1, x0:x1]
        offset_x = x0
        offset_y = y0
        if source_frame.size == 0:
            return PredictionOutputs(detected_lines=(), detected_string_points=())

    result = model.predict(
        source=source_frame,
        conf=conf_threshold,
        imgsz=image_size,
        verbose=False,
    )[0]
    detected_lines = extract_detected_lines(result, model.names)
    detected_string_points = extract_detected_string_points(result, model.names)
    if roi is not None:
        if detected_lines:
            detected_lines = offset_detected_lines(detected_lines, offset_x=offset_x, offset_y=offset_y)
        if detected_string_points:
            detected_string_points = offset_detected_string_points(
                detected_string_points,
                offset_x=offset_x,
                offset_y=offset_y,
            )
    return PredictionOutputs(
        detected_lines=detected_lines,
        detected_string_points=detected_string_points,
    )


def predict_detected_lines(
    model: YOLO,
    frame_bgr: np.ndarray,
    conf_threshold: float,
    image_size: int,
    roi: tuple[int, int, int, int] | None = None,
) -> tuple[DetectedLine, ...]:
    return predict_model_outputs(
        model=model,
        frame_bgr=frame_bgr,
        conf_threshold=conf_threshold,
        image_size=image_size,
        roi=roi,
    ).detected_lines


def build_note_markers_for_frame(
    stable_grid: StableFretboardGrid | None,
    detected_string_points: tuple[DetectedStringPoints, ...],
    selected_note: str | None,
    reverse_strings: bool,
    center_test_mode: bool,
) -> tuple[tuple[NoteMarker, ...], str]:
    if detected_string_points:
        if center_test_mode:
            note_markers = build_center_test_note_markers_from_string_points(
                detected_string_points,
                selected_note,
            )
        else:
            note_markers = build_note_markers_from_string_points(
                detected_string_points,
                selected_note,
                reverse_strings=reverse_strings,
            )
        if note_markers:
            return note_markers, "point-in-fret midpoint"

    if center_test_mode:
        return build_center_test_note_markers(stable_grid, selected_note), "grid"
    return build_note_markers(stable_grid, selected_note, reverse_strings=reverse_strings), "grid"


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


def draw_detected_string_points(
    frame_bgr: np.ndarray,
    detected_string_points: tuple[DetectedStringPoints, ...],
) -> np.ndarray:
    if not detected_string_points:
        return frame_bgr

    annotated = frame_bgr.copy()
    for point_set in detected_string_points:
        color = CLASS_COLORS.get(point_set.kind, (255, 210, 90))
        for point_xy in point_set.points:
            center = tuple(int(round(value)) for value in point_xy)
            cv2.circle(annotated, center, 4, (0, 0, 0), -1, cv2.LINE_AA)
            cv2.circle(annotated, center, 2, color, -1, cv2.LINE_AA)
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
    tracking_mode: str = "yolo_assist",
) -> tuple[np.ndarray, FrameAnalysis]:
    prediction = predict_model_outputs(
        model=model,
        frame_bgr=frame_bgr,
        conf_threshold=conf_threshold,
        image_size=image_size,
    )
    detected_lines = prediction.detected_lines
    detected_string_points = prediction.detected_string_points
    class_counts = count_detected_classes(detected_lines)
    stable_grid = tracker.update(
        build_fretboard_grid(
            detected_lines,
            prior_grid=tracker.last_grid,
        )
    )
    note_markers, guide_source = build_note_markers_for_frame(
        stable_grid=stable_grid,
        detected_string_points=detected_string_points,
        selected_note=selected_note,
        reverse_strings=reverse_strings,
        center_test_mode=center_test_mode,
    )
    hand_tracking = hand_tracker.process(frame_bgr, note_markers)

    annotated = draw_detected_string_points(frame_bgr, detected_string_points)
    annotated = draw_detected_lines(annotated, detected_lines)
    annotated = draw_note_markers(
        annotated,
        note_markers,
        matched_marker_indices=hand_tracking.matched_marker_indices,
    )
    annotated = draw_fingertips(annotated, hand_tracking.fingertips)
    analysis = FrameAnalysis(
        detected_lines=detected_lines,
        detected_string_points=detected_string_points,
        stable_grid=stable_grid,
        note_markers=note_markers,
        fingertip_points=hand_tracking.fingertips,
        matched_marker_indices=hand_tracking.matched_marker_indices,
        class_counts=class_counts,
        hand_tracking_available=hand_tracking.backend_available,
        tracking_mode=tracking_mode,
        guide_source=guide_source,
    )
    return annotated, analysis


def analyze_selected_lines_frame(
    frame_bgr: np.ndarray,
    primary_detected_lines: tuple[DetectedLine, ...],
    primary_detected_string_points: tuple[DetectedStringPoints, ...],
    fallback_detected_lines: tuple[DetectedLine, ...] | None,
    fallback_detected_string_points: tuple[DetectedStringPoints, ...] | None,
    selected_line_tracker: SelectedLineTracker,
    note_marker_tracker: NoteMarkerTracker,
    hand_tracker: MediaPipeHandTracker,
    selected_note: str | None,
    reverse_strings: bool,
    center_test_mode: bool = False,
) -> tuple[np.ndarray, FrameAnalysis]:
    reference_fret_index = selected_line_tracker.reference_fret_index
    tracking_result = selected_line_tracker.update(
        primary_detected_lines=primary_detected_lines,
        fallback_detected_lines=fallback_detected_lines,
    )
    class_count_source = fallback_detected_lines if fallback_detected_lines else primary_detected_lines
    string_point_source = fallback_detected_string_points if fallback_detected_string_points else primary_detected_string_points
    if tracking_result.stable_grid is None:
        note_marker_tracker.update(())
        hand_tracking = hand_tracker.process(frame_bgr, ())
        annotated = draw_detected_string_points(frame_bgr, string_point_source or ())
        annotated = draw_detected_lines(annotated, class_count_source)
        annotated = draw_fingertips(annotated, hand_tracking.fingertips)
        analysis = FrameAnalysis(
            detected_lines=class_count_source,
            detected_string_points=string_point_source or (),
            stable_grid=None,
            note_markers=(),
            fingertip_points=hand_tracking.fingertips,
            matched_marker_indices=(),
            class_counts=count_detected_classes(class_count_source),
            hand_tracking_available=hand_tracking.backend_available,
            tracking_mode=tracking_result.tracking_mode,
            guide_source="none",
            reference_fret_index=reference_fret_index,
        )
        return annotated, analysis

    stable_grid = tracking_result.stable_grid
    tracked_lines = tracking_result.tracked_lines
    class_counts = count_detected_classes(class_count_source)
    note_markers, guide_source = build_note_markers_for_frame(
        stable_grid=stable_grid,
        detected_string_points=string_point_source or (),
        selected_note=selected_note,
        reverse_strings=reverse_strings,
        center_test_mode=center_test_mode,
    )
    note_markers = note_marker_tracker.update(note_markers)
    hand_tracking = hand_tracker.process(frame_bgr, note_markers)
    quad_color = (255, 255, 255)
    if tracking_result.tracking_mode == "selected_lines_hold":
        quad_color = (0, 200, 255)
    elif tracking_result.tracking_mode == "selected_lines_reacquired":
        quad_color = (80, 255, 170)
    annotated = draw_detected_string_points(frame_bgr, string_point_source or ())
    annotated = draw_detected_lines(annotated, tracked_lines)
    annotated = draw_reference_quad(annotated, stable_grid, color=quad_color)
    annotated = draw_note_markers(
        annotated,
        note_markers,
        matched_marker_indices=hand_tracking.matched_marker_indices,
    )
    annotated = draw_fingertips(annotated, hand_tracking.fingertips)
    analysis = FrameAnalysis(
        detected_lines=tracked_lines,
        detected_string_points=string_point_source or (),
        stable_grid=stable_grid,
        note_markers=note_markers,
        fingertip_points=hand_tracking.fingertips,
        matched_marker_indices=hand_tracking.matched_marker_indices,
        class_counts=class_counts,
        hand_tracking_available=hand_tracking.backend_available,
        tracking_mode=tracking_result.tracking_mode,
        guide_source=guide_source,
        reference_fret_index=reference_fret_index,
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
        self.camera_calibration = camera_calibration
        self.tracker = FretboardTracker(max_history=5)
        self.note_marker_tracker = NoteMarkerTracker()
        self.selected_line_tracker = SelectedLineTracker(max_history=5)
        self.hand_tracker = MediaPipeHandTracker()
        self.latest_frame_bgr: np.ndarray | None = None
        self.frozen_selection_lines: tuple[DetectedLine, ...] = ()
        self.selection_request = False
        self.selection_active = False
        self.pending_manual_selection: tuple[DetectedLine, DetectedLine, int] | None = None
        self.source_kind = SOURCE_NONE
        self.source_path: Path | None = None
        self.capture: cv2.VideoCapture | None = None
        self.current_frame_bgr: np.ndarray | None = None
        self.current_analysis: FrameAnalysis | None = None
        self.current_annotated_bgr: np.ndarray | None = None
        self.paused = False
        self.frame_index = 0
        self.total_frames = 0
        self.pending_seek_frame: int | None = None
        self.pending_refresh = False
        self._running = True

    def stop(self) -> None:
        self._running = False
        self.wait(2000)

    def close_source(self) -> None:
        if self.capture is not None:
            self.capture.release()
        self.capture = None

    def set_conf_threshold(self, conf_threshold: float) -> None:
        self.conf_threshold = conf_threshold
        self.pending_refresh = True

    def set_image_size(self, image_size: int) -> None:
        self.image_size = image_size
        self.pending_refresh = True

    def set_selected_note(self, selected_note: str | None) -> None:
        self.selected_note = selected_note
        self.note_marker_tracker.clear()
        self.pending_refresh = True

    def set_reverse_strings(self, reverse_strings: bool) -> None:
        self.reverse_strings = reverse_strings
        self.note_marker_tracker.clear()
        self.pending_refresh = True

    def set_center_test_mode(self, center_test_mode: bool) -> None:
        self.center_test_mode = center_test_mode
        self.note_marker_tracker.clear()
        self.pending_refresh = True

    def begin_line_selection(self) -> bool:
        if self.latest_frame_bgr is None:
            return False
        self.note_marker_tracker.clear()
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
        self.note_marker_tracker.clear()
        self.cancel_line_selection()

    def set_source_webcam(self, camera_index: int) -> None:
        self.camera_index = camera_index
        self.source_kind = SOURCE_WEBCAM
        self.source_path = None
        self.paused = False
        self.frame_index = 0
        self.total_frames = 0
        self.pending_seek_frame = None
        self.current_frame_bgr = None
        self.current_analysis = None
        self.current_annotated_bgr = None
        self.close_source()

    def set_source_image(self, image_path: Path) -> None:
        self.source_kind = SOURCE_IMAGE
        self.source_path = image_path
        self.paused = True
        self.frame_index = 0
        self.total_frames = 1
        self.pending_seek_frame = None
        self.close_source()
        frame_bgr = cv2.imread(str(image_path))
        if frame_bgr is None:
            self.error_message.emit(f"Could not read image: {image_path}")
            self.current_frame_bgr = None
            self.current_analysis = None
            self.current_annotated_bgr = None
            self.source_kind = SOURCE_NONE
            return
        if self.camera_calibration is not None:
            frame_bgr = self.camera_calibration.undistort_frame(frame_bgr)
        self.current_frame_bgr = cv2.flip(frame_bgr, 1)
        self.current_analysis = None
        self.current_annotated_bgr = None
        self.pending_refresh = True

    def set_source_video(self, video_path: Path) -> None:
        self.source_kind = SOURCE_VIDEO
        self.source_path = video_path
        self.paused = False
        self.frame_index = 0
        self.pending_seek_frame = 0
        self.close_source()
        capture = cv2.VideoCapture(str(video_path))
        if not capture.isOpened():
            self.error_message.emit(f"Could not open video: {video_path}")
            self.source_kind = SOURCE_NONE
            return
        self.capture = capture
        self.total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        self.current_frame_bgr = None
        self.current_analysis = None
        self.current_annotated_bgr = None
        self.pending_refresh = True

    def toggle_pause(self) -> None:
        if self.source_kind == SOURCE_VIDEO:
            self.paused = not self.paused

    def seek_by_frames(self, frame_delta: int) -> None:
        if self.source_kind != SOURCE_VIDEO:
            return
        self.pending_seek_frame = max(0, self.frame_index + frame_delta)

    def analyze_frame(
        self,
        frame_bgr: np.ndarray,
        model: YOLO,
        selected_note: str | None,
        reverse_strings: bool,
        center_test_mode: bool,
    ) -> tuple[np.ndarray, FrameAnalysis]:
        if self.selected_line_tracker.is_active:
            roi_detected_lines: tuple[DetectedLine, ...] = ()
            roi_detected_string_points: tuple[DetectedStringPoints, ...] = ()
            fallback_detected_lines: tuple[DetectedLine, ...] | None = None
            fallback_detected_string_points: tuple[DetectedStringPoints, ...] | None = None
            search_roi = self.selected_line_tracker.search_roi(frame_bgr.shape)
            if search_roi is not None:
                roi_prediction = predict_model_outputs(
                    model=model,
                    frame_bgr=frame_bgr,
                    conf_threshold=max(TRACKING_CONF_FLOOR, self.conf_threshold * TRACKING_CONF_SCALE),
                    image_size=self.image_size,
                    roi=search_roi,
                )
                roi_detected_lines = roi_prediction.detected_lines
                roi_detected_string_points = roi_prediction.detected_string_points

            roi_counts = count_detected_classes(roi_detected_lines)
            if (
                search_roi is None
                or self.selected_line_tracker.missed_frames > 0
                or roi_counts.get("nut", 0) == 0
                or roi_counts.get("fret", 0) == 0
            ):
                fallback_prediction = predict_model_outputs(
                    model=model,
                    frame_bgr=frame_bgr,
                    conf_threshold=max(REACQUIRE_CONF_FLOOR, self.conf_threshold * REACQUIRE_CONF_SCALE),
                    image_size=self.image_size,
                )
                fallback_detected_lines = fallback_prediction.detected_lines
                fallback_detected_string_points = fallback_prediction.detected_string_points

            return analyze_selected_lines_frame(
                frame_bgr=frame_bgr,
                primary_detected_lines=roi_detected_lines,
                primary_detected_string_points=roi_detected_string_points,
                fallback_detected_lines=fallback_detected_lines,
                fallback_detected_string_points=fallback_detected_string_points,
                selected_line_tracker=self.selected_line_tracker,
                note_marker_tracker=self.note_marker_tracker,
                hand_tracker=self.hand_tracker,
                selected_note=selected_note,
                reverse_strings=reverse_strings,
                center_test_mode=center_test_mode,
            )

        prediction = predict_model_outputs(
            model=model,
            frame_bgr=frame_bgr,
            conf_threshold=self.conf_threshold,
            image_size=self.image_size,
        )
        detected_lines = prediction.detected_lines
        detected_string_points = prediction.detected_string_points
        class_counts = count_detected_classes(detected_lines)
        stable_grid = self.tracker.update(
            build_fretboard_grid(
                detected_lines,
                prior_grid=self.tracker.last_grid,
            )
        )
        note_markers, guide_source = build_note_markers_for_frame(
            stable_grid=stable_grid,
            detected_string_points=detected_string_points,
            selected_note=selected_note,
            reverse_strings=reverse_strings,
            center_test_mode=center_test_mode,
        )
        note_markers = self.note_marker_tracker.update(note_markers)
        hand_tracking = self.hand_tracker.process(frame_bgr, note_markers)
        annotated = draw_detected_string_points(frame_bgr, detected_string_points)
        annotated = draw_detected_lines(annotated, detected_lines)
        annotated = draw_note_markers(
            annotated,
            note_markers,
            matched_marker_indices=hand_tracking.matched_marker_indices,
        )
        annotated = draw_fingertips(annotated, hand_tracking.fingertips)
        analysis = FrameAnalysis(
            detected_lines=detected_lines,
            detected_string_points=detected_string_points,
            stable_grid=stable_grid,
            note_markers=note_markers,
            fingertip_points=hand_tracking.fingertips,
            matched_marker_indices=hand_tracking.matched_marker_indices,
            class_counts=class_counts,
            hand_tracking_available=hand_tracking.backend_available,
            tracking_mode="yolo_assist",
            guide_source=guide_source,
        )
        return annotated, analysis

    def run(self) -> None:
        try:
            model = load_yolo_model(self.model_path)
        except Exception as exc:  # pragma: no cover - GUI error path
            self.error_message.emit(str(exc))
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

            frame_bgr: np.ndarray | None = None
            if self.source_kind == SOURCE_WEBCAM:
                if self.capture is None:
                    self.capture = cv2.VideoCapture(self.camera_index)
                    self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                    self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
                    if not self.capture.isOpened():
                        self.error_message.emit(
                            f"Could not open webcam index {self.camera_index}. Check camera permission or another camera index."
                        )
                        self.close_source()
                        break
                ok, raw_frame = self.capture.read()
                if not ok or raw_frame is None:
                    self.error_message.emit("Failed to read frame from webcam.")
                    break
                if self.camera_calibration is not None:
                    raw_frame = self.camera_calibration.undistort_frame(raw_frame)
                frame_bgr = cv2.flip(raw_frame, 1)
                self.current_frame_bgr = frame_bgr.copy()
            elif self.source_kind == SOURCE_VIDEO:
                if self.capture is None:
                    self.msleep(30)
                    continue
                if self.pending_seek_frame is not None:
                    seek_target = min(self.pending_seek_frame, max(0, self.total_frames - 1))
                    self.capture.set(cv2.CAP_PROP_POS_FRAMES, seek_target)
                    self.frame_index = seek_target
                    self.pending_seek_frame = None
                    self.current_frame_bgr = None
                if self.current_frame_bgr is None or not self.paused:
                    ok, raw_frame = self.capture.read()
                    if not ok or raw_frame is None:
                        self.paused = True
                        self.msleep(30)
                        continue
                    if self.camera_calibration is not None:
                        raw_frame = self.camera_calibration.undistort_frame(raw_frame)
                    self.current_frame_bgr = cv2.flip(raw_frame, 1)
                    self.frame_index = int(max(0, self.capture.get(cv2.CAP_PROP_POS_FRAMES) - 1))
                frame_bgr = self.current_frame_bgr.copy()
            elif self.source_kind == SOURCE_IMAGE:
                if self.current_frame_bgr is None:
                    self.msleep(30)
                    continue
                frame_bgr = self.current_frame_bgr.copy()
            else:
                self.msleep(30)
                continue

            self.latest_frame_bgr = frame_bgr.copy()
            selected_note = self.selected_note
            reverse_strings = self.reverse_strings
            center_test_mode = self.center_test_mode

            inference_start = perf_counter()

            if self.selection_request:
                prediction = predict_model_outputs(
                    model=model,
                    frame_bgr=frame_bgr,
                    conf_threshold=self.conf_threshold,
                    image_size=self.image_size,
                )
                detected_lines = prediction.detected_lines
                detected_string_points = prediction.detected_string_points
                self.selected_line_tracker.clear()
                class_counts = count_detected_classes(detected_lines)
                stable_grid = self.tracker.update(
                    build_fretboard_grid(
                        detected_lines,
                        prior_grid=self.tracker.last_grid,
                    )
                )
                note_markers, guide_source = build_note_markers_for_frame(
                    stable_grid=stable_grid,
                    detected_string_points=detected_string_points,
                    selected_note=selected_note,
                    reverse_strings=reverse_strings,
                    center_test_mode=center_test_mode,
                )
                note_markers = self.note_marker_tracker.update(note_markers)
                hand_tracking = self.hand_tracker.process(frame_bgr, note_markers)
                annotated = draw_detected_string_points(frame_bgr, detected_string_points)
                annotated = draw_detected_lines(annotated, detected_lines)
                annotated = draw_note_markers(
                    annotated,
                    note_markers,
                    matched_marker_indices=hand_tracking.matched_marker_indices,
                )
                annotated = draw_fingertips(annotated, hand_tracking.fingertips)
                analysis = FrameAnalysis(
                    detected_lines=detected_lines,
                    detected_string_points=detected_string_points,
                    stable_grid=stable_grid,
                    note_markers=note_markers,
                    fingertip_points=hand_tracking.fingertips,
                    matched_marker_indices=hand_tracking.matched_marker_indices,
                    class_counts=class_counts,
                    hand_tracking_available=hand_tracking.backend_available,
                    tracking_mode="selection_ready",
                    guide_source=guide_source,
                )
                self.selection_request = False
                self.selection_active = True
                self.frozen_selection_lines = analysis.detected_lines
            else:
                should_reanalyze = (
                    self.source_kind == SOURCE_WEBCAM
                    or self.source_kind == SOURCE_VIDEO and not self.paused
                    or self.pending_refresh
                    or self.current_analysis is None
                    or self.current_annotated_bgr is None
                )
                if should_reanalyze:
                    annotated, analysis = self.analyze_frame(
                        frame_bgr=frame_bgr,
                        model=model,
                        selected_note=selected_note,
                        reverse_strings=reverse_strings,
                        center_test_mode=center_test_mode,
                    )
                    self.current_analysis = analysis
                    self.current_annotated_bgr = annotated.copy()
                    self.pending_refresh = False
                else:
                    annotated = self.current_annotated_bgr.copy()
                    analysis = self.current_analysis
            inference_ms = (perf_counter() - inference_start) * 1000.0

            now = perf_counter()
            fps = 1.0 / max(now - previous_frame_time, 1e-6)
            previous_frame_time = now

            visible_fret_count = 0
            if analysis.stable_grid is not None:
                visible_fret_count = len(analysis.stable_grid.ordered_fret_lines)

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
                guide_source=analysis.guide_source,
                source_kind=self.source_kind,
                source_name=self.source_path.name if self.source_path is not None else f"Camera {self.camera_index}",
                paused=self.paused,
                frame_index=self.frame_index,
                total_frames=self.total_frames,
                reference_fret_index=analysis.reference_fret_index,
            )
            self.frame_ready.emit(bgr_to_qimage(annotated), stats)

        self.close_source()
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

        self.video_label = VideoLabel("Press Start Cam to open the webcam.")
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

        self.reference_fret_spin = QSpinBox()
        self.reference_fret_spin.setRange(1, 24)
        self.reference_fret_spin.setValue(12)
        self.reference_fret_spin.setEnabled(False)
        self.reference_fret_spin.setToolTip("현재 보이는 마지막 프렛을 기준으로 자동 추정됩니다.")

        self.select_lines_button = QPushButton("Select Nut + Last Visible Fret")
        self.clear_tracking_button = QPushButton("Clear Manual Tracking")
        self.select_lines_button.clicked.connect(self.begin_line_selection)
        self.clear_tracking_button.clicked.connect(self.clear_manual_tracking)

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

        self.camera_toggle_button = QPushButton("Start Cam")
        self.browse_button = QPushButton("Choose Model")
        self.open_image_button = QPushButton("Open Image")
        self.open_video_button = QPushButton("Open Video")
        self.play_pause_button = QPushButton("Play")
        self.seek_back_button = QPushButton("-5 sec")
        self.seek_forward_button = QPushButton("+5 sec")
        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setRange(0, 0)
        self.seek_slider.setEnabled(False)
        self.frame_position_label = QLabel("Frame: - / -")
        self.time_position_label = QLabel("Time: - / -")

        self.camera_toggle_button.clicked.connect(self.toggle_camera_detection)
        self.browse_button.clicked.connect(self.choose_model_file)
        self.open_image_button.clicked.connect(self.open_image_file)
        self.open_video_button.clicked.connect(self.open_video_file)
        self.play_pause_button.clicked.connect(self.toggle_play_pause)
        self.seek_back_button.clicked.connect(lambda: self.seek_video_by_seconds(-5.0))
        self.seek_forward_button.clicked.connect(lambda: self.seek_video_by_seconds(5.0))
        self.seek_slider.sliderMoved.connect(self.handle_seek_slider)
        self.play_pause_button.setEnabled(False)
        self.seek_back_button.setEnabled(False)
        self.seek_forward_button.setEnabled(False)

        self.mode_label = QLabel("Mode: YOLO assist")
        self.status_label = QLabel("선택 음: 없음 / 후보 0개 / 정확 운지 0개 / visible frets: 0 / guide: -")
        self.fps_label = QLabel("FPS: -")
        self.inference_label = QLabel("Inference: -")
        self.counts_label = QLabel("Detections: -")

        controls = self._build_controls()
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addWidget(self.video_label, stretch=1)
        layout.addWidget(controls)
        self.setCentralWidget(root)

        self.populate_camera_list(selected=camera_index)
        self.update_camera_button_state()

    def _build_controls(self) -> QWidget:
        controls_box = QGroupBox("Controls")
        layout = QGridLayout(controls_box)

        layout.addWidget(QLabel("Model"), 0, 0)
        layout.addWidget(self.model_value, 0, 1, 1, 4)
        layout.addWidget(self.browse_button, 0, 5)
        layout.addWidget(self.open_image_button, 0, 6)
        layout.addWidget(self.open_video_button, 0, 7)

        layout.addWidget(QLabel("Camera"), 1, 0)
        layout.addWidget(self.camera_combo, 1, 1)
        layout.addWidget(self.refresh_camera_button, 1, 2)
        layout.addWidget(self.camera_toggle_button, 1, 3)

        layout.addWidget(QLabel("Confidence"), 1, 4)
        layout.addWidget(self.conf_slider, 1, 5)
        layout.addWidget(self.conf_value, 1, 6)

        layout.addWidget(QLabel("Selected Note"), 2, 0)
        layout.addWidget(self.note_combo, 2, 1)
        layout.addWidget(self.reverse_checkbox, 2, 2)
        layout.addWidget(self.center_test_checkbox, 2, 3)
        layout.addWidget(QLabel("Image Size"), 2, 4)
        layout.addWidget(self.image_size_spin, 2, 5)

        layout.addWidget(QLabel("Reference Fret (auto)"), 3, 0)
        layout.addWidget(self.reference_fret_spin, 3, 1)
        layout.addWidget(self.select_lines_button, 3, 2)
        layout.addWidget(self.clear_tracking_button, 3, 3)

        button_row = QHBoxLayout()
        button_row.addWidget(self.play_pause_button)
        button_row.addWidget(self.seek_back_button)
        button_row.addWidget(self.seek_forward_button)
        layout.addLayout(button_row, 3, 4, 1, 4)
        layout.addWidget(QLabel("Seek"), 4, 0)
        layout.addWidget(self.seek_slider, 4, 1, 1, 5)
        layout.addWidget(self.frame_position_label, 4, 6)
        layout.addWidget(self.time_position_label, 4, 7)

        layout.addWidget(QLabel("Mode"), 5, 0)
        layout.addWidget(self.mode_label, 5, 1, 1, 7)
        layout.addWidget(QLabel("Status"), 6, 0)
        layout.addWidget(self.status_label, 6, 1, 1, 7)
        layout.addWidget(self.fps_label, 7, 3)
        layout.addWidget(self.inference_label, 7, 4)
        layout.addWidget(self.counts_label, 7, 5)

        return controls_box

    def selected_note(self) -> str | None:
        note = self.note_combo.currentText()
        return None if note == "없음" else note

    def update_note_status(
        self,
        marker_count: int = 0,
        visible_frets: int = 0,
        matched_count: int = 0,
        guide_source: str = "-",
    ) -> None:
        note_label = self.selected_note() or "없음"
        suffix = " / 중앙 테스트 점" if self.center_test_checkbox.isChecked() else ""
        self.status_label.setText(
            f"선택 음: {note_label} / 후보 {marker_count}개 / 정확 운지 {matched_count}개 / visible frets: {visible_frets} / guide: {guide_source}{suffix}"
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

    def update_camera_button_state(self) -> None:
        if self.worker is None or not self.worker.isRunning():
            self.camera_toggle_button.setText("Start Cam")
            return
        if self.worker.source_kind == SOURCE_WEBCAM:
            self.camera_toggle_button.setText("Stop Cam")
            return
        self.camera_toggle_button.setText("Start Cam")

    def ensure_worker(self) -> VideoWorker | None:
        if self.worker is not None and self.worker.isRunning():
            return self.worker

        model_path = self.model_path
        if not model_path.exists():
            QMessageBox.critical(self, "Missing Weights", f"Model file does not exist:\n{model_path}")
            return None

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
            camera_calibration=self.camera_calibration,
            parent=self,
        )
        self.worker.frame_ready.connect(self.update_frame)
        self.worker.error_message.connect(self.handle_worker_error)
        self.worker.finished.connect(self.on_worker_finished)
        self.worker.start()
        self.update_camera_button_state()
        return self.worker

    def open_image_file(self) -> None:
        file_name, _ = QFileDialog.getOpenFileName(
            self,
            "Select Image",
            str(self.model_path.parent),
            "Images (*.jpg *.jpeg *.png *.bmp *.webp);;All files (*)",
        )
        if not file_name:
            return
        worker = self.ensure_worker()
        if worker is None:
            return
        worker.clear_manual_tracking()
        worker.set_source_image(Path(file_name).expanduser().resolve())
        self.update_playback_controls(SOURCE_IMAGE, paused=True, frame_index=0, total_frames=1, fps=1.0)
        self.update_camera_button_state()

    def open_video_file(self) -> None:
        file_name, _ = QFileDialog.getOpenFileName(
            self,
            "Select Video",
            str(self.model_path.parent),
            "Videos (*.mp4 *.avi *.mov *.mkv *.wmv *.m4v);;All files (*)",
        )
        if not file_name:
            return
        worker = self.ensure_worker()
        if worker is None:
            return
        worker.clear_manual_tracking()
        worker.set_source_video(Path(file_name).expanduser().resolve())
        video_fps = 30.0
        if worker.capture is not None:
            video_fps = float(worker.capture.get(cv2.CAP_PROP_FPS) or 30.0)
        self.update_playback_controls(
            SOURCE_VIDEO,
            paused=False,
            frame_index=0,
            total_frames=max(worker.total_frames, 0),
            fps=video_fps,
        )
        self.update_camera_button_state()

    def toggle_camera_detection(self) -> None:
        if self.worker is not None and self.worker.isRunning() and self.worker.source_kind == SOURCE_WEBCAM:
            self.stop_detection()
            return
        self.start_detection()

    def toggle_play_pause(self) -> None:
        if self.worker is None:
            return
        self.worker.toggle_pause()

    def seek_video_by_seconds(self, seconds: float) -> None:
        if self.worker is None or self.worker.source_kind != SOURCE_VIDEO:
            return
        fps = 30.0
        if self.worker.capture is not None:
            fps = float(self.worker.capture.get(cv2.CAP_PROP_FPS) or 30.0)
        self.worker.seek_by_frames(int(round(seconds * max(fps, 1.0))))

    def handle_seek_slider(self, value: int) -> None:
        if self.worker is None or self.worker.source_kind != SOURCE_VIDEO:
            return
        self.worker.pending_seek_frame = value

    def update_playback_controls(
        self,
        source_kind: str,
        paused: bool = False,
        frame_index: int = 0,
        total_frames: int = 0,
        fps: float = 0.0,
    ) -> None:
        is_video = source_kind == SOURCE_VIDEO
        is_file_source = source_kind in {SOURCE_VIDEO, SOURCE_IMAGE}
        self.play_pause_button.setEnabled(is_video)
        self.seek_back_button.setEnabled(is_video)
        self.seek_forward_button.setEnabled(is_video)
        self.seek_slider.setEnabled(is_video and total_frames > 0)
        self.play_pause_button.setText("Resume" if paused and is_video else "Pause")
        self.seek_slider.blockSignals(True)
        self.seek_slider.setRange(0, max(0, total_frames - 1))
        self.seek_slider.setValue(max(0, min(frame_index, max(0, total_frames - 1))))
        self.seek_slider.blockSignals(False)

        if is_file_source:
            shown_total_frames = max(total_frames, 1)
            shown_frame_index = min(max(frame_index, 0), shown_total_frames - 1)
            self.frame_position_label.setText(f"Frame: {shown_frame_index + 1} / {shown_total_frames}")
            current_seconds = shown_frame_index / max(fps, 1.0) if source_kind == SOURCE_VIDEO else 0.0
            total_seconds = max(shown_total_frames - 1, 0) / max(fps, 1.0) if source_kind == SOURCE_VIDEO else 0.0
            self.time_position_label.setText(
                f"Time: {format_timecode(current_seconds)} / {format_timecode(total_seconds)}"
            )
        else:
            self.frame_position_label.setText("Frame: - / -")
            self.time_position_label.setText("Time: - / -")

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
        self.video_label.setCursor(Qt.CursorShape.ArrowCursor)
        if self.worker is not None:
            self.worker.clear_manual_tracking()
        self.mode_label.setText("Mode: YOLO assist")
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
        if self.selection_stage in {"select_nut", "select_fret"}:
            image = self.decorate_selection_frame(image)

        pixmap = QPixmap.fromImage(image)
        scaled = pixmap.scaled(
            self.video_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.video_label.setPixmap(scaled)

    def start_detection(self) -> None:
        worker = self.ensure_worker()
        if worker is None:
            return
        worker.set_source_webcam(int(self.camera_combo.currentData() or 0))
        self.mode_label.setText("Mode: YOLO assist")
        self.update_note_status(marker_count=0, visible_frets=0)
        self.update_playback_controls(SOURCE_WEBCAM, fps=0.0)
        self.update_camera_button_state()

    def stop_detection(self) -> None:
        if self.worker is None:
            return
        self.mode_label.setText("Mode: stopping")
        self.worker.stop()
        self.update_camera_button_state()

    def update_frame(self, image: QImage, stats: InferenceStats) -> None:
        self.current_image = image
        self.update_camera_button_state()

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
        self.update_playback_controls(
            stats.source_kind,
            paused=stats.paused,
            frame_index=stats.frame_index,
            total_frames=stats.total_frames,
            fps=float(self.worker.capture.get(cv2.CAP_PROP_FPS) or 30.0) if self.worker is not None and self.worker.capture is not None and stats.source_kind == SOURCE_VIDEO else (1.0 if stats.source_kind == SOURCE_IMAGE else 0.0),
        )
        suffix = " / 중앙 테스트 점" if stats.center_test_mode else ""
        hand_suffix = ""
        if not stats.hand_tracking_available:
            hand_suffix = " / 손 검출 비활성"
        self.status_label.setText(
            f"선택 음: {stats.selected_note} / 후보 {stats.marker_count}개 / 정확 운지 {stats.matched_marker_count}개 / visible frets: {stats.visible_fret_count} / guide: {stats.guide_source} / source: {stats.source_name}{suffix}{hand_suffix}"
        )
        if stats.source_kind in {SOURCE_IMAGE, SOURCE_VIDEO} and stats.paused:
            self.fps_label.setText("FPS: -")
            self.inference_label.setText("Inference: -")
        else:
            self.fps_label.setText(f"FPS: {stats.fps:.1f}")
            self.inference_label.setText(f"Inference: {stats.inference_ms:.1f} ms")
        self.counts_label.setText(
            f"Detections: {stats.total_detections} ({format_counts(stats.class_counts)}) / fingertips: {stats.fingertip_count}"
        )

    def handle_worker_error(self, message: str) -> None:
        QMessageBox.warning(self, "Webcam / Model Error", message)
        self.mode_label.setText(message)
        self.stop_detection()

    def on_worker_finished(self) -> None:
        self.worker = None
        self.update_camera_button_state()
        self.selection_stage = "idle"
        self.selected_nut_line = None
        self.selected_reference_line = None
        self.video_label.setCursor(Qt.CursorShape.ArrowCursor)
        self.update_playback_controls(SOURCE_NONE, fps=0.0)
        if self.video_label.pixmap() is None:
            self.video_label.setText("Press Start Cam to open the webcam.")
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
