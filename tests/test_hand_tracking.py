from __future__ import annotations

import unittest

from fretboard_mapping import NoteMarker
from hand_tracking import Fingertip, estimate_marker_match_radius, match_fingertips_to_markers


class HandTrackingTests(unittest.TestCase):
    def test_match_fingertips_to_markers_marks_close_points(self) -> None:
        markers = (
            NoteMarker(string_index=0, fret_index=3, point_xy=(100.0, 200.0), is_open=False),
            NoteMarker(string_index=1, fret_index=5, point_xy=(180.0, 220.0), is_open=False),
        )
        fingertips = (
            Fingertip(point_xy=(108.0, 206.0), tip_id=8, handedness="Right"),
            Fingertip(point_xy=(260.0, 260.0), tip_id=12, handedness="Left"),
        )

        matched = match_fingertips_to_markers(markers, fingertips, match_radius_px=14.0)

        self.assertEqual(matched, (0,))

    def test_match_fingertips_to_markers_returns_empty_when_far(self) -> None:
        markers = (
            NoteMarker(string_index=0, fret_index=3, point_xy=(100.0, 200.0), is_open=False),
        )
        fingertips = (
            Fingertip(point_xy=(160.0, 260.0), tip_id=8, handedness="Right"),
        )

        matched = match_fingertips_to_markers(markers, fingertips, match_radius_px=18.0)

        self.assertEqual(matched, ())

    def test_estimate_marker_match_radius_uses_marker_spacing(self) -> None:
        markers = (
            NoteMarker(string_index=0, fret_index=1, point_xy=(100.0, 100.0), is_open=False),
            NoteMarker(string_index=1, fret_index=1, point_xy=(130.0, 100.0), is_open=False),
            NoteMarker(string_index=2, fret_index=1, point_xy=(160.0, 100.0), is_open=False),
        )

        radius = estimate_marker_match_radius(markers)

        self.assertGreaterEqual(radius, 14.0)
        self.assertLessEqual(radius, 34.0)
        self.assertAlmostEqual(radius, 14.0, places=4)


if __name__ == "__main__":
    unittest.main()
