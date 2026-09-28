from dataclasses import replace

import cv2
import numpy as np
import pytest

from vision.fretboard_mapping import (
    DetectedLine, NoteMarker, NoteMarkerTracker, build_grid_from_reference_lines, transform_grid,
)
from vision.marker_visibility import filter_visible_markers, open_string_marker


def board():
    grid = build_grid_from_reference_lines(
        DetectedLine("nut", (100.4, 75.8), (219.6, 100.3), .95),
        DetectedLine("fret", (65.2, 425.1), (246.9, 414.3), .95),
        reference_fret_index=7,
    )
    assert grid is not None
    return grid


@pytest.mark.parametrize("angle", [-35, -12, 0, 19, 35])
def test_all_open_strings_remain_visible_on_tilted_nut_boundary(angle):
    rotation = np.vstack((cv2.getRotationMatrix2D((160, 250), angle, .9), (0, 0, 1)))
    perspective = np.array([[1, .02, 40], [-.03, 1, 0], [.00015, -.0001, 1.]])
    grid = transform_grid(board(), perspective @ rotation)
    nut_delta = np.subtract(grid.nut_line.p1, grid.nut_line.p0)
    outside = np.array([nut_delta[1], -nut_delta[0]])
    outside /= np.linalg.norm(outside)
    # Boundary rounding/smoothing puts all anchors on the exterior side. A
    # board-interior polygon test wrongly removes these valid open-string notes.
    markers = tuple(NoteMarker(i, 0, tuple(np.asarray(p) + outside * .01), True)
                    for i, p in enumerate(grid.string_crossings[0]))
    shown = filter_visible_markers(markers, grid, (600, 500, 3))
    assert len(shown) == 6
    for marker in shown:
        expected = open_string_marker(grid, marker.string_index)
        np.testing.assert_allclose(marker.point_xy, expected.point_xy, atol=1e-8)
        assert marker.is_open and marker.fret_index == 0


def test_marker_deadband_cannot_leave_open_note_behind_moving_nut():
    original = board()
    tracker = NoteMarkerTracker(deadband_px=3)
    tracker.update((open_string_marker(original, 2),))
    current = transform_grid(original, np.array([[1., 0, 0], [0, 1, 2], [0, 0, 1]]))
    expected = open_string_marker(current, 2)
    lagged = tracker.update((expected,))
    assert lagged[0].point_xy != expected.point_xy
    visible = filter_visible_markers(lagged, current, (600, 500, 3))
    assert visible == (expected,)


def test_occluded_open_guide_can_be_rendered_from_valid_pose_without_shifting_to_fret_one():
    grid = board()
    marker = open_string_marker(grid, 4)
    mask = np.full((600, 500), 255, dtype=np.uint8)
    assert filter_visible_markers((marker,), grid, mask.shape, mask) == ()
    assert filter_visible_markers((marker,), grid, mask.shape, mask, hide_occluded=False) == (marker,)
    assert marker.point_xy != grid.string_crossings[1][4]


def test_hand_over_other_frets_does_not_hide_open_note():
    grid = board()
    marker = open_string_marker(grid, 2)
    mask = np.zeros((600, 500), dtype=np.uint8)
    mask[180:400, :] = 255
    assert filter_visible_markers((marker,), grid, mask.shape, mask) == (marker,)


def test_open_markers_outside_current_frame_are_hidden_even_if_old_point_was_visible():
    original = board()
    marker = open_string_marker(original, 2)
    moved = transform_grid(original, np.array([[1., 0, 0], [0, 1, -150], [0, 0, 1]]))
    assert filter_visible_markers((marker,), moved, (600, 500)) == ()


def test_open_marker_requires_valid_string_and_consistent_nut_geometry():
    grid = board()
    marker = open_string_marker(grid, 0)
    invalid = (replace(marker, string_index=-1), replace(marker, string_index=6),
               replace(marker, fret_index=1), replace(marker, is_open=False))
    assert filter_visible_markers(invalid, grid, (600, 500)) == ()
    degenerate = replace(grid, nut_line=replace(grid.nut_line, p1=grid.nut_line.p0))
    assert filter_visible_markers((marker,), degenerate, (600, 500)) == ()
    invalid_crossings = replace(grid, string_crossings=(((100., 500.),) * 6, *grid.string_crossings[1:]))
    assert filter_visible_markers((marker,), invalid_crossings, (600, 500)) == ()


def test_fretted_markers_still_require_correct_interval_even_when_occlusion_is_ignored():
    grid = board()
    rows = np.asarray(grid.string_crossings)
    good = NoteMarker(2, 3, tuple((rows[2, 2] + rows[3, 2]) / 2), False)
    wrong = replace(good, point_xy=tuple((rows[1, 2] + rows[2, 2]) / 2))
    mask = np.full((600, 500), 255, dtype=np.uint8)
    assert filter_visible_markers((good, wrong), grid, mask.shape, mask, hide_occluded=False) == (good,)


def test_subpixel_screen_edge_is_valid_for_mask_sampling():
    grid = board()
    marker = open_string_marker(grid, 0)
    shifted = transform_grid(grid, np.array([[1., 0, 499.8 - marker.point_xy[0]], [0, 1, 0], [0, 0, 1]]))
    mask = np.zeros((600, 500), dtype=np.uint8)
    shown = filter_visible_markers((marker,), shifted, mask.shape, mask)
    assert len(shown) == 1
    assert shown[0].point_xy[0] == pytest.approx(499.8, abs=1e-4)
