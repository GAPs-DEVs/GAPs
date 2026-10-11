"""Bound optional alignment work while retaining current-frame pose evidence."""
from unittest.mock import Mock

import numpy as np

from test_fretboard_motion import scene, make_tracker, translation, warp
from vision.fretboard_motion import grid_quad


def test_verified_motion_refines_periodically_without_accumulating_fret_drift():
    frame, grid = scene()
    tracker = make_tracker(frame, grid)
    tracker.reference.refine = Mock(wraps=tracker.reference.refine)
    for index in range(30):
        matrix = translation(index * .6, index * .3)
        result = tracker.update(warp(frame, matrix), lambda roi: (), timestamp=index / 30)
        assert result.result.tracking_mode == 'selected_lines_motion'
        np.testing.assert_allclose(
            grid_quad(result.result.stable_grid), grid_quad(grid) + (index * .6, index * .3), atol=1.,
        )
    assert tracker.reference.refine.call_count == 6
    # The intervening frames still must hide immediately on missing evidence.
    result = tracker.update(np.zeros_like(frame), lambda roi: (), timestamp=1.)
    assert result.homography is None
    assert result.result.tracking_mode == 'selected_lines_hold'


def test_single_valid_flow_path_still_gets_immediate_refinement():
    frame, grid = scene()
    tracker = make_tracker(frame, grid)
    tracker.anchor_motion.estimate = Mock(return_value=None)
    tracker.reference.refine = Mock(wraps=tracker.reference.refine)
    result = tracker.update(warp(frame, translation(2, 1)), lambda roi: ())
    assert result.homography is not None
    tracker.reference.refine.assert_called_once()


def test_descriptor_recovery_refines_immediately_off_the_periodic_cadence():
    frame, grid = scene()
    tracker = make_tracker(frame, grid)
    tracker.motion.estimate = Mock(return_value=None)
    tracker.anchor_motion.estimate = Mock(return_value=None)
    tracker.reference.refine = Mock(wraps=tracker.reference.refine)
    result = tracker.update(warp(frame, translation(3, 1)), lambda roi: ())
    assert result.result.tracking_mode == 'selected_lines_reacquired'
    tracker.reference.refine.assert_called_once()
