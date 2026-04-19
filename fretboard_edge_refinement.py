from __future__ import annotations

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
    line_vector,
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
MIN_FRET_SUPPORT_POINTS = 4
MIN_PEAK_ABSOLUTE_STRENGTH = 0.035


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
class EdgeRefinementResult:
    grid: StableFretboardGrid | None
    fitted_string_indices: tuple[int, ...] = ()
    refined_fret_indices: tuple[int, ...] = ()


def refine_grid_with_edge_lines(
    frame_bgr: np.ndarray,
    grid: StableFretboardGrid | None,
) -> StableFretboardGrid | None:
    return refine_grid_with_edge_lines_diagnostics(frame_bgr, grid).grid


def refine_grid_with_edge_lines_diagnostics(
    frame_bgr: np.ndarray,
    grid: StableFretboardGrid | None,
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
    horizontal_strength = normalized_sobel_strength(gray, dx=0, dy=1)

    string_lines = fit_string_lines(vertical_strength, canonical.width, canonical.height)
    fitted_string_indices = tuple(
        string_index
        for string_index, string_line in enumerate(string_lines)
        if string_line is not None
    )
    completed_string_lines = complete_string_lines(string_lines, canonical.width)
    refined_frets, refined_fret_indices = refine_fret_lines_with_support(
        horizontal_strength=horizontal_strength,
        grid=grid,
        canonical=canonical,
    )

    if not fitted_string_indices and not refined_fret_indices:
        return EdgeRefinementResult(grid=grid)

    string_crossings = build_string_crossings_from_refined_lines(
        nut_line=grid.nut_line,
        fret_lines=refined_frets,
        string_lines=completed_string_lines,
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
        refined_fret_indices=refined_fret_indices,
    )


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


def fit_string_lines(
    vertical_strength: np.ndarray,
    width: int,
    height: int,
) -> tuple[FittedCanonicalLine | None, ...]:
    expected_positions = expected_string_positions(width)
    search_radius = max(10.0, min(42.0, width * STRING_SEARCH_RADIUS_RATIO))
    bin_count = max(8, min(56, int(round(height / EDGE_BIN_TARGET_PX))))

    lines: list[FittedCanonicalLine | None] = []
    for expected_x in expected_positions:
        points = collect_axis_peak_points(
            strength=vertical_strength,
            expected_position=float(expected_x),
            search_radius=search_radius,
            bin_count=bin_count,
            fit_axis="x_from_y",
        )
        line = fit_canonical_line(
            points=points,
            expected_position=float(expected_x),
            search_radius=search_radius,
            coordinate_limit=float(height - 1),
            value_limit=float(width - 1),
            min_points=MIN_STRING_SUPPORT_POINTS,
            max_endpoint_delta=max(width * 0.13, search_radius * 2.0),
        )
        lines.append(line)
    return tuple(lines)


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
    expected_y_positions = canonical_y_positions_for_frets(grid, canonical)
    if not expected_y_positions:
        return list(grid.ordered_fret_lines), ()

    refined_frets: list[DetectedLine] = []
    refined_indices: list[int] = []
    nut_direction = normalize(line_vector(grid.nut_line))
    for fret_index, (fret_line, expected_y) in enumerate(zip(grid.ordered_fret_lines, expected_y_positions)):
        search_radius = fret_search_radius(expected_y_positions, fret_index, canonical.height)
        bin_count = max(8, min(48, int(round(canonical.width / EDGE_BIN_TARGET_PX))))
        points = collect_axis_peak_points(
            strength=horizontal_strength,
            expected_position=expected_y,
            search_radius=search_radius,
            bin_count=bin_count,
            fit_axis="y_from_x",
        )
        fitted_line = fit_canonical_line(
            points=points,
            expected_position=expected_y,
            search_radius=search_radius,
            coordinate_limit=float(canonical.width - 1),
            value_limit=float(canonical.height - 1),
            min_points=MIN_FRET_SUPPORT_POINTS,
            max_endpoint_delta=max(search_radius * 1.8, canonical.height * 0.035),
        )
        if fitted_line is None:
            refined_frets.append(fret_line)
            continue

        refined = line_from_canonical_y_fit(fitted_line, fret_line, canonical, nut_direction)
        if refined is None:
            refined_frets.append(fret_line)
        else:
            refined_frets.append(refined)
            refined_indices.append(fret_index)
    return refined_frets, tuple(refined_indices)


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
    if coverage < 0.24 and coordinates.size < min_points + 3:
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
