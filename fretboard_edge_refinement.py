from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

import cv2
import numpy as np

from fretboard_mapping import (
    PLAYABLE_EDGE_MARGIN_RATIO,
    STRING_COUNT,
    DetectedLine,
    StableFretboardGrid,
    compute_string_crossings,
    intersect_infinite_lines,
    line_length,
    line_midpoint,
    line_vector,
    neck_direction_from_pair,
    normalize,
    orient_line_for_screen,
    orient_line_like_reference,
    point_array,
    point_tuple,
)


MIN_WARP_WIDTH = 180
MAX_WARP_WIDTH = 720
MIN_WARP_HEIGHT = 180
MAX_WARP_HEIGHT = 1080
STRING_SEARCH_RADIUS_RATIO = 0.07
FRET_SEARCH_RADIUS_RATIO = 0.35
EDGE_BIN_TARGET_PX = 18
MIN_STRING_SUPPORT_POINTS = 5
MIN_FRET_SUPPORT_POINTS = 6
FRET_FALLBACK_MIN_SUPPORT_POINTS = 5
MIN_PEAK_ABSOLUTE_STRENGTH = 0.035
FRET_MIN_COVERAGE_RATIO = 0.42
FRET_FALLBACK_MIN_COVERAGE_RATIO = 0.58
RIDGE_STRENGTH_WEIGHT = 0.74
EDGE_STRENGTH_WEIGHT = 0.58
STRING_TRACKER_DETECTED_ALPHA = 0.44
STRING_TRACKER_INFERRED_ALPHA = 0.10
STRING_TRACKER_OUTLIER_ALPHA = 0.05
STRING_TRACKER_DEADBAND_NORM = 0.006
STRING_TRACKER_OUTLIER_DELTA_NORM = 0.085
STRING_TRACKER_MAX_MISSING_FRAMES = 16
YOLO_FRET_DUPLICATE_DISTANCE_PX = 2.5
YOLO_FRET_DUPLICATE_MAX_DISTANCE_PX = 5.0
YOLO_FRET_DUPLICATE_GAP_RATIO = 0.28
YOLO_FRET_DUPLICATE_MIN_DIRECTION_SIMILARITY = 0.992
YOLO_NUT_FRET_DUPLICATE_DISTANCE_PX = 3.0
YOLO_NUT_FRET_DUPLICATE_MIN_DIRECTION_SIMILARITY = 0.988


@dataclass(frozen=True)
class CanonicalFretboard:
    homography: np.ndarray
    inverse_homography: np.ndarray
    width: int
    height: int


@dataclass(frozen=True)
class FittedCanonicalLine:
    slope: float
    intercept: float
    support_count: int
    coverage_ratio: float

    def value_at(self, coordinate: float) -> float:
        return self.slope * coordinate + self.intercept


@dataclass(frozen=True)
class NormalizedStringLine:
    slope: float
    intercept: float

    def value_at(self, y_norm: float) -> float:
        return self.slope * y_norm + self.intercept


@dataclass
class TrackedStringState:
    line: NormalizedStringLine
    missing_frames: int = 0


@dataclass(frozen=True)
class EdgeRefinementResult:
    grid: StableFretboardGrid | None
    fitted_string_indices: tuple[int, ...] = ()
    refined_fret_indices: tuple[int, ...] = ()
    tracked_string_indices: tuple[int, ...] = ()
    confirmed_fret_lines: tuple[DetectedLine | None, ...] = ()


class StringLineTracker:
    def __init__(
        self,
        detected_alpha: float = STRING_TRACKER_DETECTED_ALPHA,
        inferred_alpha: float = STRING_TRACKER_INFERRED_ALPHA,
        outlier_alpha: float = STRING_TRACKER_OUTLIER_ALPHA,
        deadband_norm: float = STRING_TRACKER_DEADBAND_NORM,
        outlier_delta_norm: float = STRING_TRACKER_OUTLIER_DELTA_NORM,
        max_missing_frames: int = STRING_TRACKER_MAX_MISSING_FRAMES,
    ) -> None:
        self.detected_alpha = detected_alpha
        self.inferred_alpha = inferred_alpha
        self.outlier_alpha = outlier_alpha
        self.deadband_norm = deadband_norm
        self.outlier_delta_norm = outlier_delta_norm
        self.max_missing_frames = max_missing_frames
        self._states: list[TrackedStringState | None] = [None] * STRING_COUNT

    def clear(self) -> None:
        self._states = [None] * STRING_COUNT

    def update(
        self,
        detected_lines: tuple[FittedCanonicalLine | None, ...],
        completed_lines: tuple[FittedCanonicalLine | None, ...],
        canonical: CanonicalFretboard,
    ) -> tuple[FittedCanonicalLine | None, ...]:
        tracked_lines: list[FittedCanonicalLine | None] = []
        for string_index in range(STRING_COUNT):
            detected_line = line_at_index(detected_lines, string_index)
            completed_line = line_at_index(completed_lines, string_index)
            measurement = detected_line if detected_line is not None else completed_line
            state = self._states[string_index]

            if measurement is None:
                held_line = self._hold_or_clear(string_index, state, canonical)
                tracked_lines.append(held_line)
                continue

            normalized_measurement = normalize_string_line(measurement, canonical)
            is_detected = detected_line is not None
            if state is None:
                next_line = normalized_measurement
                missing_frames = 0 if is_detected else 1
            else:
                alpha = self._alpha_for_measurement(
                    previous=state.line,
                    measurement=normalized_measurement,
                    is_detected=is_detected,
                )
                next_line = blend_normalized_lines(state.line, normalized_measurement, alpha)
                missing_frames = 0 if is_detected else state.missing_frames + 1

            self._states[string_index] = TrackedStringState(
                line=next_line,
                missing_frames=missing_frames,
            )
            tracked_lines.append(denormalize_string_line(next_line, canonical))

        return tuple(tracked_lines)

    def _hold_or_clear(
        self,
        string_index: int,
        state: TrackedStringState | None,
        canonical: CanonicalFretboard,
    ) -> FittedCanonicalLine | None:
        if state is None:
            return None

        state.missing_frames += 1
        if state.missing_frames > self.max_missing_frames:
            self._states[string_index] = None
            return None

        return denormalize_string_line(state.line, canonical)

    def _alpha_for_measurement(
        self,
        previous: NormalizedStringLine,
        measurement: NormalizedStringLine,
        is_detected: bool,
    ) -> float:
        distance = normalized_line_distance(previous, measurement)
        if distance <= self.deadband_norm:
            return 0.0

        if not is_detected:
            return self.inferred_alpha

        if distance > self.outlier_delta_norm:
            return self.outlier_alpha

        return self.detected_alpha


def refine_grid_with_edge_lines(
    frame_bgr: np.ndarray,
    grid: StableFretboardGrid | None,
    string_tracker: StringLineTracker | None = None,
    suppressed_fret_indices: Collection[int] | None = None,
    apply_fret_refinement: bool = True,
    yolo_detected_lines: Collection[DetectedLine] | None = None,
) -> StableFretboardGrid | None:
    return refine_grid_with_edge_lines_diagnostics(
        frame_bgr,
        grid,
        string_tracker=string_tracker,
        suppressed_fret_indices=suppressed_fret_indices,
        apply_fret_refinement=apply_fret_refinement,
        yolo_detected_lines=yolo_detected_lines,
    ).grid


def refine_grid_with_edge_lines_diagnostics(
    frame_bgr: np.ndarray,
    grid: StableFretboardGrid | None,
    string_tracker: StringLineTracker | None = None,
    suppressed_fret_indices: Collection[int] | None = None,
    apply_fret_refinement: bool = True,
    yolo_detected_lines: Collection[DetectedLine] | None = None,
) -> EdgeRefinementResult:
    if grid is None or not grid.ordered_fret_lines:
        return EdgeRefinementResult(grid=grid)

    canonical = build_canonical_fretboard(grid)
    if canonical is None:
        return EdgeRefinementResult(grid=grid)

    warped = cv2.warpPerspective(
        frame_bgr,
        canonical.homography,
        (canonical.width, canonical.height),
        flags=cv2.INTER_LINEAR,
    )
    gray = preprocess_warped_frame(warped)

    vertical_strength = normalized_sobel_strength(gray, dx=1, dy=0)
    ridge_strength = normalized_ridge_strength(gray)
    string_strength = combine_edge_and_ridge_strength(vertical_strength, ridge_strength)

    string_lines = fit_string_lines(string_strength, canonical.width, canonical.height)
    fitted_string_indices = tuple(
        string_index
        for string_index, string_line in enumerate(string_lines)
        if string_line is not None
    )
    completed_string_lines = complete_string_lines(string_lines, canonical.width)
    tracked_string_lines = (
        string_tracker.update(string_lines, completed_string_lines, canonical)
        if string_tracker is not None
        else completed_string_lines
    )
    tracked_string_indices = tuple(
        string_index
        for string_index, string_line in enumerate(tracked_string_lines)
        if string_line is not None
    )
    suppressed_indices = valid_fret_indices(
        suppressed_fret_indices,
        len(grid.ordered_fret_lines),
    )
    yolo_fret_lines, yolo_fret_indices = yolo_fret_fits_from_detected_lines(
        grid=grid,
        canonical=canonical,
        yolo_detected_lines=yolo_detected_lines,
        suppressed_fret_indices=suppressed_indices,
    )
    if apply_fret_refinement:
        fallback_fret_lines = canonical_y_fits_from_grid_frets(grid, canonical)
        refined_frets = fret_lines_from_canonical_y_fits(
            grid=grid,
            canonical=canonical,
            fitted_lines=merge_detected_and_fallback_lines(yolo_fret_lines, fallback_fret_lines),
        )
    else:
        refined_frets = list(grid.ordered_fret_lines)
    confirmed_fret_lines = confirmed_fret_lines_from_canonical_y_fits(
        grid=grid,
        canonical=canonical,
        fitted_lines=yolo_fret_lines,
        confirmed_indices=yolo_fret_indices,
    )

    if (
        not fitted_string_indices
        and not tracked_string_indices
        and not yolo_fret_indices
    ):
        return EdgeRefinementResult(grid=grid)

    string_crossings = build_string_crossings_from_refined_lines(
        nut_line=grid.nut_line,
        fret_lines=refined_frets,
        string_lines=tracked_string_lines,
        canonical=canonical,
    )
    refined_grid = StableFretboardGrid(
        nut_line=grid.nut_line,
        ordered_fret_lines=tuple(refined_frets),
        string_crossings=string_crossings,
    )
    return EdgeRefinementResult(
        grid=refined_grid,
        fitted_string_indices=fitted_string_indices,
        refined_fret_indices=yolo_fret_indices,
        tracked_string_indices=tracked_string_indices,
        confirmed_fret_lines=confirmed_fret_lines,
    )


def yolo_fret_fits_from_detected_lines(
    grid: StableFretboardGrid,
    canonical: CanonicalFretboard,
    yolo_detected_lines: Collection[DetectedLine] | None,
    suppressed_fret_indices: Collection[int] | None = None,
) -> tuple[tuple[FittedCanonicalLine | None, ...], tuple[int, ...]]:
    fitted_lines: list[FittedCanonicalLine | None] = [None] * len(grid.ordered_fret_lines)
    if not yolo_detected_lines:
        return tuple(fitted_lines), ()

    yolo_frets = tuple(line for line in yolo_detected_lines if line.kind == "fret")
    if not yolo_frets:
        return tuple(fitted_lines), ()

    yolo_nuts = tuple(line for line in yolo_detected_lines if line.kind == "nut")
    current_nut_line = max(yolo_nuts, key=lambda line: line.confidence, default=grid.nut_line)
    current_nut_line = orient_line_for_screen(current_nut_line)
    nut_direction = normalize(line_vector(current_nut_line))
    neck_direction = neck_direction_from_pair(current_nut_line, grid.ordered_fret_lines[-1])
    nut_midpoint = line_midpoint(current_nut_line)
    suppressed_indices = set(suppressed_fret_indices or ())

    candidates: list[tuple[float, DetectedLine, FittedCanonicalLine]] = []
    for fret_line in yolo_frets:
        normalized_fret = orient_line_like_reference(orient_line_for_screen(fret_line), nut_direction)
        distance = float(np.dot(line_midpoint(normalized_fret) - nut_midpoint, neck_direction))
        if distance <= 0.0:
            continue
        if is_yolo_fret_duplicate_of_nut(
            current_nut_line,
            normalized_fret,
            distance,
        ):
            continue
        fitted_line = canonical_y_fit_from_image_line(normalized_fret, canonical)
        if fitted_line is None:
            continue
        candidates.append((distance, normalized_fret, fitted_line))

    duplicate_distance = yolo_fret_duplicate_distance_threshold(grid, current_nut_line)
    ordered_candidates = merge_ordered_yolo_fret_duplicates(candidates, duplicate_distance)
    confirmed_indices: list[int] = []
    for fret_index, (_, _, fitted_line) in enumerate(ordered_candidates):
        if fret_index >= len(fitted_lines):
            break
        if fret_index in suppressed_indices or not 0 <= fret_index < len(fitted_lines):
            continue
        fitted_lines[fret_index] = fitted_line
        confirmed_indices.append(fret_index)

    return tuple(fitted_lines), tuple(sorted(set(confirmed_indices)))


def merge_ordered_yolo_fret_duplicates(
    candidates: list[tuple[float, DetectedLine, FittedCanonicalLine]],
    duplicate_distance: float = YOLO_FRET_DUPLICATE_DISTANCE_PX,
) -> list[tuple[float, DetectedLine, FittedCanonicalLine]]:
    if not candidates:
        return []

    ordered = sorted(candidates, key=lambda item: item[0])
    clusters: list[list[tuple[float, DetectedLine, FittedCanonicalLine]]] = [[ordered[0]]]
    for candidate in ordered[1:]:
        representative = max(clusters[-1], key=lambda item: item[1].confidence)
        if is_duplicate_yolo_fret(representative, candidate, duplicate_distance):
            clusters[-1].append(candidate)
        else:
            clusters.append([candidate])

    return [
        max(cluster, key=lambda item: item[1].confidence)
        for cluster in clusters
    ]


def is_yolo_fret_duplicate_of_nut(
    nut_line: DetectedLine,
    fret_line: DetectedLine,
    neck_distance: float,
) -> bool:
    return (
        neck_distance <= YOLO_NUT_FRET_DUPLICATE_DISTANCE_PX
        and line_direction_similarity(nut_line, fret_line) >= YOLO_NUT_FRET_DUPLICATE_MIN_DIRECTION_SIMILARITY
    )


def is_duplicate_yolo_fret(
    first: tuple[float, DetectedLine, FittedCanonicalLine],
    second: tuple[float, DetectedLine, FittedCanonicalLine],
    duplicate_distance: float,
) -> bool:
    return (
        abs(first[0] - second[0]) <= duplicate_distance
        and line_direction_similarity(first[1], second[1]) >= YOLO_FRET_DUPLICATE_MIN_DIRECTION_SIMILARITY
    )


def line_direction_similarity(first_line: DetectedLine, second_line: DetectedLine) -> float:
    first_direction = normalize(line_vector(first_line))
    second_direction = normalize(line_vector(second_line))
    if not np.any(first_direction) or not np.any(second_direction):
        return 0.0
    return abs(float(np.dot(first_direction, second_direction)))


def yolo_fret_duplicate_distance_threshold(
    grid: StableFretboardGrid,
    nut_line: DetectedLine,
) -> float:
    neck_direction = neck_direction_from_pair(nut_line, grid.ordered_fret_lines[-1])
    reference_lines = (nut_line, *grid.ordered_fret_lines)
    projected_positions = [
        float(np.dot(line_midpoint(line) - line_midpoint(nut_line), neck_direction))
        for line in reference_lines
    ]
    gaps = [
        abs(second - first)
        for first, second in zip(projected_positions[:-1], projected_positions[1:])
        if abs(second - first) > 1e-6
    ]
    if not gaps:
        return YOLO_FRET_DUPLICATE_DISTANCE_PX

    adaptive_distance = min(gaps) * YOLO_FRET_DUPLICATE_GAP_RATIO
    return float(
        np.clip(
            adaptive_distance,
            YOLO_FRET_DUPLICATE_DISTANCE_PX,
            YOLO_FRET_DUPLICATE_MAX_DISTANCE_PX,
        )
    )


def valid_fret_indices(
    fret_indices: Collection[int] | None,
    fret_count: int,
) -> set[int]:
    if fret_indices is None:
        return set()
    return {
        int(fret_index)
        for fret_index in fret_indices
        if 0 <= int(fret_index) < fret_count
    }


def line_at_index(
    lines: tuple[FittedCanonicalLine | None, ...],
    index: int,
) -> FittedCanonicalLine | None:
    if index >= len(lines):
        return None
    return lines[index]


def normalize_string_line(
    line: FittedCanonicalLine,
    canonical: CanonicalFretboard,
) -> NormalizedStringLine:
    x_scale = max(float(canonical.width - 1), 1.0)
    y_scale = max(float(canonical.height - 1), 1.0)
    return NormalizedStringLine(
        slope=float(line.slope * y_scale / x_scale),
        intercept=float(line.intercept / x_scale),
    )


def denormalize_string_line(
    line: NormalizedStringLine,
    canonical: CanonicalFretboard,
) -> FittedCanonicalLine:
    x_scale = max(float(canonical.width - 1), 1.0)
    y_scale = max(float(canonical.height - 1), 1.0)
    return FittedCanonicalLine(
        slope=float(line.slope * x_scale / y_scale),
        intercept=float(line.intercept * x_scale),
        support_count=0,
        coverage_ratio=0.0,
    )


def blend_normalized_lines(
    previous: NormalizedStringLine,
    measurement: NormalizedStringLine,
    alpha: float,
) -> NormalizedStringLine:
    alpha = float(np.clip(alpha, 0.0, 1.0))
    keep = 1.0 - alpha
    return NormalizedStringLine(
        slope=previous.slope * keep + measurement.slope * alpha,
        intercept=previous.intercept * keep + measurement.intercept * alpha,
    )


def normalized_line_distance(
    first_line: NormalizedStringLine,
    second_line: NormalizedStringLine,
) -> float:
    sample_y = (0.0, 0.5, 1.0)
    return max(abs(first_line.value_at(y_value) - second_line.value_at(y_value)) for y_value in sample_y)


def build_canonical_fretboard(grid: StableFretboardGrid) -> CanonicalFretboard | None:
    reference_line = grid.ordered_fret_lines[-1]
    nut_line = orient_line_for_screen(grid.nut_line)
    reference_line = orient_line_like_reference(
        orient_line_for_screen(reference_line),
        normalize(line_vector(nut_line)),
    )

    source_quad = np.asarray(
        [
            nut_line.p0,
            nut_line.p1,
            reference_line.p1,
            reference_line.p0,
        ],
        dtype=np.float32,
    )
    average_width = max(line_length(nut_line), line_length(reference_line))
    average_height = float(
        (
            np.linalg.norm(point_array(reference_line.p0) - point_array(nut_line.p0))
            + np.linalg.norm(point_array(reference_line.p1) - point_array(nut_line.p1))
        )
        * 0.5
    )
    if average_width < 12.0 or average_height < 12.0:
        return None

    target_width = int(np.clip(round(average_width), MIN_WARP_WIDTH, MAX_WARP_WIDTH))
    target_height = int(np.clip(round(average_height), MIN_WARP_HEIGHT, MAX_WARP_HEIGHT))
    destination_quad = np.asarray(
        [
            (0.0, 0.0),
            (float(target_width - 1), 0.0),
            (float(target_width - 1), float(target_height - 1)),
            (0.0, float(target_height - 1)),
        ],
        dtype=np.float32,
    )

    homography = cv2.getPerspectiveTransform(source_quad, destination_quad)
    inverse_homography = cv2.getPerspectiveTransform(destination_quad, source_quad)
    return CanonicalFretboard(
        homography=homography,
        inverse_homography=inverse_homography,
        width=target_width,
        height=target_height,
    )


def preprocess_warped_frame(warped_bgr: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(warped_bgr, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    gray = clahe.apply(gray)
    return cv2.GaussianBlur(gray, (3, 3), 0)


def normalized_sobel_strength(gray: np.ndarray, dx: int, dy: int) -> np.ndarray:
    strength = np.abs(cv2.Sobel(gray, cv2.CV_32F, dx, dy, ksize=3))
    scale = float(np.percentile(strength, 98.0))
    if scale <= 1e-6:
        return np.zeros_like(strength, dtype=np.float32)
    return np.clip(strength / scale, 0.0, 1.0).astype(np.float32)


def normalized_ridge_strength(gray: np.ndarray) -> np.ndarray:
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
    ridge = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, kernel)
    ridge = cv2.GaussianBlur(ridge, (3, 3), 0).astype(np.float32)
    scale = float(np.percentile(ridge, 98.0))
    if scale <= 1e-6:
        return np.zeros_like(ridge, dtype=np.float32)
    return np.clip(ridge / scale, 0.0, 1.0).astype(np.float32)


def combine_edge_and_ridge_strength(
    edge_strength: np.ndarray,
    ridge_strength: np.ndarray,
) -> np.ndarray:
    return np.maximum(
        edge_strength * EDGE_STRENGTH_WEIGHT,
        ridge_strength * RIDGE_STRENGTH_WEIGHT,
    ).astype(np.float32)


def fit_string_lines(
    vertical_strength: np.ndarray,
    width: int,
    height: int,
) -> tuple[FittedCanonicalLine | None, ...]:
    expected_positions = expected_string_positions(width)
    search_radius = max(10.0, min(42.0, width * STRING_SEARCH_RADIUS_RATIO))
    bin_count = max(8, min(56, int(round(height / EDGE_BIN_TARGET_PX))))
    profile_positions = axis_profile_peak_positions(
        strength=vertical_strength,
        expected_positions=expected_positions,
        search_radius=search_radius,
        axis="x",
    )

    lines: list[FittedCanonicalLine | None] = []
    for string_index, expected_x in enumerate(expected_positions):
        profile_position = profile_positions[string_index]
        fit_expected_x = profile_position if profile_position is not None else float(expected_x)
        points = collect_axis_peak_points(
            strength=vertical_strength,
            expected_position=fit_expected_x,
            search_radius=search_radius,
            bin_count=bin_count,
            fit_axis="x_from_y",
        )
        line = fit_canonical_line(
            points=points,
            expected_position=fit_expected_x,
            search_radius=search_radius,
            coordinate_limit=float(height - 1),
            value_limit=float(width - 1),
            min_points=MIN_STRING_SUPPORT_POINTS,
            max_endpoint_delta=max(width * 0.13, search_radius * 2.0),
        )
        if line is None and profile_position is not None:
            line = FittedCanonicalLine(
                slope=0.0,
                intercept=float(profile_position),
                support_count=0,
                coverage_ratio=1.0,
            )
        lines.append(line)
    return tuple(lines)


def axis_profile_peak_positions(
    strength: np.ndarray,
    expected_positions: np.ndarray,
    search_radius: float,
    axis: str,
) -> tuple[float | None, ...]:
    return tuple(
        axis_profile_peak_position(
            strength=strength,
            expected_position=float(expected_position),
            search_radius=search_radius,
            axis=axis,
        )
        for expected_position in expected_positions
    )


def axis_profile_peak_position(
    strength: np.ndarray,
    expected_position: float,
    search_radius: float,
    axis: str,
) -> float | None:
    if axis == "x":
        profile = np.mean(strength, axis=0)
        value_limit = strength.shape[1]
    elif axis == "y":
        profile = np.mean(strength, axis=1)
        value_limit = strength.shape[0]
    else:
        raise ValueError(f"Unsupported axis: {axis}")

    value_start = max(0, int(np.floor(float(expected_position) - search_radius)))
    value_end = min(value_limit, int(np.ceil(float(expected_position) + search_radius)) + 1)
    if value_end - value_start < 4:
        return None

    peak = peak_centroid(profile[value_start:value_end])
    if peak is None:
        return None

    offset, _ = peak
    return float(value_start) + offset


def expected_string_positions(width: int) -> np.ndarray:
    playable_start = PLAYABLE_EDGE_MARGIN_RATIO * (width - 1)
    playable_end = (1.0 - PLAYABLE_EDGE_MARGIN_RATIO) * (width - 1)
    return np.linspace(playable_start, playable_end, STRING_COUNT, dtype=np.float32)


def complete_string_lines(
    string_lines: tuple[FittedCanonicalLine | None, ...],
    width: int,
) -> tuple[FittedCanonicalLine | None, ...]:
    expected_positions = expected_string_positions(width)
    fitted_samples = [
        (float(expected_positions[string_index]), string_line)
        for string_index, string_line in enumerate(string_lines)
        if string_line is not None
    ]
    if not fitted_samples:
        return string_lines

    completed: list[FittedCanonicalLine | None] = list(string_lines)
    if len(fitted_samples) == 1:
        expected_x, source_line = fitted_samples[0]
        offset = source_line.intercept - expected_x
        for string_index, string_line in enumerate(completed):
            if string_line is not None:
                continue
            completed[string_index] = FittedCanonicalLine(
                slope=source_line.slope,
                intercept=float(expected_positions[string_index]) + offset,
                support_count=0,
                coverage_ratio=0.0,
            )
        return tuple(completed)

    sample_x = np.asarray([sample[0] for sample in fitted_samples], dtype=np.float32)
    sample_slopes = np.asarray([sample[1].slope for sample in fitted_samples], dtype=np.float32)
    sample_offsets = np.asarray([sample[1].intercept - sample[0] for sample in fitted_samples], dtype=np.float32)
    slope_fit = np.polyfit(sample_x, sample_slopes, deg=1)
    offset_fit = np.polyfit(sample_x, sample_offsets, deg=1)

    for string_index, string_line in enumerate(completed):
        if string_line is not None:
            continue
        expected_x = float(expected_positions[string_index])
        completed[string_index] = FittedCanonicalLine(
            slope=float(np.polyval(slope_fit, expected_x)),
            intercept=expected_x + float(np.polyval(offset_fit, expected_x)),
            support_count=0,
            coverage_ratio=0.0,
        )
    return tuple(completed)


def refine_fret_lines(
    horizontal_strength: np.ndarray,
    grid: StableFretboardGrid,
    canonical: CanonicalFretboard,
) -> list[DetectedLine]:
    return refine_fret_lines_with_support(horizontal_strength, grid, canonical)[0]


def refine_fret_lines_with_support(
    horizontal_strength: np.ndarray,
    grid: StableFretboardGrid,
    canonical: CanonicalFretboard,
) -> tuple[list[DetectedLine], tuple[int, ...]]:
    fitted_fret_lines, refined_indices = fit_fret_lines_with_support(
        horizontal_strength=horizontal_strength,
        grid=grid,
        canonical=canonical,
    )
    fallback_fret_lines = canonical_y_fits_from_grid_frets(grid, canonical)
    fret_lines = fret_lines_from_canonical_y_fits(
        grid=grid,
        canonical=canonical,
        fitted_lines=merge_detected_and_fallback_lines(fitted_fret_lines, fallback_fret_lines),
    )
    return fret_lines, refined_indices


def fit_fret_lines_with_support(
    horizontal_strength: np.ndarray,
    grid: StableFretboardGrid,
    canonical: CanonicalFretboard,
) -> tuple[tuple[FittedCanonicalLine | None, ...], tuple[int, ...]]:
    expected_y_positions = canonical_y_positions_for_frets(grid, canonical)
    if not expected_y_positions:
        return tuple(None for _ in grid.ordered_fret_lines), ()

    fitted_frets: list[FittedCanonicalLine | None] = []
    refined_indices: list[int] = []
    for fret_index, expected_y in enumerate(expected_y_positions):
        neighbor_gap = fret_neighbor_gap(expected_y_positions, fret_index)
        search_radius = fret_search_radius(expected_y_positions, fret_index, canonical.height)
        endpoint_delta_limit = fret_endpoint_delta_limit(
            search_radius=search_radius,
            neighbor_gap=neighbor_gap,
            height=canonical.height,
        )
        profile_position = axis_profile_peak_position(
            strength=horizontal_strength,
            expected_position=expected_y,
            search_radius=search_radius,
            axis="y",
        )
        fit_expected_y = expected_y
        if (
            profile_position is not None
            and abs(profile_position - expected_y) <= endpoint_delta_limit
        ):
            fit_expected_y = profile_position
        bin_count = max(8, min(48, int(round(canonical.width / EDGE_BIN_TARGET_PX))))
        points = collect_axis_peak_points(
            strength=horizontal_strength,
            expected_position=fit_expected_y,
            search_radius=search_radius,
            bin_count=bin_count,
            fit_axis="y_from_x",
        )
        fitted_line = fit_canonical_line(
            points=points,
            expected_position=fit_expected_y,
            search_radius=search_radius,
            coordinate_limit=float(canonical.width - 1),
            value_limit=float(canonical.height - 1),
            min_points=MIN_FRET_SUPPORT_POINTS,
            max_endpoint_delta=endpoint_delta_limit,
            min_coverage=FRET_MIN_COVERAGE_RATIO,
            strict_coverage=True,
        )
        if fitted_line is None:
            fitted_line = fit_canonical_line(
                points=points,
                expected_position=fit_expected_y,
                search_radius=search_radius,
                coordinate_limit=float(canonical.width - 1),
                value_limit=float(canonical.height - 1),
                min_points=FRET_FALLBACK_MIN_SUPPORT_POINTS,
                max_endpoint_delta=endpoint_delta_limit,
                min_coverage=FRET_FALLBACK_MIN_COVERAGE_RATIO,
                strict_coverage=True,
            )
        if fitted_line is None:
            fitted_frets.append(None)
            continue

        fitted_frets.append(fitted_line)
        refined_indices.append(fret_index)
    return tuple(fitted_frets), tuple(refined_indices)


def fret_neighbor_gap(expected_y_positions: list[float], index: int) -> float | None:
    neighbor_gaps: list[float] = []
    if index > 0:
        neighbor_gaps.append(abs(expected_y_positions[index] - expected_y_positions[index - 1]))
    if index + 1 < len(expected_y_positions):
        neighbor_gaps.append(abs(expected_y_positions[index + 1] - expected_y_positions[index]))
    if not neighbor_gaps:
        return None
    return min(neighbor_gaps)


def fret_endpoint_delta_limit(
    search_radius: float,
    neighbor_gap: float | None,
    height: int,
) -> float:
    base_limit = max(search_radius * 1.8, height * 0.035)
    if neighbor_gap is None:
        return base_limit
    return min(base_limit, max(2.5, neighbor_gap * 0.48))


def canonical_y_fits_from_grid_frets(
    grid: StableFretboardGrid,
    canonical: CanonicalFretboard,
) -> tuple[FittedCanonicalLine | None, ...]:
    return tuple(
        canonical_y_fit_from_image_line(fret_line, canonical)
        for fret_line in grid.ordered_fret_lines
    )


def canonical_y_fit_from_image_line(
    line: DetectedLine,
    canonical: CanonicalFretboard,
) -> FittedCanonicalLine | None:
    projected = project_points(canonical.homography, np.asarray([line.p0, line.p1], dtype=np.float32))
    if projected is None:
        return None

    coordinates = projected[:, 0].astype(np.float32)
    values = projected[:, 1].astype(np.float32)
    if abs(float(coordinates[1] - coordinates[0])) <= 1e-4:
        return None

    slope = float((values[1] - values[0]) / (coordinates[1] - coordinates[0]))
    intercept = float(values[0] - slope * coordinates[0])
    return FittedCanonicalLine(
        slope=slope,
        intercept=intercept,
        support_count=0,
        coverage_ratio=0.0,
    )


def merge_detected_and_fallback_lines(
    detected_lines: tuple[FittedCanonicalLine | None, ...],
    fallback_lines: tuple[FittedCanonicalLine | None, ...],
) -> tuple[FittedCanonicalLine | None, ...]:
    line_count = max(len(detected_lines), len(fallback_lines))
    merged: list[FittedCanonicalLine | None] = []
    for line_index in range(line_count):
        detected_line = line_at_index(detected_lines, line_index)
        fallback_line = line_at_index(fallback_lines, line_index)
        merged.append(detected_line if detected_line is not None else fallback_line)
    return tuple(merged)


def fret_lines_from_canonical_y_fits(
    grid: StableFretboardGrid,
    canonical: CanonicalFretboard,
    fitted_lines: tuple[FittedCanonicalLine | None, ...],
) -> list[DetectedLine]:
    fret_lines: list[DetectedLine] = []
    nut_direction = normalize(line_vector(grid.nut_line))
    for fret_index, fallback_line in enumerate(grid.ordered_fret_lines):
        fitted_line = line_at_index(fitted_lines, fret_index)
        if fitted_line is None:
            fret_lines.append(fallback_line)
            continue

        refined = line_from_canonical_y_fit(fitted_line, fallback_line, canonical, nut_direction)
        fret_lines.append(refined if refined is not None else fallback_line)
    return fret_lines


def confirmed_fret_lines_from_canonical_y_fits(
    grid: StableFretboardGrid,
    canonical: CanonicalFretboard,
    fitted_lines: tuple[FittedCanonicalLine | None, ...],
    confirmed_indices: tuple[int, ...],
) -> tuple[DetectedLine | None, ...]:
    confirmed_index_set = set(confirmed_indices)
    nut_direction = normalize(line_vector(grid.nut_line))
    confirmed_lines: list[DetectedLine | None] = []
    for fret_index, fallback_line in enumerate(grid.ordered_fret_lines):
        fitted_line = line_at_index(fitted_lines, fret_index)
        if fret_index not in confirmed_index_set or fitted_line is None:
            confirmed_lines.append(None)
            continue

        confirmed_lines.append(
            line_from_canonical_y_fit(fitted_line, fallback_line, canonical, nut_direction)
        )
    return tuple(confirmed_lines)


def canonical_y_positions_for_frets(
    grid: StableFretboardGrid,
    canonical: CanonicalFretboard,
) -> list[float]:
    positions: list[float] = []
    for line in grid.ordered_fret_lines:
        projected = project_points(canonical.homography, np.asarray([line.p0, line.p1], dtype=np.float32))
        if projected is None:
            return []
        positions.append(float(np.mean(projected[:, 1])))
    return positions


def fret_search_radius(expected_y_positions: list[float], index: int, height: int) -> float:
    neighbor_gaps: list[float] = []
    if index > 0:
        neighbor_gaps.append(abs(expected_y_positions[index] - expected_y_positions[index - 1]))
    if index + 1 < len(expected_y_positions):
        neighbor_gaps.append(abs(expected_y_positions[index + 1] - expected_y_positions[index]))
    if neighbor_gaps:
        adaptive = min(neighbor_gaps) * FRET_SEARCH_RADIUS_RATIO
    else:
        adaptive = height * 0.04
    return max(7.0, min(32.0, adaptive))


def collect_axis_peak_points(
    strength: np.ndarray,
    expected_position: float,
    search_radius: float,
    bin_count: int,
    fit_axis: str,
) -> list[tuple[float, float, float]]:
    height, width = strength.shape[:2]
    points: list[tuple[float, float, float]] = []

    if fit_axis == "x_from_y":
        coordinate_limit = height
        value_limit = width
    else:
        coordinate_limit = width
        value_limit = height

    bin_edges = np.linspace(0, coordinate_limit, bin_count + 1, dtype=np.int32)
    for start, end in zip(bin_edges[:-1], bin_edges[1:]):
        if end <= start:
            continue

        value_start = max(0, int(np.floor(expected_position - search_radius)))
        value_end = min(value_limit, int(np.ceil(expected_position + search_radius)) + 1)
        if value_end - value_start < 4:
            continue

        if fit_axis == "x_from_y":
            region = strength[start:end, value_start:value_end]
            energy = np.mean(region, axis=0)
            coordinate = (float(start) + float(end - 1)) * 0.5
        else:
            region = strength[value_start:value_end, start:end]
            energy = np.mean(region, axis=1)
            coordinate = (float(start) + float(end - 1)) * 0.5

        peak = peak_centroid(energy)
        if peak is None:
            continue

        offset, weight = peak
        points.append((coordinate, float(value_start) + offset, weight))
    return points


def peak_centroid(energy: np.ndarray) -> tuple[float, float] | None:
    if energy.size < 4:
        return None

    smoothed = smooth_1d(energy.astype(np.float32))
    peak_index = int(np.argmax(smoothed))
    peak_value = float(smoothed[peak_index])
    median = float(np.median(smoothed))
    q25, q75 = np.percentile(smoothed, [25.0, 75.0])
    spread = float(q75 - q25)
    required = median + max(MIN_PEAK_ABSOLUTE_STRENGTH, spread * 1.45)
    if peak_value < required:
        return None

    left = max(0, peak_index - 2)
    right = min(smoothed.size, peak_index + 3)
    local_indices = np.arange(left, right, dtype=np.float32)
    local_weights = np.maximum(smoothed[left:right] - median, 0.0)
    weight_sum = float(np.sum(local_weights))
    if weight_sum <= 1e-6:
        return (float(peak_index), peak_value - median)

    centroid = float(np.sum(local_indices * local_weights) / weight_sum)
    return (centroid, peak_value - median)


def smooth_1d(values: np.ndarray) -> np.ndarray:
    if values.size < 5:
        return values
    kernel = np.asarray([1.0, 2.0, 3.0, 2.0, 1.0], dtype=np.float32)
    kernel /= float(np.sum(kernel))
    return np.convolve(values, kernel, mode="same").astype(np.float32)


def fit_canonical_line(
    points: list[tuple[float, float, float]],
    expected_position: float,
    search_radius: float,
    coordinate_limit: float,
    value_limit: float,
    min_points: int,
    max_endpoint_delta: float,
    min_coverage: float = 0.24,
    strict_coverage: bool = False,
) -> FittedCanonicalLine | None:
    if len(points) < min_points:
        return None

    coordinates = np.asarray([point[0] for point in points], dtype=np.float32)
    values = np.asarray([point[1] for point in points], dtype=np.float32)
    weights = np.asarray([max(point[2], 1e-3) for point in points], dtype=np.float32)

    line = weighted_linear_fit(coordinates, values, weights)
    if line is None:
        return None

    residuals = np.abs(values - (line.slope * coordinates + line.intercept))
    residual_limit = max(3.5, search_radius * 0.38)
    inlier_mask = residuals <= residual_limit
    if int(np.count_nonzero(inlier_mask)) >= min_points:
        refit = weighted_linear_fit(coordinates[inlier_mask], values[inlier_mask], weights[inlier_mask])
        if refit is not None:
            line = refit
            coordinates = coordinates[inlier_mask]

    start_value = line.value_at(0.0)
    end_value = line.value_at(coordinate_limit)
    if min(start_value, end_value) < -search_radius or max(start_value, end_value) > value_limit + search_radius:
        return None

    if max(abs(start_value - expected_position), abs(end_value - expected_position)) > max_endpoint_delta:
        return None

    coverage = 0.0
    if coordinates.size >= 2:
        coverage = float((np.max(coordinates) - np.min(coordinates)) / max(coordinate_limit, 1.0))
    if strict_coverage:
        if coverage < min_coverage:
            return None
    elif coverage < min_coverage and coordinates.size < min_points + 3:
        return None

    return FittedCanonicalLine(
        slope=line.slope,
        intercept=line.intercept,
        support_count=int(coordinates.size),
        coverage_ratio=coverage,
    )


def weighted_linear_fit(
    coordinates: np.ndarray,
    values: np.ndarray,
    weights: np.ndarray,
) -> FittedCanonicalLine | None:
    if coordinates.size < 2:
        return None

    design = np.column_stack([coordinates, np.ones_like(coordinates)])
    weighted_design = design * np.sqrt(weights)[:, None]
    weighted_values = values * np.sqrt(weights)
    try:
        slope, intercept = np.linalg.lstsq(weighted_design, weighted_values, rcond=None)[0]
    except np.linalg.LinAlgError:
        return None
    return FittedCanonicalLine(
        slope=float(slope),
        intercept=float(intercept),
        support_count=int(coordinates.size),
        coverage_ratio=0.0,
    )


def line_from_canonical_y_fit(
    fitted_line: FittedCanonicalLine,
    fallback_line: DetectedLine,
    canonical: CanonicalFretboard,
    reference_direction: np.ndarray,
) -> DetectedLine | None:
    canonical_points = np.asarray(
        [
            (0.0, fitted_line.value_at(0.0)),
            (float(canonical.width - 1), fitted_line.value_at(float(canonical.width - 1))),
        ],
        dtype=np.float32,
    )
    image_points = project_points(canonical.inverse_homography, canonical_points)
    if image_points is None:
        return None

    refined = DetectedLine(
        kind=fallback_line.kind,
        p0=point_tuple(image_points[0]),
        p1=point_tuple(image_points[1]),
        confidence=fallback_line.confidence,
    )
    return orient_line_like_reference(orient_line_for_screen(refined), reference_direction)


def build_string_crossings_from_refined_lines(
    nut_line: DetectedLine,
    fret_lines: list[DetectedLine],
    string_lines: tuple[FittedCanonicalLine | None, ...],
    canonical: CanonicalFretboard,
) -> tuple[tuple[tuple[float, float], ...], ...]:
    cross_lines = [nut_line, *fret_lines]
    fallback_rows = [compute_string_crossings(line) for line in cross_lines]
    image_string_lines = [
        image_line_from_canonical_x_fit(line, canonical) if line is not None else None
        for line in string_lines
    ]

    crossing_rows: list[tuple[tuple[float, float], ...]] = []
    for row_index, cross_line in enumerate(cross_lines):
        row_points: list[tuple[float, float]] = []
        for string_index, string_line in enumerate(image_string_lines):
            fallback_point = fallback_rows[row_index][string_index]
            if string_line is None:
                row_points.append(fallback_point)
                continue

            intersection = intersect_detected_lines(cross_line, string_line)
            if intersection is None or not point_is_inside_canonical(intersection, canonical):
                row_points.append(fallback_point)
            else:
                row_points.append(intersection)
        crossing_rows.append(tuple(row_points))
    return tuple(crossing_rows)


def image_line_from_canonical_x_fit(
    fitted_line: FittedCanonicalLine,
    canonical: CanonicalFretboard,
) -> DetectedLine | None:
    canonical_points = np.asarray(
        [
            (fitted_line.value_at(0.0), 0.0),
            (fitted_line.value_at(float(canonical.height - 1)), float(canonical.height - 1)),
        ],
        dtype=np.float32,
    )
    image_points = project_points(canonical.inverse_homography, canonical_points)
    if image_points is None:
        return None

    return DetectedLine(
        kind="string",
        p0=point_tuple(image_points[0]),
        p1=point_tuple(image_points[1]),
        confidence=1.0,
    )


def intersect_detected_lines(
    first_line: DetectedLine,
    second_line: DetectedLine,
) -> tuple[float, float] | None:
    first_direction = normalize(line_vector(first_line))
    second_direction = normalize(line_vector(second_line))
    if not np.any(first_direction) or not np.any(second_direction):
        return None
    return intersect_infinite_lines(
        point_array(first_line.p0),
        first_direction,
        point_array(second_line.p0),
        second_direction,
    )


def point_is_inside_canonical(
    point: tuple[float, float],
    canonical: CanonicalFretboard,
) -> bool:
    projected = project_points(canonical.homography, np.asarray([point], dtype=np.float32))
    if projected is None:
        return False
    x_value = float(projected[0, 0])
    y_value = float(projected[0, 1])
    x_margin = canonical.width * 0.08
    y_margin = canonical.height * 0.08
    return (
        -x_margin <= x_value <= canonical.width - 1 + x_margin
        and -y_margin <= y_value <= canonical.height - 1 + y_margin
    )


def project_points(
    homography: np.ndarray,
    points: np.ndarray,
) -> np.ndarray | None:
    if points.size == 0:
        return np.empty((0, 2), dtype=np.float32)

    shaped = points.reshape(-1, 1, 2).astype(np.float32)
    projected = cv2.perspectiveTransform(shaped, homography).reshape(-1, 2)
    if not np.all(np.isfinite(projected)):
        return None
    return projected.astype(np.float32)
