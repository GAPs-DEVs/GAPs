"""Small projective corrections from a family of partially visible fret lines.

Three or more numbered parallel lines constrain five homography parameters, not
all eight. The other three describe motion *along* the frets. We explicitly keep
the prior's transverse centerline and width at the board center. Consequently
this fit corrects fret spacing/tilt, but cannot measure sideways guitar motion or
string locations without image tracks, board edges, or reliable endpoints.
"""
from __future__ import annotations

from itertools import combinations

import numpy as np


MIN_SUPPORT = 4
MIN_SPAN = 0.45
MAX_HYPOTHESES = 96
INLIER_GAP_RATIO = 0.10
MAX_CORRECTION_GAP_RATIO = 0.32
MAX_JACKKNIFE_GAP_RATIO = 0.15
EXTRA_PARAMETER_PENALTY = 0.25


def fit_projective_fret_lines(
    targets: np.ndarray,
    matches: dict[int, tuple[float, float, float]],
    local_gap: np.ndarray,
) -> tuple[np.ndarray, tuple[int, ...], float] | None:
    """Fit one coherent line family, including its unobserved end frets.

    ``targets`` and ``local_gap`` use rectified board units. Each match contains
    ``(association_score, intercept, slope)`` for ``y = intercept + slope*x``.
    Segment endpoints are deliberately not treated as board-edge observations.
    The returned correction is unsmoothed. Ambiguous or unstable extrapolation
    is rejected rather than reported as a reliable hidden-fret position.
    """
    targets = np.asarray(targets, dtype=float)
    local_gap = np.asarray(local_gap, dtype=float)
    if (targets.ndim != 1 or len(targets) < MIN_SUPPORT
            or local_gap.shape != targets.shape
            or not np.isfinite(targets).all() or not np.isfinite(local_gap).all()
            or np.any(np.diff(targets) <= 1e-5) or np.any(local_gap <= 1e-5)):
        return None
    indices = np.asarray(sorted(
        index for index, match in matches.items()
        if 0 <= index < len(targets) and np.isfinite(match).all()
    ), dtype=int)
    if not _sufficient_support(indices, targets):
        return None
    positions = targets[indices]
    observed = np.asarray([(matches[i][1], matches[i][2]) for i in indices])
    tolerances = local_gap[indices] * INLIER_GAP_RATIO
    hypotheses = [triple for triple in combinations(range(len(indices)), 3)
                  if np.ptp(positions[list(triple)]) >= 0.35]
    if len(hypotheses) > MAX_HYPOTHESES:
        selected = np.linspace(0, len(hypotheses) - 1, MAX_HYPOTHESES, dtype=int)
        hypotheses = [hypotheses[i] for i in selected]
    # Include the all-observation solution so distributed small jitter is not
    # penalized against hypotheses that exactly interpolate their three seeds.
    hypotheses.insert(0, tuple(range(len(indices))))
    best_keep, best_rank, best_projective = None, None, False
    for projective in (False, True):
        for triple in hypotheses:
            chosen = np.asarray(triple)
            candidate = _solve(positions[chosen], observed[chosen], tolerances[chosen], projective)
            if candidate is None or not _safe_projection(candidate, targets, local_gap):
                continue
            errors = _line_errors(candidate, positions, observed) / tolerances
            keep = errors <= 1.0
            if not _sufficient_support(indices[keep], targets):
                continue
            # Counting every point inside the hard gate equally rewards a
            # curved compromise between incompatible planes. Tukey's bounded
            # support score gives gross outliers no influence on selection.
            evidence = float(np.sum(np.maximum(1.0 - errors**2, 0.0)**3))
            # The two additional perspective parameters must improve evidence
            # by half a perfectly agreeing fret. Otherwise prefer an affine
            # family: detector jitter must not invent remote-end curvature.
            # Vanish the penalty for an exactly coherent family. A fixed
            # penalty would leave a systematic perspective error once the
            # remaining correction becomes small during repeated updates.
            noise = min(1.0, float(np.mean(errors[keep])) / .05)
            penalty = 2 * EXTRA_PARAMETER_PENALTY * noise if projective else 0.0
            rank = (evidence - penalty, int(keep.sum()), -float(np.mean(errors[keep])))
            if best_rank is None or rank > best_rank:
                best_keep, best_rank, best_projective = keep, rank, projective
    if best_keep is None:
        return None

    # Refit the consensus, then check that it remains a consensus. Outliers do
    # not get pulled into the final plane simply because they have high scores.
    keep = best_keep
    for _ in range(2):
        fitted = _solve(positions[keep], observed[keep], tolerances[keep], best_projective)
        if fitted is None or not _safe_projection(fitted, targets, local_gap):
            return None
        errors = _line_errors(fitted, positions, observed) / tolerances
        revised = errors <= 1.0
        if not _sufficient_support(indices[revised], targets):
            return None
        if np.array_equal(revised, keep):
            break
        keep = revised
    else:
        fitted = _solve(positions[keep], observed[keep], tolerances[keep], best_projective)
        if fitted is None or not _safe_projection(fitted, targets, local_gap):
            return None
        errors = _line_errors(fitted, positions, observed) / tolerances
        if np.any(errors[keep] > 1):
            return None

    # An exact four-line fit can still extrapolate a poor last fret. Removing
    # each observation must leave the inferred board consistent with the fit.
    reference = _project_probes(fitted, targets)
    support = np.flatnonzero(keep)
    for omitted in support:
        remaining = support[support != omitted]
        held_out_fit = _solve(positions[remaining], observed[remaining], tolerances[remaining], best_projective)
        if held_out_fit is None or not _safe_projection(held_out_fit, targets, local_gap):
            return None
        held_out = _project_probes(held_out_fit, targets)
        if np.any(abs(held_out[:, :, 1] - reference[:, :, 1])
                  > local_gap[:, None] * MAX_JACKKNIFE_GAP_RATIO):
            return None
    return fitted, tuple(int(i) for i in indices[keep]), float(np.mean(errors[keep]))


def _sufficient_support(indices: np.ndarray, targets: np.ndarray) -> bool:
    return len(indices) >= MIN_SUPPORT and np.ptp(targets[indices]) >= MIN_SPAN


def _solve(positions, observed, tolerances, projective=True):
    # Homography gauge: x=1/2 remains x'=1/2, and dx'/dx=1 at (1/2,1/2).
    # This fixes only the three unobservable parameters; perspective-induced
    # taper elsewhere follows the measured fret-line family.
    rows, values = [], []
    for t, (intercept, slope), tolerance in zip(positions, observed, tolerances):
        for x in (0.0, 1.0):
            rows.append(np.asarray((
                x, t, 1.0,
                -slope * x + slope * 0.25 - intercept * x,
                -slope * x * 0.5 - slope * t * 0.5 + slope * 0.25 - intercept * t,
            )) / tolerance)
            values.append((slope * x + intercept) / tolerance)
    design = np.asarray(rows)
    if not projective:
        design = design[:, :3]
    scales = np.linalg.norm(design, axis=0)
    if np.any(scales < 1e-10):
        return None
    design /= scales
    parameters, _, rank, singular = np.linalg.lstsq(design, values, rcond=None)
    if rank < design.shape[1] or singular[-1] < singular[0] * 1e-4:
        return None
    parameters /= scales
    b, a, c = parameters[:3]
    g, h = parameters[3:] if projective else (0.0, 0.0)
    matrix = np.asarray(((1 + g + h * 0.5, h * 0.5, -(g + h) * 0.25),
                         (b, a, c), (g, h, 1.0)))
    return matrix if np.isfinite(matrix).all() else None


def _project_probes(matrix, targets):
    source = np.asarray([[(x, t, 1.0) for x in (0.0, 1.0)] for t in targets])
    projected = source @ matrix.T
    return projected[:, :, :2] / projected[:, :, 2:]


def _safe_projection(matrix, targets, local_gap):
    # Checking every fret protects the extrapolated ends as well as the visible
    # region. In particular, no projective pole may pass through the board.
    denominators = np.asarray([matrix[2, 0] * x + matrix[2, 1] * t + 1
                              for t in targets for x in (0.0, 1.0)])
    if not np.isfinite(denominators).all() or np.any(denominators <= 0.5):
        return False
    projected = _project_probes(matrix, targets)
    return (np.isfinite(projected).all()
            and np.all(abs(projected[:, :, 0] - (0, 1)) < 0.1)
            and np.all(abs(projected[:, :, 1] - targets[:, None])
                       <= local_gap[:, None] * MAX_CORRECTION_GAP_RATIO)
            and np.all(np.diff(projected[:, :, 1], axis=0) > 1e-5)
            and np.linalg.det(matrix) > 1e-5)


def _line_errors(matrix, positions, observed):
    projected = _project_probes(matrix, positions)
    delta = projected[:, 1] - projected[:, 0]
    if np.any(abs(delta[:, 0]) < 1e-6):
        return np.full(len(positions), np.inf)
    slopes = delta[:, 1] / delta[:, 0]
    intercepts = projected[:, 0, 1] - slopes * projected[:, 0, 0]
    return np.maximum(abs(intercepts - observed[:, 0]),
                      abs(intercepts + slopes - observed.sum(axis=1)))
