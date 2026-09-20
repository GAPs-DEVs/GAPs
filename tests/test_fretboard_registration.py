from dataclasses import replace

import numpy as np
import pytest
import cv2

from vision.fretboard_mapping import (
    DetectedLine, build_grid_from_reference_lines, transform_grid,
)
from vision.fretboard_registration import board_quad, register_fretboard


def seven_frets():
    return build_grid_from_reference_lines(
        DetectedLine('nut', (80, 50), (180, 50), 0.95),
        DetectedLine('fret', (80, 310), (180, 310), 0.95), 7,
    )


def observations(grid, indices):
    return tuple((grid.nut_line, *grid.ordered_fret_lines)[i] for i in indices)


@pytest.mark.parametrize('indices', [(0, 1, 3, 4, 6), (0, 2, 4, 5), (1, 3, 4, 5), (0, 7)])
def test_missing_frets_leave_the_calibrated_seventh_boundary_in_place(indices):
    grid = seven_frets()
    result = register_fretboard(grid, observations(grid, indices), has_image_motion=True)
    assert result is not None
    assert result.observed_indices == indices
    assert len(result.grid.ordered_fret_lines) == 7
    np.testing.assert_allclose(board_quad(result.grid), board_quad(grid), atol=0.01)


def test_more_distant_high_confidence_fret_cannot_replace_seventh_reference():
    grid = seven_frets()
    extra = DetectedLine('fret', (80, 340), (180, 340), 1.0)
    result = register_fretboard(grid, (*observations(grid, (0, 1, 3, 5, 6)), extra), has_image_motion=True)
    assert result is not None
    assert 7 not in result.observed_indices
    np.testing.assert_allclose(board_quad(result.grid), board_quad(grid), atol=0.01)


def test_one_wrong_reference_detection_is_rejected_by_the_other_frets():
    grid = seven_frets()
    noisy_last = DetectedLine('fret', (80, 318), (180, 318), 0.99)
    result = register_fretboard(grid, (*observations(grid, (0, 1, 2, 3, 4, 5, 6)), noisy_last), has_image_motion=True)
    assert result is not None
    assert 7 not in result.observed_indices
    assert abs(result.grid.ordered_fret_lines[-1].p0[1] - 310) < 0.1


def test_occluded_endpoints_do_not_shrink_board_or_change_string_positions():
    grid = seven_frets()
    detected = [grid.nut_line]
    for i, line in enumerate(grid.ordered_fret_lines):
        detected.append(replace(line, p0=(105 + i, line.p0[1]), p1=(165 - i, line.p1[1])))
    result = register_fretboard(grid, tuple(detected), has_image_motion=True)
    assert result is not None
    np.testing.assert_allclose(result.grid.string_crossings, grid.string_crossings, atol=0.01)


def test_unanchored_repetitive_frets_cannot_reacquire_a_lost_board():
    grid = seven_frets()
    assert register_fretboard(grid, observations(grid, (1, 2, 3, 4, 5, 6)), has_image_motion=False) is None


def test_nut_anchors_full_frame_recovery_without_the_last_fret():
    grid = seven_frets()
    motion = np.asarray(((1, 0, 100), (0, 1, 20), (0, 0, 1)), dtype=float)
    moved = transform_grid(grid, motion)
    result = register_fretboard(grid, observations(moved, (0, 1, 3, 4, 6)), has_image_motion=False)
    assert result is not None
    np.testing.assert_allclose(board_quad(result.grid), board_quad(moved), atol=0.01)


def test_local_cluster_is_not_enough_to_extrapolate_last_fret():
    grid = seven_frets()
    assert register_fretboard(grid, observations(grid, (5, 6, 7)), has_image_motion=True) is None


@pytest.mark.parametrize('seed', [72, 13, 99, 123, 414])
def test_repeated_missing_last_and_jitter_never_changes_fret_ids(seed):
    calibrated = seven_frets()
    grid = calibrated
    rng = np.random.default_rng(seed)
    for frame in range(120):
        detected = [calibrated.nut_line]
        for i, line in enumerate(calibrated.ordered_fret_lines):
            if i in (1, 6):
                continue
            jitter = rng.uniform(-2, 2)
            detected.append(replace(line, p0=(105, line.p0[1] + jitter), p1=(160, line.p1[1] + jitter)))
        result = register_fretboard(grid, tuple(detected), has_image_motion=True)
        assert result is not None
        assert set(result.observed_indices).issubset({0, 1, 3, 4, 5, 6})
        assert len(result.observed_indices) >= 4
        grid = result.grid
        assert abs(grid.ordered_fret_lines[-1].p0[1] - 310) < 3


def test_partial_frets_correct_perspective_with_nut_and_last_completely_hidden():
    grid = seven_frets()
    rectifier = cv2.getPerspectiveTransform(board_quad(grid), np.asarray(
        ((0, 0), (1, 0), (1, 1), (0, 1)), dtype=np.float32,
    ))
    perspective = np.asarray(((1.05, .05, -.025), (0, 1.10, 0), (0, .10, 1)))
    actual = transform_grid(grid, np.linalg.inv(rectifier) @ perspective @ rectifier)
    detected = []
    for line in actual.ordered_fret_lines[:-1]:
        first, last = np.asarray((line.p0, line.p1))
        detected.append(replace(line, p0=tuple(first * .72 + last * .28),
                                p1=tuple(first * .27 + last * .73)))
    for _ in range(12):
        result = register_fretboard(grid, tuple(detected), has_image_motion=True)
        assert result is not None
        assert result.observed_indices == (1, 2, 3, 4, 5, 6)
        grid = result.grid
    np.testing.assert_allclose(board_quad(grid), board_quad(actual), atol=.4)


def test_wrong_complete_endpoints_cannot_override_correct_partial_fret_majority():
    grid = seven_frets()
    detected = []
    for index, line in enumerate((grid.nut_line, *grid.ordered_fret_lines)):
        if index in (0, 3, 7):
            detected.append(replace(line, p0=(line.p0[0], line.p0[1] + 8),
                                    p1=(line.p1[0], line.p1[1] + 8)))
        else:
            detected.append(replace(line, p0=(105, line.p0[1]), p1=(165, line.p1[1])))
    result = register_fretboard(grid, tuple(detected), has_image_motion=True)
    assert result is not None
    assert result.observed_indices == (1, 2, 4, 5, 6)
    np.testing.assert_allclose(board_quad(result.grid), board_quad(grid), atol=.01)
