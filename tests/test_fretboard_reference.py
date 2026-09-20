"""Reference checks use rendered images, not detector-produced line coordinates."""
import cv2
import numpy as np
import pytest

from vision.fretboard_mapping import DetectedLine, build_grid_from_reference_lines
from vision.fretboard_reference import FretboardVisualReference


def scene():
    nut = DetectedLine("nut", (100, 60), (240, 60), 1.0)
    last = DetectedLine("fret", (100, 460), (240, 460), 1.0)
    grid = build_grid_from_reference_lines(nut, last, 9)
    assert grid is not None
    rng = np.random.default_rng(31)
    gray = np.full((600, 620), 25, dtype=np.uint8)
    texture = rng.integers(35, 175, (401, 141), dtype=np.uint8)
    gray[60:461, 100:241] = cv2.GaussianBlur(texture, (3, 3), 0.6)
    for x in np.linspace(108, 232, 6):
        cv2.line(gray, (round(x), 60), (round(x), 460), 190, 1)
    for line in (nut, *grid.ordered_fret_lines):
        cv2.line(gray, tuple(map(round, line.p0)), tuple(map(round, line.p1)), 225, 2)
    for index, y in enumerate((121, 237, 341, 419)):
        cv2.circle(gray, (164 + index * 3, y), 5, 220, -1)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR), grid


def warp(frame, matrix):
    return cv2.warpPerspective(frame, matrix, (frame.shape[1], frame.shape[0]))


def reference(frame, grid):
    result = FretboardVisualReference()
    result.seed(frame, grid)
    assert result.is_ready
    return result


@pytest.mark.parametrize("matrix", [
    np.eye(3),
    np.asarray(((0.99, -0.08, 34), (0.08, 0.99, -6), (0, 0, 1))),
    np.asarray(((1.02, 0.09, 11), (-0.035, 0.95, 19), (0.00015, -0.00012, 1))),
])
def test_correct_pose_is_valid_under_tilt_and_brightness_changes(matrix):
    frame, grid = scene()
    saved = reference(frame, grid)
    moved = np.clip(warp(frame, matrix).astype(float) * 0.82 + 24, 0, 255).astype(np.uint8)
    assert saved.validate(moved, matrix)


@pytest.mark.parametrize("matrix", [
    np.asarray(((1, 0, 12), (0, 1, 0), (0, 0, 1))),
    np.asarray(((1, 0, 0), (0, 1, 22), (0, 0, 1))),
    np.asarray(((0.72, 0, 47.6), (0, 1, 0), (0, 0, 1))),
])
def test_wrong_pose_or_shortened_fret_endpoints_cannot_validate(matrix):
    frame, grid = scene()
    assert not reference(frame, grid).validate(frame, matrix)


@pytest.mark.parametrize("use_mask", [False, True])
def test_partial_hand_occlusion_preserves_distributed_reference_support(use_mask):
    frame, grid = scene()
    saved = reference(frame, grid)
    moved = frame.copy()
    moved[200:300, 80:265] = (75, 130, 175)
    mask = np.zeros(frame.shape[:2], dtype=np.uint8)
    mask[195:305, 75:270] = 255
    assert saved.validate(moved, np.eye(3), mask if use_mask else None)


def test_disappeared_or_different_neck_and_small_matching_patch_are_rejected():
    frame, grid = scene()
    saved = reference(frame, grid)
    assert not saved.validate(np.zeros_like(frame), np.eye(3))
    impostor = frame.copy()
    rng = np.random.default_rng(93)
    impostor[60:461, 100:241] = rng.integers(35, 230, (401, 141, 1), dtype=np.uint8)
    assert not saved.validate(impostor, np.eye(3))
    impostor[195:250, 100:241] = frame[195:250, 100:241]
    assert not saved.validate(impostor, np.eye(3))


def test_large_jump_is_recovered_from_the_original_image_descriptors():
    frame, grid = scene()
    saved = reference(frame, grid)
    matrix = np.asarray(((0.995, -0.10, 205), (0.10, 0.995, 10), (0.00004, -0.00004, 1)))
    moved = warp(frame, matrix)
    recovered = saved.estimate(moved)
    assert recovered is not None
    quad = np.asarray((grid.nut_line.p0, grid.nut_line.p1, grid.ordered_fret_lines[-1].p1,
                       grid.ordered_fret_lines[-1].p0), dtype=np.float32)[:, None]
    assert np.max(np.linalg.norm(cv2.perspectiveTransform(quad, recovered) - cv2.perspectiveTransform(quad, matrix), axis=2)) < 1.0


def test_large_jump_recovery_handles_partial_occlusion():
    frame, grid = scene()
    saved = reference(frame, grid)
    matrix = np.asarray(((1, 0, 180), (0, 1, 40), (0, 0, 1)), dtype=float)
    moved = warp(frame, matrix)
    moved[240:340, 265:435] = (75, 130, 175)
    mask = np.zeros(frame.shape[:2], dtype=np.uint8)
    mask[235:345, 260:440] = 255
    recovered = saved.estimate(moved, mask)
    assert recovered is not None
    assert saved.validate(moved, recovered, mask)


def test_template_never_updates_from_later_frames_or_external_seed_array():
    frame, grid = scene()
    saved = reference(frame, grid)
    template = saved.template.copy()
    original = saved.reference_gray.copy()
    frame[:] = 0
    assert not saved.validate(frame, np.eye(3))
    assert saved.estimate(frame) is None
    np.testing.assert_array_equal(saved.template, template)
    np.testing.assert_array_equal(saved.reference_gray, original)
    assert not saved.template.flags.writeable
    saved.clear()
    assert not saved.is_ready
    assert saved.estimate(frame) is None


def test_seed_occluded_by_hand_cannot_turn_hand_into_matching_reference():
    frame, grid = scene()
    mask = np.zeros(frame.shape[:2], dtype=np.uint8)
    mask[185:295, 80:265] = 255
    frame[190:290, 85:260] = (75, 130, 175)
    saved = FretboardVisualReference()
    saved.seed(frame, grid, mask)
    assert saved.is_ready
    clear_frame, _ = scene()
    assert saved.validate(clear_frame, np.eye(3))


def test_featureless_reference_cannot_authorize_geometry_or_relocalization():
    frame, grid = scene()
    frame[:] = 80
    saved = FretboardVisualReference()
    saved.seed(frame, grid)
    assert not saved.is_ready
    assert not saved.validate(frame, np.eye(3))
    assert saved.estimate(frame) is None


def test_parallel_fret_bars_alone_cannot_validate_transverse_position():
    frame, grid = scene()
    frame[:] = 80
    for line in (grid.nut_line, *grid.ordered_fret_lines):
        cv2.line(frame, tuple(map(round, line.p0)), tuple(map(round, line.p1)), (220, 220, 220), 2)
    saved = FretboardVisualReference()
    saved.seed(frame, grid)
    assert not saved.is_ready
    assert not saved.validate(frame, np.eye(3))


def test_small_unmasked_area_is_not_enough_even_when_it_matches_perfectly():
    frame, grid = scene()
    saved = reference(frame, grid)
    mask = np.full(frame.shape[:2], 255, dtype=np.uint8)
    mask[210:285, 100:241] = 0
    assert not saved.validate(frame, np.eye(3), mask)
    assert saved.estimate(frame, mask) is None


@pytest.mark.parametrize("matrix", [np.zeros((3, 3)), np.full((3, 3), np.nan),
                                    np.diag((-1.0, 1.0, 1.0))])
def test_degenerate_or_flipped_projection_is_rejected(matrix):
    frame, grid = scene()
    assert not reference(frame, grid).validate(frame, matrix)


@pytest.mark.parametrize('masked', [False, True])
def test_direct_alignment_corrects_small_pose_bias_without_learning_the_hand(masked):
    frame, grid = scene()
    saved = reference(frame, grid)
    template = saved.template.copy()
    actual = np.asarray(((1.01, .04, 8), (-.02, .98, 10), (.00008, -.00005, 1)))
    current = warp(frame, actual)
    current[210:280, 85:285] = (75, 130, 175)
    mask = np.zeros(frame.shape[:2], dtype=np.uint8)
    mask[205:285, 80:290] = 255
    initial = np.asarray(((1, 0, 1), (0, 1, -.7), (0, 0, 1))) @ actual
    refined = saved.refine(current, initial, mask if masked else None)
    assert refined is not None
    quad = np.asarray((grid.nut_line.p0, grid.nut_line.p1,
                       grid.ordered_fret_lines[-1].p1, grid.ordered_fret_lines[-1].p0), dtype=np.float32)[:, None]
    expected = cv2.perspectiveTransform(quad, actual)
    before = np.max(np.linalg.norm(cv2.perspectiveTransform(quad, initial) - expected, axis=2))
    after = np.max(np.linalg.norm(cv2.perspectiveTransform(quad, refined) - expected, axis=2))
    assert after < before * .6
    np.testing.assert_array_equal(saved.template, template)


def test_direct_alignment_cannot_authorize_wrong_pose_or_blank_image():
    frame, grid = scene()
    saved = reference(frame, grid)
    wrong = np.asarray(((.72, 0, 47.6), (0, 1, 0), (0, 0, 1)))
    assert saved.refine(frame, wrong) is None
    assert saved.refine(np.zeros_like(frame), np.eye(3)) is None
