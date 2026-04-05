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
    "레": 2,
    "미": 4,
    "파": 5,
    "솔": 7,
    "라": 9,
    "시": 11,
}

FIRST_GAP_RATIO_MIN = 0.65
GAP_RATIO_MIN = 0.55
GAP_RATIO_MAX = 1.15


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


def count_detected_classes(lines: Iterable[DetectedLine]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in lines:
        counts[line.kind] = counts.get(line.kind, 0) + 1
    return counts
