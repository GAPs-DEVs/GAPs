from __future__ import annotations

import unittest

import numpy as np

from app.practice_markers import build_practice_step_note_markers, screen_string_index
from practice.score_steps import build_practice_steps
from vision.fretboard_mapping import FRETTED_NOTE_ANCHOR_RATIO, DetectedLine, build_grid_from_lines, transform_grid


def make_line(kind: str, y: float) -> DetectedLine:
    return DetectedLine(kind=kind, p0=(0.0, y), p1=(100.0, y), confidence=0.9)


class PracticeMarkerTests(unittest.TestCase):
    def make_grid(self):
        grid = build_grid_from_lines(
            make_line("nut", 0.0),
            [
                make_line("fret", 20.0),
                make_line("fret", 38.0),
                make_line("fret", 54.0),
            ],
        )
        assert grid is not None
        return grid

    def test_screen_string_index_maps_standard_camera_order(self) -> None:
        self.assertEqual(screen_string_index(6), 0)
        self.assertEqual(screen_string_index(1), 5)

    def test_screen_string_index_maps_reversed_camera_order(self) -> None:
        self.assertEqual(screen_string_index(1, reverse_strings=True), 0)
        self.assertEqual(screen_string_index(6, reverse_strings=True), 5)

    def test_build_practice_step_note_markers_places_fretted_note_between_boundaries(self) -> None:
        step = build_practice_steps([{"step": 1, "string": 5, "fret": 3}])[0]

        markers = build_practice_step_note_markers(self.make_grid(), step)

        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0].string_index, 1)
        self.assertEqual(markers[0].fret_index, 3)
        expected_y = 38.0 + (54.0 - 38.0) * FRETTED_NOTE_ANCHOR_RATIO
        self.assertAlmostEqual(markers[0].point_xy[1], expected_y, places=4)

    def test_build_practice_step_note_markers_keeps_chord_notes_in_same_step(self) -> None:
        step = build_practice_steps(
            [
                {"step": 1, "string": 5, "fret": 3},
                {"step": 1, "string": 4, "fret": 2},
                {"step": 1, "string": 3, "fret": 0},
            ]
        )[0]

        markers = build_practice_step_note_markers(self.make_grid(), step)

        self.assertEqual([(marker.string_index, marker.fret_index) for marker in markers], [(1, 3), (2, 2), (3, 0)])

    def test_build_practice_step_note_markers_omits_frets_outside_visible_grid(self) -> None:
        step = build_practice_steps([{"step": 1, "string": 1, "fret": 7}])[0]

        markers = build_practice_step_note_markers(self.make_grid(), step)

        self.assertEqual(markers, ())

    def test_open_string_notes_stay_on_their_nut_strings_under_perspective(self) -> None:
        grid = transform_grid(self.make_grid(), np.array([
            [.9, -.15, 100], [.2, 1.1, 50], [.001, -.001, 1],
        ]))
        step = build_practice_steps([{"step": 1, "string": string, "fret": 0} for string in range(1, 7)])[0]
        for reverse in (False, True):
            markers = build_practice_step_note_markers(grid, step, reverse_strings=reverse)
            self.assertEqual(len(markers), 6)
            for note, marker in zip(step.notes, markers):
                string_index = screen_string_index(note.string_number, reverse_strings=reverse)
                np.testing.assert_allclose(marker.point_xy, grid.string_crossings[0][string_index], atol=1e-4)
                self.assertEqual(marker.string_index, string_index)
                self.assertEqual(marker.fret_index, 0)
                self.assertTrue(marker.is_open)


if __name__ == "__main__":
    unittest.main()
