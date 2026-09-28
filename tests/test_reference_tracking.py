"""Integration regressions for detector-independent calibrated geometry."""
from pathlib import Path
from unittest.mock import Mock

import cv2
import numpy as np

from app.features.video import PracticeVideoWorker
from practice.score_steps import build_practice_steps
from vision.fretboard_mapping import transform_grid
from vision.fretboard_motion import grid_quad
from test_fretboard_motion import scene, make_tracker, translation, warp


def test_repeated_false_frets_cannot_redefine_reference_shape():
    frame, grid = scene()
    tracker = make_tracker(frame, grid)
    original_template = tracker.reference.template.copy()
    false_pose = np.asarray(((.92, .025, 9), (0, 1.015, 3), (0, 0.00012, 1)))
    false_board = transform_grid(grid, false_pose)
    detections = (false_board.nut_line, *false_board.ordered_fret_lines)
    for step in range(35):
        result = tracker.update(frame, lambda roi: detections, diagnostics=True)
        assert result.result.tracking_mode == 'selected_lines_motion'
        np.testing.assert_allclose(grid_quad(result.result.stable_grid), grid_quad(grid), atol=.1)
    assert tracker.anchor_grid == grid
    np.testing.assert_array_equal(tracker.reference.template, original_template)


def test_appearance_rejects_two_flow_paths_that_agree_on_wrong_board():
    frame, grid = scene()
    tracker = make_tracker(frame, grid)
    wrong = translation(10, 0)
    tracker.motion.estimate = Mock(return_value=wrong)
    tracker.anchor_motion.estimate = Mock(return_value=wrong)
    tracker.reference.estimate = Mock(return_value=None)
    bad = transform_grid(grid, wrong)
    result = tracker.update(frame, lambda roi: (bad.nut_line, *bad.ordered_fret_lines), diagnostics=True)
    # The unchanged pose still matches distributed pixels in THIS frame, so
    # rejecting both contaminated flow estimates need not hide a correct guide.
    assert result.result.tracking_mode == 'selected_lines_motion'
    np.testing.assert_allclose(result.homography, np.eye(3), atol=.01)
    np.testing.assert_allclose(grid_quad(result.result.stable_grid), grid_quad(grid), atol=.01)
    np.testing.assert_allclose(grid_quad(tracker.tracker.last_stable_grid), grid_quad(grid), atol=.01)


def test_reference_recovers_after_long_occlusion_without_recalibrating():
    frame, grid = scene()
    tracker = make_tracker(frame, grid)
    for _ in range(24):
        result = tracker.update(np.zeros_like(frame), lambda roi: ())
    assert result.result.tracking_mode == 'selected_lines_lost'
    result = tracker.update(frame, lambda roi: ())
    assert result.result.tracking_mode == 'selected_lines_reacquired'
    assert tracker.tracker.reference_fret_index == 5
    assert tracker.tracker.is_active
    np.testing.assert_allclose(grid_quad(result.result.stable_grid), grid_quad(grid), atol=.1)


def test_large_motion_can_recover_from_reference_descriptors_without_fret_detections():
    frame, grid = scene()
    tracker = make_tracker(frame, grid)
    # Exceeds the incremental LK geometry range; descriptors find the same neck.
    moved = warp(frame, translation(130, 0))
    first = tracker.update(moved, lambda roi: ())
    assert first.result.tracking_mode == 'selected_lines_hold'
    second = tracker.update(moved, lambda roi: ())
    assert second.result.tracking_mode == 'selected_lines_reacquired'
    np.testing.assert_allclose(grid_quad(second.result.stable_grid), grid_quad(grid) + (130, 0), atol=1)


def test_unmasked_moving_occluder_cannot_reshape_visible_board():
    frame, grid = scene()
    tracker = make_tracker(frame, grid)
    for step in range(12):
        current = frame.copy()
        cv2.rectangle(current, (70 + step, 140), (190 + step, 195), (100, 145, 185), -1)
        result = tracker.update(current, lambda roi: ())
        assert result.result.tracking_mode in {'selected_lines_motion', 'selected_lines_reacquired'}
        np.testing.assert_allclose(grid_quad(result.result.stable_grid), grid_quad(grid), atol=1)


def test_practice_open_guide_survives_hand_mask_and_no_string_edge_detection():
    frame, grid = scene()
    step = build_practice_steps([{'step': 1, 'string': 3, 'fret': 0}])[0]
    worker = PracticeVideoWorker(Path('unused.pt'), 0, .25, 960, step, False, True, True)
    worker.selected_line_tracker.initialize_from_lines(grid.nut_line, grid.ordered_fret_lines[-1], 5)
    worker.hybrid_tracker.seed(frame)
    mask = np.zeros(frame.shape[:2], dtype=np.uint8)
    mask[40:70, 65:195] = 255  # Covers the nut, not the rest of the tracked board.
    current = frame.copy()
    current[mask > 0] = (100, 145, 185)
    tracked = worker.hybrid_tracker.update(current, lambda roi: (), mask)
    _, analysis = worker._analyze_selected_lines_frame(current, tracked)
    assert len(analysis.note_markers) == 1
    marker = analysis.note_markers[0]
    assert marker.is_open and marker.fret_index == 0
    np.testing.assert_allclose(marker.point_xy, grid.string_crossings[0][marker.string_index], atol=1)
    blank = np.zeros_like(frame)
    missing = worker.hybrid_tracker.update(blank, lambda roi: ())
    annotated, analysis = worker._analyze_selected_lines_frame(blank, missing)
    assert analysis.stable_grid is None and analysis.note_markers == ()
    np.testing.assert_array_equal(annotated, blank)


def test_calibrated_camera_path_does_not_spend_frames_on_diagnostic_yolo():
    frame, grid = scene()
    tracker = make_tracker(frame, grid)
    detector = Mock()
    for _ in range(6):
        assert tracker.update(frame, detector).result.tracking_mode == 'selected_lines_motion'
    tracker.update(np.zeros_like(frame), detector)
    detector.assert_not_called()


def test_camera_preview_requires_calibration_before_showing_absolute_fret_guides():
    frame, _ = scene()
    step = build_practice_steps([{'step': 1, 'string': 3, 'fret': 0}])[0]
    worker = PracticeVideoWorker(Path('unused.pt'), 0, .25, 960, step, False, True, False)
    model = Mock()
    annotated, analysis = worker._analyze_yolo_assist_frame(model, frame)
    assert analysis.tracking_mode == 'calibration_required'
    assert analysis.note_markers == () and analysis.stable_grid is None
    np.testing.assert_array_equal(annotated, frame)
    model.predict.assert_not_called()
