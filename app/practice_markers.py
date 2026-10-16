from __future__ import annotations

from practice.score_steps import PracticeStep
from vision.fretboard_mapping import (
    FRETTED_NOTE_ANCHOR_RATIO,
    STRING_COUNT,
    NoteMarker,
    StableFretboardGrid,
    interpolate_points,
    point_array,
    point_tuple,
)
from vision.marker_visibility import open_string_marker


def screen_string_index(string_number: int, reverse_strings: bool = False) -> int:
    """Map guitar string number to the current screen string index."""
    if string_number < 1 or string_number > STRING_COUNT:
        raise ValueError(f"string number must be between 1 and {STRING_COUNT}: {string_number}")
    if reverse_strings:
        return string_number - 1
    return STRING_COUNT - string_number


def build_practice_step_note_markers(
    grid: StableFretboardGrid | None,
    step: PracticeStep | None,
    reverse_strings: bool = False,
) -> tuple[NoteMarker, ...]:
    if grid is None or step is None or not grid.string_crossings:
        return ()

    markers: list[NoteMarker] = []
    for note in step.notes:
        string_index = screen_string_index(note.string_number, reverse_strings=reverse_strings)
        if string_index >= len(grid.string_crossings[0]):
            continue

        fret_index = note.fret_number
        if fret_index == 0:
            marker = open_string_marker(grid, string_index)
            if marker is not None:
                markers.append(marker)
            continue

        if fret_index >= len(grid.string_crossings):
            continue

        previous_points = grid.string_crossings[fret_index - 1]
        current_points = grid.string_crossings[fret_index]
        if string_index >= len(previous_points) or string_index >= len(current_points):
            continue

        point = interpolate_points(
            point_array(previous_points[string_index]),
            point_array(current_points[string_index]),
            FRETTED_NOTE_ANCHOR_RATIO,
        )
        markers.append(
            NoteMarker(
                string_index=string_index,
                fret_index=fret_index,
                point_xy=point_tuple(point),
                is_open=False,
            )
        )

    return tuple(markers)
