from __future__ import annotations

import unittest

from tools.fretboard_detection_viewer import filter_note_markers_by_edge_frets
from vision.fretboard_edge_refinement import EdgeRefinementResult
from vision.fretboard_mapping import DetectedLine, FRETTED_NOTE_ANCHOR_RATIO, NoteMarker, build_grid_from_lines


def make_line(kind: str, y: float, confidence: float = 0.9) -> DetectedLine:
    return DetectedLine(kind=kind, p0=(0.0, y), p1=(100.0, y), confidence=confidence)


class NoteMarkerEdgeFilterTests(unittest.TestCase):
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

    def test_fretted_marker_requires_detected_adjacent_fret_boundaries(self) -> None:
        grid = self.make_grid()
        marker = NoteMarker(string_index=1, fret_index=3, point_xy=(120.0, 80.0), is_open=False)
        edge_refinement = EdgeRefinementResult(
            grid=grid,
            refined_fret_indices=(2,),
            fitted_string_indices=(1,),
            confirmed_fret_lines=(None, None, grid.ordered_fret_lines[2]),
        )

        filtered = filter_note_markers_by_edge_frets(
            (marker,),
            edge_refinement,
            edge_supported_notes_only=False,
        )

        self.assertEqual(filtered, ())

    def test_fretted_marker_keeps_interval_between_detected_real_frets(self) -> None:
        grid = self.make_grid()
        marker = NoteMarker(string_index=1, fret_index=3, point_xy=(120.0, 80.0), is_open=False)
        edge_refinement = EdgeRefinementResult(
            grid=grid,
            refined_fret_indices=(1, 2),
            fitted_string_indices=(1,),
            confirmed_fret_lines=(None, grid.ordered_fret_lines[1], grid.ordered_fret_lines[2]),
        )

        filtered = filter_note_markers_by_edge_frets(
            (marker,),
            edge_refinement,
            edge_supported_notes_only=False,
        )

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].fret_index, marker.fret_index)

    def test_marker_position_uses_confirmed_fret_lines_not_model_grid(self) -> None:
        grid = self.make_grid()
        confirmed_second = make_line("fret", 40.0)
        confirmed_third = make_line("fret", 60.0)
        marker = NoteMarker(string_index=1, fret_index=3, point_xy=(120.0, 80.0), is_open=False)
        edge_refinement = EdgeRefinementResult(
            grid=grid,
            refined_fret_indices=(1, 2),
            fitted_string_indices=(1,),
            confirmed_fret_lines=(None, confirmed_second, confirmed_third),
        )

        filtered = filter_note_markers_by_edge_frets(
            (marker,),
            edge_refinement,
            edge_supported_notes_only=False,
        )

        self.assertEqual(len(filtered), 1)
        expected_y = 40.0 + (60.0 - 40.0) * FRETTED_NOTE_ANCHOR_RATIO
        self.assertAlmostEqual(filtered[0].point_xy[1], expected_y, places=4)


if __name__ == "__main__":
    unittest.main()
