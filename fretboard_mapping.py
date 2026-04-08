from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np


STRING_COUNT = 6
PLAYABLE_EDGE_MARGIN_RATIO = 0.12
FRETTED_NOTE_ANCHOR_RATIO = 0.85
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
SELECTED_LINE_ROI_EXPAND_RATIO = 1.65
SELECTED_LINE_ROI_PADDING = 36.0


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
    ) -> SelectedTrackingResult:
        if (
            not self.is_active
            or self.current_nut_line is None
            or self.current_reference_fret_line is None
            or self.reference_fret_index is None
        ):
            return SelectedTrackingResult(stable_grid=None, tracked_lines=(), tracking_mode="selected_lines_lost")

        primary_pair = match_line_pair_to_reference(
            self.current_nut_line,
            self.current_reference_fret_line,
            [line for line in primary_detected_lines if line.kind == "nut"],
            [line for line in primary_detected_lines if line.kind == "fret"],
        )
        if primary_pair is not None:
            return self._apply_pair_match(*primary_pair, tracking_mode="selected_lines_tracking")

        if fallback_detected_lines is not None:
            fallback_pair = match_line_pair_to_reference(
                self.current_nut_line,
                self.current_reference_fret_line,
                [line for line in fallback_detected_lines if line.kind == "nut"],
                [line for line in fallback_detected_lines if line.kind == "fret"],
            )
            if fallback_pair is not None:
                return self._apply_pair_match(*fallback_pair, tracking_mode="selected_lines_reacquired")

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
    ) -> SelectedTrackingResult:
        self.missed_frames = 0
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

    def _update_partial_lines(
        self,
        detected_lines: Sequence[DetectedLine],
    ) -> StableFretboardGrid | None:
        if self.current_nut_line is None or self.current_reference_fret_line is None:
            return None

        matched_any = False
        matched_nut = match_line_to_reference(
            self.current_nut_line,
            [line for line in detected_lines if line.kind == "nut"],
        )
        matched_reference = match_line_to_reference(
            self.current_reference_fret_line,
            [line for line in detected_lines if line.kind == "fret"],
        )

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

        return build_grid_from_lines(smoothed_nut, smoothed_frets)


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


def normalize(vector: np.ndarray) -> np.ndarray:
    length = float(np.linalg.norm(vector))
    if length <= 1e-6:
        return np.zeros(2, dtype=np.float32)
    return vector / length


def interpolate_points(start: np.ndarray, end: np.ndarray, ratio: float) -> np.ndarray:
    return start + (end - start) * ratio


def orient_line_for_screen(line: DetectedLine) -> DetectedLine:
    if (line.p1[1], line.p1[0]) < (line.p0[1], line.p0[0]):
        return DetectedLine(kind=line.kind, p0=line.p1, p1=line.p0, confidence=line.confidence)
    return line


def orient_line_like_reference(line: DetectedLine, reference_direction: np.ndarray) -> DetectedLine:
    vector = line_vector(line)
    if float(np.dot(vector, reference_direction)) < 0:
        return DetectedLine(kind=line.kind, p0=line.p1, p1=line.p0, confidence=line.confidence)
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


def build_grid_from_reference_lines(
    nut_line: DetectedLine,
    reference_fret_line: DetectedLine,
    reference_fret_index: int,
) -> StableFretboardGrid | None:
    if reference_fret_index < 1:
        return None

    nut_line = orient_line_for_screen(nut_line)
    nut_direction = normalize(line_vector(nut_line))
    if not np.any(nut_direction):
        return None

    reference_fret_line = orient_line_like_reference(
        orient_line_for_screen(reference_fret_line),
        nut_direction,
    )

    canonical_points = np.asarray(
        [[0.0, 0.0], [1.0, 0.0], [0.0, 1.0], [1.0, 1.0]],
        dtype=np.float32,
    )
    image_points = np.asarray(
        [nut_line.p0, nut_line.p1, reference_fret_line.p0, reference_fret_line.p1],
        dtype=np.float32,
    )
    homography = compute_homography(canonical_points, image_points)

    reference_ratio = scale_length_ratio(reference_fret_index)
    if reference_ratio <= 1e-6:
        return None

    ordered_fret_lines: list[DetectedLine] = []
    for fret_index in range(1, reference_fret_index + 1):
        y_ratio = scale_length_ratio(fret_index) / reference_ratio
        ordered_fret_lines.append(
            DetectedLine(
                kind="fret",
                p0=project_homography(homography, 0.0, y_ratio),
                p1=project_homography(homography, 1.0, y_ratio),
                confidence=min(nut_line.confidence, reference_fret_line.confidence),
            )
        )

    x_positions = np.linspace(
        PLAYABLE_EDGE_MARGIN_RATIO,
        1.0 - PLAYABLE_EDGE_MARGIN_RATIO,
        STRING_COUNT,
        dtype=np.float32,
    )
    y_positions = [0.0] + [
        scale_length_ratio(fret_index) / reference_ratio
        for fret_index in range(1, reference_fret_index + 1)
    ]
    string_crossings = tuple(
        tuple(project_homography(homography, float(x_value), float(y_value)) for x_value in x_positions)
        for y_value in y_positions
    )

    return StableFretboardGrid(
        nut_line=nut_line,
        ordered_fret_lines=tuple(ordered_fret_lines),
        string_crossings=string_crossings,
    )


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


def build_fretboard_grid(detected_lines: Sequence[DetectedLine]) -> StableFretboardGrid | None:
    nut_candidates = [line for line in detected_lines if line.kind == "nut"]
    if not nut_candidates:
        return None

    nut_line = orient_line_for_screen(max(nut_candidates, key=lambda line: line.confidence))
    nut_direction = normalize(line_vector(nut_line))
    if not np.any(nut_direction):
        return None

    fret_lines = [
        orient_line_like_reference(line, nut_direction)
        for line in detected_lines
        if line.kind == "fret"
    ]

    if not fret_lines:
        return build_grid_from_lines(nut_line, [])

    neck_direction = np.asarray([-nut_direction[1], nut_direction[0]], dtype=np.float32)
    nut_midpoint = line_midpoint(nut_line)
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
        return build_grid_from_lines(nut_line, [])

    distance_candidates.sort(key=lambda item: item[0])
    dedupe_tolerance = max(6.0, line_length(nut_line) * 0.03)
    merged_candidates = merge_close_candidates(distance_candidates, tolerance=dedupe_tolerance)
    ordered_frets = [line for _, line in select_consecutive_prefix(merged_candidates)]
    return build_grid_from_lines(nut_line, ordered_frets)


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
