"""Frame-local reuse must not carry image evidence into the next frame."""
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import pytest

from app.features.video import PracticeVideoWorker
from app.practice_markers import build_practice_step_note_markers
from practice.score_steps import build_practice_steps
from test_fretboard_reference import scene, reference, warp
from test_fretboard_motion import scene as small_scene, make_tracker
import vision.fretboard_reference as reference_module


def test_repeated_pose_checks_share_grayscale_warp_and_scores():
    frame, grid = scene()
    saved = reference(frame, grid)
    pose = np.eye(3)
    with (patch.object(reference_module, '_gray', wraps=reference_module._gray) as gray,
          patch.object(saved, '_validate', wraps=saved._validate) as validation,
          patch.object(saved, '_compute_tile_scores', wraps=saved._compute_tile_scores) as scores):
        with saved.frame_scope(frame):
            assert saved.validate(frame, pose)
            assert saved.validate(frame, pose.copy())
            assert saved.refine(frame, pose) is not None
            assert saved.select_pose(frame, pose, pose) is not None
            saved.tracking_exclusion(frame, pose)
        assert gray.call_count == 1
        assert validation.call_count == 1
        assert scores.call_count == 1
    assert saved._frame_source is None and not saved._frame_values


@pytest.mark.parametrize('mutate', ['frame', 'mask'])
def test_reused_camera_buffers_are_revalidated_on_next_scope(mutate):
    frame, grid = scene()
    saved = reference(frame, grid)
    mask = np.zeros(frame.shape[:2], dtype=np.uint8)
    with saved.frame_scope(frame, mask):
        assert saved.validate(frame, np.eye(3), mask)
    if mutate == 'frame':
        frame[:] = 0
    else:
        mask[:] = 255
    with saved.frame_scope(frame, mask):
        assert not saved.validate(frame, np.eye(3), mask)


def test_standalone_calls_do_not_reuse_image_evidence():
    frame, grid = scene()
    saved = reference(frame, grid)
    assert saved.validate(frame, np.eye(3))
    frame[:] = 0
    assert not saved.validate(frame, np.eye(3))


def test_changed_mask_and_in_place_pose_have_separate_results():
    frame, grid = scene()
    saved = reference(frame, grid)
    clear = np.zeros(frame.shape[:2], dtype=np.uint8)
    covered = np.full_like(clear, 255)
    pose = np.eye(3)
    with saved.frame_scope(frame, clear):
        assert saved.validate(frame, pose, clear)
        assert not saved.validate(frame, pose, covered)
        assert saved.validate(frame, pose, clear)
        pose[0, 2] = 12
        assert not saved.validate(frame, pose, clear)


@pytest.mark.parametrize('condition', ['normal', 'hand', 'blur', 'blank'])
def test_reuse_preserves_validation_alignment_scores_and_feature_masks(condition):
    frame, grid = scene()
    saved = reference(frame, grid)
    pose = np.asarray(((1., .02, 4.), (-.01, 1., 3.), (0, 0, 1.)))
    current = warp(frame, pose)
    mask = None
    if condition == 'hand':
        mask = np.zeros(frame.shape[:2], dtype=np.uint8)
        mask[200:280, 80:260] = 255
        current[mask > 0] = (75, 130, 175)
    elif condition == 'blur':
        current = cv2.GaussianBlur(current, (9, 9), 0)
    elif condition == 'blank':
        current[:] = 0

    def evaluate(cached):
        with saved.frame_scope(current, mask) if cached else nullcontext():
            return (saved.validate(current, pose, mask),
                    saved.validate(current, pose, mask, coarse=True),
                    saved.refine(current, pose, mask),
                    saved._visible_tile_scores(current, pose, mask),
                    saved.tracking_exclusion(current, pose, mask))

    expected, actual = evaluate(False), evaluate(True)
    assert expected[:2] == actual[:2]
    if expected[2] is None:
        assert actual[2] is None
    else:
        np.testing.assert_array_equal(actual[2], expected[2])
    assert actual[3] == expected[3]
    np.testing.assert_array_equal(actual[4], expected[4])


def test_frame_context_releases_data_even_on_failure_and_reseed_clears_template_cache():
    frame, grid = scene()
    saved = reference(frame, grid)
    with pytest.raises(RuntimeError, match='processing failed'):
        with saved.frame_scope(frame):
            assert saved.validate(frame, np.eye(3), coarse=True)
            raise RuntimeError('processing failed')
    assert saved._frame_source is None and not saved._frame_values
    assert saved._coarse_template is not None
    saved.seed(frame, grid)
    assert saved._coarse_template is None


def test_live_calibrated_path_draws_same_markers_without_edge_diagnostics():
    frame, grid = small_scene()
    tracker = make_tracker(frame, grid)
    step = build_practice_steps([{'step': 1, 'string': 3, 'fret': 4}])[0]
    worker = PracticeVideoWorker(Path('unused.pt'), 0, .25, 960, step, False, True, True)
    worker.selected_line_tracker = tracker.tracker
    tracked = tracker.update(frame, lambda roi: ())
    with patch('app.features.video.refine_grid_with_edge_lines_diagnostics',
               side_effect=AssertionError('Live diagnostics must not execute')):
        _, result = worker._analyze_selected_lines_frame(frame, tracked)
    assert result.edge_refinement is None
    assert result.note_markers == build_practice_step_note_markers(result.stable_grid, step)
