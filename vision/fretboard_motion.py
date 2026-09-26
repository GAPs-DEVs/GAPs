"""Track a calibrated board against an immutable visual reference.

Detector segments remain observations. They never rewrite the calibrated board
or the reference image, so occluding fingers cannot gradually redefine its shape.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic

import cv2
import numpy as np

from vision.fretboard_mapping import (
    DetectedLine,
    SelectedLineTracker,
    SelectedTrackingResult,
    StableFretboardGrid,
    transform_grid,
)

from vision.fretboard_reference import FretboardVisualReference
from vision.marker_visibility import filter_visible_markers


ROI = tuple[int, int, int, int]
Detector = Callable[[ROI | None], tuple[DetectedLine, ...]]


def unmasked_points(points: np.ndarray, mask: np.ndarray | None) -> np.ndarray:
    points = np.asarray(points).reshape(-1, 2)
    finite = np.isfinite(points).all(axis=1)
    if mask is None:
        return finite
    xy = np.rint(np.where(np.isfinite(points), points, -1)).astype(int)
    valid = finite & (xy[:, 0] >= 0) & (xy[:, 0] < mask.shape[1]) & (xy[:, 1] >= 0) & (xy[:, 1] < mask.shape[0])
    indices = np.flatnonzero(valid)
    valid[indices] &= mask[xy[indices, 1], xy[indices, 0]] == 0
    return valid


def unoccluded_lines(lines, mask):
    if mask is None:
        return tuple(lines)
    clean = []
    for line in lines:
        samples = np.linspace(line.p0, line.p1, 25)
        # Preserve separate visible fragments on both sides of a finger. Never
        # join them into fabricated full-width endpoint observations.
        visible = unmasked_points(samples, mask)
        runs = np.split(np.arange(25), np.flatnonzero(np.diff(visible.astype(int))) + 1)
        for run in runs:
            if len(run) >= 7 and visible[run[0]]:
                clean.append(DetectedLine(line.kind, tuple(samples[run[0]]), tuple(samples[run[-1]]), line.confidence,
                                          endpoints_observed=line.endpoints_observed and len(run) == 25))
    return tuple(clean)


def valid_board_projection(grid, frame_shape):
    quad = grid_quad(grid)
    if not np.isfinite(quad).all() or not cv2.isContourConvex(quad):
        return False
    area = abs(cv2.contourArea(quad))
    if area < 100 or min(np.linalg.norm(quad[1] - quad[0]), np.linalg.norm(quad[2] - quad[3])) < 12:
        return False
    height, width = frame_shape[:2]
    screen = np.asarray(((0, 0), (width - 1, 0), (width - 1, height - 1), (0, height - 1)), dtype=np.float32)
    visible_area, _ = cv2.intersectConvexConvex(quad, screen)
    return visible_area / area >= 0.5


def grid_quad(grid: StableFretboardGrid) -> np.ndarray:
    last = grid.ordered_fret_lines[-1]
    return np.asarray([grid.nut_line.p0, grid.nut_line.p1, last.p1, last.p0], dtype=np.float32)


class FretboardMotionTracker:
    def __init__(self, *, blur_size: int = 1) -> None:
        self.blur_size = blur_size
        self.clear()

    def _gray(self, frame: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return cv2.GaussianBlur(gray, (self.blur_size, self.blur_size), 0) if self.blur_size > 1 else gray

    def clear(self) -> None:
        self.previous_gray: np.ndarray | None = None
        self.points: np.ndarray | None = None
        self.tracked_points: np.ndarray | None = None
        self.previous_exclusion_mask: np.ndarray | None = None

    def estimate(self, frame: np.ndarray, grid: StableFretboardGrid | None, exclusion_mask=None,
                 *, initial_homography: np.ndarray | None = None) -> np.ndarray | None:
        self.tracked_points = None
        gray = self._gray(frame)
        if (
            grid is None or not grid.ordered_fret_lines
            or self.previous_gray is None or self.previous_gray.shape != gray.shape
            or self.points is None or len(self.points) < 12
        ):
            return None
        initial_points = None
        flags = 0
        if initial_homography is not None:
            initial_points = cv2.perspectiveTransform(self.points, initial_homography)
            if not np.isfinite(initial_points).all():
                return None
            flags = cv2.OPTFLOW_USE_INITIAL_FLOW
        next_points, forward_status, errors = cv2.calcOpticalFlowPyrLK(
            self.previous_gray, gray, self.points, initial_points,
            winSize=(21, 21), maxLevel=3,
            criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01),
            flags=flags,
        )
        if next_points is None or forward_status is None or errors is None:
            return None
        backward_points, backward_status, _ = cv2.calcOpticalFlowPyrLK(
            gray, self.previous_gray, next_points, self.points.copy() if flags else None,
            winSize=(21, 21), maxLevel=3, flags=flags,
        )
        if backward_points is None or backward_status is None:
            return None
        old = self.points.reshape(-1, 2)
        new = next_points.reshape(-1, 2)
        fb_error = np.linalg.norm(old - backward_points.reshape(-1, 2), axis=1)
        good = (
            (forward_status.ravel() == 1) & (backward_status.ravel() == 1)
            & (errors.ravel() < 30) & (fb_error < 1.5)
            & np.isfinite(new).all(axis=1)
            & (new[:, 0] >= 0) & (new[:, 0] < gray.shape[1])
            & (new[:, 1] >= 0) & (new[:, 1] < gray.shape[0])
        )
        good &= unmasked_points(old, self.previous_exclusion_mask) & unmasked_points(new, exclusion_mask)
        old, new = old[good], new[good]
        if len(old) < 12:
            return None
        homography, inliers = cv2.findHomography(old, new, cv2.RANSAC, 2.0)
        if homography is None or inliers is None or not np.isfinite(homography).all():
            return None
        keep = inliers.ravel().astype(bool)
        if keep.sum() < 12 or keep.mean() < 0.65:
            return None
        old, new = old[keep], new[keep]
        quad = grid_quad(grid)
        area = abs(cv2.contourArea(quad))
        if area < 100 or abs(cv2.contourArea(cv2.convexHull(old))) < area * 0.18:
            return None
        # Reject tracks concentrated on a hand or one small patch of the neck.
        to_unit = cv2.getPerspectiveTransform(
            quad, np.asarray([(0, 0), (1, 0), (1, 1), (0, 1)], dtype=np.float32),
        )
        unit = cv2.perspectiveTransform(old.reshape(-1, 1, 2), to_unit).reshape(-1, 2)
        if np.any(np.ptp(unit, axis=0) < (0.45, 0.4)):
            return None
        denominators = np.c_[quad, np.ones(4)] @ homography[2]
        if np.any(np.abs(denominators) < 1e-6) or np.any(denominators * denominators[0] <= 0):
            return None
        moved = cv2.perspectiveTransform(quad.reshape(-1, 1, 2), homography).reshape(-1, 2)
        if not np.isfinite(moved).all() or not cv2.isContourConvex(moved):
            return None
        if cv2.contourArea(quad, oriented=True) * cv2.contourArea(moved, oriented=True) <= 0:
            return None
        area_ratio = abs(cv2.contourArea(moved)) / area
        edges = np.linalg.norm(np.roll(quad, -1, axis=0) - quad, axis=1)
        moved_edges = np.linalg.norm(np.roll(moved, -1, axis=0) - moved, axis=1)
        ratios = moved_edges / np.maximum(edges, 1)
        if not 0.65 < area_ratio < 1.55 or np.any((ratios < 0.65) | (ratios > 1.5)):
            return None
        if np.max(np.linalg.norm(moved - quad, axis=1)) > max(100, float(edges.max()) * 0.4):
            return None
        projected = cv2.perspectiveTransform(old.reshape(-1, 1, 2), homography).reshape(-1, 2)
        if np.median(np.linalg.norm(projected - new, axis=1)) > 1.25:
            return None
        self.tracked_points = new.reshape(-1, 1, 2)
        return homography

    def commit(self, frame: np.ndarray, grid: StableFretboardGrid | None, *, refresh: bool, exclusion_mask=None) -> None:
        if grid is None or not grid.ordered_fret_lines:
            self.clear()
            return
        gray = self._gray(frame)
        mask = np.zeros(gray.shape, dtype=np.uint8)
        cv2.fillConvexPoly(mask, np.rint(grid_quad(grid)).astype(np.int32), 255)
        mask = cv2.erode(mask, np.ones((5, 5), dtype=np.uint8))
        if exclusion_mask is not None:
            mask[exclusion_mask > 0] = 0
        retained = self.tracked_points
        if retained is not None:
            xy = np.rint(retained.reshape(-1, 2)).astype(int)
            inside = (xy[:, 0] >= 0) & (xy[:, 0] < gray.shape[1]) & (xy[:, 1] >= 0) & (xy[:, 1] < gray.shape[0])
            xy, retained = xy[inside], retained[inside]
            retained = retained[mask[xy[:, 1], xy[:, 0]] > 0]
        if refresh:
            if retained is not None:
                for x, y in retained.reshape(-1, 2):
                    cv2.circle(mask, (round(float(x)), round(float(y))), 7, 0, -1)
            count = 240 - (len(retained) if retained is not None else 0)
            extra = cv2.goodFeaturesToTrack(gray, maxCorners=count, qualityLevel=0.015, minDistance=7, mask=mask) if count > 0 else None
            if extra is not None:
                retained = extra if retained is None else np.concatenate((retained, extra))
        self.points = retained
        self.previous_gray = gray
        self.previous_exclusion_mask = exclusion_mask.copy() if exclusion_mask is not None else None
        self.tracked_points = None


@dataclass(frozen=True)
class HybridTrackingFrame:
    result: SelectedTrackingResult
    detected_lines: tuple[DetectedLine, ...]
    support_lines: tuple[DetectedLine, ...]
    homography: np.ndarray | None
    exclusion_mask: np.ndarray | None = None


class HybridSelectedLineTracker:
    """Project an immutable calibrated board using verified image correspondence.

    YOLO is a diagnostic observation after calibration. It cannot change board
    shape, change fret IDs, reseed the reference, or revive a missing image.
    """

    def __init__(self, tracker: SelectedLineTracker) -> None:
        self.tracker = tracker
        self.motion = FretboardMotionTracker()
        self.reference = FretboardVisualReference()
        self.clear()

    def clear(self) -> None:
        self.motion.clear()
        self.reference.clear()
        self.anchor_motion = FretboardMotionTracker()
        self.coarse_motion = FretboardMotionTracker(blur_size=5)
        self.anchor_grid: StableFretboardGrid | None = None
        self.reference_fret_index: int | None = None
        self.last_pose = np.eye(3)
        self.frame_index = 0
        self.last_full_search = -3
        self.pending_recovery: StableFretboardGrid | None = None
        self.was_visible = False
        self.last_verified_at = float('-inf')
        self.continuity_frames = 0

    def seed(self, frame: np.ndarray, exclusion_mask=None) -> None:
        self.clear()
        self.anchor_grid = self.tracker.last_stable_grid
        self.reference_fret_index = self.tracker.reference_fret_index
        if self.anchor_grid is None:
            return
        self.motion.commit(frame, self.anchor_grid, refresh=True, exclusion_mask=exclusion_mask)
        self.anchor_motion.commit(frame, self.anchor_grid, refresh=True, exclusion_mask=exclusion_mask)
        self.coarse_motion.commit(frame, self.anchor_grid, refresh=True, exclusion_mask=exclusion_mask)
        self.reference.seed(frame, self.anchor_grid, exclusion_mask)
        self.was_visible = True
        self.last_verified_at = monotonic()

    def _brief_continuity(self, frame, exclusion_mask):
        """Fresh reference correspondence through brief blur, never stale dots.

        Coarse evidence cannot initialize/reacquire tracking, survive a hard
        loss, or extend its own deadline. The original feature set stays fixed.
        """
        if (not self.was_visible or self.continuity_frames >= 6
                or monotonic() - self.last_verified_at > .25):
            return None
        pose = self.coarse_motion.estimate(
            frame, self.anchor_grid, exclusion_mask, initial_homography=self.last_pose,
        )
        if pose is None:
            return None
        old = grid_quad(transform_grid(self.anchor_grid, self.last_pose))
        new_grid = transform_grid(self.anchor_grid, pose)
        new = grid_quad(new_grid)
        short_edge = min(np.linalg.norm(old[1] - old[0]), np.linalg.norm(old[2] - old[3]))
        if (not valid_board_projection(new_grid, frame.shape)
                or np.max(np.linalg.norm(new - old, axis=1)) > max(12., short_edge * .12)):
            return None
        return pose if self.reference.validate(frame, pose, exclusion_mask, coarse=True) else None

    def _verified(self, frame, pose, exclusion_mask):
        if pose is None or not np.isfinite(pose).all():
            return False
        projected = transform_grid(self.anchor_grid, pose)
        return valid_board_projection(projected, frame.shape) and self.reference.validate(frame, pose, exclusion_mask)

    def update(self, frame: np.ndarray, detect: Detector, exclusion_mask=None,
               *, diagnostics: bool = False) -> HybridTrackingFrame:
        self.frame_index += 1
        if self.anchor_grid is None:
            return HybridTrackingFrame(SelectedTrackingResult(None, (), 'selected_lines_lost'), (), (), None, exclusion_mask)
        if not self.reference.is_ready:
            return HybridTrackingFrame(SelectedTrackingResult(None, (), 'selected_lines_reference_weak'), (), (), None, exclusion_mask)
        previous_grid = transform_grid(self.anchor_grid, self.last_pose)
        incremental = self.motion.estimate(frame, previous_grid, exclusion_mask)
        incremental_pose = incremental @ self.last_pose if incremental is not None else None
        anchor_pose = self.anchor_motion.estimate(
            frame, self.anchor_grid, exclusion_mask,
            initial_homography=incremental_pose if incremental_pose is not None else self.last_pose,
        )
        anchor_valid = self._verified(frame, anchor_pose, exclusion_mask)
        incremental_valid = self._verified(frame, incremental_pose, exclusion_mask)
        pose = anchor_pose if anchor_valid else incremental_pose if incremental_valid else None
        directly_aligned = False
        if pose is None:
            # Small LK bias may sit outside strict photometric acceptance while
            # enough distributed texture remains for bounded direct alignment.
            for candidate in (anchor_pose, incremental_pose):
                if candidate is None:
                    continue
                aligned = self.reference.refine(frame, candidate, exclusion_mask)
                if self._verified(frame, aligned, exclusion_mask):
                    pose = aligned
                    directly_aligned = True
                    break
        if anchor_valid and incremental_valid:
            first = grid_quad(transform_grid(self.anchor_grid, anchor_pose))
            second = grid_quad(transform_grid(self.anchor_grid, incremental_pose))
            lines = (previous_grid.nut_line, *previous_grid.ordered_fret_lines)
            centers = np.asarray([(np.asarray(line.p0) + line.p1) / 2 for line in lines])
            tolerance = max(1.0, float(np.min(np.linalg.norm(np.diff(centers, axis=0), axis=1))) * .2)
            if np.max(np.linalg.norm(first - second, axis=1)) >= tolerance:
                pose = None  # Resolve disagreement from descriptors, not fret repetitions.

        recovered = False
        if pose is None and (self.pending_recovery is not None or self.frame_index - self.last_full_search >= 3):
            self.last_full_search = self.frame_index
            candidate = self.reference.estimate(frame, exclusion_mask)
            if self._verified(frame, candidate, exclusion_mask):
                pose = candidate
                recovered = True
        if pose is not None and recovered:
            candidate_grid = transform_grid(self.anchor_grid, pose)
            jump = np.max(np.linalg.norm(grid_quad(candidate_grid) - grid_quad(previous_grid), axis=1))
            if jump > 12:
                agrees = self.pending_recovery is not None and np.max(np.linalg.norm(
                    grid_quad(candidate_grid) - grid_quad(self.pending_recovery), axis=1,
                )) < 6
                self.pending_recovery = candidate_grid
                if not agrees:
                    pose = None
            else:
                self.pending_recovery = None
        elif pose is None:
            self.pending_recovery = None

        if pose is not None and not directly_aligned:
            pose = self.reference.refine(frame, pose, exclusion_mask)

        # Revalidate the last pose against this frame before allowing a hand to
        # nudge the neck. This is fresh visual evidence, not a stale-pose hold.
        pose = self.reference.select_pose(frame, pose, self.last_pose, exclusion_mask)
        continuity = False
        if pose is None:
            candidate = self._brief_continuity(frame, exclusion_mask)
            if candidate is not None:
                pose = candidate
                continuity = not self.reference.validate(frame, pose, exclusion_mask)

        # Keeping diagnostics on a separate cadence makes them incapable of
        # redefining geometry when fingers produce convincing false segments.
        run_detector = diagnostics and (self.frame_index % 3 == 1 or pose is None)
        actual = tuple(detect(self.tracker.search_roi(frame.shape))) if run_detector else ()
        if pose is None:
            self.motion.clear()
            self.was_visible = False
            held = self.tracker.hold_or_lose()
            return HybridTrackingFrame(held, actual, (), None, exclusion_mask)

        grid = transform_grid(self.anchor_grid, pose)
        relative_motion = pose @ np.linalg.inv(self.last_pose)
        self.last_pose = pose
        self.tracker.reference_fret_index = self.reference_fret_index
        self.tracker.accept_registered_grid(grid)
        mode = ('selected_lines_continuity' if continuity else
                'selected_lines_reacquired' if recovered or not self.was_visible else 'selected_lines_motion')
        if continuity:
            self.continuity_frames += 1
        else:
            self.continuity_frames = 0
            self.last_verified_at = monotonic()
        self.was_visible = True
        self.pending_recovery = None
        tracking_mask = self.reference.tracking_exclusion(frame, pose, exclusion_mask)
        self.motion.commit(frame, grid, refresh=not continuity, exclusion_mask=tracking_mask)
        # The seed image, its feature positions and the seed grid stay untouched.
        # Missing frets are projected from that same model on every frame.
        result = SelectedTrackingResult(grid, (grid.nut_line, grid.ordered_fret_lines[-1]), mode)
        return HybridTrackingFrame(result, actual, (), relative_motion, exclusion_mask)
