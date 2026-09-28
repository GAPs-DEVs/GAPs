from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import numpy as np
import pytest

from tools.fretboard_detection_viewer import (
    analyze_selected_lines_frame, draw_calibrated_guides, draw_reference_quad,
    filter_note_markers_by_edge_frets,
)
from vision.fretboard_edge_refinement import EdgeRefinementResult
from vision.fretboard_mapping import (
    DetectedLine, FRETTED_NOTE_ANCHOR_RATIO, NoteMarker, NoteMarkerTracker,
    SelectedTrackingResult, build_grid_from_lines,
)
from vision.fretboard_motion import HybridTrackingFrame
from vision.hand_tracking import HandTrackingResult


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


def calibrated_render_fixture(mode="selected_lines_tracking", *, hand_mask=None):
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    lines = tuple(DetectedLine("nut" if index == 0 else "fret", (40., y), (160., y), .9)
                  for index, y in enumerate((40., 90., 135., 175.)))
    grid = build_grid_from_lines(lines[0], lines[1:])
    assert grid is not None
    # A finger edge was incorrectly classified far outside the actual board.
    poison = DetectedLine("fret", (235., 20.), (305., 210.), .99)
    tracking = HybridTrackingFrame(SelectedTrackingResult(grid, (poison,), mode),
                                   (poison,), (), None, hand_mask)
    diagnostics = EdgeRefinementResult(grid=grid, refined_fret_indices=(0, 1, 2),
                                       confirmed_fret_lines=(poison,) * 3)
    return frame, grid, tracking, diagnostics


def render_calibrated(frame, tracking, diagnostics, *, selected_note="없음", edge_only=False):
    tracker = Mock(reference_fret_index=3)
    hand_tracker = Mock()
    hand_tracker.process.return_value = HandTrackingResult((), (), 20., True)
    with patch("tools.fretboard_detection_viewer.refine_grid_with_edge_lines_diagnostics",
               return_value=diagnostics) as refine:
        rendered, analysis = analyze_selected_lines_frame(
            frame, tracking.detected_lines, None, tracker, NoteMarkerTracker(), hand_tracker,
            selected_note, False, show_edge_guides=True, edge_supported_notes_only=edge_only,
            tracking_frame=tracking,
        )
    return rendered, analysis, refine


def test_calibrated_render_never_draws_poisoned_raw_edge_or_detector_coordinates():
    frame, grid, tracking, diagnostics = calibrated_render_fixture()
    rendered, analysis, _ = render_calibrated(frame, tracking, diagnostics)
    expected = draw_calibrated_guides(draw_reference_quad(frame, grid), grid)
    np.testing.assert_array_equal(rendered, expected)
    assert not rendered[:, 200:].any()
    assert analysis.stable_grid is grid
    assert rendered[90, 100].any()  # Model fret remains visible despite bad raw edge.


@pytest.mark.parametrize("mode", ["selected_lines_hold", "selected_lines_lost"])
def test_uncertain_calibrated_pose_hides_all_stale_guides_and_detections(mode):
    frame, _, tracking, diagnostics = calibrated_render_fixture(mode)
    rendered, analysis, refine = render_calibrated(frame, tracking, diagnostics, selected_note="미")
    np.testing.assert_array_equal(rendered, frame)
    assert analysis.note_markers == ()
    assert analysis.detected_lines == ()
    assert analysis.stable_grid is None
    refine.assert_not_called()


def test_calibrated_open_guides_survive_hand_mask_and_missing_raw_string_edges():
    mask = np.full((240, 320), 255, dtype=np.uint8)
    frame, _, tracking, diagnostics = calibrated_render_fixture(hand_mask=mask)
    _, analysis, _ = render_calibrated(frame, tracking, diagnostics, selected_note="미", edge_only=True)
    open_markers = tuple(marker for marker in analysis.note_markers if marker.is_open)
    assert len(open_markers) == 2
    assert {marker.string_index for marker in open_markers} == {0, 5}


def test_calibrated_guides_draw_all_six_model_strings_and_nut():
    frame, grid, _, _ = calibrated_render_fixture()
    rendered = draw_calibrated_guides(frame, grid)
    for point in grid.string_crossings[0]:
        assert rendered[65, round(point[0])].any()
    assert rendered[40, 100].any()
    assert not rendered[:35].any()


if __name__ == "__main__":
    unittest.main()
