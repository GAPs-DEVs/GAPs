from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from vision.fretboard_mapping import (
    DetectedLine, NoteMarker, NoteMarkerTracker, SelectedLineTracker,
    build_grid_from_reference_lines, transform_grid,
)
from vision.fretboard_motion import FretboardMotionTracker, HybridSelectedLineTracker, grid_quad


def scene():
    nut = DetectedLine('nut', (80, 50), (180, 50), 0.95)
    reference = DetectedLine('fret', (80, 310), (180, 310), 0.95)
    grid = build_grid_from_reference_lines(nut, reference, 5)
    assert grid is not None
    frame = np.zeros((380, 340, 3), dtype=np.uint8)
    rng = np.random.default_rng(42)
    frame[50:311, 80:181] = rng.integers(35, 220, (261, 101, 1), dtype=np.uint8)
    for line in (nut, *grid.ordered_fret_lines):
        cv2.line(frame, tuple(map(round, line.p0)), tuple(map(round, line.p1)), (230, 230, 230), 2)
    return frame, grid


def warp(frame, matrix):
    return cv2.warpPerspective(frame, matrix, (frame.shape[1], frame.shape[0]))


def translation(x, y):
    return np.asarray([[1, 0, x], [0, 1, y], [0, 0, 1]], dtype=float)


def make_tracker(frame, grid):
    selected = SelectedLineTracker()
    selected.initialize_from_lines(grid.nut_line, grid.ordered_fret_lines[-1], 5)
    hybrid = HybridSelectedLineTracker(selected)
    hybrid.seed(frame)
    return hybrid


@pytest.mark.parametrize('matrix', [
    translation(8, -5),
    np.asarray([[0.9986, -0.0523, 12], [0.0523, 0.9986, -4], [0, 0, 1]]),
    np.asarray([[1.02, 0.025, -3], [0.01, 1.015, 3], [0.00008, 0.00012, 1]]),
])
def test_image_motion_recovers_translation_rotation_and_perspective(matrix):
    frame, grid = scene()
    tracker = FretboardMotionTracker()
    tracker.commit(frame, grid, refresh=True)
    estimated = tracker.estimate(warp(frame, matrix), grid)
    assert estimated is not None
    predicted = grid_quad(transform_grid(grid, estimated))
    expected = grid_quad(transform_grid(grid, matrix))
    assert np.max(np.linalg.norm(predicted - expected, axis=1)) < 1.0


def test_partial_occlusion_does_not_drag_board_onto_occluder():
    frame, grid = scene()
    matrix = translation(6, 4)
    moved = warp(frame, matrix)
    moved[145:205, 70:200] = 110
    tracker = FretboardMotionTracker()
    tracker.commit(frame, grid, refresh=True)
    estimated = tracker.estimate(moved, grid)
    assert estimated is not None
    assert np.max(np.linalg.norm(
        grid_quad(transform_grid(grid, estimated)) - grid_quad(transform_grid(grid, matrix)), axis=1,
    )) < 1.0


def test_blank_frame_and_resolution_change_reject_motion():
    frame, grid = scene()
    tracker = FretboardMotionTracker()
    tracker.commit(frame, grid, refresh=True)
    assert tracker.estimate(np.zeros_like(frame), grid) is None
    assert tracker.estimate(frame[:200], grid) is None


def test_features_on_one_small_patch_cannot_move_entire_fretboard():
    frame, grid = scene()
    patch = np.zeros_like(frame)
    patch[150:180, 105:145] = frame[150:180, 105:145]
    tracker = FretboardMotionTracker()
    tracker.commit(patch, grid, refresh=True)
    assert tracker.estimate(warp(patch, translation(5, 3)), grid) is None


def test_diagnostic_detector_misses_do_not_interrupt_verified_image_tracking():
    frame, grid = scene()
    tracker = make_tracker(frame, grid)
    calls = []

    def detector(roi):
        calls.append(roi)
        return (grid.nut_line, *grid.ordered_fret_lines) if roi is None else ()

    result = tracker.update(frame, detector, diagnostics=True)
    assert len(calls) == 1 and calls[0] is not None
    assert result.result.tracking_mode == 'selected_lines_motion'
    assert tracker.tracker.reference_fret_index == 5
    assert len(result.result.stable_grid.ordered_fret_lines) == 5


def test_detection_dropout_tracks_image_and_keeps_observed_fret_positions():
    frame, grid = scene()
    tracker = make_tracker(frame, grid)
    tracker.update(frame, lambda roi: (grid.nut_line, *grid.ordered_fret_lines))
    matrix = translation(7, 3)
    result = tracker.update(warp(frame, matrix), lambda roi: ())
    assert result.result.tracking_mode == 'selected_lines_motion'
    assert result.support_lines == ()  # No new detector observations; the calibrated grid still moves.
    expected = grid_quad(transform_grid(grid, matrix))
    assert np.max(np.linalg.norm(grid_quad(result.result.stable_grid) - expected, axis=1)) < 1
    assert tracker.tracker.missed_frames == 0  # Independently verified against the fixed keyframe.


def test_missing_images_cannot_keep_a_lock_alive_indefinitely():
    frame, grid = scene()
    tracker = make_tracker(frame, grid)
    tracker.update(frame, lambda roi: (grid.nut_line, *grid.ordered_fret_lines))
    full_calls = 0

    def detector(roi):
        nonlocal full_calls
        full_calls += roi is None
        return ()

    for index in range(1, 22):
        result = tracker.update(np.zeros_like(frame), detector)
        if index > 8:
            assert result.support_lines == ()
            assert result.result.tracking_mode != 'selected_lines_motion'
    assert not tracker.tracker.is_active
    assert result.result.stable_grid is None
    assert full_calls <= 8


def test_failed_motion_drops_stale_evidence_immediately():
    frame, grid = scene()
    tracker = make_tracker(frame, grid)
    tracker.update(frame, lambda roi: (grid.nut_line, *grid.ordered_fret_lines))
    result = tracker.update(np.zeros_like(frame), lambda roi: ())
    assert result.result.tracking_mode == 'selected_lines_hold'
    assert result.homography is None
    assert result.support_lines == ()


def test_marker_smoothing_follows_motion_without_trailing():
    tracker = NoteMarkerTracker()
    tracker.update((NoteMarker(2, 3, (100, 100), False),))
    tracker.apply_motion(translation(12, 7))
    result = tracker.update((NoteMarker(2, 3, (112, 107), False),))
    assert result[0].point_xy == (112, 107)
    # Residual detector jitter is still smoothed after motion compensation.
    jittered = tracker.update((NoteMarker(2, 3, (116, 107), False),))
    assert 112 < jittered[0].point_xy[0] < 116


def test_selected_line_history_moves_without_resetting_missed_count():
    _, grid = scene()
    tracker = SelectedLineTracker()
    tracker.initialize_from_lines(grid.nut_line, grid.ordered_fret_lines[-1], 5)
    tracker.missed_frames = 2
    tracker.apply_motion(translation(8, -5))
    assert tracker.missed_frames == 2
    assert tracker.nut_history[-1].p0 == (88, 45)
    assert tracker.last_stable_grid.nut_line.p0 == (88, 45)


def test_practice_worker_keeps_markers_during_short_detection_dropout_and_hides_on_loss():
    from app.features.video import PracticeVideoWorker
    from practice.score_steps import build_practice_steps

    frame, grid = scene()
    worker = PracticeVideoWorker(Path('unused.pt'), 0, 0.25, 960,
                                 build_practice_steps([{'step': 1, 'string': 3, 'fret': 3}])[0],
                                 False, False, False)
    worker.selected_line_tracker.initialize_from_lines(grid.nut_line, grid.ordered_fret_lines[-1], 5)
    worker.hybrid_tracker.seed(frame)
    initial = worker.hybrid_tracker.update(frame, lambda roi: (grid.nut_line, *grid.ordered_fret_lines))
    _, first = worker._analyze_selected_lines_frame(frame, initial)
    assert len(first.note_markers) == 1
    moved = warp(frame, translation(7, 3))
    tracked = worker.hybrid_tracker.update(moved, lambda roi: ())
    _, second = worker._analyze_selected_lines_frame(moved, tracked)
    assert len(second.note_markers) == 1
    delta = np.asarray(second.note_markers[0].point_xy) - first.note_markers[0].point_xy
    assert np.linalg.norm(delta - (7, 3)) < 2
    blank = np.zeros_like(frame)
    lost = worker.hybrid_tracker.update(blank, lambda roi: ())
    _, third = worker._analyze_selected_lines_frame(blank, lost)
    assert third.note_markers == ()


def test_fixed_keyframe_verifies_long_detector_dropout_without_drift():
    frame, grid = scene()
    tracker = make_tracker(frame, grid)
    for index in range(1, 61):
        matrix = translation(8 * np.sin(index / 12), 4 * np.cos(index / 10))
        tracked = tracker.update(warp(frame, matrix), lambda roi: ())
        assert tracked.result.tracking_mode == 'selected_lines_motion'
        expected = grid_quad(transform_grid(grid, matrix))
        assert np.max(np.linalg.norm(grid_quad(tracked.result.stable_grid) - expected, axis=1)) < 1
    assert tracker.tracker.is_active


def test_seventh_note_keeps_its_interval_when_last_and_middle_frets_are_missing():
    from app.features.video import PracticeVideoWorker
    from practice.score_steps import build_practice_steps
    from vision.fretboard_registration import board_quad

    frame, _ = scene()
    worker = PracticeVideoWorker(Path('unused.pt'), 0, 0.25, 960,
                                 build_practice_steps([{'step': 1, 'string': 3, 'fret': 7}])[0],
                                 False, False, False)
    worker.selected_line_tracker.initialize_from_lines(
        DetectedLine('nut', (80, 50), (180, 50), 0.95),
        DetectedLine('fret', (80, 310), (180, 310), 0.95), 7,
    )
    grid = worker.selected_line_tracker.last_stable_grid
    worker.hybrid_tracker.seed(frame)
    # Last fret 7 and middle frets 2/5 never appear in detector output.
    detections = (grid.nut_line, *(grid.ordered_fret_lines[i - 1] for i in (1, 3, 4, 6)),
                  DetectedLine('fret', (80, 340), (180, 340), 0.99))
    for index in range(25):
        tracked = worker.hybrid_tracker.update(frame, lambda roi: detections)
        _, analysis = worker._analyze_selected_lines_frame(frame, tracked)
        assert analysis.reference_fret_index == 7
        assert len(analysis.note_markers) == 1
        marker = analysis.note_markers[0]
        assert marker.fret_index == 7
        sixth_y = grid.ordered_fret_lines[5].p0[1]
        seventh_y = grid.ordered_fret_lines[6].p0[1]
        assert sixth_y < marker.point_xy[1] < seventh_y
        np.testing.assert_allclose(board_quad(analysis.stable_grid), board_quad(grid), atol=0.1)


def test_optional_diagnostics_do_not_run_yolo_on_every_verified_frame():
    frame, grid = scene()
    tracker = make_tracker(frame, grid)
    calls = []

    def detect(roi):
        calls.append(roi)
        return (grid.nut_line, *grid.ordered_fret_lines)

    for _ in range(18):
        tracked = tracker.update(frame, detect, diagnostics=True)
        assert tracked.result.stable_grid is not None
    assert len(calls) == 6
    tracker.update(np.zeros_like(frame), detect, diagnostics=True)
    assert len(calls) == 7
