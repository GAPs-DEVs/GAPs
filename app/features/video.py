from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import cv2
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QWidget

from app.practice_markers import build_practice_step_note_markers
from app.features.video_capture import LatestFrameCapture
from practice.score_steps import PracticeStep
from tools.fretboard_detection_viewer import (
    TRACKING_CONF_FLOOR,
    TRACKING_CONF_SCALE,
    bgr_to_qimage,
    draw_calibrated_guides,
    draw_detected_lines,
    draw_fingertips,
    draw_note_markers,
    filter_note_markers_by_edge_frets,
    load_yolo_model,
    marker_grid_context,
    predict_detected_lines,
)
from vision.camera_calibration import CameraCalibration
from vision.fretboard_edge_refinement import EdgeRefinementResult, StringLineTracker, refine_grid_with_edge_lines_diagnostics
from vision.fretboard_mapping import (
    DetectedLine,
    FretboardTracker,
    NoteMarker,
    NoteMarkerTracker,
    SelectedLineTracker,
    StableFretboardGrid,
    build_fretboard_grid,
    count_detected_classes,
)
from vision.fretboard_motion import HybridSelectedLineTracker, HybridTrackingFrame, filter_visible_markers
from vision.fretboard_recovery import BackgroundFretboardRecovery
from vision.hand_tracking import Fingertip, HandTrackingResult, MediaPipeHandTracker, HandOcclusionMasker


HAND_TRACKING_ENABLED = False


@dataclass
class PracticeFrameAnalysis:
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
class PracticeFrameStats:
    fps: float
    inference_ms: float
    total_detections: int
    class_counts: dict[str, int]
    visible_fret_count: int
    marker_count: int
    matched_marker_count: int
    fingertip_count: int
    hand_tracking_available: bool
    tracking_mode: str
    reference_fret_index: int | None = None
    edge_string_count: int = 0
    edge_fret_count: int = 0
    stable_grid: StableFretboardGrid | None = None
    edge_refinement: EdgeRefinementResult | None = None
    hand_mask_available: bool = False
    hand_mask_ms: float = 0.
    tracking_ms: float = 0.
    last_recovery_ms: float = 0.
    recovery_pending: bool = False


class PracticeVideoWorker(QThread):
    frame_ready = Signal(QImage, object)
    error_message = Signal(str)

    def __init__(
        self,
        model_path: Path,
        camera_index: int,
        conf_threshold: float,
        image_size: int,
        current_step: PracticeStep | None,
        reverse_strings: bool,
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
        self.current_step = current_step
        self.reverse_strings = reverse_strings
        self.show_edge_guides = show_edge_guides
        self.edge_supported_notes_only = edge_supported_notes_only
        self.camera_calibration = camera_calibration
        self.tracker = FretboardTracker(max_history=5)
        self.note_marker_tracker = NoteMarkerTracker()
        self.selected_line_tracker = SelectedLineTracker(max_history=5)
        self.hybrid_tracker = HybridSelectedLineTracker(
            self.selected_line_tracker, recovery=BackgroundFretboardRecovery(),
        )
        self.occlusion_masker = HandOcclusionMasker()
        self.latest_exclusion_mask = None
        self.manual_tracking_requested = False
        self.string_line_tracker = StringLineTracker()
        self.hand_tracker = MediaPipeHandTracker() if HAND_TRACKING_ENABLED else None
        self.latest_frame_bgr = None
        self.frozen_selection_lines: tuple[DetectedLine, ...] = ()
        self.selection_request = False
        self.selection_active = False
        self.pending_manual_selection: tuple[DetectedLine, DetectedLine, int] | None = None
        self.pending_calibrated_selection: tuple[StableFretboardGrid, int] | None = None
        self.pending_reference_fret_index: int | None = None
        self._running = True
        self._capture: LatestFrameCapture | None = None
        self._clear_tracking_requested = False

    def stop(self) -> None:
        self._running = False
        capture = self._capture
        if capture is not None:
            capture.request_stop()
        self.wait(2000)

    def set_current_step(self, current_step: PracticeStep | None) -> None:
        self.current_step = current_step
        self.note_marker_tracker.clear()

    def set_conf_threshold(self, conf_threshold: float) -> None:
        self.conf_threshold = conf_threshold

    def set_image_size(self, image_size: int) -> None:
        self.image_size = image_size

    def set_reverse_strings(self, reverse_strings: bool) -> None:
        self.reverse_strings = reverse_strings
        self.note_marker_tracker.clear()

    def set_show_edge_guides(self, show_edge_guides: bool) -> None:
        self.show_edge_guides = show_edge_guides

    def set_edge_supported_notes_only(self, edge_supported_notes_only: bool) -> None:
        self.edge_supported_notes_only = edge_supported_notes_only
        self.note_marker_tracker.clear()

    def set_reference_fret_index(self, reference_fret_index: int) -> None:
        if self.pending_manual_selection is not None:
            nut_line, reference_fret_line, _ = self.pending_manual_selection
            self.pending_manual_selection = (nut_line, reference_fret_line, reference_fret_index)
        self.pending_reference_fret_index = reference_fret_index

    def begin_line_selection(self) -> bool:
        if self.latest_frame_bgr is None:
            return False
        self.note_marker_tracker.clear()
        self.string_line_tracker.clear()
        self.selection_request = True
        return True

    def cancel_line_selection(self) -> None:
        self.selection_request = False
        self.selection_active = False
        self.frozen_selection_lines = ()
        self.pending_manual_selection = None
        self.pending_calibrated_selection = None

    def apply_calibrated_selection(self, grid: StableFretboardGrid, reference_index: int) -> None:
        self.pending_manual_selection = None
        self.pending_reference_fret_index = None
        self.pending_calibrated_selection = (grid, reference_index)
        self.manual_tracking_requested = True

    def _apply_pending_calibration(self) -> None:
        pending = self.pending_calibrated_selection
        if pending is None:
            return
        self.pending_calibrated_selection = None
        grid, reference_index = pending
        self.selected_line_tracker.clear()
        self.selected_line_tracker.reference_fret_index = reference_index
        self.selected_line_tracker.accept_registered_grid(grid)
        if self.latest_frame_bgr is not None:
            self.hybrid_tracker.seed(self.latest_frame_bgr, self.latest_exclusion_mask)
        self.note_marker_tracker.clear()
        self.string_line_tracker.clear()
        self.selection_active = False
        self.frozen_selection_lines = ()

    def apply_line_selection(
        self,
        nut_line: DetectedLine,
        reference_fret_line: DetectedLine,
        reference_fret_index: int,
    ) -> None:
        self.pending_manual_selection = (nut_line, reference_fret_line, reference_fret_index)
        self.manual_tracking_requested = True

    def clear_manual_tracking(self) -> None:
        # Apply tracker/reference mutations on their owning video thread.
        self.manual_tracking_requested = False
        self._clear_tracking_requested = True

    def _clear_manual_tracking(self) -> None:
        self.manual_tracking_requested = False
        self.selected_line_tracker.clear()
        self.hybrid_tracker.clear()
        self.string_line_tracker.clear()
        self.note_marker_tracker.clear()
        self.cancel_line_selection()

    def run(self) -> None:
        try:
            model = load_yolo_model(self.model_path)
        except Exception as exc:  # pragma: no cover - GUI error path
            self.error_message.emit(str(exc))
            return

        capture = LatestFrameCapture(self.camera_index)
        self._capture = capture
        capture.start()
        try:
            self._run_frames(model, capture)
        except Exception as exc:  # pragma: no cover - GUI error path
            if self._running:
                self.error_message.emit(str(exc))
        finally:
            capture.close()
            self.hybrid_tracker.close()
            self._capture = None
            self.occlusion_masker.close()
            if self.hand_tracker is not None:
                self.hand_tracker.close()

    def _run_frames(self, model, capture: LatestFrameCapture) -> None:
        previous_frame_time = perf_counter()

        while self._running:
            if self._clear_tracking_requested:
                self._clear_tracking_requested = False
                self._clear_manual_tracking()
            self._apply_pending_calibration()
            if self.pending_reference_fret_index is not None:
                reference_index = self.pending_reference_fret_index
                self.pending_reference_fret_index = None
                if self.selected_line_tracker.is_active:
                    self.selected_line_tracker.set_reference_fret_index(reference_index)
                    if self.latest_frame_bgr is not None:
                        self.hybrid_tracker.seed(self.latest_frame_bgr, self.latest_exclusion_mask)
                    self.string_line_tracker.clear()
                    self.note_marker_tracker.clear()
            if self.pending_manual_selection is not None:
                nut_line, reference_fret_line, reference_fret_index = self.pending_manual_selection
                self.selected_line_tracker.initialize_from_lines(
                    nut_line,
                    reference_fret_line,
                    reference_fret_index,
                )
                if self.latest_frame_bgr is not None:
                    self.hybrid_tracker.seed(self.latest_frame_bgr, self.latest_exclusion_mask)
                self.pending_manual_selection = None
                self.selection_active = False
                self.frozen_selection_lines = ()

            if self.selection_active:
                self.msleep(30)
                continue

            captured = capture.read()
            if captured is None:
                break
            frame_bgr = captured.image
            frame_timestamp = captured.timestamp

            if self.camera_calibration is not None:
                frame_bgr = self.camera_calibration.undistort_frame(frame_bgr)
            frame_bgr = cv2.flip(frame_bgr, 1)

            self.latest_frame_bgr = frame_bgr.copy()
            inference_start = perf_counter()
            self.latest_exclusion_mask = self.occlusion_masker.process(frame_bgr)
            hand_mask_ms = (perf_counter() - inference_start) * 1000
            tracking_start = perf_counter()

            if self.selection_request:
                annotated, analysis = self._analyze_selection_frame(model, frame_bgr)
                self.selection_request = False
                self.selection_active = True
                self.frozen_selection_lines = analysis.detected_lines
            elif self.selected_line_tracker.is_active or self.manual_tracking_requested:
                def detect(roi):
                    return predict_detected_lines(
                        model=model,
                        frame_bgr=frame_bgr,
                        conf_threshold=(
                            max(TRACKING_CONF_FLOOR, self.conf_threshold * TRACKING_CONF_SCALE)
                            if roi is not None else self.conf_threshold
                        ),
                        image_size=self.image_size,
                        roi=roi,
                    )
                tracking_frame = self.hybrid_tracker.update(
                    frame_bgr, detect, self.latest_exclusion_mask, timestamp=frame_timestamp,
                )
                annotated, analysis = self._analyze_selected_lines_frame(frame_bgr, tracking_frame)
            else:
                annotated, analysis = self._analyze_yolo_assist_frame(model, frame_bgr)

            inference_ms = (perf_counter() - inference_start) * 1000.0
            now = perf_counter()
            fps = 1.0 / max(now - previous_frame_time, 1e-6)
            previous_frame_time = now

            visible_fret_count = len(analysis.stable_grid.ordered_fret_lines) if analysis.stable_grid is not None else 0
            stats = PracticeFrameStats(
                fps=fps,
                inference_ms=inference_ms,
                total_detections=sum(analysis.class_counts.values()),
                class_counts=analysis.class_counts,
                visible_fret_count=visible_fret_count,
                marker_count=len(analysis.note_markers),
                matched_marker_count=len(analysis.matched_marker_indices),
                fingertip_count=len(analysis.fingertip_points),
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
                stable_grid=analysis.stable_grid,
                edge_refinement=analysis.edge_refinement,
                hand_mask_available=self.occlusion_masker.available,
                hand_mask_ms=hand_mask_ms,
                tracking_ms=(perf_counter() - tracking_start) * 1000,
                last_recovery_ms=self.hybrid_tracker.last_recovery_ms,
                recovery_pending=self.hybrid_tracker.recovery.busy,
            )
            self.frame_ready.emit(bgr_to_qimage(annotated), stats)

    def _analyze_selection_frame(self, model, frame_bgr) -> tuple[object, PracticeFrameAnalysis]:
        detected_lines = predict_detected_lines(
            model=model,
            frame_bgr=frame_bgr,
            conf_threshold=self.conf_threshold,
            image_size=self.image_size,
        )
        self.selected_line_tracker.clear()
        self.hybrid_tracker.clear()
        class_counts = count_detected_classes(detected_lines)
        stable_grid = self.tracker.update(build_fretboard_grid(detected_lines, prior_grid=self.tracker.last_grid))
        edge_refinement = refine_grid_with_edge_lines_diagnostics(
            frame_bgr,
            stable_grid,
            string_tracker=self.string_line_tracker,
            suppressed_fret_indices=frozenset(),
            apply_fret_refinement=False,
            yolo_detected_lines=detected_lines,
        )
        stable_grid = edge_refinement.grid
        # This frozen frame is for choosing physical boundaries, before an
        # absolute fret number and visual reference exist. Do not show AR notes.
        note_markers = ()
        hand_tracking = self._process_hand_tracking(frame_bgr, note_markers)
        annotated = draw_detected_lines(frame_bgr, detected_lines)
        annotated = draw_note_markers(annotated, note_markers, matched_marker_indices=hand_tracking.matched_marker_indices)
        annotated = draw_fingertips(annotated, hand_tracking.fingertips)
        return annotated, PracticeFrameAnalysis(
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

    def _analyze_yolo_assist_frame(self, model, frame_bgr) -> tuple[object, PracticeFrameAnalysis]:
        # Fret identity and the camera reference are established explicitly once.
        # Rendering the old uncalibrated edge grid here would bypass the verified
        # tracking path and recreate the same wrong-fret/hand-warp failures.
        self.note_marker_tracker.clear()
        hand_tracking = self._process_hand_tracking(frame_bgr, ())
        return frame_bgr.copy(), PracticeFrameAnalysis(
            detected_lines=(),
            stable_grid=None,
            note_markers=(),
            fingertip_points=hand_tracking.fingertips,
            matched_marker_indices=(),
            class_counts={},
            hand_tracking_available=hand_tracking.backend_available,
            tracking_mode="calibration_required",
        )

    def _analyze_selected_lines_frame(
        self,
        frame_bgr,
        tracking_frame: HybridTrackingFrame,
    ) -> tuple[object, PracticeFrameAnalysis]:
        reference_fret_index = self.selected_line_tracker.reference_fret_index
        tracking_result = tracking_frame.result
        primary_detected_lines = tracking_frame.detected_lines
        if tracking_result.tracking_mode in {"selected_lines_reacquired", "selected_lines_hold", "selected_lines_lost"}:
            self.note_marker_tracker.clear()
            self.string_line_tracker.clear()
        elif tracking_frame.homography is not None:
            self.note_marker_tracker.apply_motion(tracking_frame.homography)
        class_counts = count_detected_classes(primary_detected_lines)
        if tracking_result.stable_grid is None or tracking_result.tracking_mode == "selected_lines_hold":
            self.note_marker_tracker.update(())
            hand_tracking = self._process_hand_tracking(frame_bgr, ())
            annotated = frame_bgr.copy()
            annotated = draw_fingertips(annotated, hand_tracking.fingertips)
            return annotated, PracticeFrameAnalysis(
                detected_lines=primary_detected_lines,
                stable_grid=None,
                note_markers=(),
                fingertip_points=hand_tracking.fingertips,
                matched_marker_indices=(),
                class_counts=class_counts,
                hand_tracking_available=hand_tracking.backend_available,
                tracking_mode=tracking_result.tracking_mode,
                reference_fret_index=reference_fret_index,
            )

        # Calibrated guides use only the registered model. Per-frame edge
        # diagnostics do not affect their coordinates; keep them in the viewer.
        stable_grid = tracking_result.stable_grid
        edge_refinement = None
        note_markers = (
            self._build_current_note_markers(stable_grid, edge_refinement, calibrated=True)
            if tracking_result.tracking_mode != "selected_lines_hold" else ()
        )
        note_markers = filter_visible_markers(note_markers, stable_grid, frame_bgr.shape,
                                               tracking_frame.exclusion_mask, hide_occluded=False)
        hand_tracking = self._process_hand_tracking(frame_bgr, note_markers)

        annotated = frame_bgr.copy()
        if self.show_edge_guides:
            annotated = draw_calibrated_guides(annotated, stable_grid)
        annotated = draw_note_markers(annotated, note_markers, matched_marker_indices=hand_tracking.matched_marker_indices)
        annotated = draw_fingertips(annotated, hand_tracking.fingertips)
        return annotated, PracticeFrameAnalysis(
            detected_lines=tracking_result.tracked_lines,
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

    def _build_current_note_markers(
        self,
        stable_grid: StableFretboardGrid | None,
        edge_refinement: EdgeRefinementResult | None,
        *,
        calibrated: bool = False,
    ) -> tuple[NoteMarker, ...]:
        marker_grid, marker_edge_refinement = marker_grid_context(
            stable_grid,
            edge_refinement,
            suppressed_fret_indices=frozenset(),
        )
        note_markers = build_practice_step_note_markers(
            marker_grid,
            self.current_step,
            reverse_strings=self.reverse_strings,
        )
        note_markers = filter_note_markers_by_edge_frets(
            note_markers,
            marker_edge_refinement,
            self.edge_supported_notes_only and not calibrated,
            require_detected_fret_interval=not calibrated,
        )
        return self.note_marker_tracker.update(note_markers)

    def _process_hand_tracking(
        self,
        frame_bgr,
        note_markers: tuple[NoteMarker, ...],
    ) -> HandTrackingResult:
        if self.hand_tracker is None:
            return HandTrackingResult(
                fingertips=(),
                matched_marker_indices=(),
                match_radius_px=0.0,
                backend_available=False,
            )
        return self.hand_tracker.process(frame_bgr, note_markers)
