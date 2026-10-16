from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np


STRING_COUNT = 6
PLAYABLE_EDGE_MARGIN_RATIO = 0.10
FRETTED_NOTE_ANCHOR_RATIO = 0.5
NOTE_MARKER_STABLE_ALPHA = 0.16
NOTE_MARKER_FAST_ALPHA = 0.42
NOTE_MARKER_FAST_MOVE_PX = 28.0
NOTE_MARKER_DEADBAND_PX = 3.0
NOTE_MARKER_KEEP_MISSING_FRAMES = 8
STANDARD_TUNING_MIDI = (40, 45, 50, 55, 59, 64)  # E2, A2, D3, G3, B3, E4
NOTE_NAME_TO_PITCH_CLASS = {
    "도": 0,
    "높은 도": 0,
    "레": 2,
    "미": 4,
    "파": 5,
    "솔": 7,
    "라": 9,
    "시": 11,
}
TEST_CENTER_NOTE_TO_FRET_INDEX = {
    "도": 0,
    "레": 2,
    "미": 4,
    "파": 5,
    "솔": 7,
    "라": 9,
    "시": 11,
    "높은 도": 12,
}

FIRST_GAP_RATIO_MIN = 0.65
GAP_RATIO_MIN = 0.55
GAP_RATIO_MAX = 1.15
SELECTED_LINE_HOLD_FRAMES = 12
SELECTED_LINE_CLEAR_FRAMES = 20
SELECTED_LINE_MOTION_FRAMES = 8
SELECTED_LINE_ROI_EXPAND_RATIO = 2.0
SELECTED_LINE_ROI_PADDING = 72.0
EDGE_SUPPORT_ANGLE_MIN = 0.92
EDGE_SUPPORT_MIN_LINES = 3
EDGE_SUPPORT_PRIOR_WEIGHT = 0.34
EDGE_SHORT_LINE_WEIGHT_FLOOR = 0.14
SELECTED_REFERENCE_GAP_RATIO_TRACK_MIN = 0.88
SELECTED_REFERENCE_GAP_RATIO_TRACK_MAX = 1.18
SELECTED_REFERENCE_GAP_RATIO_REACQUIRE_MIN = 0.72
SELECTED_REFERENCE_GAP_RATIO_REACQUIRE_MAX = 1.35
SELECTED_REFERENCE_CONFIDENCE_BONUS = 4.0
SELECTED_REFERENCE_LOCAL_GAP_TOLERANCE_MIN_PX = 3.5
SELECTED_REFERENCE_SUPPORT_MIN_LINES = 2
SELECTED_REFERENCE_INNER_FRET_GUARD_RATIO = 0.45
INDEXED_FRET_DISTANCE_TOLERANCE_RATIO = 0.35
INDEXED_FRET_DISTANCE_TOLERANCE_MIN_PX = 4.0
INDEXED_FRET_DISTANCE_TOLERANCE_MAX_PX = 16.0


@dataclass(frozen=True)
class SelectedTrackingResult:
    stable_grid: StableFretboardGrid | None
    tracked_lines: tuple[DetectedLine, ...]
    tracking_mode: str


@dataclass(frozen=True)
class DetectedLine:
    kind: str
    p0: tuple[float, float]
    p1: tuple[float, float]
    confidence: float
    endpoints_observed: bool = True


@dataclass(frozen=True)
class StableFretboardGrid:
    nut_line: DetectedLine
    ordered_fret_lines: tuple[DetectedLine, ...]
    string_crossings: tuple[tuple[tuple[float, float], ...], ...]


@dataclass(frozen=True)
class NoteMarker:
    string_index: int
    fret_index: int
    point_xy: tuple[float, float]
    is_open: bool


@dataclass(frozen=True)
class TrackedMarkerState:
    point_xy: tuple[float, float]
    missing_frames: int = 0


class NoteMarkerTracker:
    def __init__(
        self,
        stable_alpha: float = NOTE_MARKER_STABLE_ALPHA,
        fast_alpha: float = NOTE_MARKER_FAST_ALPHA,
        fast_move_px: float = NOTE_MARKER_FAST_MOVE_PX,
        deadband_px: float = NOTE_MARKER_DEADBAND_PX,
        keep_missing_frames: int = NOTE_MARKER_KEEP_MISSING_FRAMES,
    ) -> None:
        self.stable_alpha = stable_alpha
        self.fast_alpha = fast_alpha
        self.fast_move_px = fast_move_px
        self.deadband_px = deadband_px
        self.keep_missing_frames = keep_missing_frames
        self._states: dict[tuple[int, int, bool], TrackedMarkerState] = {}

    def clear(self) -> None:
        self._states.clear()

    def apply_motion(self, homography: np.ndarray) -> None:
        """Move smoothing history with the guitar, so smoothing adds no motion lag."""
        self._states = {
            key: TrackedMarkerState(
                point_xy=project_homography(homography, *state.point_xy),
                missing_frames=state.missing_frames,
            )
            for key, state in self._states.items()
        }

    def update(self, markers: Sequence[NoteMarker]) -> tuple[NoteMarker, ...]:
        if not markers:
            self._decay_missing_states(seen_keys=set())
            return ()

        next_states: dict[tuple[int, int, bool], TrackedMarkerState] = {}
        smoothed_markers: list[NoteMarker] = []
        seen_keys: set[tuple[int, int, bool]] = set()

        for marker in markers:
            key = (marker.string_index, marker.fret_index, marker.is_open)
            seen_keys.add(key)
            current_point = point_array(marker.point_xy)
            previous_state = self._states.get(key)
            if previous_state is None:
                smoothed_point = current_point
            else:
                previous_point = point_array(previous_state.point_xy)
                delta = float(np.linalg.norm(current_point - previous_point))
                if delta <= self.deadband_px:
                    smoothed_point = previous_point
                else:
                    alpha = self.fast_alpha if delta >= self.fast_move_px else self.stable_alpha
                    smoothed_point = interpolate_points(previous_point, current_point, alpha)

            smoothed_point_xy = point_tuple(smoothed_point)
            next_states[key] = TrackedMarkerState(point_xy=smoothed_point_xy, missing_frames=0)
            smoothed_markers.append(
                NoteMarker(
                    string_index=marker.string_index,
                    fret_index=marker.fret_index,
                    point_xy=smoothed_point_xy,
                    is_open=marker.is_open,
                )
            )

        for key, state in self._states.items():
            if key in seen_keys:
                continue
            missing_frames = state.missing_frames + 1
            if missing_frames <= self.keep_missing_frames:
                next_states[key] = TrackedMarkerState(point_xy=state.point_xy, missing_frames=missing_frames)

        self._states = next_states
        return tuple(smoothed_markers)

    def _decay_missing_states(self, seen_keys: set[tuple[int, int, bool]]) -> None:
        next_states: dict[tuple[int, int, bool], TrackedMarkerState] = {}
        for key, state in self._states.items():
            if key in seen_keys:
                next_states[key] = state
                continue
            missing_frames = state.missing_frames + 1
            if missing_frames <= self.keep_missing_frames:
                next_states[key] = TrackedMarkerState(point_xy=state.point_xy, missing_frames=missing_frames)
        self._states = next_states


@dataclass(frozen=True)
class FretboardEdgeFit:
    reference_direction: np.ndarray
    edge_p0: tuple[np.ndarray, np.ndarray]
    edge_p1: tuple[np.ndarray, np.ndarray]
    support_length_hint: float


class SelectedLineTracker:
    def __init__(
        self,
        max_history: int = 5,
        hold_frames: int = SELECTED_LINE_HOLD_FRAMES,
        clear_frames: int = SELECTED_LINE_CLEAR_FRAMES,
    ) -> None:
        self.max_history = max_history
        self.hold_frames = hold_frames
        self.clear_frames = max(clear_frames, hold_frames)
        self.nut_history: deque[DetectedLine] = deque(maxlen=max_history)
        self.reference_history: deque[DetectedLine] = deque(maxlen=max_history)
        self.current_nut_line: DetectedLine | None = None
        self.current_reference_fret_line: DetectedLine | None = None
        self.reference_fret_index: int | None = None
        self.last_stable_grid: StableFretboardGrid | None = None
        self.missed_frames = 0

    @property
    def is_active(self) -> bool:
        return (
            self.current_nut_line is not None
            and self.current_reference_fret_line is not None
            and self.reference_fret_index is not None
        )

    def clear(self) -> None:
        self.nut_history.clear()
        self.reference_history.clear()
        self.current_nut_line = None
        self.current_reference_fret_line = None
        self.reference_fret_index = None
        self.last_stable_grid = None
        self.missed_frames = 0

    def apply_motion(self, homography: np.ndarray) -> None:
        """Predict this frame's geometry without counting prediction as detection."""
        if self.current_nut_line is not None:
            self.current_nut_line = transform_line(self.current_nut_line, homography)
        if self.current_reference_fret_line is not None:
            self.current_reference_fret_line = transform_line(self.current_reference_fret_line, homography)
        self.nut_history = deque(
            (transform_line(line, homography) for line in self.nut_history), maxlen=self.max_history,
        )
        self.reference_history = deque(
            (transform_line(line, homography) for line in self.reference_history), maxlen=self.max_history,
        )
        if self.last_stable_grid is not None:
            self.last_stable_grid = transform_grid(self.last_stable_grid, homography)

    def accept_registered_grid(self, grid: StableFretboardGrid) -> None:
        """Accept one coherent registration without refitting individual endpoints."""
        self.last_stable_grid = grid
        self.current_nut_line = grid.nut_line
        self.current_reference_fret_line = grid.ordered_fret_lines[-1]
        self.nut_history.clear()
        self.reference_history.clear()
        self.nut_history.append(self.current_nut_line)
        self.reference_history.append(self.current_reference_fret_line)
        self.missed_frames = 0

    def hold_or_lose(self) -> SelectedTrackingResult:
        return self._handle_hold_or_loss()

    def initialize_from_lines(
        self,
        nut_line: DetectedLine,
        reference_fret_line: DetectedLine,
        reference_fret_index: int,
    ) -> None:
        if reference_fret_index < 1:
            raise ValueError("Reference fret index must be at least 1.")

        normalized_nut = orient_line_for_screen(nut_line)
        normalized_reference = orient_line_like_reference(
            orient_line_for_screen(reference_fret_line),
            normalize(line_vector(normalized_nut)),
        )
        self.current_nut_line = normalized_nut
        self.current_reference_fret_line = normalized_reference
        self.reference_fret_index = reference_fret_index
        self.nut_history.clear()
        self.reference_history.clear()
        self.nut_history.append(normalized_nut)
        self.reference_history.append(normalized_reference)
        self.last_stable_grid = build_grid_from_reference_lines(
            nut_line=normalized_nut,
            reference_fret_line=normalized_reference,
            reference_fret_index=reference_fret_index,
        )
        self.missed_frames = 0

    def set_reference_fret_index(self, reference_fret_index: int) -> None:
        if reference_fret_index < 1:
            raise ValueError("Reference fret index must be at least 1.")

        self.reference_fret_index = reference_fret_index
        if self.current_nut_line is None or self.current_reference_fret_line is None:
            return

        self.nut_history.clear()
        self.reference_history.clear()
        self.nut_history.append(self.current_nut_line)
        self.reference_history.append(self.current_reference_fret_line)
        self.last_stable_grid = build_grid_from_reference_lines(
            nut_line=self.current_nut_line,
            reference_fret_line=self.current_reference_fret_line,
            reference_fret_index=reference_fret_index,
        )
        self.missed_frames = 0

    def search_roi(self, frame_shape: Sequence[int]) -> tuple[int, int, int, int] | None:
        frame_height, frame_width = int(frame_shape[0]), int(frame_shape[1])
        tracked_lines = self._current_tracked_lines()
        if tracked_lines is None:
            return None

        nut_line, reference_line = tracked_lines
        points = np.asarray(
            [nut_line.p0, nut_line.p1, reference_line.p0, reference_line.p1],
            dtype=np.float32,
        )
        min_x = float(np.min(points[:, 0]))
        max_x = float(np.max(points[:, 0]))
        min_y = float(np.min(points[:, 1]))
        max_y = float(np.max(points[:, 1]))
        width = max(max_x - min_x, 24.0)
        height = max(max_y - min_y, 24.0)
        center_x = (min_x + max_x) * 0.5
        center_y = (min_y + max_y) * 0.5
        half_width = width * SELECTED_LINE_ROI_EXPAND_RATIO * 0.5 + SELECTED_LINE_ROI_PADDING
        half_height = height * SELECTED_LINE_ROI_EXPAND_RATIO * 0.5 + SELECTED_LINE_ROI_PADDING
        x0 = max(0, int(np.floor(center_x - half_width)))
        y0 = max(0, int(np.floor(center_y - half_height)))
        x1 = min(frame_width, int(np.ceil(center_x + half_width)))
        y1 = min(frame_height, int(np.ceil(center_y + half_height)))
        if x1 - x0 < 8 or y1 - y0 < 8:
            return None
        return (x0, y0, x1, y1)

    def update(
        self,
        primary_detected_lines: Sequence[DetectedLine],
        fallback_detected_lines: Sequence[DetectedLine] | None = None,
        motion_grid: StableFretboardGrid | None = None,
    ) -> SelectedTrackingResult:
        if (
            not self.is_active
            or self.current_nut_line is None
            or self.current_reference_fret_line is None
            or self.reference_fret_index is None
        ):
            return SelectedTrackingResult(stable_grid=None, tracked_lines=(), tracking_mode="selected_lines_lost")

        primary_support_grid = self._build_support_projected_grid(primary_detected_lines, relaxed=False)
        if primary_support_grid is not None:
            return self._apply_grid_match(primary_support_grid, tracking_mode="selected_lines_tracking")

        primary_pair = self._match_locked_pair(
            nut_candidates=[line for line in primary_detected_lines if line.kind == "nut"],
            fret_candidates=[line for line in primary_detected_lines if line.kind == "fret"],
            relaxed=False,
        )
        if primary_pair is not None and self._is_pair_gap_consistent(*primary_pair, relaxed=False):
            support_grid = self._build_reference_locked_grid(*primary_pair, support_lines=primary_detected_lines)
            if support_grid is not None:
                return self._apply_grid_match(support_grid, tracking_mode="selected_lines_tracking")
            return self._apply_pair_match(
                *primary_pair,
                tracking_mode="selected_lines_tracking",
                support_lines=primary_detected_lines,
            )

        if fallback_detected_lines is not None:
            fallback_support_grid = self._build_support_projected_grid(
                fallback_detected_lines,
                relaxed=True,
            )
            if fallback_support_grid is not None:
                return self._apply_grid_match(
                    fallback_support_grid,
                    tracking_mode="selected_lines_reacquired",
                )

            fallback_pair = self._match_locked_pair(
                nut_candidates=[line for line in fallback_detected_lines if line.kind == "nut"],
                fret_candidates=[line for line in fallback_detected_lines if line.kind == "fret"],
                relaxed=True,
            )
            if fallback_pair is not None and self._is_pair_gap_consistent(*fallback_pair, relaxed=True):
                fallback_support_lines = tuple(primary_detected_lines) + tuple(fallback_detected_lines)
                support_grid = self._build_reference_locked_grid(
                    *fallback_pair,
                    support_lines=fallback_support_lines,
                )
                if support_grid is not None:
                    return self._apply_grid_match(support_grid, tracking_mode="selected_lines_reacquired")
                return self._apply_pair_match(
                    *fallback_pair,
                    tracking_mode="selected_lines_reacquired",
                    support_lines=fallback_support_lines,
                )

        if motion_grid is not None and self.missed_frames < SELECTED_LINE_MOTION_FRAMES:
            self.missed_frames += 1
            self.last_stable_grid = motion_grid
            return SelectedTrackingResult(
                stable_grid=motion_grid,
                tracked_lines=self._tracked_lines_from_grid(motion_grid),
                tracking_mode="selected_lines_motion",
            )

        combined_lines = tuple(primary_detected_lines) + tuple(fallback_detected_lines or ())
        hold_grid = self._update_partial_lines(combined_lines)
        if hold_grid is not None:
            return SelectedTrackingResult(
                stable_grid=hold_grid,
                tracked_lines=self._tracked_lines_from_grid(hold_grid),
                tracking_mode="selected_lines_hold",
            )

        return self._handle_hold_or_loss()

    def _current_tracked_lines(self) -> tuple[DetectedLine, DetectedLine] | None:
        if self.last_stable_grid is not None and self.last_stable_grid.ordered_fret_lines:
            return (self.last_stable_grid.nut_line, self.last_stable_grid.ordered_fret_lines[-1])
        if self.current_nut_line is None or self.current_reference_fret_line is None:
            return None
        return (self.current_nut_line, self.current_reference_fret_line)

    def _tracked_lines_from_grid(self, grid: StableFretboardGrid) -> tuple[DetectedLine, DetectedLine]:
        if not grid.ordered_fret_lines:
            return (grid.nut_line, grid.nut_line)
        return (grid.nut_line, grid.ordered_fret_lines[-1])

    def _is_pair_gap_consistent(
        self,
        matched_nut: DetectedLine,
        matched_reference: DetectedLine,
        relaxed: bool = False,
    ) -> bool:
        if self.current_nut_line is None or self.current_reference_fret_line is None:
            return True

        current_gap = max(projected_pair_gap(self.current_nut_line, self.current_reference_fret_line), 1.0)
        candidate_gap = projected_pair_gap(matched_nut, matched_reference)
        gap_ratio = candidate_gap / current_gap
        minimum_ratio, maximum_ratio = self._gap_ratio_bounds(relaxed=relaxed)
        return minimum_ratio <= gap_ratio <= maximum_ratio

    def _gap_ratio_bounds(self, relaxed: bool) -> tuple[float, float]:
        if relaxed:
            return (
                SELECTED_REFERENCE_GAP_RATIO_REACQUIRE_MIN,
                SELECTED_REFERENCE_GAP_RATIO_REACQUIRE_MAX,
            )
        return (
            SELECTED_REFERENCE_GAP_RATIO_TRACK_MIN,
            SELECTED_REFERENCE_GAP_RATIO_TRACK_MAX,
        )

    def _match_locked_pair(
        self,
        nut_candidates: Sequence[DetectedLine],
        fret_candidates: Sequence[DetectedLine],
        relaxed: bool,
    ) -> tuple[DetectedLine, DetectedLine] | None:
        if self.current_nut_line is None or self.current_reference_fret_line is None:
            return None

        matched_nut = match_line_to_reference(self.current_nut_line, nut_candidates)
        if matched_nut is None:
            return None

        matched_reference = self._match_locked_reference_line(
            matched_nut=matched_nut,
            fret_candidates=fret_candidates,
            relaxed=relaxed,
        )
        if matched_reference is None:
            return None
        return (matched_nut, matched_reference)

    def _match_locked_reference_line(
        self,
        matched_nut: DetectedLine,
        fret_candidates: Sequence[DetectedLine],
        relaxed: bool,
    ) -> DetectedLine | None:
        if self.current_nut_line is None or self.current_reference_fret_line is None:
            return None

        reference_direction = normalize(line_vector(self.current_nut_line))
        current_gap = max(projected_pair_gap(self.current_nut_line, self.current_reference_fret_line), 1.0)
        minimum_ratio, maximum_ratio = self._gap_ratio_bounds(relaxed=relaxed)

        expected_reference = self._translate_reference_with_nut(matched_nut) or self.current_reference_fret_line
        expected_gap = projected_pair_gap(matched_nut, expected_reference)
        inner_fret_guard = self._reference_inner_fret_guard(current_gap)
        ranked_candidates: list[tuple[float, float, float, DetectedLine]] = []
        for fret_candidate in fret_candidates:
            normalized_candidate = orient_line_like_reference(
                orient_line_for_screen(fret_candidate),
                reference_direction,
            )
            candidate_gap = projected_pair_gap(matched_nut, normalized_candidate)
            if candidate_gap <= 0:
                continue

            gap_ratio = candidate_gap / current_gap
            if gap_ratio < minimum_ratio or gap_ratio > maximum_ratio:
                continue

            distance_past_expected = candidate_gap - expected_gap
            confidence_bonus = normalized_candidate.confidence * SELECTED_REFERENCE_CONFIDENCE_BONUS
            shape_score = line_match_score(expected_reference, normalized_candidate) * 0.02
            ranked_score = -candidate_gap + shape_score - confidence_bonus
            ranked_candidates.append(
                (ranked_score, distance_past_expected, -normalized_candidate.confidence, normalized_candidate)
            )

        if not ranked_candidates:
            return None

        ranked_candidates.sort(key=lambda candidate: (candidate[0], candidate[1], candidate[2]))
        _, distance_past_expected, _, selected_candidate = ranked_candidates[0]
        if distance_past_expected < -inner_fret_guard:
            return None
        return selected_candidate

    def _reference_inner_fret_guard(self, current_gap: float) -> float:
        if self.reference_fret_index is None or self.reference_fret_index <= 1:
            return max(SELECTED_REFERENCE_LOCAL_GAP_TOLERANCE_MIN_PX, current_gap * 0.04)

        current_ratio = scale_length_ratio(self.reference_fret_index)
        previous_ratio = scale_length_ratio(self.reference_fret_index - 1)
        if current_ratio <= 1e-6 or previous_ratio <= 0.0:
            return max(SELECTED_REFERENCE_LOCAL_GAP_TOLERANCE_MIN_PX, current_gap * 0.04)

        local_gap = current_gap * max(current_ratio - previous_ratio, 0.0) / current_ratio
        return max(SELECTED_REFERENCE_LOCAL_GAP_TOLERANCE_MIN_PX, local_gap * SELECTED_REFERENCE_INNER_FRET_GUARD_RATIO)

    def _build_reference_locked_grid(
        self,
        matched_nut: DetectedLine,
        matched_reference: DetectedLine,
        support_lines: Sequence[DetectedLine],
    ) -> StableFretboardGrid | None:
        if self.reference_fret_index is None:
            return None

        refined_pair = refine_reference_pair_with_support(
            matched_nut,
            matched_reference,
            support_lines=support_lines,
            prior_grid=self.last_stable_grid,
        )
        if refined_pair is not None:
            matched_nut, matched_reference = refined_pair

        provisional_grid = build_grid_from_reference_lines(
            nut_line=matched_nut,
            reference_fret_line=matched_reference,
            reference_fret_index=self.reference_fret_index,
        )
        if provisional_grid is None:
            return None

        reference_direction = normalize(line_vector(matched_nut))
        support_fret_lines = [
            orient_line_like_reference(orient_line_for_screen(line), reference_direction)
            for line in support_lines
            if line.kind == "fret"
        ]
        if support_fret_lines:
            refined_support_frets = refine_lines_with_fitted_edges(
                lines_to_refine=support_fret_lines,
                nut_line=matched_nut,
                reference_fret_line=matched_reference,
                support_lines=support_lines,
                prior_grid=self.last_stable_grid,
            )
            if refined_support_frets is not None and len(refined_support_frets) == len(support_fret_lines):
                support_fret_lines = list(refined_support_frets)

        indexed_fret_lines = dict(
            infer_indexed_fret_lines_from_reference_grid(
                provisional_grid,
                support_fret_lines,
            )
        )
        indexed_fret_lines[self.reference_fret_index] = matched_reference
        return build_grid_from_indexed_fret_lines(
            nut_line=matched_nut,
            indexed_fret_lines=tuple(sorted(indexed_fret_lines.items())),
            max_fret_index=self.reference_fret_index,
            support_lines=(matched_nut, *support_fret_lines),
            prior_grid=self.last_stable_grid,
        )

    def _translate_reference_with_nut(
        self,
        matched_nut: DetectedLine,
    ) -> DetectedLine | None:
        if self.current_nut_line is None or self.current_reference_fret_line is None:
            return None

        midpoint_delta = line_midpoint(matched_nut) - line_midpoint(self.current_nut_line)
        translated_reference = DetectedLine(
            kind="fret",
            p0=point_tuple(point_array(self.current_reference_fret_line.p0) + midpoint_delta),
            p1=point_tuple(point_array(self.current_reference_fret_line.p1) + midpoint_delta),
            confidence=self.current_reference_fret_line.confidence,
        )
        return orient_line_like_reference(
            orient_line_for_screen(translated_reference),
            normalize(line_vector(matched_nut)),
        )

    def _build_support_projected_grid(
        self,
        detected_lines: Sequence[DetectedLine],
        relaxed: bool,
    ) -> StableFretboardGrid | None:
        if self.reference_fret_index is None:
            return None

        matched_nut = match_line_to_reference(
            self.current_nut_line,
            [line for line in detected_lines if line.kind == "nut"],
        )
        if matched_nut is None:
            return None

        translated_reference = self._translate_reference_with_nut(matched_nut)
        if translated_reference is None:
            return None

        reference_direction = normalize(line_vector(matched_nut))
        support_fret_lines = [
            orient_line_like_reference(orient_line_for_screen(line), reference_direction)
            for line in detected_lines
            if line.kind == "fret"
        ]
        if len(support_fret_lines) < SELECTED_REFERENCE_SUPPORT_MIN_LINES:
            return None

        refined_pair = refine_reference_pair_with_support(
            matched_nut,
            translated_reference,
            support_lines=(matched_nut, *support_fret_lines),
            prior_grid=self.last_stable_grid,
        )
        if refined_pair is not None:
            matched_nut, translated_reference = refined_pair
            reference_direction = normalize(line_vector(matched_nut))
            support_fret_lines = [
                orient_line_like_reference(line, reference_direction)
                for line in support_fret_lines
            ]

        provisional_grid = build_grid_from_reference_lines(
            nut_line=matched_nut,
            reference_fret_line=translated_reference,
            reference_fret_index=self.reference_fret_index,
        )
        if provisional_grid is None:
            return None

        refined_support_frets = refine_lines_with_fitted_edges(
            lines_to_refine=support_fret_lines,
            nut_line=matched_nut,
            reference_fret_line=translated_reference,
            support_lines=(matched_nut, *support_fret_lines),
            prior_grid=self.last_stable_grid,
        )
        if refined_support_frets is not None and len(refined_support_frets) == len(support_fret_lines):
            support_fret_lines = list(refined_support_frets)

        indexed_fret_lines = infer_indexed_fret_lines_from_reference_grid(
            provisional_grid,
            support_fret_lines,
        )
        if len(indexed_fret_lines) < SELECTED_REFERENCE_SUPPORT_MIN_LINES:
            return None

        candidate_grid = build_grid_from_indexed_fret_lines(
            nut_line=matched_nut,
            indexed_fret_lines=indexed_fret_lines,
            max_fret_index=self.reference_fret_index,
            support_lines=(matched_nut, *support_fret_lines),
            prior_grid=self.last_stable_grid,
        )
        if candidate_grid is None or not candidate_grid.ordered_fret_lines:
            return None

        candidate_reference = candidate_grid.ordered_fret_lines[-1]
        if not self._is_pair_gap_consistent(matched_nut, candidate_reference, relaxed=relaxed):
            return None
        return candidate_grid

    def _build_smoothed_grid(self) -> StableFretboardGrid | None:
        if (
            self.current_nut_line is None
            or self.current_reference_fret_line is None
            or self.reference_fret_index is None
        ):
            return None

        nut_samples: Sequence[DetectedLine] = tuple(self.nut_history) or (self.current_nut_line,)
        reference_samples: Sequence[DetectedLine] = tuple(self.reference_history) or (
            self.current_reference_fret_line,
        )
        nut_line = median_line(nut_samples, kind="nut")
        reference_fret_line = median_line(reference_samples, kind="fret")
        return build_grid_from_reference_lines(
            nut_line=nut_line,
            reference_fret_line=reference_fret_line,
            reference_fret_index=self.reference_fret_index,
        )

    def _apply_pair_match(
        self,
        matched_nut: DetectedLine,
        matched_reference: DetectedLine,
        tracking_mode: str,
        support_lines: Sequence[DetectedLine] | None = None,
    ) -> SelectedTrackingResult:
        self.missed_frames = 0
        refined_pair = None
        if support_lines is not None:
            refined_pair = refine_reference_pair_with_support(
                matched_nut,
                matched_reference,
                support_lines=support_lines,
                prior_grid=self.last_stable_grid,
            )
        if refined_pair is not None:
            matched_nut, matched_reference = refined_pair
        self.current_nut_line = matched_nut
        self.current_reference_fret_line = matched_reference
        self.nut_history.append(matched_nut)
        self.reference_history.append(matched_reference)

        stable_grid = self._build_smoothed_grid()
        self.last_stable_grid = stable_grid
        return SelectedTrackingResult(
            stable_grid=stable_grid,
            tracked_lines=self._tracked_lines_from_grid(stable_grid) if stable_grid is not None else (),
            tracking_mode=tracking_mode if stable_grid is not None else "selected_lines_lost",
        )

    def _apply_grid_match(
        self,
        stable_grid: StableFretboardGrid,
        tracking_mode: str,
    ) -> SelectedTrackingResult:
        self.missed_frames = 0
        self.last_stable_grid = stable_grid
        self.current_nut_line = stable_grid.nut_line
        self.nut_history.append(stable_grid.nut_line)
        if stable_grid.ordered_fret_lines:
            self.current_reference_fret_line = stable_grid.ordered_fret_lines[-1]
            self.reference_history.append(self.current_reference_fret_line)
        return SelectedTrackingResult(
            stable_grid=stable_grid,
            tracked_lines=self._tracked_lines_from_grid(stable_grid),
            tracking_mode=tracking_mode,
        )

    def _update_partial_lines(
        self,
        detected_lines: Sequence[DetectedLine],
    ) -> StableFretboardGrid | None:
        if self.current_nut_line is None or self.current_reference_fret_line is None:
            return None

        matched_nut = match_line_to_reference(
            self.current_nut_line,
            [line for line in detected_lines if line.kind == "nut"],
        )
        matched_reference = self._match_locked_reference_line(
            matched_nut=matched_nut or self.current_nut_line,
            fret_candidates=[line for line in detected_lines if line.kind == "fret"],
            relaxed=self.missed_frames > 0,
        )
        if matched_reference is not None:
            candidate_nut = matched_nut or self.current_nut_line
            if not self._is_pair_gap_consistent(
                candidate_nut,
                matched_reference,
                relaxed=self.missed_frames > 0,
            ):
                matched_reference = None

        if matched_nut is not None and matched_reference is not None:
            support_grid = self._build_reference_locked_grid(
                matched_nut,
                matched_reference,
                support_lines=detected_lines,
            )
            if support_grid is not None:
                self.current_nut_line = support_grid.nut_line
                if support_grid.ordered_fret_lines:
                    self.current_reference_fret_line = support_grid.ordered_fret_lines[-1]
                    self.reference_history.append(self.current_reference_fret_line)
                self.nut_history.append(self.current_nut_line)
                self.missed_frames += 1
                self.last_stable_grid = support_grid
                if self.missed_frames <= self.clear_frames:
                    return support_grid
                return None

        refined_pair = refine_reference_pair_with_support(
            matched_nut or self.current_nut_line,
            matched_reference or self.current_reference_fret_line,
            support_lines=detected_lines,
            prior_grid=self.last_stable_grid,
        )
        if refined_pair is not None:
            self.current_nut_line, self.current_reference_fret_line = refined_pair
            self.nut_history.append(self.current_nut_line)
            self.reference_history.append(self.current_reference_fret_line)
        else:
            matched_any = False
            if matched_nut is not None:
                self.current_nut_line = matched_nut
                self.nut_history.append(matched_nut)
                matched_any = True
            if matched_reference is not None:
                self.current_reference_fret_line = matched_reference
                self.reference_history.append(matched_reference)
                matched_any = True
            if not matched_any:
                return None

        self.missed_frames += 1
        stable_grid = self._build_smoothed_grid() or self.last_stable_grid
        if stable_grid is not None:
            self.last_stable_grid = stable_grid
        if self.missed_frames <= self.clear_frames:
            return stable_grid
        return None

    def _handle_hold_or_loss(self) -> SelectedTrackingResult:
        self.missed_frames += 1
        if self.last_stable_grid is not None and self.missed_frames <= self.clear_frames:
            return SelectedTrackingResult(
                stable_grid=self.last_stable_grid,
                tracked_lines=self._tracked_lines_from_grid(self.last_stable_grid),
                tracking_mode="selected_lines_hold",
            )
        self.clear()
        return SelectedTrackingResult(stable_grid=None, tracked_lines=(), tracking_mode="selected_lines_lost")


class FretboardTracker:
    def __init__(self, max_history: int = 5) -> None:
        self.max_history = max_history
        self.history: deque[StableFretboardGrid] = deque(maxlen=max_history)
        self.missed_frames = 0

    @property
    def last_grid(self) -> StableFretboardGrid | None:
        if not self.history:
            return None
        return self.history[-1]

    def update(self, grid: StableFretboardGrid | None) -> StableFretboardGrid | None:
        if grid is None:
            self.missed_frames += 1
            if self.missed_frames >= 2:
                self.history.clear()
            return None

        self.missed_frames = 0
        self.history.append(grid)

        smoothed_nut = median_line([entry.nut_line for entry in self.history], kind="nut")
        current_count = len(grid.ordered_fret_lines)
        smoothed_frets = []
        for fret_index in range(current_count):
            samples = [
                entry.ordered_fret_lines[fret_index]
                for entry in self.history
                if len(entry.ordered_fret_lines) > fret_index
            ]
            smoothed_frets.append(median_line(samples, kind="fret"))

        if not smoothed_frets:
            return build_grid_from_lines(smoothed_nut, [])
        return build_grid_from_indexed_fret_lines(
            smoothed_nut,
            tuple((fret_index + 1, fret_line) for fret_index, fret_line in enumerate(smoothed_frets)),
            support_lines=(smoothed_nut, *smoothed_frets),
            prior_grid=self.last_grid,
        )


def build_fretboard_grid_with_anchored_nut(
    nut_line: DetectedLine,
    detected_lines: Sequence[DetectedLine],
    prior_grid: StableFretboardGrid | None = None,
) -> StableFretboardGrid | None:
    nut_line = orient_line_for_screen(nut_line)
    nut_direction = normalize(line_vector(nut_line))
    if not np.any(nut_direction):
        return None

    fret_lines = [
        orient_line_like_reference(line, nut_direction)
        for line in detected_lines
        if line.kind == "fret"
    ]
    if not fret_lines:
        if prior_grid is not None and prior_grid.ordered_fret_lines:
            return prior_grid
        return build_grid_from_lines(nut_line, [])

    neck_direction = np.asarray([-nut_direction[1], nut_direction[0]], dtype=np.float32)
    nut_midpoint = line_midpoint(nut_line)
    if prior_grid is not None and prior_grid.ordered_fret_lines:
        if float(np.dot(line_midpoint(prior_grid.ordered_fret_lines[-1]) - nut_midpoint, neck_direction)) < 0:
            neck_direction *= -1.0
    else:
        projected_offsets = [
            float(np.dot(line_midpoint(line) - nut_midpoint, neck_direction))
            for line in fret_lines
        ]
        if projected_offsets and float(np.mean(projected_offsets)) < 0:
            neck_direction *= -1.0

    minimum_distance = max(4.0, line_length(nut_line) * 0.02)
    distance_candidates = []
    for fret_line in fret_lines:
        distance = float(np.dot(line_midpoint(fret_line) - nut_midpoint, neck_direction))
        if distance > minimum_distance:
            distance_candidates.append((distance, fret_line))

    if not distance_candidates:
        if prior_grid is not None and prior_grid.ordered_fret_lines:
            return prior_grid
        return build_grid_from_lines(nut_line, [])

    distance_candidates.sort(key=lambda item: item[0])
    dedupe_tolerance = max(6.0, line_length(nut_line) * 0.03)
    merged_candidates = merge_close_candidates(distance_candidates, tolerance=dedupe_tolerance)
    if prior_grid is not None and prior_grid.ordered_fret_lines:
        indexed_fret_lines = infer_indexed_fret_lines_from_reference_grid(
            prior_grid,
            tuple(line for _, line in merged_candidates),
            current_nut_line=nut_line,
        )
        if indexed_fret_lines:
            support_lines = (nut_line, *(line for _, line in indexed_fret_lines))
            reference_fret_index, reference_fret_line = indexed_fret_lines[-1]
            refined_lines = refine_lines_with_fitted_edges(
                lines_to_refine=support_lines,
                nut_line=nut_line,
                reference_fret_line=reference_fret_line,
                support_lines=(nut_line, *(line for _, line in merged_candidates)),
                prior_grid=prior_grid,
            )
            if refined_lines is not None and len(refined_lines) == len(support_lines):
                nut_line = refined_lines[0]
                indexed_fret_lines = tuple(
                    (fret_index, refined_lines[line_index + 1])
                    for line_index, (fret_index, _) in enumerate(indexed_fret_lines)
                )

            return build_grid_from_indexed_fret_lines(
                nut_line,
                indexed_fret_lines,
                max_fret_index=max(
                    len(prior_grid.ordered_fret_lines),
                    indexed_fret_lines[-1][0],
                    len(merged_candidates),
                ),
                support_lines=(nut_line, *(line for _, line in merged_candidates)),
                prior_grid=prior_grid,
            )

    ordered_frets = [line for _, line in select_consecutive_prefix(merged_candidates)]
    if not ordered_frets:
        if prior_grid is not None and prior_grid.ordered_fret_lines:
            return prior_grid
        return build_grid_from_lines(nut_line, [])

    support_lines = (nut_line, *ordered_frets)
    refined_lines = refine_lines_with_fitted_edges(
        lines_to_refine=support_lines,
        nut_line=nut_line,
        reference_fret_line=ordered_frets[-1],
        support_lines=support_lines,
        prior_grid=prior_grid,
    )
    if refined_lines is not None and len(refined_lines) == len(support_lines):
        nut_line = refined_lines[0]
        ordered_frets = list(refined_lines[1:])

    return build_grid_from_indexed_fret_lines(
        nut_line,
        tuple((fret_index + 1, fret_line) for fret_index, fret_line in enumerate(ordered_frets)),
        max_fret_index=len(ordered_frets),
        support_lines=(nut_line, *ordered_frets),
        prior_grid=prior_grid,
    )


def infer_selected_reference_fret_index(
    nut_line: DetectedLine,
    selected_reference_line: DetectedLine,
    detected_lines: Sequence[DetectedLine],
) -> int:
    grid = build_fretboard_grid_with_anchored_nut(nut_line, detected_lines)
    if grid is None or not grid.ordered_fret_lines:
        return 1

    selected_reference = orient_line_like_reference(
        orient_line_for_screen(selected_reference_line),
        normalize(line_vector(orient_line_for_screen(nut_line))),
    )
    best_index = 1
    best_score = float("inf")
    for fret_index, fret_line in enumerate(grid.ordered_fret_lines, start=1):
        score = line_match_score(selected_reference, fret_line)
        if score < best_score:
            best_score = score
            best_index = fret_index
    return best_index


def point_array(point: tuple[float, float]) -> np.ndarray:
    return np.asarray(point, dtype=np.float32)


def point_tuple(point: np.ndarray) -> tuple[float, float]:
    return (float(point[0]), float(point[1]))


def line_vector(line: DetectedLine) -> np.ndarray:
    return point_array(line.p1) - point_array(line.p0)


def line_midpoint(line: DetectedLine) -> np.ndarray:
    return (point_array(line.p0) + point_array(line.p1)) * 0.5


def line_length(line: DetectedLine) -> float:
    return float(np.linalg.norm(line_vector(line)))


def line_match_score(reference_line: DetectedLine, candidate_line: DetectedLine) -> float:
    reference_direction = normalize(line_vector(reference_line))
    candidate_line = orient_line_like_reference(orient_line_for_screen(candidate_line), reference_direction)
    midpoint_score = float(np.linalg.norm(line_midpoint(reference_line) - line_midpoint(candidate_line)))
    endpoint_score = (
        float(np.linalg.norm(point_array(reference_line.p0) - point_array(candidate_line.p0)))
        + float(np.linalg.norm(point_array(reference_line.p1) - point_array(candidate_line.p1)))
    ) * 0.5
    length_score = abs(line_length(reference_line) - line_length(candidate_line)) * 0.35
    return midpoint_score + endpoint_score + length_score


def neck_direction_from_pair(
    nut_line: DetectedLine,
    reference_fret_line: DetectedLine,
) -> np.ndarray:
    nut_direction = normalize(line_vector(nut_line))
    neck_direction = np.asarray([-nut_direction[1], nut_direction[0]], dtype=np.float32)
    nut_midpoint = line_midpoint(nut_line)
    reference_midpoint = line_midpoint(reference_fret_line)
    if float(np.dot(reference_midpoint - nut_midpoint, neck_direction)) < 0:
        neck_direction *= -1.0
    return neck_direction


def projected_pair_gap(
    nut_line: DetectedLine,
    reference_fret_line: DetectedLine,
) -> float:
    neck_direction = neck_direction_from_pair(nut_line, reference_fret_line)
    return float(np.dot(line_midpoint(reference_fret_line) - line_midpoint(nut_line), neck_direction))


def line_parallel_penalty(first_line: DetectedLine, second_line: DetectedLine) -> float:
    first_direction = normalize(line_vector(first_line))
    second_direction = normalize(line_vector(second_line))
    if not np.any(first_direction) or not np.any(second_direction):
        return 9999.0
    similarity = abs(float(np.dot(first_direction, second_direction)))
    return (1.0 - similarity) * 140.0


def line_pair_match_score(
    reference_nut_line: DetectedLine,
    reference_fret_line: DetectedLine,
    candidate_nut_line: DetectedLine,
    candidate_fret_line: DetectedLine,
) -> float:
    reference_gap = projected_pair_gap(reference_nut_line, reference_fret_line)
    candidate_gap = projected_pair_gap(candidate_nut_line, candidate_fret_line)
    gap_penalty = abs(candidate_gap - reference_gap) * 1.1
    width_delta_penalty = abs(
        (line_length(candidate_fret_line) - line_length(candidate_nut_line))
        - (line_length(reference_fret_line) - line_length(reference_nut_line))
    ) * 0.3
    return (
        line_match_score(reference_nut_line, candidate_nut_line)
        + line_match_score(reference_fret_line, candidate_fret_line)
        + gap_penalty
        + width_delta_penalty
        + line_parallel_penalty(candidate_nut_line, candidate_fret_line)
    )


def best_line_match(
    reference_line: DetectedLine,
    candidates: Sequence[DetectedLine],
) -> tuple[DetectedLine | None, float]:
    if not candidates:
        return None, float("inf")

    reference_direction = normalize(line_vector(reference_line))
    best_score = float("inf")
    best_line: DetectedLine | None = None
    for candidate in candidates:
        normalized_candidate = orient_line_like_reference(orient_line_for_screen(candidate), reference_direction)
        score = line_match_score(reference_line, normalized_candidate)
        if score < best_score:
            best_score = score
            best_line = normalized_candidate

    if best_line is None:
        return None, float("inf")

    score_limit = max(90.0, line_length(reference_line) * 1.2)
    if best_score > score_limit:
        return None, best_score
    return best_line, best_score


def match_line_to_reference(
    reference_line: DetectedLine,
    candidates: Sequence[DetectedLine],
) -> DetectedLine | None:
    best_line, _ = best_line_match(reference_line, candidates)
    return best_line


def match_line_pair_to_reference(
    reference_nut_line: DetectedLine,
    reference_fret_line: DetectedLine,
    nut_candidates: Sequence[DetectedLine],
    fret_candidates: Sequence[DetectedLine],
) -> tuple[DetectedLine, DetectedLine] | None:
    if not nut_candidates or not fret_candidates:
        return None

    reference_direction = normalize(line_vector(reference_nut_line))
    reference_gap = projected_pair_gap(reference_nut_line, reference_fret_line)
    minimum_gap = max(8.0, reference_gap * 0.4)
    best_pair: tuple[DetectedLine, DetectedLine] | None = None
    best_score = float("inf")

    for nut_candidate in nut_candidates:
        normalized_nut = orient_line_like_reference(orient_line_for_screen(nut_candidate), reference_direction)
        for fret_candidate in fret_candidates:
            normalized_fret = orient_line_like_reference(
                orient_line_for_screen(fret_candidate),
                reference_direction,
            )
            candidate_gap = projected_pair_gap(normalized_nut, normalized_fret)
            if candidate_gap <= minimum_gap:
                continue
            score = line_pair_match_score(
                reference_nut_line,
                reference_fret_line,
                normalized_nut,
                normalized_fret,
            )
            if score < best_score:
                best_score = score
                best_pair = (normalized_nut, normalized_fret)

    if best_pair is None:
        return None

    score_limit = max(
        220.0,
        reference_gap * 1.6 + max(line_length(reference_nut_line), line_length(reference_fret_line)) * 1.1,
    )
    if best_score > score_limit:
        return None
    return best_pair


def weighted_principal_line(
    points: Sequence[tuple[float, float]],
    weights: Sequence[float],
) -> tuple[np.ndarray, np.ndarray] | None:
    if len(points) < 2 or len(points) != len(weights):
        return None

    point_values = np.asarray(points, dtype=np.float32)
    weight_values = np.asarray(weights, dtype=np.float32)
    weight_sum = float(np.sum(weight_values))
    if weight_sum <= 1e-6:
        return None

    centroid = np.sum(point_values * weight_values[:, None], axis=0) / weight_sum
    centered_points = point_values - centroid
    covariance = (centered_points * weight_values[:, None]).T @ centered_points / weight_sum
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    direction = eigenvectors[:, int(np.argmax(eigenvalues))]
    direction = normalize(direction.astype(np.float32))
    if not np.any(direction):
        return None
    return centroid.astype(np.float32), direction


def intersect_infinite_lines(
    first_point: np.ndarray,
    first_direction: np.ndarray,
    second_point: np.ndarray,
    second_direction: np.ndarray,
) -> tuple[float, float] | None:
    matrix = np.asarray(
        [
            [first_direction[0], -second_direction[0]],
            [first_direction[1], -second_direction[1]],
        ],
        dtype=np.float32,
    )
    determinant = float(np.linalg.det(matrix))
    if abs(determinant) <= 1e-6:
        return None

    offset = second_point - first_point
    parameters = np.linalg.solve(matrix, offset)
    intersection = first_point + first_direction * float(parameters[0])
    return point_tuple(intersection)


def collect_edge_support_lines(
    nut_line: DetectedLine,
    reference_fret_line: DetectedLine,
    detected_lines: Sequence[DetectedLine],
) -> tuple[DetectedLine, ...]:
    reference_direction = normalize(line_vector(nut_line))
    if not np.any(reference_direction):
        return ()

    neck_direction = neck_direction_from_pair(nut_line, reference_fret_line)
    nut_midpoint = line_midpoint(nut_line)
    reference_gap = max(projected_pair_gap(nut_line, reference_fret_line), 1.0)
    nut_distance_limit = max(24.0, line_length(nut_line) * 0.25)
    fret_distance_limit = max(
        reference_gap * 1.45,
        reference_gap + max(line_length(nut_line), line_length(reference_fret_line)) * 0.7,
    )

    distance_candidates: list[tuple[float, DetectedLine]] = []
    for line in detected_lines:
        if line.kind not in {"nut", "fret"}:
            continue

        normalized_line = orient_line_like_reference(
            orient_line_for_screen(line),
            reference_direction,
        )
        direction = normalize(line_vector(normalized_line))
        if not np.any(direction):
            continue

        if abs(float(np.dot(direction, reference_direction))) < EDGE_SUPPORT_ANGLE_MIN:
            continue

        distance = float(np.dot(line_midpoint(normalized_line) - nut_midpoint, neck_direction))
        if normalized_line.kind == "nut":
            if abs(distance) > nut_distance_limit:
                continue
            ordering_distance = 0.0
        else:
            if distance < -nut_distance_limit or distance > fret_distance_limit:
                continue
            ordering_distance = max(distance, 0.0)

        distance_candidates.append((ordering_distance, normalized_line))

    if not distance_candidates:
        return ()

    distance_candidates.sort(key=lambda item: item[0])
    dedupe_tolerance = max(6.0, line_length(nut_line) * 0.03)
    merged_candidates = merge_close_candidates(distance_candidates, tolerance=dedupe_tolerance)
    return tuple(line for _, line in merged_candidates)


def fit_fretboard_edges(
    nut_line: DetectedLine,
    reference_fret_line: DetectedLine,
    support_lines: Sequence[DetectedLine],
    prior_grid: StableFretboardGrid | None = None,
) -> FretboardEdgeFit | None:
    normalized_nut = orient_line_for_screen(nut_line)
    reference_direction = normalize(line_vector(normalized_nut))
    if not np.any(reference_direction):
        return None

    normalized_reference = orient_line_like_reference(
        orient_line_for_screen(reference_fret_line),
        reference_direction,
    )
    normalized_support = collect_edge_support_lines(
        normalized_nut,
        normalized_reference,
        support_lines,
    )
    if len(normalized_support) < EDGE_SUPPORT_MIN_LINES:
        return None

    support_lengths = [line_length(line) for line in normalized_support]
    prior_length_samples: list[float] = []
    if prior_grid is not None:
        prior_length_samples.append(line_length(prior_grid.nut_line))
        prior_length_samples.extend(line_length(line) for line in prior_grid.ordered_fret_lines)
    length_reference = float(
        np.percentile(
            np.asarray(prior_length_samples or support_lengths, dtype=np.float32),
            75,
        )
    )

    p0_points = [line.p0 for line in normalized_support]
    p1_points = [line.p1 for line in normalized_support]
    p0_weights = []
    p1_weights = []
    for line, current_length in zip(normalized_support, support_lengths):
        length_ratio = min(1.0, current_length / max(length_reference, 1.0))
        weight = max(EDGE_SHORT_LINE_WEIGHT_FLOOR, line.confidence * (length_ratio ** 2))
        p0_weights.append(weight)
        p1_weights.append(weight)

    if prior_grid is not None:
        prior_lines = [prior_grid.nut_line]
        if prior_grid.ordered_fret_lines:
            prior_lines.append(prior_grid.ordered_fret_lines[0])
            prior_lines.append(prior_grid.ordered_fret_lines[len(prior_grid.ordered_fret_lines) // 2])
            prior_lines.append(prior_grid.ordered_fret_lines[-1])
        for prior_line in prior_lines:
            normalized_prior = orient_line_like_reference(
                orient_line_for_screen(prior_line),
                reference_direction,
            )
            p0_points.append(normalized_prior.p0)
            p1_points.append(normalized_prior.p1)
            p0_weights.append(EDGE_SUPPORT_PRIOR_WEIGHT)
            p1_weights.append(EDGE_SUPPORT_PRIOR_WEIGHT)

    edge_p0 = weighted_principal_line(p0_points, p0_weights)
    edge_p1 = weighted_principal_line(p1_points, p1_weights)
    if edge_p0 is None or edge_p1 is None:
        return None

    support_length_hint = float(np.percentile([line_length(line) for line in normalized_support], 75))
    return FretboardEdgeFit(
        reference_direction=reference_direction,
        edge_p0=edge_p0,
        edge_p1=edge_p1,
        support_length_hint=support_length_hint,
    )


def extend_line_to_fitted_edges(
    line: DetectedLine,
    reference_direction: np.ndarray,
    edge_p0: tuple[np.ndarray, np.ndarray],
    edge_p1: tuple[np.ndarray, np.ndarray],
    support_length_hint: float,
) -> DetectedLine | None:
    normalized_line = orient_line_like_reference(orient_line_for_screen(line), reference_direction)
    line_direction = normalize(line_vector(normalized_line))
    if not np.any(line_direction):
        return None

    midpoint = line_midpoint(normalized_line)
    refined_p0 = intersect_infinite_lines(midpoint, line_direction, edge_p0[0], edge_p0[1])
    refined_p1 = intersect_infinite_lines(midpoint, line_direction, edge_p1[0], edge_p1[1])
    if refined_p0 is None or refined_p1 is None:
        return None

    refined_line = orient_line_like_reference(
        orient_line_for_screen(
            DetectedLine(
                kind=normalized_line.kind,
                p0=refined_p0,
                p1=refined_p1,
                confidence=normalized_line.confidence,
            )
        ),
        reference_direction,
    )
    refined_length = line_length(refined_line)
    min_length = max(18.0, support_length_hint * 0.55)
    max_length = max(min_length * 1.2, support_length_hint * 1.85)
    if refined_length < min_length or refined_length > max_length:
        return None
    return refined_line


def refine_lines_with_fitted_edges(
    lines_to_refine: Sequence[DetectedLine],
    nut_line: DetectedLine,
    reference_fret_line: DetectedLine,
    support_lines: Sequence[DetectedLine],
    prior_grid: StableFretboardGrid | None = None,
) -> tuple[DetectedLine, ...] | None:
    edge_fit = fit_fretboard_edges(
        nut_line=nut_line,
        reference_fret_line=reference_fret_line,
        support_lines=support_lines,
        prior_grid=prior_grid,
    )
    if edge_fit is None:
        return None

    refined_lines: list[DetectedLine] = []
    for line in lines_to_refine:
        refined_line = extend_line_to_fitted_edges(
            line=line,
            reference_direction=edge_fit.reference_direction,
            edge_p0=edge_fit.edge_p0,
            edge_p1=edge_fit.edge_p1,
            support_length_hint=edge_fit.support_length_hint,
        )
        if refined_line is None:
            refined_line = orient_line_like_reference(
                orient_line_for_screen(line),
                edge_fit.reference_direction,
            )
        refined_lines.append(refined_line)
    return tuple(refined_lines)


def refine_reference_pair_with_support(
    nut_line: DetectedLine,
    reference_fret_line: DetectedLine,
    support_lines: Sequence[DetectedLine],
    prior_grid: StableFretboardGrid | None = None,
) -> tuple[DetectedLine, DetectedLine] | None:
    refined_lines = refine_lines_with_fitted_edges(
        lines_to_refine=(nut_line, reference_fret_line),
        nut_line=nut_line,
        reference_fret_line=reference_fret_line,
        support_lines=support_lines,
        prior_grid=prior_grid,
    )
    if refined_lines is None or len(refined_lines) != 2:
        return None
    refined_nut, refined_reference = refined_lines
    refined_reference = orient_line_like_reference(
        refined_reference,
        normalize(line_vector(refined_nut)),
    )
    return refined_nut, refined_reference


def normalize(vector: np.ndarray) -> np.ndarray:
    length = float(np.linalg.norm(vector))
    if length <= 1e-6:
        return np.zeros(2, dtype=np.float32)
    return vector / length


def interpolate_points(start: np.ndarray, end: np.ndarray, ratio: float) -> np.ndarray:
    return start + (end - start) * ratio


def orient_line_for_screen(line: DetectedLine) -> DetectedLine:
    if (line.p1[1], line.p1[0]) < (line.p0[1], line.p0[0]):
        return DetectedLine(kind=line.kind, p0=line.p1, p1=line.p0, confidence=line.confidence,
                            endpoints_observed=line.endpoints_observed)
    return line


def orient_line_like_reference(line: DetectedLine, reference_direction: np.ndarray) -> DetectedLine:
    vector = line_vector(line)
    if float(np.dot(vector, reference_direction)) < 0:
        return DetectedLine(kind=line.kind, p0=line.p1, p1=line.p0, confidence=line.confidence,
                            endpoints_observed=line.endpoints_observed)
    return line


def median_line(lines: Sequence[DetectedLine], kind: str) -> DetectedLine:
    p0_values = np.asarray([line.p0 for line in lines], dtype=np.float32)
    p1_values = np.asarray([line.p1 for line in lines], dtype=np.float32)
    confidence_values = np.asarray([line.confidence for line in lines], dtype=np.float32)
    return DetectedLine(
        kind=kind,
        p0=point_tuple(np.median(p0_values, axis=0)),
        p1=point_tuple(np.median(p1_values, axis=0)),
        confidence=float(np.median(confidence_values)),
        endpoints_observed=all(line.endpoints_observed for line in lines),
    )


def compute_string_crossings(line: DetectedLine) -> tuple[tuple[float, float], ...]:
    start = point_array(line.p0)
    end = point_array(line.p1)
    trimmed_start = interpolate_points(start, end, PLAYABLE_EDGE_MARGIN_RATIO)
    trimmed_end = interpolate_points(start, end, 1.0 - PLAYABLE_EDGE_MARGIN_RATIO)
    return tuple(
        point_tuple(interpolate_points(trimmed_start, trimmed_end, ratio))
        for ratio in np.linspace(0.0, 1.0, STRING_COUNT, dtype=np.float32)
    )


def scale_length_ratio(fret_index: int) -> float:
    if fret_index <= 0:
        return 0.0
    return 1.0 - float(2 ** (-fret_index / 12.0))


def project_homography(
    homography: np.ndarray,
    x_value: float,
    y_value: float,
) -> tuple[float, float]:
    homogeneous_point = np.asarray([x_value, y_value, 1.0], dtype=np.float32)
    projected = homography @ homogeneous_point
    if abs(float(projected[2])) <= 1e-6:
        return (0.0, 0.0)
    projected /= projected[2]
    return (float(projected[0]), float(projected[1]))


def transform_line(line: DetectedLine, homography: np.ndarray) -> DetectedLine:
    return DetectedLine(
        kind=line.kind,
        p0=project_homography(homography, *line.p0),
        p1=project_homography(homography, *line.p1),
        confidence=line.confidence,
        endpoints_observed=line.endpoints_observed,
    )


def transform_grid(grid: StableFretboardGrid, homography: np.ndarray) -> StableFretboardGrid:
    return StableFretboardGrid(
        nut_line=transform_line(grid.nut_line, homography),
        ordered_fret_lines=tuple(transform_line(line, homography) for line in grid.ordered_fret_lines),
        string_crossings=tuple(
            tuple(project_homography(homography, *point) for point in row)
            for row in grid.string_crossings
        ),
    )


def build_grid_from_lines(
    nut_line: DetectedLine,
    ordered_fret_lines: Sequence[DetectedLine],
) -> StableFretboardGrid:
    lines = [nut_line, *ordered_fret_lines]
    string_crossings = tuple(compute_string_crossings(line) for line in lines)
    return StableFretboardGrid(
        nut_line=nut_line,
        ordered_fret_lines=tuple(ordered_fret_lines),
        string_crossings=string_crossings,
    )


def build_grid_from_indexed_fret_lines(
    nut_line: DetectedLine,
    indexed_fret_lines: Sequence[tuple[int, DetectedLine]],
    max_fret_index: int | None = None,
    support_lines: Sequence[DetectedLine] | None = None,
    prior_grid: StableFretboardGrid | None = None,
) -> StableFretboardGrid | None:
    nut_line = orient_line_for_screen(nut_line)
    nut_direction = normalize(line_vector(nut_line))
    if not np.any(nut_direction):
        return None

    canonical_points: list[tuple[float, float]] = [(0.0, 0.0), (1.0, 0.0)]
    image_points: list[tuple[float, float]] = [nut_line.p0, nut_line.p1]
    indexed_fret_line_map: dict[int, DetectedLine] = {}

    sorted_indexed_fret_lines = sorted(
        ((fret_index, fret_line) for fret_index, fret_line in indexed_fret_lines if fret_index >= 1),
        key=lambda item: item[0],
    )

    for fret_index, fret_line in sorted_indexed_fret_lines:
        if fret_index < 1:
            continue
        y_value = scale_length_ratio(fret_index)
        if y_value <= 1e-6:
            continue
        normalized_fret_line = orient_line_like_reference(
            orient_line_for_screen(fret_line),
            nut_direction,
        )
        indexed_fret_line_map[fret_index] = normalized_fret_line
        canonical_points.extend(((0.0, y_value), (1.0, y_value)))
        image_points.extend((normalized_fret_line.p0, normalized_fret_line.p1))

    if not indexed_fret_line_map:
        return build_grid_from_lines(nut_line, [])

    homography = compute_homography(
        np.asarray(canonical_points, dtype=np.float32),
        np.asarray(image_points, dtype=np.float32),
    )
    confidence_values = [nut_line.confidence, *(line.confidence for line in indexed_fret_line_map.values())]
    grid_confidence = float(np.median(np.asarray(confidence_values, dtype=np.float32)))

    if max_fret_index is None:
        max_fret_index = sorted_indexed_fret_lines[-1][0]
    if max_fret_index < 1:
        return build_grid_from_lines(nut_line, [])

    final_fret_lines: list[DetectedLine] = []
    for fret_index in range(1, max_fret_index + 1):
        detected_fret_line = indexed_fret_line_map.get(fret_index)
        if detected_fret_line is not None:
            final_fret_lines.append(detected_fret_line)
            continue

        final_fret_lines.append(
            DetectedLine(
                kind="fret",
                p0=project_homography(homography, 0.0, scale_length_ratio(fret_index)),
                p1=project_homography(homography, 1.0, scale_length_ratio(fret_index)),
                confidence=grid_confidence,
            )
        )

    refinement_support_lines = tuple(support_lines or ())
    if refinement_support_lines and final_fret_lines:
        reference_fret_line = None
        if sorted_indexed_fret_lines:
            highest_detected_index = sorted_indexed_fret_lines[-1][0]
            if 1 <= highest_detected_index <= len(final_fret_lines):
                reference_fret_line = final_fret_lines[highest_detected_index - 1]
        if reference_fret_line is None:
            reference_fret_line = final_fret_lines[-1]

        normalized_support_lines = list(refinement_support_lines)
        if not any(line.kind == "nut" for line in normalized_support_lines):
            normalized_support_lines.insert(
                0,
                DetectedLine(kind="nut", p0=nut_line.p0, p1=nut_line.p1, confidence=nut_line.confidence),
            )

        refined_lines = refine_lines_with_fitted_edges(
            lines_to_refine=(nut_line, *final_fret_lines),
            nut_line=nut_line,
            reference_fret_line=reference_fret_line,
            support_lines=tuple(normalized_support_lines),
            prior_grid=prior_grid,
        )
        if refined_lines is not None and len(refined_lines) == len(final_fret_lines) + 1:
            nut_line = refined_lines[0]
            final_fret_lines = list(refined_lines[1:])

    string_crossings = tuple(
        compute_string_crossings(line)
        for line in (
            DetectedLine(kind="nut", p0=nut_line.p0, p1=nut_line.p1, confidence=nut_line.confidence),
            *final_fret_lines,
        )
    )

    return StableFretboardGrid(
        nut_line=DetectedLine(kind="nut", p0=nut_line.p0, p1=nut_line.p1, confidence=nut_line.confidence),
        ordered_fret_lines=tuple(final_fret_lines),
        string_crossings=string_crossings,
    )


def build_grid_from_reference_lines(
    nut_line: DetectedLine,
    reference_fret_line: DetectedLine,
    reference_fret_index: int,
) -> StableFretboardGrid | None:
    if reference_fret_index < 1:
        return None
    return build_grid_from_indexed_fret_lines(
        nut_line=nut_line,
        indexed_fret_lines=((reference_fret_index, reference_fret_line),),
        max_fret_index=reference_fret_index,
        support_lines=(nut_line, reference_fret_line),
    )


def infer_indexed_fret_lines_from_reference_grid(
    reference_grid: StableFretboardGrid,
    fret_lines: Sequence[DetectedLine],
    current_nut_line: DetectedLine | None = None,
) -> tuple[tuple[int, DetectedLine], ...]:
    if not reference_grid.ordered_fret_lines or not fret_lines:
        return ()

    reference_nut_line = reference_grid.nut_line
    reference_fret_lines = reference_grid.ordered_fret_lines
    if current_nut_line is not None:
        normalized_nut = orient_line_like_reference(
            orient_line_for_screen(current_nut_line),
            normalize(line_vector(reference_grid.nut_line)),
        )
        midpoint_delta = line_midpoint(normalized_nut) - line_midpoint(reference_grid.nut_line)
        reference_nut_line = DetectedLine(
            kind="nut",
            p0=point_tuple(point_array(reference_grid.nut_line.p0) + midpoint_delta),
            p1=point_tuple(point_array(reference_grid.nut_line.p1) + midpoint_delta),
            confidence=normalized_nut.confidence,
        )
        reference_fret_lines = tuple(
            DetectedLine(
                kind=fret_line.kind,
                p0=point_tuple(point_array(fret_line.p0) + midpoint_delta),
                p1=point_tuple(point_array(fret_line.p1) + midpoint_delta),
                confidence=fret_line.confidence,
            )
            for fret_line in reference_grid.ordered_fret_lines
        )

    nut_midpoint = line_midpoint(reference_nut_line)
    reference_fret_line = reference_fret_lines[-1]
    neck_direction = neck_direction_from_pair(reference_nut_line, reference_fret_line)

    target_distance_entries = [
        (
            fret_index,
            float(np.dot(line_midpoint(projected_line) - nut_midpoint, neck_direction)),
            projected_line,
        )
        for fret_index, projected_line in enumerate(reference_fret_lines, start=1)
    ]
    if not target_distance_entries:
        return ()

    target_distances: list[tuple[int, float, DetectedLine, float]] = []
    for target_index, (fret_index, target_distance, projected_line) in enumerate(target_distance_entries):
        neighbor_gaps: list[float] = []
        if target_index > 0:
            neighbor_gaps.append(abs(target_distance - target_distance_entries[target_index - 1][1]))
        if target_index + 1 < len(target_distance_entries):
            neighbor_gaps.append(abs(target_distance_entries[target_index + 1][1] - target_distance))
        if neighbor_gaps:
            local_tolerance = min(
                INDEXED_FRET_DISTANCE_TOLERANCE_MAX_PX,
                max(
                    INDEXED_FRET_DISTANCE_TOLERANCE_MIN_PX,
                    min(neighbor_gaps) * INDEXED_FRET_DISTANCE_TOLERANCE_RATIO,
                ),
            )
        else:
            local_tolerance = INDEXED_FRET_DISTANCE_TOLERANCE_MIN_PX
        target_distances.append((fret_index, target_distance, projected_line, local_tolerance))

    best_by_index: dict[int, tuple[float, DetectedLine]] = {}
    for fret_line in fret_lines:
        distance = float(np.dot(line_midpoint(fret_line) - nut_midpoint, neck_direction))
        if distance <= 0.0:
            continue

        best_index, target_distance, projected_line, distance_tolerance = min(
            target_distances,
            key=lambda item: abs(item[1] - distance),
        )
        distance_delta = abs(target_distance - distance)
        if distance_delta > distance_tolerance:
            continue

        combined_score = distance_delta + line_match_score(projected_line, fret_line) * 0.2
        existing = best_by_index.get(best_index)
        if (
            existing is None
            or combined_score < existing[0]
            or (
                np.isclose(combined_score, existing[0])
                and fret_line.confidence > existing[1].confidence
            )
        ):
            best_by_index[best_index] = (combined_score, fret_line)

    return tuple((fret_index, line) for fret_index, (_, line) in sorted(best_by_index.items()))


def compute_homography(
    canonical_points: np.ndarray,
    image_points: np.ndarray,
) -> np.ndarray:
    matrix_rows: list[list[float]] = []
    for (x_value, y_value), (u_value, v_value) in zip(canonical_points, image_points):
        matrix_rows.append(
            [x_value, y_value, 1.0, 0.0, 0.0, 0.0, -u_value * x_value, -u_value * y_value, -u_value]
        )
        matrix_rows.append(
            [0.0, 0.0, 0.0, x_value, y_value, 1.0, -v_value * x_value, -v_value * y_value, -v_value]
        )

    _, _, vh = np.linalg.svd(np.asarray(matrix_rows, dtype=np.float32))
    homography = vh[-1].reshape(3, 3)
    return homography / homography[2, 2]


def merge_close_candidates(
    candidates: Sequence[tuple[float, DetectedLine]],
    tolerance: float,
) -> list[tuple[float, DetectedLine]]:
    if not candidates:
        return []

    clusters: list[list[tuple[float, DetectedLine]]] = [[candidates[0]]]
    for candidate in candidates[1:]:
        if candidate[0] - clusters[-1][-1][0] <= tolerance:
            clusters[-1].append(candidate)
        else:
            clusters.append([candidate])

    merged: list[tuple[float, DetectedLine]] = []
    for cluster in clusters:
        best_distance, best_line = max(cluster, key=lambda item: item[1].confidence)
        merged.append((best_distance, best_line))
    return merged


def select_consecutive_prefix(
    candidates: Sequence[tuple[float, DetectedLine]],
) -> list[tuple[float, DetectedLine]]:
    if not candidates:
        return []

    selected = [candidates[0]]
    if len(candidates) == 1:
        return selected

    previous_distance = candidates[0][0]
    previous_gap = previous_distance

    for distance, line in candidates[1:]:
        gap = distance - previous_distance
        if gap <= 1e-3:
            continue

        gap_ratio = gap / max(previous_gap, 1e-3)
        minimum_ratio = FIRST_GAP_RATIO_MIN if len(selected) == 1 else GAP_RATIO_MIN
        if gap_ratio < minimum_ratio or gap_ratio > GAP_RATIO_MAX:
            break

        selected.append((distance, line))
        previous_distance = distance
        previous_gap = gap

    return selected


def build_fretboard_grid(
    detected_lines: Sequence[DetectedLine],
    prior_grid: StableFretboardGrid | None = None,
) -> StableFretboardGrid | None:
    nut_candidates = [line for line in detected_lines if line.kind == "nut"]
    if not nut_candidates:
        return None

    nut_line = orient_line_for_screen(max(nut_candidates, key=lambda line: line.confidence))
    return build_fretboard_grid_with_anchored_nut(
        nut_line,
        detected_lines,
        prior_grid=prior_grid,
    )


def build_note_markers(
    grid: StableFretboardGrid | None,
    selected_note: str | None,
    reverse_strings: bool = False,
) -> tuple[NoteMarker, ...]:
    if grid is None or not selected_note or selected_note == "없음":
        return ()

    pitch_class = NOTE_NAME_TO_PITCH_CLASS.get(selected_note)
    if pitch_class is None:
        return ()

    tuning = STANDARD_TUNING_MIDI[::-1] if reverse_strings else STANDARD_TUNING_MIDI
    markers: list[NoteMarker] = []

    nut_points = grid.string_crossings[0]
    for screen_index, open_midi in enumerate(tuning):
        if open_midi % 12 == pitch_class:
            markers.append(
                NoteMarker(
                    string_index=screen_index,
                    fret_index=0,
                    point_xy=nut_points[screen_index],
                    is_open=True,
                )
            )

    for fret_index in range(1, len(grid.string_crossings)):
        previous_points = grid.string_crossings[fret_index - 1]
        current_points = grid.string_crossings[fret_index]
        for screen_index, open_midi in enumerate(tuning):
            if (open_midi + fret_index) % 12 != pitch_class:
                continue
            point = interpolate_points(
                point_array(previous_points[screen_index]),
                point_array(current_points[screen_index]),
                FRETTED_NOTE_ANCHOR_RATIO,
            )
            markers.append(
                NoteMarker(
                    string_index=screen_index,
                    fret_index=fret_index,
                    point_xy=point_tuple(point),
                    is_open=False,
                )
            )

    return tuple(markers)


def suppress_fret_indices_from_grid(
    grid: StableFretboardGrid | None,
    suppressed_fret_indices: Iterable[int] | None,
) -> StableFretboardGrid | None:
    if grid is None:
        return None

    suppressed_indices = {
        int(fret_index)
        for fret_index in suppressed_fret_indices or ()
        if 0 <= int(fret_index) < len(grid.ordered_fret_lines)
    }
    if not suppressed_indices:
        return grid

    kept_fret_lines: list[DetectedLine] = []
    kept_crossing_rows: list[tuple[tuple[float, float], ...]] = [
        grid.string_crossings[0] if grid.string_crossings else compute_string_crossings(grid.nut_line)
    ]
    for fret_index, fret_line in enumerate(grid.ordered_fret_lines):
        if fret_index in suppressed_indices:
            continue

        kept_fret_lines.append(fret_line)
        crossing_row_index = fret_index + 1
        if crossing_row_index < len(grid.string_crossings):
            kept_crossing_rows.append(grid.string_crossings[crossing_row_index])
        else:
            kept_crossing_rows.append(compute_string_crossings(fret_line))

    return StableFretboardGrid(
        nut_line=grid.nut_line,
        ordered_fret_lines=tuple(kept_fret_lines),
        string_crossings=tuple(kept_crossing_rows),
    )


def build_center_test_note_markers(
    grid: StableFretboardGrid | None,
    selected_note: str | None,
) -> tuple[NoteMarker, ...]:
    if grid is None or not selected_note or selected_note == "없음":
        return ()

    fret_index = TEST_CENTER_NOTE_TO_FRET_INDEX.get(selected_note)
    if fret_index is None:
        return ()

    if fret_index == 0:
        point = point_tuple(line_midpoint(grid.nut_line))
        return (
            NoteMarker(
                string_index=STRING_COUNT // 2,
                fret_index=0,
                point_xy=point,
                is_open=True,
            ),
        )

    if fret_index > len(grid.ordered_fret_lines):
        return ()

    previous_line = grid.nut_line if fret_index == 1 else grid.ordered_fret_lines[fret_index - 2]
    current_line = grid.ordered_fret_lines[fret_index - 1]
    point = point_tuple(
        interpolate_points(
            line_midpoint(previous_line),
            line_midpoint(current_line),
            0.5,
        )
    )
    return (
        NoteMarker(
            string_index=STRING_COUNT // 2,
            fret_index=fret_index,
            point_xy=point,
            is_open=False,
        ),
    )


def count_detected_classes(lines: Iterable[DetectedLine]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in lines:
        counts[line.kind] = counts.get(line.kind, 0) + 1
    return counts
