"""Pixel-level regressions: no mocked fret detections in these measurements."""
import cv2
import numpy as np
import pytest

from vision.fretboard_mapping import DetectedLine, SelectedLineTracker
from vision.fretboard_motion import HybridSelectedLineTracker, unoccluded_lines
from vision.fretboard_observation import observe_fret_segments
from vision.fretboard_registration import board_quad, register_fretboard
from test_fretboard_registration import seven_frets


def rendered_board():
    grid = seven_frets()
    frame = np.full((380, 340, 3), 25, dtype=np.uint8)
    # Low-texture wood, six longitudinal strings and metal wires.
    frame[48:314, 78:183] = (45, 62, 80)
    for x in np.linspace(85, 175, 6):
        cv2.line(frame, (round(x), 50), (round(x), 310), (130, 140, 145), 1)
    for line in (grid.nut_line, *grid.ordered_fret_lines):
        cv2.line(frame, tuple(map(round, line.p0)), tuple(map(round, line.p1)), (220, 225, 230), 2)
    return frame, grid


def fret_indices(lines, grid):
    targets = np.asarray([line.p0[1] for line in grid.ordered_fret_lines])
    return tuple(int(np.argmin(abs(targets - (line.p0[1] + line.p1[1]) / 2))) + 1 for line in lines)


def test_real_pixels_detect_missing_frets_without_fabricating_hidden_last():
    frame, grid = rendered_board()
    mask = np.zeros(frame.shape[:2], dtype=np.uint8)
    mask[295:325, 70:190] = 255
    frame[mask > 0] = (95, 140, 175)
    observed = observe_fret_segments(frame, grid, mask)
    assert fret_indices(observed, grid) == (1, 2, 3, 4, 5, 6)
    assert all(line.p0[0] > 85 and line.p1[0] < 175 for line in observed)
    result = register_fretboard(grid, observed, has_image_motion=True)
    assert result is not None
    assert 7 not in result.observed_indices
    np.testing.assert_allclose(board_quad(result.grid), board_quad(grid), atol=1.5)


def test_partial_fret_pixels_survive_middle_finger_mask():
    frame, grid = rendered_board()
    mask = np.zeros(frame.shape[:2], dtype=np.uint8)
    mask[45:315, 124:137] = 255
    frame[mask > 0] = (95, 140, 175)
    observed = observe_fret_segments(frame, grid, mask)
    assert len(observed) >= 5
    result = register_fretboard(grid, observed, has_image_motion=True)
    assert result is not None
    np.testing.assert_allclose(board_quad(result.grid), board_quad(grid), atol=1.5)


def test_blank_noise_and_fully_masked_frames_supply_no_measurements():
    frame, grid = rendered_board()
    assert observe_fret_segments(np.zeros_like(frame), grid) == ()
    noise = np.random.default_rng(414).integers(0, 255, frame.shape, dtype=np.uint8)
    assert observe_fret_segments(noise, grid) == ()
    assert observe_fret_segments(frame, grid, np.full(frame.shape[:2], 255, dtype=np.uint8)) == ()


def test_bad_sloped_detector_segment_does_not_suppress_image_observation():
    frame, grid = rendered_board()
    y = grid.ordered_fret_lines[2].p0[1]
    invalid = DetectedLine('fret', (80, y - 20), (180, y + 20), .99)
    observed = observe_fret_segments(frame, grid, existing=(invalid,))
    assert 3 in fret_indices(observed, grid)
    correct = grid.ordered_fret_lines[2]
    assert 3 not in fret_indices(observe_fret_segments(frame, grid, existing=(correct,)), grid)


def test_two_visible_fragments_remain_separate_observations():
    _, grid = rendered_board()
    mask = np.zeros((380, 340), dtype=np.uint8)
    mask[:, 113:147] = 255
    fragments = unoccluded_lines((grid.ordered_fret_lines[2],), mask)
    assert len(fragments) == 2
    assert fragments[0].p1[0] < 113
    assert round(fragments[1].p0[0]) >= 147
    assert all(not line.endpoints_observed for line in fragments)


def test_clipped_nut_cannot_reacquire_and_shrink_the_neck_without_image_motion():
    frame, grid = rendered_board()
    mask = np.zeros(frame.shape[:2], dtype=np.uint8)
    mask[:, 75:90] = 255
    visible = unoccluded_lines((grid.nut_line, *grid.ordered_fret_lines), mask)
    assert visible and visible[0].kind == 'nut'
    assert not visible[0].endpoints_observed
    assert register_fretboard(grid, visible, has_image_motion=False) is None


@pytest.mark.parametrize('strong_tilt', [False, True])
def test_real_pixel_motion_tracks_tilt_and_hidden_last_without_detector_geometry(strong_tilt):
    frame, grid = rendered_board()
    selected = SelectedLineTracker()
    selected.initialize_from_lines(grid.nut_line, grid.ordered_fret_lines[-1], 7)
    tracker = HybridSelectedLineTracker(selected)
    tracker.seed(frame)
    original = board_quad(grid)
    tilted = (np.asarray(((95, 65), (165, 55), (190, 300), (80, 315)), dtype=np.float32)
              if strong_tilt else original + np.asarray(((6, 4), (2, 1), (7, 2), (2, 4)), dtype=np.float32))
    steps = 30 if strong_tilt else 12
    for step in range(1, steps + 1):
        corners = original + (tilted - original) * step / steps
        matrix = cv2.getPerspectiveTransform(original, corners)
        moved = cv2.warpPerspective(frame, matrix, (frame.shape[1], frame.shape[0]))
        mask = np.zeros(frame.shape[:2], dtype=np.uint8)
        mask[290:330, 65:205] = 255  # Last fret completely invisible.
        moved[mask > 0] = (95, 140, 175)
        result = tracker.update(moved, lambda roi: (), mask)
        assert result.result.tracking_mode in {'selected_lines_tracking', 'selected_lines_motion'}
        assert result.support_lines == ()  # Model projection is not a fresh line detection.
        np.testing.assert_allclose(board_quad(result.result.stable_grid), corners, atol=3.0 if strong_tilt else 2.0)


def test_stationary_pose_requires_fresh_image_support_when_motion_is_unavailable():
    from unittest.mock import Mock

    frame, grid = rendered_board()
    selected = SelectedLineTracker()
    selected.initialize_from_lines(grid.nut_line, grid.ordered_fret_lines[-1], 7)
    tracker = HybridSelectedLineTracker(selected)
    tracker.seed(frame)
    tracker.motion.estimate = Mock(return_value=None)
    tracker.anchor_motion.estimate = Mock(return_value=None)
    tracker.reference.estimate = Mock(return_value=None)
    result = tracker.update(frame, lambda roi: ())
    assert result.result.tracking_mode == 'selected_lines_motion'
    np.testing.assert_allclose(board_quad(result.result.stable_grid), board_quad(grid), atol=.01)
    # No temporal freeze: once those pixels disappear, the previous pose is
    # no longer sufficient evidence to keep showing the guide.
    result = tracker.update(np.zeros_like(frame), lambda roi: ())
    assert result.result.tracking_mode == 'selected_lines_hold'
    assert result.homography is None
    assert result.support_lines == ()
