"""Visibility of semantic note anchors on an already validated fretboard pose.

An open note belongs to a string on the nut, which is the boundary of the board.
It must not be tested as an interior point: projection rounding and independent
marker smoothing can put that boundary point just outside the board polygon.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

import cv2
import numpy as np

from vision.fretboard_mapping import NoteMarker, StableFretboardGrid


def open_string_marker(grid: StableFretboardGrid, string_index: int) -> NoteMarker | None:
    """Anchor fret zero to the current nut while retaining its string position."""
    if not grid.string_crossings or not 0 <= string_index < len(grid.string_crossings[0]):
        return None
    point = np.asarray(grid.string_crossings[0][string_index], dtype=np.float64)
    start = np.asarray(grid.nut_line.p0, dtype=np.float64)
    delta = np.asarray(grid.nut_line.p1, dtype=np.float64) - start
    if not np.isfinite(np.concatenate((point, start, delta))).all():
        return None
    length_squared = float(delta @ delta)
    if length_squared < 1.0:
        return None
    fraction = float((point - start) @ delta / length_squared)
    if not -1e-5 <= fraction <= 1.0 + 1e-5:
        return None
    projected = start + np.clip(fraction, 0.0, 1.0) * delta
    # Crossings and nut must describe the same pose, not an unrelated string fit.
    if np.linalg.norm(projected - point) > 1.0:
        return None
    return NoteMarker(string_index, 0, tuple(map(float, projected)), True)


def filter_visible_markers(
    markers: Sequence[NoteMarker],
    grid: StableFretboardGrid | None,
    frame_shape: Sequence[int],
    exclusion_mask: np.ndarray | None = None,
    *,
    hide_occluded: bool = True,
) -> tuple[NoteMarker, ...]:
    """Reject invalid anchors, reattach open notes, then apply screen/hand masks.

    Occlusion does not invalidate the calibrated nut. With ``hide_occluded`` the
    marker is hidden only when its current anchor itself is covered by the hand.
    Callers must already have accepted the current whole-board tracking pose.
    """
    if grid is None:
        return ()
    visible = []
    height, width = frame_shape[:2]
    for marker in markers:
        if marker.is_open:
            if marker.fret_index != 0:
                continue
            # Independent pixel smoothing may leave the old point outside a
            # moving nut. Fret zero is defined by this pose's nut, not that point.
            anchored = open_string_marker(grid, marker.string_index)
            if anchored is None:
                continue
            marker = replace(marker, point_xy=anchored.point_xy)
        elif not 1 <= marker.fret_index <= len(grid.ordered_fret_lines):
            continue
        point = np.asarray(marker.point_xy, dtype=np.float64)
        if not np.isfinite(point).all() or not (0 <= point[0] < width and 0 <= point[1] < height):
            continue
        if hide_occluded and exclusion_mask is not None:
            mask_height, mask_width = exclusion_mask.shape[:2]
            if not (point[0] < mask_width and point[1] < mask_height):
                continue
            x = min(int(round(point[0])), mask_width - 1)
            y = min(int(round(point[1])), mask_height - 1)
            if exclusion_mask[y, x]:
                continue
        if not marker.is_open:
            previous = grid.nut_line if marker.fret_index == 1 else grid.ordered_fret_lines[marker.fret_index - 2]
            current = grid.ordered_fret_lines[marker.fret_index - 1]
            polygon = np.asarray((previous.p0, previous.p1, current.p1, current.p0), dtype=np.float32)
            if not np.isfinite(polygon).all():
                continue
            # At edge-on viewing angles a fret interval can be smaller than a dot.
            if np.linalg.norm((polygon[2] + polygon[3] - polygon[0] - polygon[1]) / 2) < 5:
                continue
            if cv2.pointPolygonTest(polygon, tuple(map(float, point)), False) < 0:
                continue
        visible.append(marker)
    return tuple(visible)
