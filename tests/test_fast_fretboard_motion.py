"""Fast movement must recover while moving, with independent image evidence."""
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

from test_fretboard_motion import scene, make_tracker, translation, warp
from vision.fretboard_motion import grid_quad
from vision.fretboard_prediction import CornerMotionPredictor


def test_descriptor_recovery_succeeds_while_guitar_keeps_moving():
    frame, grid = scene()
    tracker = make_tracker(frame, grid)
    modes = []
    for index, x in enumerate((110, 122, 134, 146)):
        result = tracker.update(warp(frame, translation(x, 0)), lambda roi: (), timestamp=10 + index / 30)
        modes.append(result.result.tracking_mode)
        if result.homography is not None:
            np.testing.assert_allclose(grid_quad(result.result.stable_grid), grid_quad(grid) + (x, 0), atol=1.)
    assert modes == ['selected_lines_hold', 'selected_lines_reacquired',
                     'selected_lines_motion', 'selected_lines_motion']
    assert tracker.pending_recovery is None
    assert tracker.recovery_motion.points is None


def test_recovery_compensates_rotation_as_well_as_translation():
    frame, grid = scene()
    tracker = make_tracker(frame, grid)
    first = translation(110, 0)
    angle = np.deg2rad(3)
    second = np.asarray(((np.cos(angle), -np.sin(angle), 124),
                         (np.sin(angle), np.cos(angle), 0), (0, 0, 1)))
    assert tracker.update(warp(frame, first), lambda roi: (), timestamp=10).homography is None
    result = tracker.update(warp(frame, second), lambda roi: (), timestamp=10.04)
    assert result.result.tracking_mode == 'selected_lines_reacquired'
    expected = cv2.perspectiveTransform(grid_quad(grid)[:, None], second).reshape(-1, 2)
    np.testing.assert_allclose(grid_quad(result.result.stable_grid), expected, atol=1.)


@pytest.mark.parametrize('image_x,candidate_x', [(110, 122), (122, 110)])
def test_recovery_rejects_descriptors_disagreeing_with_actual_image_motion(image_x, candidate_x):
    frame, grid = scene()
    tracker = make_tracker(frame, grid)
    tracker.frame_index = 1
    assert not tracker._confirm_recovery(warp(frame, translation(110, 0)), translation(110, 0), 10., None)
    tracker.frame_index = 2
    # Neither a false moving candidate on a static image nor a false static
    # candidate on a moving image can pass just because descriptors repeat.
    assert not tracker._confirm_recovery(warp(frame, translation(image_x, 0)),
                                         translation(candidate_x, 0), 10.03, None)


@pytest.mark.parametrize('elapsed,frame_index,mask_all', [(1., 2, False), (.03, 3, False), (.03, 2, True)])
def test_recovery_requires_recent_consecutive_unoccluded_pixel_support(elapsed, frame_index, mask_all):
    frame, grid = scene()
    tracker = make_tracker(frame, grid)
    moved = warp(frame, translation(110, 0))
    tracker.frame_index = 1
    assert not tracker._confirm_recovery(moved, translation(110, 0), 10., None)
    tracker.frame_index = frame_index
    mask = np.full(frame.shape[:2], 255, dtype=np.uint8) if mask_all else None
    assert not tracker._confirm_recovery(moved, translation(110, 0), 10. + elapsed, mask)


def test_prediction_uses_time_between_captures():
    _, grid = scene()
    quad = grid_quad(grid)
    predictor = CornerMotionPredictor()
    predictor.observe(quad, 10.)
    predictor.observe(quad + (8, 2), 10.04)
    hint = predictor.predict_relative(10.10)
    projected = cv2.perspectiveTransform((quad + (8, 2)).astype(np.float32)[:, None], hint).reshape(-1, 2)
    np.testing.assert_allclose(projected, quad + (20, 5), atol=1e-4)
    assert predictor.predict_relative(10.04) is None
    assert predictor.predict_relative(10.5) is None


def test_prediction_models_rotation_and_resets_after_long_gap():
    _, grid = scene()
    quad = grid_quad(grid)
    angle = np.deg2rad(2)
    matrix = np.asarray(((np.cos(angle), -np.sin(angle), 12),
                         (np.sin(angle), np.cos(angle), -1), (0, 0, 1)))
    second = cv2.perspectiveTransform(quad[:, None], matrix).reshape(-1, 2)
    predictor = CornerMotionPredictor()
    predictor.observe(quad, 10.)
    predictor.observe(second, 10.04)
    hint = predictor.predict_relative(10.08)
    predicted = cv2.perspectiveTransform(second[:, None], hint).reshape(-1, 2)
    np.testing.assert_allclose(predicted, second + (second - quad), atol=1e-4)
    predictor.observe(second, 11.)
    assert predictor.predict_relative(11.04) is None
    predictor.clear()
    assert not predictor.samples


def test_prediction_is_bounded_under_rapid_acceleration():
    _, grid = scene()
    quad = grid_quad(grid)
    predictor = CornerMotionPredictor()
    predictor.observe(quad, 10.)
    predictor.observe(quad + (90, 0), 10.01)
    hint = predictor.predict_relative(10.20)
    predicted = cv2.perspectiveTransform((quad + (90, 0)).astype(np.float32)[:, None], hint).reshape(-1, 2)
    assert np.max(np.linalg.norm(predicted - (quad + (90, 0)), axis=1)) <= 65.01


def test_velocity_hint_does_not_move_dots_after_stop_reversal_or_blank():
    frame, grid = scene()
    frame = cv2.copyMakeBorder(frame, 0, 0, 0, 400, cv2.BORDER_CONSTANT)
    tracker = make_tracker(frame, grid)
    observer = Mock(wraps=tracker.motion.estimate)
    tracker.motion.estimate = observer
    times = (10., 10.04, 10.08, 10.14, 10.18, 10.22)
    for timestamp, x in zip(times, (0, 25, 50, 87.5, 87.5, 65)):
        result = tracker.update(warp(frame, translation(x, 0)), lambda roi: (), timestamp=timestamp)
        assert result.homography is not None
        np.testing.assert_allclose(grid_quad(result.result.stable_grid), grid_quad(grid) + (x, 0), atol=1.)
    assert any(call.kwargs.get('initial_homography') is not None for call in observer.call_args_list)
    blank = tracker.update(np.zeros_like(frame), lambda roi: (), timestamp=10.26)
    assert blank.homography is None
    assert not tracker.predictor.samples


def test_incorrect_hint_retries_unpredicted_flow_without_relaxing_validation():
    frame, grid = scene()
    tracker = make_tracker(frame, grid)
    tracker.predictor.predict_relative = Mock(return_value=translation(80, 0))
    actual = tracker.motion.estimate

    def fail_hint(frame, grid, mask=None, *, initial_homography=None):
        if initial_homography is not None:
            return None
        return actual(frame, grid, mask)

    tracker.motion.estimate = Mock(side_effect=fail_hint)
    result = tracker.update(warp(frame, translation(3, 0)), lambda roi: ())
    assert result.homography is not None
    assert tracker.motion.estimate.call_count == 2
    np.testing.assert_allclose(grid_quad(result.result.stable_grid), grid_quad(grid) + (3, 0), atol=1.)
