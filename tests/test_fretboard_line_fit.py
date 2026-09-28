import numpy as np
import pytest

from vision.fretboard_line_fit import fit_projective_fret_lines


def fret_coordinates(count=7):
    positions = 1 - 2 ** (-np.arange(count + 1) / 12)
    positions /= positions[-1]
    gaps = np.diff(positions)
    return positions, np.minimum(np.r_[gaps[0], gaps], np.r_[gaps, gaps[-1]])


def neutral_transverse_transform(b=0, a=1, c=0, g=0, h=0):
    return np.asarray(((1 + g + h / 2, h / 2, -(g + h) / 4),
                       (b, a, c), (g, h, 1)), dtype=float)


def partial_observations(targets, matrix, indices):
    matches = {}
    for index in indices:
        # Only an interior fragment is visible: its ends are not neck edges.
        source = np.asarray(((0.28, targets[index], 1), (0.73, targets[index], 1)))
        projected = source @ matrix.T
        points = projected[:, :2] / projected[:, 2:]
        slope = (points[1, 1] - points[0, 1]) / (points[1, 0] - points[0, 0])
        intercept = points[0, 1] - slope * points[0, 0]
        matches[index] = (0.01, intercept, slope)
    return matches


def test_partial_frets_recover_projective_spacing_with_both_ends_hidden():
    targets, gaps = fret_coordinates()
    matrix = neutral_transverse_transform(a=1.10, h=0.10)
    matches = partial_observations(targets, matrix, (1, 2, 3, 4, 5, 6))
    result = fit_projective_fret_lines(targets, matches, gaps)
    assert result is not None
    correction, indices, residual = result
    assert indices == (1, 2, 3, 4, 5, 6)
    np.testing.assert_allclose(correction, matrix, atol=1e-10)
    assert residual < 1e-10
    # Affine spacing cannot represent this curvature even with perfect data.
    visible = targets[list(matches)]
    centers = np.asarray([match[1] + match[2] / 2 for match in matches.values()])
    affine = np.polyfit(visible, centers, 1)
    assert np.max(abs(np.polyval(affine, visible) - centers)) > 0.002


def test_projective_family_handles_varying_fret_slopes_and_rejects_outlier():
    targets, gaps = fret_coordinates()
    matrix = neutral_transverse_transform(b=0.006, a=1.04, c=-0.003, g=0.01, h=0.05)
    matches = partial_observations(targets, matrix, range(8))
    _, intercept, slope = matches[5]
    matches[5] = (-1.0, intercept + gaps[5] * 0.28, slope + 0.01)
    result = fit_projective_fret_lines(targets, matches, gaps)
    assert result is not None
    correction, indices, _ = result
    assert 5 not in indices
    np.testing.assert_allclose(correction, matrix, atol=1e-10)


def test_incompatible_shifted_lines_cannot_win_with_a_curved_compromise():
    targets, gaps = fret_coordinates()
    matches = partial_observations(targets, np.eye(3), range(8))
    for index in (0, 3, 7):
        _, intercept, slope = matches[index]
        matches[index] = (-1.0, intercept + 8 / 260, slope)
    result = fit_projective_fret_lines(targets, matches, gaps)
    assert result is not None
    correction, indices, residual = result
    assert indices == (1, 2, 4, 5, 6)
    np.testing.assert_allclose(correction, np.eye(3), atol=1e-10)
    assert residual < 1e-10


@pytest.mark.parametrize('indices', [(0, 3, 7), (4, 5, 6, 7)])
def test_sparse_or_clustered_support_does_not_infer_remote_frets(indices):
    targets, gaps = fret_coordinates()
    matches = partial_observations(targets, np.eye(3), indices)
    assert fit_projective_fret_lines(targets, matches, gaps) is None


def test_identity_measurements_preserve_transverse_pose():
    targets, gaps = fret_coordinates()
    result = fit_projective_fret_lines(
        targets, partial_observations(targets, np.eye(3), (0, 2, 4, 6)), gaps,
    )
    assert result is not None
    np.testing.assert_allclose(result[0], np.eye(3), atol=1e-10)


def test_unobservable_transverse_shift_is_not_fabricated_from_fret_fragments():
    targets, gaps = fret_coordinates()
    lateral = np.asarray(((0.85, 0, 0.12), (0, 1, 0), (0, 0, 1)))
    result = fit_projective_fret_lines(
        targets, partial_observations(targets, lateral, range(8)), gaps,
    )
    assert result is not None
    # The line family is unchanged; width and sideways shift are unobservable.
    np.testing.assert_allclose(result[0], np.eye(3), atol=1e-10)


def test_projective_pole_or_large_unseen_end_displacement_is_rejected():
    targets, gaps = fret_coordinates()
    matrix = neutral_transverse_transform(a=0.96, h=-0.12)
    matches = partial_observations(targets, matrix, (0, 1, 2, 3, 4))
    # All observations satisfy the association gate, but extrapolation would
    # put the hidden last fret more than .32 of an interval from the prior.
    assert all(abs(intercept - targets[i]) < gaps[i] * .32
               for i, (_, intercept, _) in matches.items())
    hidden_end = (matrix[1, 1] + matrix[1, 2]) / (matrix[2, 1] + 1)
    assert abs(hidden_end - targets[-1]) > gaps[-1] * .32
    assert fit_projective_fret_lines(targets, matches, gaps) is None


@pytest.mark.parametrize('invalid', ['nan', 'repeated', 'zero_gap'])
def test_invalid_or_degenerate_coordinates_are_rejected(invalid):
    targets, gaps = fret_coordinates()
    matches = partial_observations(targets, np.eye(3), range(8))
    if invalid == 'nan':
        targets[3] = np.nan
    elif invalid == 'repeated':
        targets[3] = targets[2]
    else:
        gaps[3] = 0
    assert fit_projective_fret_lines(targets, matches, gaps) is None
