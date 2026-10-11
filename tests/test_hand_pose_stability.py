"""Moving, textured fingers must not become the neck's motion reference."""
import cv2
import numpy as np
import pytest

from test_fretboard_observation import rendered_board
from vision.fretboard_mapping import SelectedLineTracker
from vision.fretboard_motion import HybridSelectedLineTracker, grid_quad
from vision.fretboard_recovery import BackgroundFretboardRecovery


@pytest.mark.parametrize('moving_board', [False, True])
@pytest.mark.parametrize('finger_width', [18, 30])
@pytest.mark.parametrize('partial_hand_mask', [False, True])
@pytest.mark.parametrize('background', [False, True])
def test_textured_finger_motion_preserves_fret_coordinates_and_clean_features(
    moving_board, finger_width, partial_hand_mask, background, request,
):
    frame, grid = rendered_board()
    selected = SelectedLineTracker()
    selected.initialize_from_lines(grid.nut_line, grid.ordered_fret_lines[-1], 7)
    tracker = HybridSelectedLineTracker(selected, recovery=BackgroundFretboardRecovery() if background else None)
    request.addfinalizer(tracker.close)
    tracker.seed(frame)
    texture = np.random.default_rng(25).integers(55, 210, (140, finger_width, 3), dtype=np.uint8)
    reference_crossings = np.asarray(grid.string_crossings, dtype=np.float32).reshape(-1, 1, 2)

    for step in range(30):
        # Independent camera motion and finger motion. The finger has enough
        # texture to attract freshly detected corners, unlike a flat rectangle.
        matrix = np.asarray(((1, .0005 * step, .65 * step), (0, 1, .2 * step), (0, 0, 1.))) if moving_board else np.eye(3)
        current = cv2.warpPerspective(frame, matrix, (frame.shape[1], frame.shape[0]))
        x = round(128 - finger_width / 2 + 8 * np.sin(step * .3))
        y = round(145 + 18 * np.sin(step * .2))
        current[y:y + 140, x:x + finger_width] = texture
        mask = None
        if partial_hand_mask:
            # Simulate a detector that sees only half the finger, not an oracle.
            mask = np.zeros(frame.shape[:2], dtype=np.uint8)
            mask[y:y + 140, x:x + finger_width // 2] = 255

        result = tracker.update(current, lambda roi: (), mask)
        assert result.result.tracking_mode == 'selected_lines_motion'
        expected = cv2.perspectiveTransform(reference_crossings, matrix).reshape(-1, 2)
        actual = np.asarray(result.result.stable_grid.string_crossings).reshape(-1, 2)
        # Includes nut, middle frets and last fret, including hidden portions.
        assert np.max(np.linalg.norm(actual - expected, axis=1)) < (3.0 if moving_board else .1)
        expected_quad = cv2.perspectiveTransform(grid_quad(grid)[:, None], matrix).reshape(-1, 2)
        assert np.max(np.linalg.norm(grid_quad(result.result.stable_grid) - expected_quad, axis=1)) < (3.0 if moving_board else .1)

        points = tracker.motion.points
        assert points is not None and len(points) >= 12
        xy = points.reshape(-1, 2)
        on_finger = ((xy[:, 0] >= x) & (xy[:, 0] < x + finger_width)
                     & (xy[:, 1] >= y) & (xy[:, 1] < y + 140))
        assert not on_finger.any()

    # Removing the finger must not leave a changed model or lose the reference.
    clear_frame = cv2.warpPerspective(frame, matrix, (frame.shape[1], frame.shape[0]))
    recovered = tracker.update(clear_frame, lambda roi: ())
    assert recovered.homography is not None
    assert tracker.anchor_grid == grid


def test_previous_pose_is_rejected_when_board_moves_and_motion_is_unavailable():
    frame, grid = rendered_board()
    selected = SelectedLineTracker()
    selected.initialize_from_lines(grid.nut_line, grid.ordered_fret_lines[-1], 7)
    tracker = HybridSelectedLineTracker(selected)
    tracker.seed(frame)
    moved = cv2.warpPerspective(frame, np.asarray(((1., 0, 12), (0, 1, 0), (0, 0, 1))), (340, 380))
    # A rejected candidate must not silently become a stale stationary pose.
    assert tracker.reference.select_pose(moved, None, np.eye(3)) is None
