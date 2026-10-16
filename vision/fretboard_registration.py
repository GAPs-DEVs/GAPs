"""Register numbered fret lines to a calibrated board without changing their IDs.

Detector endpoints can stop at an occluding finger. We use each segment's line
inside a rectified board, not its length, to correct the longitudinal geometry.
All frets share one robustly fitted transform; an absent last fret is not replaced
by whichever detected line happens to be farthest away.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

import cv2
import numpy as np

from vision.fretboard_mapping import DetectedLine, StableFretboardGrid, transform_grid
from vision.fretboard_line_fit import fit_projective_fret_lines


@dataclass(frozen=True)
class BoardRegistration:
    grid: StableFretboardGrid
    homography: np.ndarray
    observed_indices: tuple[int, ...]  # Nut=0, actual fret numbers=1..N.
    residual: float


def board_quad(grid: StableFretboardGrid) -> np.ndarray:
    return np.asarray((grid.nut_line.p0, grid.nut_line.p1,
                       grid.ordered_fret_lines[-1].p1, grid.ordered_fret_lines[-1].p0), dtype=np.float32)


def register_fretboard(
    grid: StableFretboardGrid,
    detected: tuple[DetectedLine, ...],
    *,
    has_image_motion: bool,
) -> BoardRegistration | None:
    """Fit a small global correction, anchored by motion or a uniquely typed nut."""
    if not grid.ordered_fret_lines:
        return None
    proposals = [np.eye(3)] if has_image_motion else []
    if not has_image_motion:
        # Without image motion, only the nut may propose a new position. Never
        # bootstrap a displacement from repetitive, unnumbered fret detections.
        old = np.asarray((grid.nut_line.p0, grid.nut_line.p1), dtype=float)
        direction = old[1] - old[0]
        length = np.linalg.norm(direction)
        if length < 8:
            return None
        for line in sorted((line for line in detected if line.kind == 'nut' and line.endpoints_observed),
                           key=lambda line: line.confidence, reverse=True)[:3]:
            new = np.asarray((line.p0, line.p1), dtype=float)
            if np.dot(new[1] - new[0], direction) < 0:
                new = new[::-1]
            next_direction = new[1] - new[0]
            scale = np.linalg.norm(next_direction) / length
            if not 0.8 <= scale <= 1.25:
                continue
            a = np.dot(next_direction, direction) / length**2
            b = (direction[0] * next_direction[1] - direction[1] * next_direction[0]) / length**2
            linear = np.asarray(((a, -b), (b, a)))
            offset = new.mean(axis=0) - linear @ old.mean(axis=0)
            matrix = np.eye(3)
            matrix[:2, :2], matrix[:2, 2] = linear, offset
            proposals.append(matrix)
    best = None
    for coarse in proposals:
        prior = transform_grid(grid, coarse)
        result = _fit_rectified_lines(prior, detected, require_nut=not has_image_motion)
        if result is None:
            continue
        correction, indices, residual = result
        total = correction @ coarse
        candidate = BoardRegistration(transform_grid(grid, total), total, indices, residual)
        if best is None or (len(indices), -residual) > (len(best.observed_indices), -best.residual):
            best = candidate
    return best


def _fit_rectified_lines(grid, detected, *, require_nut):
    quad = board_quad(grid)
    if not np.isfinite(quad).all() or abs(cv2.contourArea(quad)) < 100:
        return None
    rectifier = cv2.getPerspectiveTransform(
        quad, np.asarray(((0, 0), (1, 0), (1, 1), (0, 1)), dtype=np.float32),
    )
    lines = (grid.nut_line, *grid.ordered_fret_lines)
    targets = []
    for line in lines:
        points = cv2.perspectiveTransform(np.asarray((line.p0, line.p1), dtype=np.float32)[None], rectifier)[0]
        targets.append(float(np.mean(points[:, 1])))
    targets = np.asarray(targets)
    gaps = np.diff(targets)
    if np.any(gaps <= 1e-5):
        return None
    local_gap = np.minimum(np.r_[gaps[0], gaps], np.r_[gaps, gaps[-1]])
    matches = {}
    endpoint_matches = {}
    for line in detected:
        if line.kind not in {'nut', 'fret'}:
            continue
        points = cv2.perspectiveTransform(np.asarray((line.p0, line.p1), dtype=np.float32)[None], rectifier)[0]
        if not np.isfinite(points).all():
            continue
        x0, y0 = points[0]
        x1, y1 = points[1]
        if abs(x1 - x0) < 0.25 or min(x0, x1) > 0.85 or max(x0, x1) < 0.15:
            continue
        slope = (y1 - y0) / (x1 - x0)
        intercept = y0 - slope * x0
        center = intercept + slope * 0.5
        if abs(slope) > 0.15:
            continue
        index = 0 if line.kind == 'nut' else int(np.argmin(abs(targets[1:] - center))) + 1
        # Less than half an interval in either direction: adjacent frets cannot
        # substitute for the selected boundary, even at high detector confidence.
        error = max(abs(intercept - targets[index]), abs(intercept + slope - targets[index])) / local_gap[index]
        if error > 0.32:
            continue
        score = error - min(line.confidence, 1) * 0.01
        if index not in matches or score < matches[index][0]:
            matches[index] = (score, float(intercept), float(slope))
            endpoint_matches.pop(index, None)
            if line.endpoints_observed and abs(min(x0, x1)) < 0.08 and abs(max(x0, x1) - 1) < 0.08:
                endpoint_matches[index] = (line.p0, line.p1) if x0 < x1 else (line.p1, line.p0)
    if require_nut and 0 not in matches:
        return None
    indices = sorted(matches)
    if not _sufficient_support(indices, targets):
        return None
    projective = fit_projective_fret_lines(targets, matches, local_gap)
    # Full-width observations constrain lateral position and perspective as well
    # as fret spacing. Fit perspective before an affine consensus could discard
    # valid lines on a tilted guitar. Never use finger-shortened endpoints here.
    full_indices = [index for index in indices if index in endpoint_matches]
    if projective is not None:
        full_indices = [index for index in full_indices if index in projective[1]]
    if len(full_indices) >= 3 and _sufficient_support(full_indices, targets):
        source = np.asarray([point for index in full_indices for point in (lines[index].p0, lines[index].p1)], dtype=np.float32)
        destination = np.asarray([point for index in full_indices for point in endpoint_matches[index]], dtype=np.float32)
        fitted, inliers = cv2.findHomography(source, destination, cv2.RANSAC, 2.0)
        if fitted is not None and inliers is not None and np.isfinite(fitted).all():
            supported = [index for index, pair in zip(full_indices, inliers.reshape(-1, 2)) if np.all(pair)]
            if len(supported) >= 3 and _sufficient_support(supported, targets) and (not require_nut or 0 in supported):
                canonical_fit = rectifier @ fitted @ np.linalg.inv(rectifier)
                if abs(canonical_fit[2, 2]) > 1e-6:
                    canonical_fit /= canonical_fit[2, 2]
                    probes = np.asarray([(x, y) for y in targets for x in (0, 1)], dtype=np.float32)
                    warped = cv2.perspectiveTransform(probes[None], canonical_fit)[0]
                    valid = np.isfinite(warped).all() and np.all(abs(warped[:, 0] - probes[:, 0]) < 0.1)
                    valid = valid and np.all(abs(warped[:, 1] - probes[:, 1]) < np.repeat(local_gap * 0.32, 2))
                    # Complete endpoints are stronger lateral evidence, not
                    # permission for a few bad detections to override the other
                    # frets. Check the pose against partial observations too.
                    all_supported = []
                    for index in indices:
                        pair = warped[2 * index:2 * index + 2]
                        dx = pair[1, 0] - pair[0, 0]
                        if abs(dx) < 1e-6:
                            continue
                        slope = (pair[1, 1] - pair[0, 1]) / dx
                        intercept = pair[0, 1] - slope * pair[0, 0]
                        _, observed_intercept, observed_slope = matches[index]
                        error = max(abs(intercept - observed_intercept),
                                    abs(intercept + slope - observed_intercept - observed_slope))
                        if error <= local_gap[index] * .10:
                            all_supported.append(index)
                    valid = valid and len(all_supported) > len(indices) / 2
                    valid = valid and len(set(supported).intersection(all_supported)) >= 3
                    valid = valid and (not require_nut or 0 in all_supported)
                    valid = valid and (projective is None or len(all_supported) >= len(projective[1]))
                    if valid:
                        # A perspective error can be large at just one end of
                        # the board; the median would leave that end drifting.
                        error = float(np.percentile(np.linalg.norm(destination - source, axis=1)[inliers.ravel().astype(bool)], 90))
                        alpha = 0.85 if error > 2 else 0.35
                        smoothed = np.eye(3) + alpha * (canonical_fit - np.eye(3))
                        projected = cv2.perspectiveTransform(source[None], fitted)[0]
                        residual = float(np.mean(np.linalg.norm(projected - destination, axis=1)[inliers.ravel().astype(bool)]))
                        return np.linalg.inv(rectifier) @ smoothed @ rectifier, tuple(all_supported), residual
    if projective is not None and (not require_nut or 0 in projective[1]):
        correction, supported, residual = projective
        # Keep detector jitter damped without smoothing away the image motion.
        probes = np.asarray([(x, y) for y in targets for x in (0, 1)], dtype=np.float32)
        image_points = cv2.perspectiveTransform(probes[None], np.linalg.inv(rectifier))[0]
        moved = cv2.perspectiveTransform(probes[None], np.linalg.inv(rectifier) @ correction)[0]
        error = float(np.percentile(np.linalg.norm(moved - image_points, axis=1), 90))
        # Large extrapolated motion from noisy interior lines is not evidence
        # that the guitar moved quickly; image flow already handles that motion.
        coherent = residual < .05 and len(supported) == len(matches)
        alpha = .75 if error > 2 and coherent else .25 / (1 + 4 * residual)
        correction = np.eye(3) + alpha * (correction - np.eye(3))
        return np.linalg.inv(rectifier) @ correction @ rectifier, supported, residual

    # With fewer visible frets, retain the bounded affine correction. It cannot
    # invent perspective from a poorly constrained or narrow cluster of lines.
    centers = np.asarray([matches[i][1] + matches[i][2] * 0.5 for i in indices])
    positions = targets[indices]
    tolerances = local_gap[indices] * 0.16
    best_keep = None
    best_error = float('inf')
    for first, second in combinations(range(len(indices)), 2):
        if positions[second] - positions[first] < 0.15:
            continue
        scale = (centers[second] - centers[first]) / (positions[second] - positions[first])
        shift = centers[first] - scale * positions[first]
        residuals = abs(centers - (scale * positions + shift))
        keep = residuals <= tolerances
        kept_indices = [index for index, valid in zip(indices, keep) if valid]
        if not _sufficient_support(kept_indices, targets) or (require_nut and 0 not in kept_indices):
            continue
        error = float(np.mean(residuals[keep] / tolerances[keep]))
        if best_keep is None or keep.sum() > best_keep.sum() or (keep.sum() == best_keep.sum() and error < best_error):
            best_keep, best_error = keep, error
    if best_keep is None:
        return None
    indices = [index for index, keep in zip(indices, best_keep) if keep]
    design, values = [], []
    for index in indices:
        _, intercept, slope = matches[index]
        design.extend(((targets[index], 0, 1), (targets[index], 1, 1)))
        values.extend((intercept, intercept + slope))
    scale, shear, shift = np.linalg.lstsq(np.asarray(design), np.asarray(values), rcond=None)[0]
    correction = np.asarray(((1, 0, 0), (shear, scale, shift), (0, 0, 1)), dtype=float)
    # Validate the extrapolated last fret too, even when it is occluded.
    for x in (0, 1):
        offsets = abs(shear * x + scale * targets + shift - targets)
        if np.any(offsets > local_gap * 0.32):
            return None
    # Smooth only the small detector correction, not real image motion.
    correction = np.eye(3) + 0.25 * (correction - np.eye(3))
    image_correction = np.linalg.inv(rectifier) @ correction @ rectifier
    return image_correction, tuple(indices), best_error


def _sufficient_support(indices, positions):
    if len(indices) < 2:
        return False
    # Two explicitly anchored ends, or three spread-out numbered lines.
    anchored_pair = 0 in indices and len(positions) - 1 in indices
    return (anchored_pair or len(indices) >= 3) and positions[indices[-1]] - positions[indices[0]] >= 0.35
