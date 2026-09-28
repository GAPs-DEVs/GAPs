from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np

from vision.fretboard_mapping import NoteMarker, transform_grid
from vision.fretboard_motion import (
    FretboardMotionTracker, filter_visible_markers, grid_quad, valid_board_projection,
)
from vision.hand_tracking import extract_hand_polygons, hand_exclusion_mask, HandOcclusionMasker, HandTrackingResult
from test_fretboard_motion import scene, make_tracker, translation, warp


def test_hand_hull_covers_fingers_and_padded_boundary():
    result = SimpleNamespace(hand_landmarks=[[SimpleNamespace(x=x, y=y) for x, y in
                                             ((.2, .2), (.4, .2), (.4, .7), (.2, .5))]])
    polygons = extract_hand_polygons(result, 100, 100)
    mask = hand_exclusion_mask(polygons, (100, 100, 3))
    assert mask[65, 38] == 255  # Finger tip, outside a palm-only region.
    assert mask[18, 30] == 255  # Padding for skin around the landmarks.
    assert mask[95, 95] == 0


def test_mask_holds_one_previous_frame_without_latching_a_removed_hand():
    masker = HandOcclusionMasker()
    polygon = (((10, 10), (30, 10), (30, 30), (10, 30)),)
    empty = HandTrackingResult((), (), 22, True)
    masker.tracker.process = Mock(side_effect=[replace(empty, hand_polygons=polygon), empty, empty])
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    assert masker.process(frame)[20, 20] == 255
    assert masker.process(frame)[20, 20] == 255
    assert masker.process(frame)[20, 20] == 0


def test_tracking_points_are_never_seeded_on_a_hand():
    frame, grid = scene()
    mask = np.zeros(frame.shape[:2], dtype=np.uint8)
    mask[100:250] = 255
    tracker = FretboardMotionTracker()
    tracker.commit(frame, grid, refresh=True, exclusion_mask=mask)
    assert tracker.points is not None
    points = tracker.points.reshape(-1, 2).astype(int)
    assert not np.any(mask[points[:, 1], points[:, 0]])


def test_independently_moving_textured_hand_cannot_drag_the_fretboard():
    frame, grid = scene()
    rng = np.random.default_rng(123)
    hand = rng.integers(0, 255, (175, 100, 3), dtype=np.uint8)
    first = frame.copy()
    first[100:275, 80:180] = hand
    first_mask = np.zeros(frame.shape[:2], dtype=np.uint8)
    first_mask[92:283, 72:188] = 255
    motion = translation(3, 2)
    second = warp(frame, motion)
    second[100:275, 92:192] = hand  # Hand moves differently from the guitar.
    second_mask = np.zeros_like(first_mask)
    second_mask[92:283, 84:200] = 255
    tracker = FretboardMotionTracker()
    tracker.commit(first, grid, refresh=True, exclusion_mask=first_mask)
    estimated = tracker.estimate(second, grid, second_mask)
    assert estimated is not None
    np.testing.assert_allclose(grid_quad(transform_grid(grid, estimated)),
                               grid_quad(transform_grid(grid, motion)), atol=1)


def test_new_hand_is_excluded_even_if_previous_frame_was_unoccluded():
    frame, grid = scene()
    tracker = FretboardMotionTracker()
    tracker.commit(frame, grid, refresh=True)
    assert tracker.estimate(frame, grid, np.full(frame.shape[:2], 255, dtype=np.uint8)) is None


def test_appearance_rejects_wrong_incremental_motion_and_keeps_correct_reference_pose():
    frame, grid = scene()
    tracker = make_tracker(frame, grid)
    tracker.motion.estimate = Mock(return_value=translation(15, 0))
    tracker.anchor_motion.estimate = Mock(return_value=np.eye(3))
    tracked = tracker.update(frame, lambda roi: (grid.nut_line, *grid.ordered_fret_lines))
    assert tracked.result.tracking_mode == 'selected_lines_motion'
    np.testing.assert_allclose(grid_quad(tracker.tracker.last_stable_grid), grid_quad(grid), atol=0.01)


def test_all_occluded_detections_cannot_revalidate_a_hand_covered_board():
    frame, grid = scene()
    tracker = make_tracker(frame, grid)
    mask = np.full(frame.shape[:2], 255, dtype=np.uint8)
    tracked = tracker.update(frame, lambda roi: (grid.nut_line, *grid.ordered_fret_lines), mask)
    assert tracked.result.tracking_mode == 'selected_lines_hold'
    assert tracked.support_lines == ()


def test_even_consistent_detector_only_jumps_cannot_reacquire_without_reference_pixels():
    frame, grid = scene()
    frame[:] = 0  # No optical flow evidence.
    tracker = make_tracker(frame, grid)
    moved = transform_grid(grid, translation(70, 0))
    detector = lambda roi: (moved.nut_line, *moved.ordered_fret_lines)
    first = tracker.update(frame, detector)
    assert first.result.tracking_mode == 'selected_lines_reference_weak'
    np.testing.assert_allclose(grid_quad(tracker.tracker.last_stable_grid), grid_quad(grid), atol=0.01)
    second = tracker.update(frame, detector)
    assert second.result.tracking_mode == 'selected_lines_reference_weak'
    assert second.result.stable_grid is None


def test_large_alternating_detector_jumps_are_never_renderable():
    frame, grid = scene()
    frame[:] = 0
    tracker = make_tracker(frame, grid)
    for offset in (70, 35, 70, 35):
        moved = transform_grid(grid, translation(offset, 0))
        result = tracker.update(frame, lambda roi: (moved.nut_line, *moved.ordered_fret_lines))
        assert result.result.tracking_mode == 'selected_lines_reference_weak'


def test_markers_outside_their_fret_interval_frame_or_under_hand_are_hidden():
    frame, grid = scene()
    row = np.asarray(grid.string_crossings)
    good_xy = tuple((row[2, 2] + row[3, 2]) / 2)
    wrong_xy = tuple((row[1, 2] + row[2, 2]) / 2)
    good = NoteMarker(2, 3, good_xy, False)
    markers = (good, replace(good, point_xy=wrong_xy), replace(good, point_xy=(-10, 100)),
               replace(good, point_xy=(float('nan'), 100)))
    assert filter_visible_markers(markers, grid, frame.shape) == (good,)
    mask = np.zeros(frame.shape[:2], dtype=np.uint8)
    cv2.circle(mask, tuple(map(round, good_xy)), 10, 255, -1)
    assert filter_visible_markers(markers, grid, frame.shape, mask) == ()


def test_edge_on_or_offscreen_board_projection_is_rejected():
    frame, grid = scene()
    assert valid_board_projection(grid, frame.shape)
    assert not valid_board_projection(transform_grid(grid, translation(500, 0)), frame.shape)
    assert not valid_board_projection(transform_grid(grid, np.diag((0.05, 1, 1))), frame.shape)


def test_gradual_tilt_with_partial_hand_mask_keeps_projected_corners():
    frame, grid = scene()
    tracker = make_tracker(frame, grid)
    original = grid_quad(grid)
    tilted = np.asarray(((95, 65), (165, 55), (190, 300), (80, 315)), dtype=np.float32)
    for step in range(1, 21):
        target = original * (1 - step / 20) + tilted * (step / 20)
        transform = cv2.getPerspectiveTransform(original, target.astype(np.float32))
        current = warp(frame, transform)
        mask = np.zeros(current.shape[:2], dtype=np.uint8)
        mask[140:190, 70:200] = 255
        current[mask > 0] = 110
        detected_grid = transform_grid(grid, transform)
        result = tracker.update(current, lambda roi: (detected_grid.nut_line, *detected_grid.ordered_fret_lines), mask)
        assert result.result.tracking_mode != 'selected_lines_hold'
        np.testing.assert_allclose(grid_quad(result.result.stable_grid), target, atol=2)


def test_lost_calibration_never_falls_back_to_uncalibrated_note_positions():
    from app.features.video import PracticeVideoWorker
    frame, grid = scene()
    worker = PracticeVideoWorker(Path('unused.pt'), 0, .25, 960, None, False, False, False)
    worker.apply_line_selection(grid.nut_line, grid.ordered_fret_lines[-1], 5)
    assert worker.manual_tracking_requested
    worker.selected_line_tracker.clear()
    detector = Mock()
    tracked = worker.hybrid_tracker.update(frame, detector)
    assert tracked.result.tracking_mode == 'selected_lines_lost'
    detector.assert_not_called()
    worker.clear_manual_tracking()
    assert not worker.manual_tracking_requested
