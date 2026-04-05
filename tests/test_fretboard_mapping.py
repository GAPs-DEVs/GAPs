from __future__ import annotations

import unittest

from fretboard_mapping import (
    DetectedLine,
    FRETTED_NOTE_ANCHOR_RATIO,
    FretboardTracker,
    build_fretboard_grid,
    build_grid_from_lines,
    build_note_markers,
)


def make_line(kind: str, y: float, confidence: float = 0.9) -> DetectedLine:
    return DetectedLine(kind=kind, p0=(0.0, y), p1=(100.0, y), confidence=confidence)


class FretboardMappingTests(unittest.TestCase):
    def test_build_note_markers_matches_standard_tuning_positions(self) -> None:
        grid = build_grid_from_lines(
            make_line("nut", 0.0),
            [
                make_line("fret", 20.0),
                make_line("fret", 38.0),
                make_line("fret", 54.0),
                make_line("fret", 68.0),
                make_line("fret", 80.0),
            ],
        )

        markers = build_note_markers(grid, "라")
        marker_pairs = sorted((marker.string_index, marker.fret_index) for marker in markers)

        self.assertEqual(
            marker_pairs,
            [
                (0, 5),
                (1, 0),
                (3, 2),
                (5, 5),
            ],
        )

    def test_fretted_marker_uses_85_percent_anchor(self) -> None:
        grid = build_grid_from_lines(
            make_line("nut", 0.0),
            [
                make_line("fret", 20.0),
                make_line("fret", 38.0),
            ],
        )

        markers = build_note_markers(grid, "미")
        d_string_fret_two = next(
            marker for marker in markers if marker.string_index == 2 and marker.fret_index == 2
        )

        expected_y = 20.0 + (38.0 - 20.0) * FRETTED_NOTE_ANCHOR_RATIO
        self.assertAlmostEqual(d_string_fret_two.point_xy[1], expected_y, places=4)

    def test_open_string_marker_is_on_nut_line(self) -> None:
        grid = build_grid_from_lines(make_line("nut", 0.0), [make_line("fret", 20.0)])

        markers = build_note_markers(grid, "미")
        open_markers = [marker for marker in markers if marker.is_open]

        self.assertEqual(len(open_markers), 2)
        for marker in open_markers:
            self.assertAlmostEqual(marker.point_xy[1], 0.0, places=4)

    def test_reverse_strings_flips_screen_assignment(self) -> None:
        grid = build_grid_from_lines(
            make_line("nut", 0.0),
            [
                make_line("fret", 20.0),
                make_line("fret", 38.0),
                make_line("fret", 54.0),
                make_line("fret", 68.0),
                make_line("fret", 80.0),
            ],
        )

        default_markers = build_note_markers(grid, "라", reverse_strings=False)
        reversed_markers = build_note_markers(grid, "라", reverse_strings=True)

        default_open = next(marker for marker in default_markers if marker.is_open)
        reversed_open = next(marker for marker in reversed_markers if marker.is_open)

        self.assertLess(default_open.point_xy[0], reversed_open.point_xy[0])

    def test_missing_middle_fret_stops_visible_sequence(self) -> None:
        grid = build_fretboard_grid(
            [
                make_line("nut", 0.0),
                make_line("fret", 20.0, confidence=0.91),
                make_line("fret", 54.0, confidence=0.88),
                make_line("fret", 68.0, confidence=0.86),
            ]
        )

        self.assertIsNotNone(grid)
        self.assertEqual(len(grid.ordered_fret_lines), 1)

    def test_missing_nut_returns_no_grid(self) -> None:
        grid = build_fretboard_grid([make_line("fret", 20.0), make_line("fret", 38.0)])
        self.assertIsNone(grid)

    def test_tracker_uses_median_smoothing(self) -> None:
        tracker = FretboardTracker(max_history=5)
        grids = [
            build_grid_from_lines(make_line("nut", 0.0), [make_line("fret", 20.0)]),
            build_grid_from_lines(make_line("nut", 1.0), [make_line("fret", 21.0)]),
            build_grid_from_lines(make_line("nut", -1.0), [make_line("fret", 19.0)]),
            build_grid_from_lines(make_line("nut", 2.0), [make_line("fret", 22.0)]),
            build_grid_from_lines(make_line("nut", 50.0), [make_line("fret", 70.0)]),
        ]

        stable = None
        for grid in grids:
            stable = tracker.update(grid)

        self.assertIsNotNone(stable)
        self.assertAlmostEqual(stable.nut_line.p0[1], 1.0, places=4)
        self.assertAlmostEqual(stable.ordered_fret_lines[0].p0[1], 21.0, places=4)


if __name__ == "__main__":
    unittest.main()
