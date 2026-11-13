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
from vision.fretboard_recovery import BackgroundFretboardRecovery, RecoveryResult
from vision.fretboard_prediction import CornerMotionPredictor
from vision.marker_visibility import filter_visible_markers


ROI = tuple[int, int, int, int]
# How long the inter-frame tracker survives without a verified pose.
MOTION_RETENTION_SECONDS = .3
# Guides may follow the last measured velocity for this many unverified frames,
# slowing by COAST_DECAY each frame. Sustained motion blur hides the neck for a
# few frames at a time; blanking the guides for that long reads as flicker.
# On recorded fast motion the predicted corners were off by a median of 6 px
# after one frame and 12 px after two, but 20 px and 28 px after three and
# four, which is more than a fret at the narrow end of the neck.
COAST_FRAMES = 2
COAST_DECAY = .8
COAST_MAX_FRAME_GAP = .1
# A prediction is drawn only while optical flow still follows this many points
# of the neck consistently in both directions, even though they no longer
# yield a verified pose. A neck that has left the image, or a blank frame,
# keeps no such points and hides the guides at once.
COAST_MIN_TRACKED = 12
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
        # Why the latest ``estimate`` returned no motion; diagnostics only.
        self.last_rejection = ''
        # Points the latest ``estimate`` followed forward and back consistently.
        self.last_tracked = 0

    def _reject(self, reason: str) -> None:
        self.last_rejection = reason
        return None

    def estimate(self, frame: np.ndarray, grid: StableFretboardGrid | None, exclusion_mask=None,
                 *, initial_homography: np.ndarray | None = None) -> np.ndarray | None:
        self.tracked_points = None
        self.last_rejection = ''
        self.last_tracked = 0
        gray = self._gray(frame)
        if (
            grid is None or not grid.ordered_fret_lines
            or self.previous_gray is None or self.previous_gray.shape != gray.shape
            or self.points is None or len(self.points) < 12
        ):
            return self._reject('no_points')
        initial_points = None
        flags = 0
        if initial_homography is not None:
            initial_points = cv2.perspectiveTransform(self.points, initial_homography)
            if not np.isfinite(initial_points).all():
                return self._reject('bad_hint')
            flags = cv2.OPTFLOW_USE_INITIAL_FLOW
        next_points, forward_status, errors = cv2.calcOpticalFlowPyrLK(
            self.previous_gray, gray, self.points, initial_points,
            winSize=(21, 21), maxLevel=3,
            criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01),
            flags=flags,
        )
        if next_points is None or forward_status is None or errors is None:
            return self._reject('flow_failed')
        backward_points, backward_status, _ = cv2.calcOpticalFlowPyrLK(
            gray, self.previous_gray, next_points, self.points.copy() if flags else None,
            winSize=(21, 21), maxLevel=3, flags=flags,
        )
        if backward_points is None or backward_status is None:
            return self._reject('flow_failed')
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
        self.last_tracked = len(old)
        if len(old) < 12:
            return self._reject(f'few_tracked:{len(old)}')
        homography, inliers = cv2.findHomography(old, new, cv2.RANSAC, 2.0)
        if homography is None or inliers is None or not np.isfinite(homography).all():
            return self._reject('no_homography')
        keep = inliers.ravel().astype(bool)
        if keep.sum() < 12 or keep.mean() < 0.65:
            return self._reject(f'few_inliers:{int(keep.sum())}/{len(keep)}')
        old, new = old[keep], new[keep]
        quad = grid_quad(grid)
        area = abs(cv2.contourArea(quad))
        if area < 100 or abs(cv2.contourArea(cv2.convexHull(old))) < area * 0.18:
            return self._reject('small_coverage')
        # Reject tracks concentrated on a hand or one small patch of the neck.
        to_unit = cv2.getPerspectiveTransform(
            quad, np.asarray([(0, 0), (1, 0), (1, 1), (0, 1)], dtype=np.float32),
        )
        unit = cv2.perspectiveTransform(old.reshape(-1, 1, 2), to_unit).reshape(-1, 2)
        if np.any(np.ptp(unit, axis=0) < (0.45, 0.4)):
            return self._reject('narrow_spread')
        denominators = np.c_[quad, np.ones(4)] @ homography[2]
        if np.any(np.abs(denominators) < 1e-6) or np.any(denominators * denominators[0] <= 0):
            return self._reject('bad_perspective')
        moved = cv2.perspectiveTransform(quad.reshape(-1, 1, 2), homography).reshape(-1, 2)
        if not np.isfinite(moved).all() or not cv2.isContourConvex(moved):
            return self._reject('not_convex')
        if cv2.contourArea(quad, oriented=True) * cv2.contourArea(moved, oriented=True) <= 0:
            return self._reject('flipped')
        area_ratio = abs(cv2.contourArea(moved)) / area
        edges = np.linalg.norm(np.roll(quad, -1, axis=0) - quad, axis=1)
        moved_edges = np.linalg.norm(np.roll(moved, -1, axis=0) - moved, axis=1)
        ratios = moved_edges / np.maximum(edges, 1)
        if not 0.65 < area_ratio < 1.55 or np.any((ratios < 0.65) | (ratios > 1.5)):
            return self._reject('bad_scale')
        if np.max(np.linalg.norm(moved - quad, axis=1)) > max(100, float(edges.max()) * 0.4):
            return self._reject('too_far')
        projected = cv2.perspectiveTransform(old.reshape(-1, 1, 2), homography).reshape(-1, 2)
        if np.median(np.linalg.norm(projected - new, axis=1)) > 1.25:
            return self._reject('residual')
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

    def __init__(self, tracker: SelectedLineTracker, *,
                 recovery: BackgroundFretboardRecovery | None = None) -> None:
        self.tracker = tracker
        self.recovery = recovery
        self.motion = FretboardMotionTracker()
        self.reference = FretboardVisualReference()
        self.clear()

    def clear(self) -> None:
        if self.recovery is not None:
            self.recovery.reset()
        self.motion.clear()
        self.reference.clear()
        self.anchor_motion = FretboardMotionTracker()
        self.coarse_motion = FretboardMotionTracker(blur_size=5)
        self.recovery_motion = FretboardMotionTracker()
        self.predictor = CornerMotionPredictor()
        self.anchor_grid: StableFretboardGrid | None = None
        self.reference_fret_index: int | None = None
        self.last_pose = np.eye(3)
        self.frame_index = 0
        self.last_full_search = -3
        self.pending_recovery: StableFretboardGrid | None = None
        self.pending_recovery_frame = -1
        self.pending_recovery_at = float('-inf')
        self.was_visible = False
        self.last_verified_at = float('-inf')
        self.continuity_frames = 0
        self._async_confirmation_pose = None
        self._async_descriptor_frame = -1
        self.last_recovery_ms = 0.
        self.last_recovery_error = None
        # How the latest frame's pose was decided; never read by tracking.
        self.last_decision = ''
        # Last drawn board corners with their time and velocity, for coasting.
        self._drawn_quad: np.ndarray | None = None
        self._drawn_at: float | None = None
        self._drawn_pose = np.eye(3)
        self._velocity: np.ndarray | None = None
        self._coast_frames = 0
        # This frame's flow poses that failed only the full-detail image check.
        self._flow_candidates = ()

    def close(self) -> None:
        if self.recovery is not None:
            self.recovery.close()

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
        if self.recovery is not None:
            self.recovery.reset(self.reference)
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
        if pose is not None:
            old = grid_quad(transform_grid(self.anchor_grid, self.last_pose))
            new_grid = transform_grid(self.anchor_grid, pose)
            new = grid_quad(new_grid)
            short_edge = min(np.linalg.norm(old[1] - old[0]), np.linalg.norm(old[2] - old[3]))
            if (valid_board_projection(new_grid, frame.shape)
                    and np.max(np.linalg.norm(new - old, axis=1)) <= max(12., short_edge * .12)
                    and self.reference.validate(frame, pose, exclusion_mask, coarse=True)):
                return pose
        # Motion blur softens every tile at once, so a correct pose from this
        # frame's own optical flow can miss the full-detail image check for a
        # few frames. Such a pose is fresh geometric evidence; accept it when
        # the neck still matches at the blur's own scale, within the same
        # frame and time limits as any other continuity.
        for candidate in self._flow_candidates:
            if (np.isfinite(candidate).all()
                    and valid_board_projection(transform_grid(self.anchor_grid, candidate), frame.shape)
                    and self.reference.validate(frame, candidate, exclusion_mask, coarse=True)):
                return candidate
        return None

    def _verified(self, frame, pose, exclusion_mask):
        if pose is None or not np.isfinite(pose).all():
            return False
        projected = transform_grid(self.anchor_grid, pose)
        return valid_board_projection(projected, frame.shape) and self.reference.validate(frame, pose, exclusion_mask)

    def _describe(self, frame, pose, exclusion_mask, motion) -> str:
        """Outcome of one flow estimate: no motion, rejected image check, or valid."""
        if pose is None:
            return f'none({motion.last_rejection})'
        if not np.isfinite(pose).all() or not valid_board_projection(transform_grid(self.anchor_grid, pose), frame.shape):
            return 'invalid(projection)'
        valid, accepted, visible = self.reference.validation_support(frame, pose, exclusion_mask)
        return f"{'valid' if valid else 'invalid'}({accepted}/{visible})"

    def _note_drawn(self, quad: np.ndarray, pose: np.ndarray, timestamp: float) -> None:
        """Record a measured pose that was drawn, and the velocity leading to it."""
        elapsed = timestamp - self._drawn_at if self._drawn_at is not None else None
        # A velocity needs two consecutive measured frames. One that follows a
        # coasted or hidden frame would mix a prediction into the estimate.
        measured = self._coast_frames == 0 and self.was_visible and self._drawn_quad is not None
        self._velocity = ((quad - self._drawn_quad) / elapsed
                          if measured and elapsed is not None and .005 <= elapsed <= COAST_MAX_FRAME_GAP else None)
        self._drawn_quad, self._drawn_pose, self._drawn_at = quad, pose, timestamp
        self._coast_frames = 0

    def _coast(self, frame, timestamp, exclusion_mask, actual) -> HybridTrackingFrame | None:
        """Carry the guides along their last measured velocity for a few frames.

        This is a prediction, not a measurement. It is drawn only: the verified
        pose, the flow trackers and the reference are left exactly as they
        were, so a prediction can never become the basis of later tracking.
        """
        if self._velocity is None or self._coast_frames >= COAST_FRAMES or self._drawn_at is None:
            return None
        if max(self.motion.last_tracked, self.anchor_motion.last_tracked) < COAST_MIN_TRACKED:
            return None
        elapsed = timestamp - self._drawn_at
        if not 0 < elapsed <= COAST_MAX_FRAME_GAP:
            return None
        velocity = self._velocity * COAST_DECAY
        quad = (self._drawn_quad + velocity * elapsed).astype(np.float32)
        if not np.isfinite(quad).all() or not cv2.isContourConvex(quad):
            return None
        pose = cv2.getPerspectiveTransform(grid_quad(self.anchor_grid), quad)
        grid = transform_grid(self.anchor_grid, pose)
        if not np.isfinite(pose).all() or not valid_board_projection(grid, frame.shape):
            return None
        relative = pose @ np.linalg.inv(self._drawn_pose)
        self._velocity, self._drawn_quad, self._drawn_pose, self._drawn_at = velocity, quad, pose, timestamp
        self._coast_frames += 1
        result = SelectedTrackingResult(grid, (grid.nut_line, grid.ordered_fret_lines[-1]), 'selected_lines_predicted')
        return HybridTrackingFrame(result, actual, (), relative, exclusion_mask)

    def _clear_recovery(self):
        self.pending_recovery = None
        self.pending_recovery_frame = -1
        self.pending_recovery_at = float('-inf')
        self.recovery_motion.clear()
        self._async_confirmation_pose = None
        self._async_descriptor_frame = -1

    def _advance_async_confirmation(self, frame, timestamp, exclusion_mask):
        """Carry a pending descriptor pose using independent current-image flow.

        A second descriptor result may arrive several frames later. Never use
        that result to hint the flow that is supposed to independently check it.
        """
        if self.pending_recovery is None:
            return
        if (self.pending_recovery_frame != self.frame_index - 1
                or not 0 <= timestamp - self.pending_recovery_at <= .5):
            self._clear_recovery()
            return
        movement = self.recovery_motion.estimate(frame, self.pending_recovery, exclusion_mask)
        pose = movement @ self._async_confirmation_pose if movement is not None else None
        if not self._verified(frame, pose, exclusion_mask):
            self._clear_recovery()
            return
        self._async_confirmation_pose = pose
        self.pending_recovery = transform_grid(self.anchor_grid, pose)
        self.pending_recovery_frame = self.frame_index
        mask = self.reference.tracking_exclusion(frame, pose, exclusion_mask)
        self.recovery_motion.commit(frame, self.pending_recovery, refresh=True, exclusion_mask=mask)

    def _transport_recovery(self, result: RecoveryResult, frame, timestamp, exclusion_mask):
        """Move delayed source-frame coordinates into THIS frame before use."""
        request = result.request
        if (result.pose is None or request.frame_index >= self.frame_index
                or not 0 <= timestamp - request.timestamp <= .5
                or request.frame.shape != frame.shape):
            return None
        source_grid = transform_grid(self.anchor_grid, result.pose)
        source_mask = self.reference.tracking_exclusion(request.frame, result.pose, request.exclusion_mask)
        transport = FretboardMotionTracker()
        transport.commit(request.frame, source_grid, refresh=True, exclusion_mask=source_mask)
        movement = transport.estimate(frame, source_grid, exclusion_mask)
        pose = movement @ result.pose if movement is not None else None
        return pose if self._verified(frame, pose, exclusion_mask) else None

    def _background_pose(self, frame, pose, timestamp, exclusion_mask):
        result = self.recovery.poll()
        if result is not None:
            self.last_recovery_ms = result.elapsed_ms
            self.last_recovery_error = result.error
        if pose is not None:
            # Fresh, verified flow has already recovered; a delayed search
            # must not overwrite it or force otherwise visible markers off.
            return pose, False, False
        self._advance_async_confirmation(frame, timestamp, exclusion_mask)
        if self._verified(frame, self.last_pose, exclusion_mask):
            return self.last_pose, False, False
        continuity_pose = self._brief_continuity(frame, exclusion_mask)
        candidate = self._transport_recovery(result, frame, timestamp, exclusion_mask) if result is not None else None
        if candidate is not None:
            candidate_grid = transform_grid(self.anchor_grid, candidate)
            jump = np.max(np.linalg.norm(
                grid_quad(candidate_grid) - grid_quad(transform_grid(self.anchor_grid, self.last_pose)), axis=1,
            ))
            agrees = (self.pending_recovery is not None
                      and result.request.frame_index > self._async_descriptor_frame
                      and np.max(np.linalg.norm(grid_quad(candidate_grid) - grid_quad(self.pending_recovery), axis=1)) < 6)
            if jump <= 12 or agrees:
                self._clear_recovery()
                return candidate, True, False
            # Large jumps require two independent descriptor observations as
            # well as the inter-frame flow and unchanged reference validation.
            self.pending_recovery = candidate_grid
            self.pending_recovery_at = timestamp
            self.pending_recovery_frame = self.frame_index
            self._async_confirmation_pose = candidate
            self._async_descriptor_frame = result.request.frame_index
            mask = self.reference.tracking_exclusion(frame, candidate, exclusion_mask)
            self.recovery_motion.clear()
            self.recovery_motion.commit(frame, candidate_grid, refresh=True, exclusion_mask=mask)
        search_pose = (self._async_confirmation_pose if self.pending_recovery is not None else
                       continuity_pose if continuity_pose is not None else self.last_pose)
        self.recovery.submit(
            frame, exclusion_mask, search_pose, timestamp=timestamp, frame_index=self.frame_index,
            confirmation=self.pending_recovery is not None,
        )
        continuity = continuity_pose is not None and not self.reference.validate(frame, continuity_pose, exclusion_mask)
        return continuity_pose, False, continuity

    def _confirm_recovery(self, frame, candidate, timestamp, exclusion_mask):
        """Confirm two descriptor poses in the SAME frame's coordinates.

        Independent flow from the first candidate image supplies the movement.
        The second SIFT result is not used to initialize that flow; otherwise a
        false descriptor jump could validate its own proposed displacement.
        """
        candidate_grid = transform_grid(self.anchor_grid, candidate)
        agrees = False
        if (self.pending_recovery is not None
                and self.pending_recovery_frame == self.frame_index - 1
                and 0 <= timestamp - self.pending_recovery_at <= .5):
            movement = self.recovery_motion.estimate(frame, self.pending_recovery, exclusion_mask)
            if movement is not None:
                expected = transform_grid(self.pending_recovery, movement)
                agrees = np.max(np.linalg.norm(
                    grid_quad(candidate_grid) - grid_quad(expected), axis=1,
                )) < 6
        if not agrees:
            self.pending_recovery = candidate_grid
            self.pending_recovery_frame = self.frame_index
            self.pending_recovery_at = timestamp
            mask = self.reference.tracking_exclusion(frame, candidate, exclusion_mask)
            self.recovery_motion.clear()
            self.recovery_motion.commit(frame, candidate_grid, refresh=True, exclusion_mask=mask)
        return agrees

    def update(self, frame: np.ndarray, detect: Detector, exclusion_mask=None,
               *, diagnostics: bool = False, timestamp: float | None = None) -> HybridTrackingFrame:
        with self.reference.frame_scope(frame, exclusion_mask):
            return self._update_frame(frame, detect, exclusion_mask, diagnostics=diagnostics, timestamp=timestamp)

    def _update_frame(self, frame, detect, exclusion_mask, *, diagnostics, timestamp):
        timestamp = monotonic() if timestamp is None else timestamp
        self.frame_index += 1
        if self.anchor_grid is None:
            return HybridTrackingFrame(SelectedTrackingResult(None, (), 'selected_lines_lost'), (), (), None, exclusion_mask)
        if not self.reference.is_ready:
            return HybridTrackingFrame(SelectedTrackingResult(None, (), 'selected_lines_reference_weak'), (), (), None, exclusion_mask)
        previous_grid = transform_grid(self.anchor_grid, self.last_pose)
        prediction = self.predictor.predict_relative(timestamp)
        incremental = self.motion.estimate(frame, previous_grid, exclusion_mask, initial_homography=prediction)
        incremental_pose = incremental @ self.last_pose if incremental is not None else None
        incremental_valid = self._verified(frame, incremental_pose, exclusion_mask)
        if prediction is not None and not incremental_valid:
            # A stop or reversal can invalidate constant velocity. Retry the
            # unpredicted search rather than making the hint a motion constraint.
            incremental = self.motion.estimate(frame, previous_grid, exclusion_mask)
            incremental_pose = incremental @ self.last_pose if incremental is not None else None
            incremental_valid = self._verified(frame, incremental_pose, exclusion_mask)
        anchor_pose = self.anchor_motion.estimate(
            frame, self.anchor_grid, exclusion_mask,
            initial_homography=(incremental_pose if incremental_pose is not None else
                                prediction @ self.last_pose if prediction is not None else self.last_pose),
        )
        anchor_valid = self._verified(frame, anchor_pose, exclusion_mask)
        decision = [f'inc={self._describe(frame, incremental_pose, exclusion_mask, self.motion)}',
                    f'anchor={self._describe(frame, anchor_pose, exclusion_mask, self.anchor_motion)}']
        pose = anchor_pose if anchor_valid else incremental_pose if incremental_valid else None
        directly_aligned = False
        disagreed = False
        identity_unclear = False
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
            spacing = float(np.min(np.linalg.norm(np.diff(centers, axis=0), axis=1)))
            tolerance = max(1.0, spacing * .2)
            gap = float(np.max(np.linalg.norm(first - second, axis=1)))
            if gap >= tolerance:
                decision.append(f'disagree={gap:.1f}/{tolerance:.1f}')
                if gap < spacing * .5:
                    # Both poses matched the reference image and differ by less
                    # than half the narrowest fret interval, so they cannot be
                    # different frets: this is flow noise on a moving neck, not
                    # an identity question. Keep the better supported one and
                    # let direct alignment settle the remainder below.
                    pose = self.reference.better_supported(frame, anchor_pose, incremental_pose, exclusion_mask)
                    disagreed = True
                else:
                    pose = None  # Resolve disagreement from descriptors, not fret repetitions.
                    identity_unclear = True

        # Two validated but far apart poses are an identity question; blur
        # tolerance must not answer it.
        self._flow_candidates = () if identity_unclear else tuple(
            candidate for candidate in (anchor_pose, incremental_pose) if candidate is not None)
        recovered = continuity = False
        if self.recovery is not None:
            pose, recovered, continuity = self._background_pose(frame, pose, timestamp, exclusion_mask)
            directly_aligned = directly_aligned or recovered
        elif pose is None and (self.pending_recovery is not None or self.frame_index - self.last_full_search >= 3):
            self.last_full_search = self.frame_index
            candidate = self.reference.estimate(frame, exclusion_mask)
            if self._verified(frame, candidate, exclusion_mask):
                pose = candidate
                recovered = True
                # A descriptor pose found in a blurred image is about a pixel
                # off; blur-tolerant flow from the last pose is sub-pixel.
                # Where both see the same place, draw the flow's.
                flow = self._brief_continuity(frame, exclusion_mask)
                if flow is not None and np.max(np.linalg.norm(
                        grid_quad(transform_grid(self.anchor_grid, flow))
                        - grid_quad(transform_grid(self.anchor_grid, pose)), axis=1)) < 3:
                    pose = flow
        if self.recovery is None and pose is not None and recovered:
            candidate_grid = transform_grid(self.anchor_grid, pose)
            jump = np.max(np.linalg.norm(grid_quad(candidate_grid) - grid_quad(previous_grid), axis=1))
            if jump > 12:
                agrees = self._confirm_recovery(frame, pose, timestamp, exclusion_mask)
                if not agrees:
                    pose = None
            else:
                self._clear_recovery()
        elif self.recovery is None and pose is None:
            self._clear_recovery()

        if directly_aligned and not recovered:
            decision.append('aligned')
        if recovered:
            decision.append('recovered')
        if continuity:
            decision.append('continuity')
        # Both flow paths still have to pass the immutable-image checks and
        # agree geometrically on EVERY frame. When they do, a dense ECC solve
        # is only a periodic subpixel correction, not evidence needed to track.
        # Recovery and uncertain flow retain immediate direct refinement.
        needs_refinement = (recovered or disagreed or not (anchor_valid and incremental_valid)
                            or self.frame_index % 5 == 0)
        if pose is not None and not continuity and not directly_aligned and needs_refinement:
            pose = self.reference.refine(frame, pose, exclusion_mask)

        # Revalidate the last pose against this frame before allowing a hand to
        # nudge the neck. This is fresh visual evidence, not a stale-pose hold.
        if not continuity:
            proposed = pose
            pose = self.reference.select_pose(frame, pose, self.last_pose, exclusion_mask)
            if proposed is not None and pose is None:
                decision.append('select=dropped')
            elif proposed is not None and pose is not proposed:
                decision.append('select=kept_previous')
            elif proposed is None and pose is not None:
                decision.append('select=previous_still_valid')
        if pose is None and self.recovery is None:
            candidate = self._brief_continuity(frame, exclusion_mask)
            if candidate is not None:
                pose = candidate
                continuity = not self.reference.validate(frame, pose, exclusion_mask)

        # Keeping diagnostics on a separate cadence makes them incapable of
        # redefining geometry when fingers produce convincing false segments.
        run_detector = diagnostics and (self.frame_index % 3 == 1 or pose is None)
        actual = tuple(detect(self.tracker.search_roi(frame.shape))) if run_detector else ()
        self.last_decision = ' '.join((*decision, 'pose=ok' if pose is not None else 'pose=none'))
        if pose is None:
            # The inter-frame tracker still describes the last verified frame
            # and pose. Keep it through a short failure so the next frame can
            # be measured from there; any pose it yields must pass the same
            # reference checks. Nothing is drawn from it in the meantime.
            if monotonic() - self.last_verified_at > MOTION_RETENTION_SECONDS:
                self.motion.clear()
            self.predictor.clear()
            self.was_visible = False
            coasted = self._coast(frame, timestamp, exclusion_mask, actual)
            if coasted is not None:
                self.last_decision += ' predicted'
                return coasted
            self._velocity = None
            held = self.tracker.hold_or_lose()
            return HybridTrackingFrame(held, actual, (), None, exclusion_mask)

        grid = transform_grid(self.anchor_grid, pose)
        relative_motion = pose @ np.linalg.inv(self.last_pose)
        self.last_pose = pose
        self.tracker.reference_fret_index = self.reference_fret_index
        self.tracker.accept_registered_grid(grid)
        mode = ('selected_lines_continuity' if continuity else
                'selected_lines_reacquired' if recovered or not self.was_visible else 'selected_lines_motion')
        self._note_drawn(grid_quad(grid), pose, timestamp)
        if continuity or recovered or not self.was_visible:
            self.predictor.clear()
        if not continuity:
            self.predictor.observe(grid_quad(grid), timestamp)
        if continuity:
            self.continuity_frames += 1
        else:
            self.continuity_frames = 0
            self.last_verified_at = monotonic()
        self.was_visible = True
        if not continuity:
            self._clear_recovery()
        tracking_mask = self.reference.tracking_exclusion(frame, pose, exclusion_mask)
        self.motion.commit(frame, grid, refresh=not continuity, exclusion_mask=tracking_mask)
        # The seed image, its feature positions and the seed grid stay untouched.
        # Missing frets are projected from that same model on every frame.
        result = SelectedTrackingResult(grid, (grid.nut_line, grid.ordered_fret_lines[-1]), mode)
        return HybridTrackingFrame(result, actual, (), relative_motion, exclusion_mask)
