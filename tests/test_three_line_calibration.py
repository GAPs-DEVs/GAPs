"""Ground truth is a physical tapered neck, not the model under test."""
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.features.video import PracticeVideoWorker
from vision.fretboard_calibration import build_calibrated_grid
from vision.fretboard_mapping import DetectedLine, build_grid_from_reference_lines


def physical_line(index, matrix=None):
    distance = 1 - 2 ** (-index / 12)
    width = 90 + 48 * distance
    points = np.asarray(((180 - width / 2, 50 + 700 * distance),
                         (180 + width / 2, 50 + 700 * distance)), dtype=np.float64)
    if matrix is not None:
        points = cv2.perspectiveTransform(points[:, None], matrix).reshape(-1, 2)
    return DetectedLine('fret' if index else 'nut', tuple(points[0]), tuple(points[1]), 1.)


@pytest.mark.parametrize('last,middle', [(7, 3), (12, 5), (20, 7), (24, 9)])
@pytest.mark.parametrize('matrix', [None, np.asarray(((.96, .3, 20), (-.12, 1.05, 10), (.0005, .0008, 1)))])
def test_every_fret_matches_tapered_physical_neck_even_under_perspective(last, middle, matrix):
    grid = build_calibrated_grid(physical_line(0, matrix), physical_line(middle, matrix), middle,
                                 physical_line(last, matrix), last)
    for n, actual in enumerate(grid.ordered_fret_lines, 1):
        expected = physical_line(n, matrix)
        # Screen orientation reverses endpoints for this tilted camera view.
        error = min(np.max(np.abs(np.asarray((actual.p0, actual.p1)) - (expected.p0, expected.p1))),
                    np.max(np.abs(np.asarray((actual.p0, actual.p1)) - (expected.p1, expected.p0))))
        assert error < 1e-4


def test_twelve_fret_reference_no_longer_shifts_fifth_fret_by_twenty_pixels():
    nut, middle, last = physical_line(0), physical_line(5), physical_line(12)
    old = build_grid_from_reference_lines(nut, last, 12)
    assert abs(old.ordered_fret_lines[4].p0[1] - middle.p0[1]) > 20
    new = build_calibrated_grid(nut, middle, 5, last, 12)
    assert abs(new.ordered_fret_lines[4].p0[1] - middle.p0[1]) < 1e-4


def test_clipped_middle_wire_does_not_shrink_the_neck():
    middle = physical_line(5)
    clipped = DetectedLine('fret', (170, middle.p0[1]), (190, middle.p1[1]), 1.)
    full = build_calibrated_grid(physical_line(0), middle, 5, physical_line(12), 12)
    partial = build_calibrated_grid(physical_line(0), clipped, 5, physical_line(12), 12)
    np.testing.assert_allclose(partial.string_crossings, full.string_crossings, atol=1e-5)


@pytest.mark.parametrize('middle_index,last_index', [(0, 12), (12, 12), (13, 12), (5, 0)])
def test_invalid_or_unset_numbers_are_rejected(middle_index, last_index):
    with pytest.raises(ValueError):
        build_calibrated_grid(physical_line(0), physical_line(5), middle_index, physical_line(12), last_index)


@pytest.mark.parametrize('middle', [physical_line(0), physical_line(15),
    DetectedLine('fret', (170, 120), (170, 220), 1),
    DetectedLine('fret', (np.nan, 120), (180, 120), 1)])
def test_invalid_middle_selection_is_rejected(middle):
    with pytest.raises(ValueError):
        build_calibrated_grid(physical_line(0), middle, 5, physical_line(12), 12)


def test_worker_preserves_calibrated_geometry_instead_of_rebuilding_rectangle():
    grid = build_calibrated_grid(physical_line(0), physical_line(5), 5, physical_line(12), 12)
    worker = PracticeVideoWorker(Path('unused.pt'), 0, .25, 960, None, False, False, False)
    worker.latest_frame_bgr = np.zeros((500, 400, 3), dtype=np.uint8)
    worker.apply_calibrated_selection(grid, 12)
    worker._apply_pending_calibration()
    assert worker.hybrid_tracker.anchor_grid is grid
    assert worker.selected_line_tracker.last_stable_grid is grid
    assert worker.selected_line_tracker.reference_fret_index == 12
    assert worker.pending_calibrated_selection is None
    assert not worker.selection_active
