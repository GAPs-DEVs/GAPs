"""Brief blur must not blink; missing image evidence must never coast blindly."""
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.features.video import PracticeVideoWorker
from practice.score_steps import build_practice_steps
from test_fretboard_motion import scene, make_tracker, translation, warp
from vision.fretboard_motion import grid_quad


@pytest.mark.parametrize('blur', [9, 11])
def test_brief_repeated_blur_keeps_guides_visible_and_tracks_current_position(monkeypatch, blur):
    now = [10.]
    monkeypatch.setattr('vision.fretboard_motion.monotonic', lambda: now[0])
    frame, grid = scene()
    tracker = make_tracker(frame, grid)
    reference = tracker.reference.template.copy()
    original_points = tracker.coarse_motion.points.copy()
    step = build_practice_steps([{'step': 1, 'string': 3, 'fret': 4}])[0]
    worker = PracticeVideoWorker(Path('unused.pt'), 0, .25, 960, step, False, True, False)
    worker.selected_line_tracker = tracker.tracker
    modes = []
    for index in range(1, 13):
        now[0] += 1 / 30
        matrix = translation(index * 1.5, index * .4)
        current = warp(frame, matrix)
        if index % 3:
            current = cv2.GaussianBlur(current, (blur, blur), 0)
        result = tracker.update(current, lambda roi: ())
        modes.append(result.result.tracking_mode)
        assert result.homography is not None
        expected = cv2.perspectiveTransform(grid_quad(grid)[:, None], matrix).reshape(-1, 2)
        assert np.max(np.linalg.norm(grid_quad(result.result.stable_grid) - expected, axis=1)) < .8
        _, analysis = worker._analyze_selected_lines_frame(current, result)
        assert len(analysis.note_markers) == 1
        assert analysis.stable_grid is not None
    assert 'selected_lines_continuity' in modes
    np.testing.assert_array_equal(tracker.reference.template, reference)
    np.testing.assert_array_equal(tracker.coarse_motion.points, original_points)


def test_continuity_cannot_extend_its_own_time_limit(monkeypatch):
    now = [10.]
    monkeypatch.setattr('vision.fretboard_motion.monotonic', lambda: now[0])
    frame, grid = scene()
    tracker = make_tracker(frame, grid)
    current = cv2.GaussianBlur(warp(frame, translation(1.5, .4)), (11, 11), 0)
    now[0] += .1
    assert tracker.update(current, lambda roi: ()).result.tracking_mode == 'selected_lines_continuity'
    assert tracker.last_verified_at == 10.
    now[0] = 10.251
    result = tracker.update(current, lambda roi: ())
    assert result.result.tracking_mode == 'selected_lines_hold'
    assert result.homography is None


def test_continuity_has_frame_limit_even_when_wall_clock_does_not_advance(monkeypatch):
    monkeypatch.setattr('vision.fretboard_motion.monotonic', lambda: 10.)
    frame, grid = scene()
    tracker = make_tracker(frame, grid)
    current = cv2.GaussianBlur(frame, (11, 11), 0)
    for _ in range(6):
        assert tracker.update(current, lambda roi: ()).result.tracking_mode == 'selected_lines_continuity'
    assert tracker.update(current, lambda roi: ()).result.tracking_mode == 'selected_lines_hold'


@pytest.mark.parametrize('masked', [False, True])
def test_complete_loss_hides_immediately_and_coarse_evidence_cannot_reacquire(monkeypatch, masked):
    monkeypatch.setattr('vision.fretboard_motion.monotonic', lambda: 10.)
    frame, grid = scene()
    tracker = make_tracker(frame, grid)
    blurred = cv2.GaussianBlur(frame, (11, 11), 0)
    assert tracker.update(blurred, lambda roi: ()).homography is not None
    mask = np.full(frame.shape[:2], 255, dtype=np.uint8) if masked else None
    lost = tracker.update(frame if masked else np.zeros_like(frame), lambda roi: (), mask)
    assert lost.homography is None
    assert lost.result.tracking_mode == 'selected_lines_hold'
    assert tracker.update(blurred, lambda roi: ()).homography is None
    assert tracker.update(frame, lambda roi: ()).result.tracking_mode == 'selected_lines_reacquired'


def test_coarse_appearance_does_not_accept_a_wrong_fret_or_displaced_board():
    frame, grid = scene()
    tracker = make_tracker(frame, grid)
    blurred = cv2.GaussianBlur(frame, (11, 11), 0)
    for matrix in (translation(12, 0), translation(0, 30)):
        assert not tracker.reference.validate(blurred, matrix, coarse=True)


def test_one_unverified_frame_keeps_the_inter_frame_tracker_but_a_long_loss_clears_it(monkeypatch):
    now = [10.]
    monkeypatch.setattr('vision.fretboard_motion.monotonic', lambda: now[0])
    frame, grid = scene()
    tracker = make_tracker(frame, grid)
    now[0] += 1 / 30
    assert tracker.update(warp(frame, translation(1.5, .4)), lambda roi: ()).homography is not None

    unrelated = np.random.default_rng(3).integers(0, 255, frame.shape, dtype=np.uint8)
    now[0] += 1 / 30
    lost = tracker.update(unrelated, lambda roi: ())
    # The app draws no guides in hold mode.
    assert lost.homography is None and lost.result.tracking_mode == 'selected_lines_hold'
    assert tracker.motion.points is not None  # The next frame is still measured from the last verified one.
    assert 'pose=none' in tracker.last_decision

    now[0] += 1 / 30
    back = tracker.update(warp(frame, translation(4.5, 1.2)), lambda roi: ())
    assert back.homography is not None
    assert 'inc=valid' in tracker.last_decision

    now[0] += 1.
    assert tracker.update(unrelated, lambda roi: ()).homography is None
    assert tracker.motion.points is None


def _moving_tracker(steps=4):
    frame, grid = scene()
    tracker = make_tracker(frame, grid)
    for index in range(1, steps + 1):
        result = tracker.update(warp(frame, translation(index * 6., index * 2.)), lambda roi: (), timestamp=index / 30)
        assert result.result.tracking_mode == 'selected_lines_motion'
    return frame, tracker, result


def _flow_follows_points_without_a_pose(tracker):
    """Flow still follows the neck's points, as in motion blur, but yields no pose."""
    for motion in (tracker.motion, tracker.anchor_motion):
        def estimate(*args, motion=motion, **kwargs):
            motion.last_tracked = 40
            return None
        motion.estimate = estimate


def test_brief_unverified_motion_is_drawn_from_velocity_then_hidden():
    frame, tracker, last = _moving_tracker()
    verified_pose = tracker.last_pose.copy()
    _flow_follows_points_without_a_pose(tracker)
    before = grid_quad(last.result.stable_grid)
    unreadable = np.zeros_like(frame)

    first = tracker.update(unreadable, lambda roi: (), timestamp=5 / 30)
    assert first.result.tracking_mode == 'selected_lines_predicted'
    assert first.homography is not None
    # One frame further along the measured (6, 2) px per frame motion, slowed by the decay.
    np.testing.assert_allclose(grid_quad(first.result.stable_grid) - before, np.tile((4.8, 1.6), (4, 1)), atol=.5)

    second = tracker.update(unreadable, lambda roi: (), timestamp=6 / 30)
    assert second.result.tracking_mode == 'selected_lines_predicted'
    third = tracker.update(unreadable, lambda roi: (), timestamp=7 / 30)
    assert third.homography is None and third.result.tracking_mode == 'selected_lines_hold'
    # A prediction is only drawn; it never becomes the tracked pose.
    np.testing.assert_array_equal(tracker.last_pose, verified_pose)


def test_prediction_is_not_drawn_when_flow_follows_nothing():
    frame, tracker, _ = _moving_tracker()
    gone = tracker.update(np.zeros_like(frame), lambda roi: (), timestamp=5 / 30)
    assert gone.homography is None and gone.result.tracking_mode == 'selected_lines_hold'


def test_prediction_needs_a_measured_velocity():
    frame, grid = scene()
    tracker = make_tracker(frame, grid)
    tracker.update(warp(frame, translation(6., 2.)), lambda roi: (), timestamp=1 / 30)
    _flow_follows_points_without_a_pose(tracker)
    result = tracker.update(np.zeros_like(frame), lambda roi: (), timestamp=2 / 30)
    assert result.result.tracking_mode == 'selected_lines_hold'
