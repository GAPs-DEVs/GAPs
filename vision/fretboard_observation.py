"""Observe visible metal fret segments near a motion-anchored board prediction.

These are image measurements, never substitutes generated from the fret model.
They carry no nut identity and cannot reacquire a board without an image anchor.
"""
from __future__ import annotations

import cv2
import numpy as np

from vision.fretboard_mapping import DetectedLine, StableFretboardGrid
from vision.fretboard_registration import board_quad


def observe_fret_segments(
    frame: np.ndarray,
    grid: StableFretboardGrid,
    exclusion_mask: np.ndarray | None = None,
    existing: tuple[DetectedLine, ...] = (),
) -> tuple[DetectedLine, ...]:
    """Supplement missing detector lines with locally constrained LSD edges.

    Keep the actual visible segment extents. In particular, an interior metal
    edge must never masquerade as two measured neck-boundary endpoints.
    """
    if not grid.ordered_fret_lines:
        return ()
    quad = board_quad(grid)
    if not np.isfinite(quad).all() or abs(cv2.contourArea(quad)) < 100:
        return ()
    width = int(np.clip(max(np.linalg.norm(quad[1] - quad[0]), np.linalg.norm(quad[2] - quad[3])), 48, 320))
    height = int(np.clip(max(np.linalg.norm(quad[3] - quad[0]), np.linalg.norm(quad[2] - quad[1])), 100, 960))
    rectifier = cv2.getPerspectiveTransform(quad, np.asarray(
        ((0, 0), (width - 1, 0), (width - 1, height - 1), (0, height - 1)), dtype=np.float32,
    ))
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    warped = cv2.warpPerspective(gray, rectifier, (width, height))
    # Do not paint the hand mask into the image: its boundary creates false lines.
    visible = np.full(gray.shape, 255, dtype=np.uint8)
    if exclusion_mask is not None:
        visible[exclusion_mask > 0] = 0
    visible = cv2.warpPerspective(visible, rectifier, (width, height), flags=cv2.INTER_NEAREST)
    visible = cv2.erode(visible, np.ones((5, 5), dtype=np.uint8))
    source = (grid.nut_line, *grid.ordered_fret_lines)
    points = np.asarray([(line.p0, line.p1) for line in source], dtype=np.float32)
    targets = cv2.perspectiveTransform(points.reshape(1, -1, 2), rectifier)[0, :, 1].reshape(-1, 2).mean(axis=1)
    gaps = np.diff(targets)
    if np.any(gaps <= 3):
        return ()
    local_gap = np.minimum(np.r_[gaps[0], gaps], np.r_[gaps, gaps[-1]])
    occupied = set()
    for line in existing:
        if line.kind != 'fret':
            continue
        pair = cv2.perspectiveTransform(np.asarray([[line.p0, line.p1]], dtype=np.float32), rectifier)[0]
        if not np.isfinite(pair).all() or abs(pair[1, 0] - pair[0, 0]) < width * .25:
            continue
        slope = (pair[1, 1] - pair[0, 1]) / (pair[1, 0] - pair[0, 0])
        center = pair[0, 1] + slope * ((width - 1) / 2 - pair[0, 0])
        index = int(np.argmin(abs(targets[1:] - center))) + 1
        intercept = pair[0, 1] - slope * pair[0, 0]
        error = max(abs(intercept - targets[index]), abs(intercept + slope * (width - 1) - targets[index]))
        if abs(slope * (width - 1) / (height - 1)) <= .15 and error < local_gap[index] * .32:
            occupied.add(index)
    segments = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD).detect(warped)[0]
    if segments is None:
        return ()
    candidates: dict[int, list] = {}
    for segment in segments[:, 0]:
        pair = segment.reshape(2, 2)
        pair = pair[np.argsort(pair[:, 0])]
        # Trim away board edges: LSD measures wire edges, not neck width.
        left, right = max(float(pair[0, 0]), width * .1), min(float(pair[1, 0]), width * .9)
        if right - left < width * .26:
            continue
        slope = float((pair[1, 1] - pair[0, 1]) / (pair[1, 0] - pair[0, 0]))
        if abs(slope * width / height) > .12:
            continue
        intercept = float(pair[0, 1] - slope * pair[0, 0])
        center = intercept + slope * (width - 1) / 2
        index = int(np.argmin(abs(targets[1:] - center))) + 1
        if index in occupied or max(abs(intercept - targets[index]), abs(intercept + slope * width - targets[index])) > local_gap[index] * .30:
            continue
        xs = np.linspace(left, right, max(12, int(right - left)))
        ys = intercept + slope * xs
        pixels = np.rint(np.column_stack((xs, ys))).astype(int)
        valid = (pixels[:, 1] >= 0) & (pixels[:, 1] < height)
        valid_indices = np.flatnonzero(valid)
        valid[valid_indices] &= visible[pixels[valid_indices, 1], pixels[valid_indices, 0]] > 0
        runs = np.split(np.arange(len(xs)), np.flatnonzero(np.diff(valid.astype(int))) + 1)
        run = max((run for run in runs if valid[run[0]]), key=len, default=[])
        if len(run) < 2 or xs[run[-1]] - xs[run[0]] < width * .26:
            continue
        candidates.setdefault(index, []).append((center, slope, intercept, xs[run[0]], xs[run[-1]]))
    observed = []
    inverse = np.linalg.inv(rectifier)
    for index, entries in sorted(candidates.items()):
        # A metal wire usually creates two parallel image edges. Average their
        # center, but reject competing ridges instead of snapping to the prior.
        centers = np.asarray([entry[0] for entry in entries])
        if np.ptp(centers) > min(5.0, local_gap[index] * .22):
            continue
        lengths = np.asarray([entry[4] - entry[3] for entry in entries])
        slope, intercept = np.average(np.asarray(entries)[:, 1:3], axis=0, weights=lengths)
        longest = entries[int(np.argmax(lengths))]
        left, right = longest[3:5]
        samples_x = np.linspace(left, right, max(12, int(right - left)))
        pixels = np.rint(np.column_stack((samples_x, intercept + slope * samples_x))).astype(int)
        if np.any(pixels[:, 1] < 0) or np.any(pixels[:, 1] >= height) or np.any(visible[pixels[:, 1], pixels[:, 0]] == 0):
            continue
        segment = np.asarray([[(left, intercept + slope * left), (right, intercept + slope * right)]], dtype=np.float32)
        image_points = cv2.perspectiveTransform(segment, inverse)[0]
        observed.append(DetectedLine('fret', tuple(image_points[0]), tuple(image_points[1]), .55,
                                     endpoints_observed=False))
    return tuple(observed)
