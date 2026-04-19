from __future__ import annotations

import unittest

import cv2
import numpy as np

from fretboard_edge_refinement import refine_grid_with_edge_lines, refine_grid_with_edge_lines_diagnostics
from fretboard_mapping import (
    DetectedLine,
    FRETTED_NOTE_ANCHOR_RATIO,
    FretboardTracker,
    NoteMarker,
    NoteMarkerTracker,
    SelectedLineTracker,
    build_center_test_note_markers,
    build_fretboard_grid,
    build_grid_from_indexed_fret_lines,
    build_grid_from_lines,
    build_grid_from_reference_lines,
    build_note_markers,
    infer_selected_reference_fret_index,
    scale_length_ratio,
)


def make_line(kind: str, y: float, confidence: float = 0.9) -> DetectedLine:
    return DetectedLine(kind=kind, p0=(0.0, y), p1=(100.0, y), confidence=confidence)


class FretboardMappingTests(unittest.TestCase):
    def test_note_marker_tracker_smooths_small_position_jitter(self) -> None:
        tracker = NoteMarkerTracker()
        first = tracker.update(
            [NoteMarker(string_index=1, fret_index=3, point_xy=(100.0, 120.0), is_open=False)]
        )
        second = tracker.update(
            [NoteMarker(string_index=1, fret_index=3, point_xy=(104.0, 124.0), is_open=False)]
        )

        self.assertEqual(first[0].point_xy, (100.0, 120.0))
        self.assertLess(second[0].point_xy[0], 104.0)
        self.assertLess(second[0].point_xy[1], 124.0)

    def test_note_marker_tracker_keeps_marker_identity_separate(self) -> None:
        tracker = NoteMarkerTracker()
        tracker.update(
            [
                NoteMarker(string_index=0, fret_index=2, point_xy=(40.0, 60.0), is_open=False),
                NoteMarker(string_index=1, fret_index=2, point_xy=(70.0, 60.0), is_open=False),
            ]
        )
        updated = tracker.update(
            [
                NoteMarker(string_index=0, fret_index=2, point_xy=(44.0, 64.0), is_open=False),
                NoteMarker(string_index=1, fret_index=2, point_xy=(74.0, 64.0), is_open=False),
            ]
        )

        self.assertLess(updated[0].point_xy[0], updated[1].point_xy[0])

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

    def test_fretted_marker_uses_fret_side_anchor(self) -> None:
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
        self.assertGreater(FRETTED_NOTE_ANCHOR_RATIO, 0.5)
        self.assertAlmostEqual(d_string_fret_two.point_xy[1], expected_y, places=4)

    def test_c_note_maps_to_a_string_third_fret(self) -> None:
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

        markers = build_note_markers(grid, "도")
        marker_pairs = {(marker.string_index, marker.fret_index) for marker in markers}

        self.assertIn((1, 3), marker_pairs)
        self.assertNotIn((1, 2), marker_pairs)

    def test_build_fretboard_grid_reuses_prior_grid_when_frets_are_fully_occluded(self) -> None:
        prior_grid = build_grid_from_lines(
            make_line("nut", 0.0),
            [
                make_line("fret", 20.0),
                make_line("fret", 38.0),
                make_line("fret", 54.0),
            ],
        )

        grid = build_fretboard_grid(
            [make_line("nut", 6.0)],
            prior_grid=prior_grid,
        )

        self.assertIs(grid, prior_grid)

    def test_open_string_marker_is_on_nut_line(self) -> None:
        grid = build_grid_from_lines(make_line("nut", 0.0), [make_line("fret", 20.0)])

        markers = build_note_markers(grid, "미")
        open_markers = [marker for marker in markers if marker.is_open]

        self.assertEqual(len(open_markers), 2)
        for marker in open_markers:
            self.assertAlmostEqual(marker.point_xy[1], 0.0, places=4)

    def test_center_test_marker_uses_fretboard_midline(self) -> None:
        grid = build_grid_from_lines(
            make_line("nut", 0.0),
            [
                make_line("fret", 20.0),
                make_line("fret", 40.0),
                make_line("fret", 60.0),
                make_line("fret", 80.0),
                make_line("fret", 100.0),
            ],
        )

        markers = build_center_test_note_markers(grid, "미")

        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0].fret_index, 4)
        self.assertAlmostEqual(markers[0].point_xy[0], 50.0, places=4)
        self.assertAlmostEqual(markers[0].point_xy[1], 70.0, places=4)

    def test_center_test_marker_returns_empty_when_fret_not_visible(self) -> None:
        grid = build_grid_from_lines(make_line("nut", 0.0), [make_line("fret", 20.0)])
        markers = build_center_test_note_markers(grid, "높은 도")
        self.assertEqual(markers, ())

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

    def test_build_grid_from_reference_lines_reconstructs_frets(self) -> None:
        grid = build_grid_from_reference_lines(
            nut_line=DetectedLine("nut", (10.0, 0.0), (110.0, 0.0), 1.0),
            reference_fret_line=DetectedLine("fret", (10.0, 120.0), (110.0, 120.0), 1.0),
            reference_fret_index=12,
        )

        self.assertIsNotNone(grid)
        assert grid is not None
        self.assertEqual(len(grid.ordered_fret_lines), 12)

        third_fret = grid.ordered_fret_lines[2]
        expected_y = 120.0 * scale_length_ratio(3) / scale_length_ratio(12)
        self.assertAlmostEqual(third_fret.p0[1], expected_y, places=4)
        self.assertAlmostEqual(third_fret.p1[1], expected_y, places=4)

    def test_build_grid_from_indexed_fret_lines_preserves_detected_frets(self) -> None:
        fret_one = DetectedLine("fret", (4.0, 20.0), (96.0, 22.0), 0.91)
        fret_three = DetectedLine("fret", (2.0, 58.0), (98.0, 62.0), 0.89)

        grid = build_grid_from_indexed_fret_lines(
            nut_line=DetectedLine("nut", (0.0, 0.0), (100.0, 2.0), 1.0),
            indexed_fret_lines=((1, fret_one), (3, fret_three)),
            max_fret_index=3,
        )

        self.assertIsNotNone(grid)
        assert grid is not None
        self.assertEqual(grid.ordered_fret_lines[0], fret_one)
        self.assertEqual(grid.ordered_fret_lines[2], fret_three)

        expected_y = 60.0 * scale_length_ratio(2) / scale_length_ratio(3)
        interpolated_mid_y = (grid.ordered_fret_lines[1].p0[1] + grid.ordered_fret_lines[1].p1[1]) * 0.5
        self.assertAlmostEqual(interpolated_mid_y, expected_y, delta=1.0)

    def test_build_fretboard_grid_preserves_detected_fret_positions_and_refines_edges(self) -> None:
        reference_grid = build_grid_from_reference_lines(
            nut_line=DetectedLine("nut", (0.0, 0.0), (100.0, 0.0), 1.0),
            reference_fret_line=DetectedLine("fret", (0.0, 80.0), (100.0, 80.0), 1.0),
            reference_fret_index=4,
        )
        assert reference_grid is not None

        noisy_detected_lines = [DetectedLine("nut", (0.0, 0.0), (100.0, 0.0), 0.99)]
        vertical_offsets = (0.8, -0.3, 2.2, -0.8)
        for fret_index, offset in enumerate(vertical_offsets, start=1):
            expected_line = reference_grid.ordered_fret_lines[fret_index - 1]
            noisy_detected_lines.append(
                DetectedLine(
                    "fret",
                    (expected_line.p0[0] + 1.0, expected_line.p0[1] + offset),
                    (expected_line.p1[0] - 2.0, expected_line.p1[1] + offset * 0.5),
                    0.9,
                )
            )

        grid = build_fretboard_grid(noisy_detected_lines)
        self.assertIsNotNone(grid)
        assert grid is not None

        expected_third = reference_grid.ordered_fret_lines[2]
        noisy_third = noisy_detected_lines[3]
        noisy_y = (noisy_third.p0[1] + noisy_third.p1[1]) * 0.5
        fitted_y = (grid.ordered_fret_lines[2].p0[1] + grid.ordered_fret_lines[2].p1[1]) * 0.5

        self.assertAlmostEqual(fitted_y, noisy_y, delta=1.5)
        self.assertAlmostEqual(grid.ordered_fret_lines[2].p0[0], 0.0, delta=3.0)
        self.assertAlmostEqual(grid.ordered_fret_lines[2].p1[0], 100.0, delta=3.0)

    def test_build_fretboard_grid_uses_prior_grid_to_keep_occluded_middle_fret(self) -> None:
        reference_grid = build_grid_from_reference_lines(
            nut_line=DetectedLine("nut", (0.0, 0.0), (100.0, 0.0), 1.0),
            reference_fret_line=DetectedLine("fret", (0.0, 80.0), (100.0, 80.0), 1.0),
            reference_fret_index=5,
        )
        assert reference_grid is not None

        shifted_nut = DetectedLine("nut", (4.0, 6.0), (104.0, 6.0), 0.98)
        visible_indices = (1, 2, 4, 5)
        detected_lines = [shifted_nut]
        for fret_index in visible_indices:
            source_line = reference_grid.ordered_fret_lines[fret_index - 1]
            detected_lines.append(
                DetectedLine(
                    "fret",
                    (source_line.p0[0] + 4.0, source_line.p0[1] + 6.0),
                    (source_line.p1[0] + 4.0, source_line.p1[1] + 6.0),
                    0.92,
                )
            )

        grid = build_fretboard_grid(detected_lines, prior_grid=reference_grid)
        self.assertIsNotNone(grid)
        assert grid is not None
        self.assertEqual(len(grid.ordered_fret_lines), 5)

        for output_index, fret_index in enumerate(visible_indices):
            expected = detected_lines[output_index + 1]
            actual = grid.ordered_fret_lines[fret_index - 1]
            self.assertAlmostEqual(actual.p0[1], expected.p0[1], delta=1.0)
            self.assertAlmostEqual(actual.p1[1], expected.p1[1], delta=1.0)

        hidden_third = grid.ordered_fret_lines[2]
        expected_hidden_y = (
            (reference_grid.ordered_fret_lines[2].p0[1] + reference_grid.ordered_fret_lines[2].p1[1]) * 0.5
            + 6.0
        )
        hidden_mid_y = (hidden_third.p0[1] + hidden_third.p1[1]) * 0.5
        self.assertAlmostEqual(hidden_mid_y, expected_hidden_y, delta=2.0)

    def test_build_fretboard_grid_extends_short_detected_fret_using_other_frets(self) -> None:
        prior_grid = build_grid_from_lines(
            make_line("nut", 0.0),
            [
                make_line("fret", 20.0),
                make_line("fret", 38.0),
                make_line("fret", 54.0),
            ],
        )

        grid = build_fretboard_grid(
            [
                make_line("nut", 0.0),
                make_line("fret", 20.0),
                DetectedLine("fret", (24.0, 38.0), (76.0, 38.0), 0.92),
                make_line("fret", 54.0),
            ],
            prior_grid=prior_grid,
        )

        self.assertIsNotNone(grid)
        assert grid is not None
        extended_middle = grid.ordered_fret_lines[1]
        self.assertLess(extended_middle.p0[0], 10.0)
        self.assertGreater(extended_middle.p1[0], 90.0)

    def test_infer_selected_reference_fret_index_uses_clicked_last_visible_fret(self) -> None:
        reference_grid = build_grid_from_reference_lines(
            nut_line=DetectedLine("nut", (0.0, 0.0), (100.0, 0.0), 1.0),
            reference_fret_line=DetectedLine("fret", (0.0, 80.0), (100.0, 80.0), 1.0),
            reference_fret_index=4,
        )
        assert reference_grid is not None

        detected_lines = [DetectedLine("nut", (0.0, 0.0), (100.0, 0.0), 0.99)]
        for fret_line in reference_grid.ordered_fret_lines:
            detected_lines.append(
                DetectedLine(
                    "fret",
                    (fret_line.p0[0] + 1.0, fret_line.p0[1] + 0.5),
                    (fret_line.p1[0] - 1.0, fret_line.p1[1] - 0.5),
                    0.9,
                )
            )

        inferred_index = infer_selected_reference_fret_index(
            detected_lines[0],
            detected_lines[-1],
            detected_lines,
        )

        self.assertEqual(inferred_index, 4)

    def test_selected_line_tracker_matches_updated_yolo_lines(self) -> None:
        tracker = SelectedLineTracker(max_history=5)
        tracker.initialize_from_lines(
            DetectedLine("nut", (40.0, 30.0), (40.0, 190.0), 1.0),
            DetectedLine("fret", (160.0, 30.0), (160.0, 190.0), 1.0),
            reference_fret_index=12,
        )

        result = tracker.update(
            [
                DetectedLine("nut", (46.0, 34.0), (46.0, 194.0), 0.95),
                DetectedLine("fret", (166.0, 34.0), (166.0, 194.0), 0.93),
                DetectedLine("fret", (120.0, 34.0), (120.0, 194.0), 0.85),
            ]
        )
        self.assertIsNotNone(result.stable_grid)
        assert result.stable_grid is not None
        self.assertEqual(result.tracking_mode, "selected_lines_tracking")
        self.assertEqual(tracker.reference_fret_index, 12)
        self.assertEqual(len(result.stable_grid.ordered_fret_lines), 12)
        self.assertAlmostEqual(result.stable_grid.nut_line.p0[0], 46.0, delta=1.0)
        self.assertLess(result.stable_grid.nut_line.p0[1], 34.5)
        self.assertGreater(result.stable_grid.nut_line.p1[1], 190.0)
        self.assertAlmostEqual(result.stable_grid.ordered_fret_lines[-1].p0[0], 166.0, delta=3.0)

    def test_selected_line_tracker_uses_current_last_visible_fret_as_reference(self) -> None:
        tracker = SelectedLineTracker(max_history=5)
        tracker.initialize_from_lines(
            DetectedLine("nut", (40.0, 30.0), (40.0, 190.0), 1.0),
            DetectedLine("fret", (160.0, 30.0), (160.0, 190.0), 1.0),
            reference_fret_index=12,
        )

        result = tracker.update(
            [
                DetectedLine("nut", (42.0, 32.0), (42.0, 192.0), 0.95),
                DetectedLine("fret", (72.0, 32.0), (72.0, 192.0), 0.91),
                DetectedLine("fret", (102.0, 32.0), (102.0, 192.0), 0.90),
                DetectedLine("fret", (132.0, 32.0), (132.0, 192.0), 0.89),
            ]
        )

        self.assertIsNotNone(result.stable_grid)
        assert result.stable_grid is not None
        self.assertEqual(result.tracking_mode, "selected_lines_tracking")
        self.assertEqual(len(result.stable_grid.ordered_fret_lines), 12)
        self.assertEqual(tracker.reference_fret_index, 12)
        self.assertGreater(result.stable_grid.ordered_fret_lines[-1].p0[0], 145.0)

    def test_selected_line_tracker_holds_last_grid_when_detection_temporarily_drops(self) -> None:
        tracker = SelectedLineTracker(max_history=5, hold_frames=4, clear_frames=6)
        tracker.initialize_from_lines(
            DetectedLine("nut", (20.0, 10.0), (20.0, 150.0), 1.0),
            DetectedLine("fret", (120.0, 10.0), (120.0, 150.0), 1.0),
            reference_fret_index=12,
        )

        result = tracker.update(primary_detected_lines=(), fallback_detected_lines=None)

        self.assertEqual(result.tracking_mode, "selected_lines_hold")
        self.assertIsNotNone(result.stable_grid)
        assert result.stable_grid is not None
        self.assertEqual(len(result.stable_grid.ordered_fret_lines), 12)

    def test_selected_line_tracker_reacquires_from_fallback_full_frame(self) -> None:
        tracker = SelectedLineTracker(max_history=5)
        tracker.initialize_from_lines(
            DetectedLine("nut", (40.0, 30.0), (40.0, 190.0), 1.0),
            DetectedLine("fret", (160.0, 30.0), (160.0, 190.0), 1.0),
            reference_fret_index=12,
        )

        result = tracker.update(
            primary_detected_lines=(),
            fallback_detected_lines=[
                DetectedLine("nut", (48.0, 36.0), (48.0, 196.0), 0.96),
                DetectedLine("fret", (168.0, 36.0), (168.0, 196.0), 0.94),
                DetectedLine("fret", (118.0, 36.0), (118.0, 196.0), 0.80),
            ],
        )

        self.assertEqual(result.tracking_mode, "selected_lines_reacquired")
        self.assertIsNotNone(result.stable_grid)
        assert result.stable_grid is not None
        self.assertEqual(tracker.reference_fret_index, 12)
        self.assertEqual(len(result.stable_grid.ordered_fret_lines), 12)
        self.assertAlmostEqual(result.stable_grid.nut_line.p0[0], 48.0, delta=1.0)
        self.assertAlmostEqual(result.stable_grid.ordered_fret_lines[-1].p0[0], 168.0, delta=3.0)

    def test_selected_line_tracker_projects_last_fret_from_visible_support_frets(self) -> None:
        initial_nut = DetectedLine("nut", (40.0, 30.0), (40.0, 190.0), 1.0)
        initial_reference = DetectedLine("fret", (160.0, 30.0), (160.0, 190.0), 1.0)
        tracker = SelectedLineTracker(max_history=5)
        tracker.initialize_from_lines(
            initial_nut,
            initial_reference,
            reference_fret_index=12,
        )

        base_grid = build_grid_from_reference_lines(
            nut_line=initial_nut,
            reference_fret_line=initial_reference,
            reference_fret_index=12,
        )
        assert base_grid is not None

        shifted_nut = DetectedLine("nut", (48.0, 30.0), (48.0, 190.0), 0.96)
        support_indices = (4, 7, 9)
        detected_lines = [shifted_nut]
        for fret_index in support_indices:
            source_line = base_grid.ordered_fret_lines[fret_index - 1]
            detected_lines.append(
                DetectedLine(
                    "fret",
                    (source_line.p0[0] + 8.0, source_line.p0[1]),
                    (source_line.p1[0] + 8.0, source_line.p1[1]),
                    0.91,
                )
            )

        result = tracker.update(detected_lines)

        self.assertEqual(result.tracking_mode, "selected_lines_tracking")
        self.assertIsNotNone(result.stable_grid)
        assert result.stable_grid is not None
        self.assertEqual(len(result.stable_grid.ordered_fret_lines), 12)
        self.assertAlmostEqual(result.stable_grid.nut_line.p0[0], 48.0, delta=1.0)
        expected_reference_x = base_grid.ordered_fret_lines[-1].p0[0] + 8.0
        self.assertAlmostEqual(result.stable_grid.ordered_fret_lines[-1].p0[0], expected_reference_x, delta=4.0)

    def test_selected_line_tracker_restores_occluded_endpoints_using_visible_frets(self) -> None:
        initial_nut = DetectedLine("nut", (40.0, 30.0), (40.0, 190.0), 1.0)
        initial_reference = DetectedLine("fret", (160.0, 30.0), (160.0, 190.0), 1.0)
        tracker = SelectedLineTracker(max_history=5)
        tracker.initialize_from_lines(
            initial_nut,
            initial_reference,
            reference_fret_index=12,
        )

        base_grid = build_grid_from_reference_lines(
            nut_line=initial_nut,
            reference_fret_line=initial_reference,
            reference_fret_index=12,
        )
        assert base_grid is not None

        shifted_support_lines = []
        for fret_number in (3, 6, 9):
            support_line = base_grid.ordered_fret_lines[fret_number - 1]
            shifted_support_lines.append(
                DetectedLine(
                    "fret",
                    (support_line.p0[0] + 6.0, support_line.p0[1] + 4.0),
                    (support_line.p1[0] + 6.0, support_line.p1[1] + 4.0),
                    0.92,
                )
            )

        result = tracker.update(
            [
                DetectedLine("nut", (46.0, 60.0), (46.0, 170.0), 0.96),
                *shifted_support_lines,
                DetectedLine("fret", (166.0, 56.0), (166.0, 174.0), 0.94),
            ]
        )

        self.assertEqual(result.tracking_mode, "selected_lines_tracking")
        self.assertIsNotNone(result.stable_grid)
        assert result.stable_grid is not None
        self.assertEqual(tracker.reference_fret_index, 12)
        self.assertEqual(len(result.stable_grid.ordered_fret_lines), 12)
        self.assertLess(result.stable_grid.nut_line.p0[1], 50.0)
        self.assertGreater(result.stable_grid.nut_line.p1[1], 180.0)
        self.assertLess(result.stable_grid.ordered_fret_lines[-1].p0[1], 50.0)
        self.assertGreater(result.stable_grid.ordered_fret_lines[-1].p1[1], 180.0)

        shifted_ninth_fret = shifted_support_lines[-1]
        restored_ninth_fret = result.stable_grid.ordered_fret_lines[8]
        self.assertAlmostEqual(restored_ninth_fret.p0[0], shifted_ninth_fret.p0[0], delta=4.0)
        self.assertAlmostEqual(restored_ninth_fret.p1[0], shifted_ninth_fret.p1[0], delta=4.0)

    def test_edge_refinement_moves_string_crossings_to_visible_string_lines(self) -> None:
        frame = np.zeros((180, 140, 3), dtype=np.uint8)
        grid = build_grid_from_lines(
            DetectedLine("nut", (10.0, 20.0), (110.0, 20.0), 1.0),
            [
                DetectedLine("fret", (10.0, 60.0), (110.0, 60.0), 1.0),
                DetectedLine("fret", (10.0, 100.0), (110.0, 100.0), 1.0),
                DetectedLine("fret", (10.0, 140.0), (110.0, 140.0), 1.0),
            ],
        )

        string_x_positions = [18, 34, 51, 69, 86, 103]
        for x_position in string_x_positions:
            cv2.line(frame, (x_position, 20), (x_position, 140), (255, 255, 255), 1, cv2.LINE_AA)
        for y_position in (20, 60, 100, 140):
            cv2.line(frame, (10, y_position), (110, y_position), (190, 190, 190), 1, cv2.LINE_AA)

        refined_grid = refine_grid_with_edge_lines(frame, grid)

        self.assertIsNotNone(refined_grid)
        assert refined_grid is not None
        middle_fret_crossings = refined_grid.string_crossings[2]
        for crossing, expected_x in zip(middle_fret_crossings, string_x_positions):
            self.assertAlmostEqual(crossing[0], expected_x, delta=3.0)

    def test_edge_refinement_keeps_original_grid_when_edges_are_missing(self) -> None:
        frame = np.zeros((180, 140, 3), dtype=np.uint8)
        grid = build_grid_from_lines(
            DetectedLine("nut", (10.0, 20.0), (110.0, 20.0), 1.0),
            [
                DetectedLine("fret", (10.0, 60.0), (110.0, 60.0), 1.0),
                DetectedLine("fret", (10.0, 100.0), (110.0, 100.0), 1.0),
                DetectedLine("fret", (10.0, 140.0), (110.0, 140.0), 1.0),
            ],
        )

        refined_grid = refine_grid_with_edge_lines(frame, grid)

        self.assertIs(refined_grid, grid)

    def test_edge_refinement_reports_only_frets_with_edge_support(self) -> None:
        frame = np.zeros((180, 140, 3), dtype=np.uint8)
        grid = build_grid_from_lines(
            DetectedLine("nut", (10.0, 20.0), (110.0, 20.0), 1.0),
            [
                DetectedLine("fret", (10.0, 60.0), (110.0, 60.0), 1.0),
                DetectedLine("fret", (10.0, 100.0), (110.0, 100.0), 1.0),
                DetectedLine("fret", (10.0, 140.0), (110.0, 140.0), 1.0),
            ],
        )
        cv2.line(frame, (10, 60), (110, 60), (220, 220, 220), 1, cv2.LINE_AA)
        cv2.line(frame, (10, 140), (110, 140), (220, 220, 220), 1, cv2.LINE_AA)

        result = refine_grid_with_edge_lines_diagnostics(frame, grid)

        self.assertIn(0, result.refined_fret_indices)
        self.assertIn(2, result.refined_fret_indices)
        self.assertNotIn(1, result.refined_fret_indices)

    def test_edge_refinement_infers_missing_strings_from_partial_detections(self) -> None:
        frame = np.zeros((220, 380, 3), dtype=np.uint8)
        grid = build_grid_from_lines(
            DetectedLine("nut", (20.0, 30.0), (320.0, 30.0), 1.0),
            [
                DetectedLine("fret", (20.0, 80.0), (320.0, 80.0), 1.0),
                DetectedLine("fret", (20.0, 130.0), (320.0, 130.0), 1.0),
                DetectedLine("fret", (20.0, 180.0), (320.0, 180.0), 1.0),
            ],
        )
        fallback_nut_points = grid.string_crossings[0]
        visible_string_one_x = int(round(fallback_nut_points[1][0] + 10.0))
        visible_string_four_x = int(round(fallback_nut_points[4][0] + 10.0))
        cv2.line(frame, (visible_string_one_x, 30), (visible_string_one_x, 180), (255, 255, 255), 1, cv2.LINE_AA)
        cv2.line(frame, (visible_string_four_x, 30), (visible_string_four_x, 180), (255, 255, 255), 1, cv2.LINE_AA)

        result = refine_grid_with_edge_lines_diagnostics(frame, grid)

        self.assertEqual(result.fitted_string_indices, (1, 4))
        assert result.grid is not None
        middle_row = result.grid.string_crossings[2]
        fallback_middle_row = grid.string_crossings[2]
        self.assertGreater(middle_row[2][0], fallback_middle_row[2][0] + 4.0)
        self.assertGreater(middle_row[3][0], fallback_middle_row[3][0] + 4.0)


if __name__ == "__main__":
    unittest.main()
