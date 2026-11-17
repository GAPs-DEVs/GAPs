"""An immutable image reference for a calibrated, approximately planar neck.

Fret endpoints are often endpoints of an *occlusion*, not of the neck. This
module consequently accepts poses only against the image saved at calibration.
Neither detected fret lines nor later frames can replace that image. A modest
amount of hidden texture is allowed, but one matching patch cannot validate the
whole board. Repetitive frets without distributed texture can be insufficient;
in that case recovery deliberately fails rather than guessing a fret number.
"""
from __future__ import annotations

from contextlib import contextmanager

import cv2
import numpy as np

from vision.fretboard_mapping import StableFretboardGrid


_TILE_COLUMNS = 4
_TILE_ROWS = 10


def _gray(frame: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame.copy()


def _quad(grid: StableFretboardGrid) -> np.ndarray:
    last = grid.ordered_fret_lines[-1]
    return np.asarray((grid.nut_line.p0, grid.nut_line.p1, last.p1, last.p0), dtype=np.float32)


def _readonly(array: np.ndarray) -> np.ndarray:
    array.setflags(write=False)
    return array


def _correlation(first: np.ndarray, second: np.ndarray) -> float:
    a = first.astype(np.float32)
    b = second.astype(np.float32)
    a -= a.mean()
    b -= b.mean()
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / denominator) if denominator > 1e-6 else -1.0


def _tile_sums(values: np.ndarray, row_starts: np.ndarray, col_starts: np.ndarray) -> np.ndarray:
    # Columns first: reducing along the contiguous axis is about twice as fast.
    return np.add.reduceat(np.add.reduceat(values, col_starts, axis=1), row_starts, axis=0)


def _tile_statistics(first, second, valid, row_starts, col_starts):
    """Masked count, both standard deviations and correlation of every tile.

    One pass over the whole tile grid replaces a Python loop of small masked
    reductions. Values are centered globally first, which leaves each statistic
    unchanged while keeping the sums of squares well conditioned in float64.
    """
    weight = valid.astype(np.float64)
    first = np.asarray(first, dtype=np.float64)
    second = np.asarray(second, dtype=np.float64)
    first = first - first.mean()
    second = second - second.mean()
    weighted_first = first * weight
    weighted_second = second * weight
    count = _tile_sums(weight, row_starts, col_starts)
    safe = np.maximum(count, 1.)
    sum_first = _tile_sums(weighted_first, row_starts, col_starts)
    sum_second = _tile_sums(weighted_second, row_starts, col_starts)
    first_squares = np.maximum(
        _tile_sums(weighted_first * first, row_starts, col_starts) - sum_first * sum_first / safe, 0.)
    second_squares = np.maximum(
        _tile_sums(weighted_second * second, row_starts, col_starts) - sum_second * sum_second / safe, 0.)
    products = _tile_sums(weighted_first * second, row_starts, col_starts) - sum_first * sum_second / safe
    denominator = np.sqrt(first_squares * second_squares)
    usable = (denominator > 1e-6) & (count > 0)
    correlation = np.where(usable, products / np.where(usable, denominator, 1.), -1.)
    return count, np.sqrt(first_squares / safe), np.sqrt(second_squares / safe), correlation


class FretboardVisualReference:
    """Validate image poses cheaply; recover large motion using SIFT on demand.

    ``homography`` always maps the original calibration image into the current
    image, not the previous frame. ``seed`` is the only operation that changes
    the reference. Hand masks use nonzero pixels for excluded image regions.
    """

    def __init__(self) -> None:
        self._sift = cv2.SIFT_create(nfeatures=2400, contrastThreshold=0.015, edgeThreshold=12)
        self.clear()

    def clear(self) -> None:
        self.reference_grid: StableFretboardGrid | None = None
        self.reference_gray: np.ndarray | None = None
        self.template: np.ndarray | None = None
        self._template_dx: np.ndarray | None = None
        self._template_valid: np.ndarray | None = None
        self._canonical_from_reference: np.ndarray | None = None
        self._reference_quad: np.ndarray | None = None
        self._reference_points: np.ndarray | None = None
        self._reference_descriptors: np.ndarray | None = None
        self._tiles: tuple[tuple[slice, slice], ...] = ()
        # Grid layout of ``_tiles`` for whole-grid statistics; set by ``seed``.
        self._row_starts = self._col_starts = None
        self._row_of_y = self._col_of_x = None
        self._tile_area = None
        self._tile_rows = self._tile_cols = None
        self._tile_centers = None
        self._template_normalized = None
        self._coarse_template = None
        self._coarse_template_dx = None
        self._frame_source = None
        self._frame_mask = None
        self._frame_values = {}

    def recovery_snapshot(self) -> FretboardVisualReference:
        """Share immutable calibration arrays, never SIFT or frame caches.

        A background search may outlive a recalibration. Rebinding the live
        reference must not change the image that an in-flight search validates.
        """
        snapshot = FretboardVisualReference()
        for name in (
            'reference_grid', 'reference_gray', 'template', '_template_dx',
            '_template_valid', '_canonical_from_reference', '_reference_quad',
            '_reference_points', '_reference_descriptors', '_tiles',
            '_row_starts', '_col_starts', '_row_of_y', '_col_of_x', '_tile_area',
            '_tile_rows', '_tile_cols', '_tile_centers', '_template_normalized',
        ):
            setattr(snapshot, name, getattr(self, name))
        return snapshot

    @contextmanager
    def frame_scope(self, frame: np.ndarray, exclusion_mask=None):
        """Reuse work only inside one update with unchanged input pixels/mask.

        Explicit lifetime matters: a camera can reuse and mutate its ndarray
        on the next frame. Standalone method calls never reuse cached results.
        """
        if self._frame_source is not None:
            raise RuntimeError('Reference frame scopes cannot overlap.')
        self._frame_source = frame
        self._frame_mask = exclusion_mask
        self._frame_values = {}
        try:
            yield
        finally:
            self._frame_source = None
            self._frame_mask = None
            self._frame_values = {}

    def _remember(self, frame, key, compute):
        if frame is not self._frame_source or key is None:
            return compute()
        if key not in self._frame_values:
            self._frame_values[key] = compute()
        return self._frame_values[key]

    def _pose_key(self, operation, homography, *options):
        matrix = np.asarray(homography)
        # Exact values, not rounded poses: nearby poses must still be tested.
        return (operation, matrix.dtype.str, matrix.shape, matrix.tobytes(), *options)

    def _frame_gray(self, frame):
        return self._remember(frame, 'gray', lambda: _readonly(_gray(frame)))

    def _canonical_image(self, frame, homography):
        def compute():
            height, width = self.template.shape
            transform = self._canonical_from_reference @ np.linalg.inv(homography)
            current = cv2.warpPerspective(self._frame_gray(frame), transform, (width, height)).astype(np.float32)
            current = cv2.GaussianBlur(current, (5, 5), .85)
            dx = cv2.Sobel(current, cv2.CV_32F, 1, 0, ksize=3)
            return _readonly(current), _readonly(dx), transform
        return self._remember(frame, self._pose_key('image', homography), compute)

    def _canonical_valid(self, frame, homography, exclusion_mask, *, erode_hand=False):
        erode_hand = erode_hand and exclusion_mask is not None
        cacheable = exclusion_mask is self._frame_mask

        def image_mask():
            mask = np.full(frame.shape[:2], 255, dtype=np.uint8)
            if exclusion_mask is not None:
                mask[np.asarray(exclusion_mask) > 0] = 0
                if erode_hand:
                    mask = cv2.erode(mask, np.ones((5, 5), dtype=np.uint8))
            return mask

        def compute():
            height, width = self.template.shape
            _, _, transform = self._canonical_image(frame, homography)
            mask = self._remember(frame, ('mask', erode_hand) if cacheable else None, image_mask)
            valid = self._template_valid & (cv2.warpPerspective(
                mask, transform, (width, height), flags=cv2.INTER_NEAREST,
            ) > 0)
            return _readonly(valid)
        key = self._pose_key('valid', homography, erode_hand) if cacheable else None
        return self._remember(frame, key, compute)

    @property
    def is_ready(self) -> bool:
        return self.template is not None and len(self._tiles) >= 10

    def seed(self, frame: np.ndarray, grid: StableFretboardGrid | None, exclusion_mask=None) -> None:
        self.clear()
        if grid is None or not grid.ordered_fret_lines:
            return
        quad = _quad(grid)
        if not np.isfinite(quad).all() or not cv2.isContourConvex(quad) or abs(cv2.contourArea(quad)) < 100:
            return
        gray = _gray(frame)
        mask = np.zeros(gray.shape, dtype=np.uint8)
        cv2.fillConvexPoly(mask, np.rint(quad).astype(np.int32), 255)
        mask = cv2.erode(mask, np.ones((5, 5), dtype=np.uint8))
        if exclusion_mask is not None:
            mask[np.asarray(exclusion_mask) > 0] = 0
        width = int(np.clip(np.mean((np.linalg.norm(quad[1] - quad[0]), np.linalg.norm(quad[2] - quad[3]))), 64, 128))
        height = int(np.clip(np.mean((np.linalg.norm(quad[3] - quad[0]), np.linalg.norm(quad[2] - quad[1]))), 160, 512))
        canonical = cv2.getPerspectiveTransform(
            quad, np.asarray(((0, 0), (width - 1, 0), (width - 1, height - 1), (0, height - 1)), dtype=np.float32),
        )
        template = cv2.warpPerspective(gray, canonical, (width, height)).astype(np.float32)
        template = cv2.GaussianBlur(template, (5, 5), 0.85)
        template_dx = cv2.Sobel(template, cv2.CV_32F, 1, 0, ksize=3)
        valid = cv2.warpPerspective(mask, canonical, (width, height), flags=cv2.INTER_NEAREST) > 0
        tiles = []
        tile_cells = []
        normalized = np.zeros_like(template)
        for row in range(_TILE_ROWS):
            for col in range(_TILE_COLUMNS):
                ys = slice(row * height // _TILE_ROWS, (row + 1) * height // _TILE_ROWS)
                xs = slice(col * width // _TILE_COLUMNS, (col + 1) * width // _TILE_COLUMNS)
                support = valid[ys, xs]
                patch = template[ys, xs]
                normalized[ys, xs] = (patch - patch.mean()) / max(float(patch.std()), 2.)
                if (support.mean() >= 0.6 and template[ys, xs][support].std() >= 2.0
                        and template_dx[ys, xs][support].std() >= 3.0):
                    tiles.append((ys, xs))
                    tile_cells.append((row, col))
        row_starts = np.arange(_TILE_ROWS) * height // _TILE_ROWS
        col_starts = np.arange(_TILE_COLUMNS) * width // _TILE_COLUMNS
        row_sizes = np.diff(np.append(row_starts, height))
        col_sizes = np.diff(np.append(col_starts, width))
        cells = np.asarray(tile_cells, dtype=np.intp).reshape(-1, 2)
        self._row_starts = _readonly(row_starts)
        self._col_starts = _readonly(col_starts)
        self._row_of_y = _readonly(np.repeat(np.arange(_TILE_ROWS), row_sizes))
        self._col_of_x = _readonly(np.repeat(np.arange(_TILE_COLUMNS), col_sizes))
        self._tile_area = _readonly(np.outer(row_sizes, col_sizes).astype(np.float64))
        self._tile_rows = _readonly(cells[:, 0].copy())
        self._tile_cols = _readonly(cells[:, 1].copy())
        self._tile_centers = _readonly(np.asarray(
            [((xs.start + xs.stop) / (2 * width), (ys.start + ys.stop) / (2 * height)) for ys, xs in tiles],
            dtype=np.float32,
        ).reshape(-1, 2))
        self._template_normalized = _readonly(normalized)
        self.reference_grid = grid
        self.reference_gray = _readonly(gray.copy())
        self.template = _readonly(template)
        self._template_dx = _readonly(template_dx)
        self._template_valid = _readonly(valid)
        self._canonical_from_reference = _readonly(canonical)
        self._reference_quad = _readonly(quad)
        self._tiles = tuple(tiles)
        # Restrict reference features to the neck. The descriptor positions remain
        # in calibration-image coordinates, so recovery directly yields its pose.
        left, top = np.maximum(np.floor(quad.min(axis=0)).astype(int) - 8, 0)
        right, bottom = np.minimum(np.ceil(quad.max(axis=0)).astype(int) + 9, (gray.shape[1], gray.shape[0]))
        if right <= left or bottom <= top:
            return
        keypoints, descriptors = self._sift.detectAndCompute(gray[top:bottom, left:right], mask[top:bottom, left:right])
        if descriptors is not None and len(keypoints) >= 12:
            points = np.asarray([point.pt for point in keypoints], dtype=np.float32) + np.asarray((left, top), dtype=np.float32)
            self._reference_points = _readonly(points)
            self._reference_descriptors = _readonly(descriptors)

    def _valid_projection(self, homography: np.ndarray, frame_shape) -> bool:
        matrix = np.asarray(homography)
        if matrix.shape != (3, 3) or not np.isfinite(matrix).all() or self._reference_quad is None:
            return False
        scale = np.linalg.norm(matrix)
        if scale < 1e-12 or abs(np.linalg.det(matrix / scale)) < 1e-12:
            return False
        denominators = np.c_[self._reference_quad, np.ones(4)] @ matrix[2]
        if np.any(np.abs(denominators) < 1e-8) or np.any(denominators * denominators[0] <= 0):
            return False
        projected = cv2.perspectiveTransform(self._reference_quad[:, None], matrix).reshape(-1, 2)
        if not np.isfinite(projected).all() or not cv2.isContourConvex(projected):
            return False
        if cv2.contourArea(projected, oriented=True) * cv2.contourArea(self._reference_quad, oriented=True) <= 0:
            return False
        area = abs(cv2.contourArea(projected))
        if area < 100 or min(np.linalg.norm(projected[1] - projected[0]), np.linalg.norm(projected[2] - projected[3])) < 12:
            return False
        height, width = frame_shape[:2]
        screen = np.asarray(((0, 0), (width - 1, 0), (width - 1, height - 1), (0, height - 1)), dtype=np.float32)
        visible_area, _ = cv2.intersectConvexConvex(projected, screen)
        return visible_area / area >= 0.55

    def _tile_stats(self, first, second, valid):
        return _tile_statistics(first, second, valid, self._row_starts, self._col_starts)

    def _of_tiles(self, grid_values: np.ndarray) -> np.ndarray:
        """Select the usable reference tiles, in ``_tiles`` order, from a grid."""
        return grid_values[self._tile_rows, self._tile_cols]

    def _tile_image(self, accepted: np.ndarray) -> np.ndarray:
        """Expand a boolean per-``_tiles`` selection to template pixels."""
        cells = np.zeros((_TILE_ROWS, _TILE_COLUMNS), dtype=bool)
        cells[self._tile_rows[accepted], self._tile_cols[accepted]] = True
        return cells[self._row_of_y][:, self._col_of_x]

    def validate(self, frame: np.ndarray, homography: np.ndarray, exclusion_mask=None,
                 *, coarse: bool = False) -> bool:
        return self.validation_support(frame, homography, exclusion_mask, coarse=coarse)[0]

    def validation_support(self, frame: np.ndarray, homography: np.ndarray, exclusion_mask=None,
                           *, coarse: bool = False) -> tuple[bool, int, int]:
        """Validation verdict with (accepted, visible) tile counts for diagnostics.

        Counts are -1 when the pose is rejected before any image comparison.
        """
        key = self._pose_key('validate', homography, coarse) if exclusion_mask is self._frame_mask else None
        return self._remember(frame, key, lambda: self._validate(frame, homography, exclusion_mask, coarse=coarse))

    def _validate(self, frame, homography, exclusion_mask, *, coarse):
        if not self.is_ready or not self._valid_projection(homography, frame.shape):
            return False, -1, -1
        try:
            current, current_dx, _ = self._canonical_image(frame, homography)
        except np.linalg.LinAlgError:
            return False, -1, -1
        template = self.template
        template_dx = self._template_dx
        if coarse:
            # Only the bounded continuity path may use this scale. Compare both
            # images at the same scale; keep all spatial and correlation gates.
            if self._coarse_template is None:
                self._coarse_template = _readonly(cv2.GaussianBlur(template, (9, 9), 2.))
                self._coarse_template_dx = _readonly(cv2.Sobel(self._coarse_template, cv2.CV_32F, 1, 0, ksize=3))
            template = self._coarse_template
            current = cv2.GaussianBlur(current, (9, 9), 2.)
            template_dx = self._coarse_template_dx
            current_dx = cv2.Sobel(current, cv2.CV_32F, 1, 0, ksize=3)
        valid = self._canonical_valid(frame, homography, exclusion_mask, erode_hand=True)
        count, reference_std, current_std, intensity = self._tile_stats(template, current, valid)
        _, _, _, transverse = self._tile_stats(template_dx, current_dx, valid)
        present = self._of_tiles(count / self._tile_area) >= 0.55
        available = int(present.sum())
        # Local centering and normalization make this insensitive to additive
        # illumination and moderate contrast changes, without moving pixels.
        # Horizontal fret bars can remain perfectly aligned after an unsafe
        # width change. Require transverse texture (strings/wood/inlays) as
        # independent evidence of lateral position and width as well.
        accepted = (present & (self._of_tiles(reference_std) >= 2.0) & (self._of_tiles(current_std) >= 1.5)
                    & (self._of_tiles(intensity) >= 0.72) & (self._of_tiles(transverse) >= 0.55))
        support_points = np.ascontiguousarray(self._tile_centers[accepted])
        counts = (len(support_points), available)
        if len(support_points) < max(10, int(np.ceil(len(self._tiles) * 0.45))) or len(support_points) < available * 0.6:
            return False, *counts
        # A hand can carry one matching patch or repeated frets; the unchanged
        # template must still be supported across both dimensions of the neck.
        return (bool(np.ptp(support_points, axis=0)[0] >= 0.7
                     and np.ptp(support_points, axis=0)[1] >= 0.5
                     and abs(cv2.contourArea(cv2.convexHull(support_points))) >= 0.28), *counts)

    def refine(self, frame: np.ndarray, homography: np.ndarray, exclusion_mask=None) -> np.ndarray | None:
        """Correct small LK bias by direct alignment to the unchanged template.

        Only a neighborhood with distributed appearance support may be optimized.
        Masked and mismatching tiles cannot drive ECC. Failure leaves the valid
        input pose intact; a successful fit must improve agreement and pass the
        same independent longitudinal/transverse checks as any other pose.
        """
        if not self.is_ready or not self._valid_projection(homography, frame.shape):
            return None
        fallback = homography if self.validate(frame, homography, exclusion_mask) else None
        height, width = self.template.shape
        canonical = self._canonical_from_reference
        current, current_dx, _ = self._canonical_image(frame, homography)
        valid = self._canonical_valid(frame, homography, exclusion_mask).astype(np.uint8) * 255
        # The mask may miss a finger. Reject visibly different template tiles
        # too, instead of asking the dense optimizer to align that moving skin.
        visible = valid > 0
        count, _, _, intensity = self._tile_stats(self.template, current, visible)
        _, _, _, transverse = self._tile_stats(self._template_dx, current_dx, visible)
        accepted = ((self._of_tiles(count / self._tile_area) >= .55)
                    & (self._of_tiles(intensity) > .5) & (self._of_tiles(transverse) > .25))
        supported = np.where(self._tile_image(accepted), valid, 0).astype(np.uint8)
        centers = np.ascontiguousarray(self._tile_centers[accepted])
        if len(centers) < max(10, int(np.ceil(len(self._tiles) * .45))):
            return fallback
        if np.any(np.ptp(centers, axis=0) < (.7, .5)) or abs(cv2.contourArea(cv2.convexHull(centers))) < .28:
            return fallback
        supported = cv2.erode(supported, np.ones((5, 5), dtype=np.uint8))
        if np.count_nonzero(supported) < width * height * .25:
            return fallback
        selected = supported > 0
        before = _correlation(self.template[selected], current[selected])
        if before > .999:
            return fallback
        try:
            _, correction = cv2.findTransformECC(
                self.template, current, np.eye(3, dtype=np.float32), cv2.MOTION_HOMOGRAPHY,
                # This is a bounded correction of an existing flow pose. Keep
                # its cost below a full recovery solve; acceptance below still
                # requires improved appearance and independent validation.
                (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 12, 1e-5), supported, 3,
            )
        except cv2.error:
            return fallback
        candidate = homography @ np.linalg.inv(canonical) @ correction @ canonical
        if not self._valid_projection(candidate, frame.shape):
            return fallback
        old = cv2.perspectiveTransform(self._reference_quad[:, None], homography).reshape(-1, 2)
        new = cv2.perspectiveTransform(self._reference_quad[:, None], candidate).reshape(-1, 2)
        short_edge = min(np.linalg.norm(old[1] - old[0]), np.linalg.norm(old[2] - old[3]))
        if np.max(np.linalg.norm(new - old, axis=1)) > np.clip(short_edge * .08, 2, 8):
            return fallback
        aligned = cv2.warpPerspective(current, correction, (width, height), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)
        aligned_mask = cv2.warpPerspective(supported, correction, (width, height), flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP)
        common = selected & (aligned_mask > 0)
        if common.sum() < selected.sum() * .8:
            return fallback
        before = _correlation(self.template[common], current[common])
        after = _correlation(self.template[common], aligned[common])
        if after < before + .0005 or not self.validate(frame, candidate, exclusion_mask):
            return fallback
        return candidate

    def _visible_tile_scores(self, frame: np.ndarray, homography: np.ndarray,
                             exclusion_mask=None) -> dict[int, float]:
        """Score reference regions with local appearance and transverse support."""
        key = self._pose_key('scores', homography) if exclusion_mask is self._frame_mask else None
        return self._remember(frame, key, lambda: self._compute_tile_scores(frame, homography, exclusion_mask))

    def _compute_tile_scores(self, frame, homography, exclusion_mask):
        if not self.is_ready or not self._valid_projection(homography, frame.shape):
            return {}
        current, dx, _ = self._canonical_image(frame, homography)
        valid = self._canonical_valid(frame, homography, exclusion_mask)
        count, _, _, intensity = self._tile_stats(self.template, current, valid)
        _, _, _, transverse = self._tile_stats(self._template_dx, dx, valid)
        intensity, transverse = self._of_tiles(intensity), self._of_tiles(transverse)
        accepted = (self._of_tiles(count / self._tile_area) >= .6) & (intensity >= .65) & (transverse >= .4)
        return {int(index): float(.5 * (intensity[index] + transverse[index]))
                for index in np.flatnonzero(accepted)}

    def select_pose(self, frame: np.ndarray, candidate: np.ndarray | None,
                    previous: np.ndarray, exclusion_mask=None) -> np.ndarray | None:
        """Move only if visible reference regions support the change together.

        A hand may make a global LK/ECC solution slightly better locally while
        shifting hidden frets. The previous pose is usable only with fresh image
        validation. Then a candidate must improve a distributed majority of the
        *same* visible tiles, rather than win by fitting the newly arrived hand.
        """
        previous_valid = self.validate(frame, previous, exclusion_mask)
        candidate_valid = candidate is not None and self.validate(frame, candidate, exclusion_mask)
        if not previous_valid:
            return candidate if candidate_valid else None
        if not candidate_valid:
            return previous
        old_scores = self._visible_tile_scores(frame, previous, exclusion_mask)
        new_scores = self._visible_tile_scores(frame, candidate, exclusion_mask)
        common = sorted(set(old_scores).intersection(new_scores))
        if len(common) < 10:
            return previous
        delta = np.asarray([new_scores[i] - old_scores[i] for i in common])
        improving = [i for i, value in zip(common, delta) if value > .002]
        if np.median(delta) < .003 or len(improving) < max(6, int(np.ceil(len(common) * .6))):
            return previous
        height, width = self.template.shape
        centers = np.asarray([((self._tiles[i][1].start + self._tiles[i][1].stop) / (2 * width),
                               (self._tiles[i][0].start + self._tiles[i][0].stop) / (2 * height))
                              for i in improving], dtype=np.float32)
        if np.any(np.ptp(centers, axis=0) < (.5, .4)):
            return previous
        return candidate

    def better_supported(self, frame: np.ndarray, first: np.ndarray, second: np.ndarray,
                         exclusion_mask=None) -> np.ndarray:
        """Of two validated poses, the one the same visible tiles match better.

        Only tiles scored under both poses are compared, so neither wins by
        covering a different part of the neck. ``first`` wins a tie.
        """
        first_scores = self._visible_tile_scores(frame, first, exclusion_mask)
        second_scores = self._visible_tile_scores(frame, second, exclusion_mask)
        common = set(first_scores).intersection(second_scores)
        if not common:
            return first
        difference = sum(second_scores[index] - first_scores[index] for index in common)
        return second if difference > 0 else first

    def tracking_exclusion(self, frame: np.ndarray, homography: np.ndarray,
                           exclusion_mask=None) -> np.ndarray:
        """Restrict new LK points to pixels matching the immutable neck image.

        This also works when MediaPipe misses a finger: a textured new hand must
        not become a source of neck features on the following frame.
        """
        current, _, _ = self._canonical_image(frame, homography)
        scores = self._visible_tile_scores(frame, homography, exclusion_mask)
        accepted = np.zeros(len(self._tiles), dtype=bool)
        accepted[np.fromiter(scores, dtype=np.intp, count=len(scores))] = True
        # Normalize every tile by its own mean and deviation, for all tiles at once.
        centered = current.astype(np.float64)
        centered -= centered.mean()
        mean = _tile_sums(centered, self._row_starts, self._col_starts) / self._tile_area
        variance = _tile_sums(centered * centered, self._row_starts, self._col_starts) / self._tile_area - mean * mean
        deviation = np.maximum(np.sqrt(np.maximum(variance, 0.)), 2.)
        expand = (self._row_of_y[:, None], self._col_of_x[None, :])
        normalized = (centered - mean[expand]) / deviation[expand]
        matching = np.abs(self._template_normalized - normalized) < .8
        trusted = np.where(matching & self._tile_image(accepted) & self._template_valid, 255, 0).astype(np.uint8)
        # A whole local patch must be reliable, not just its center pixel.
        trusted = cv2.erode(trusted, np.ones((5, 5), dtype=np.uint8))
        into_image = homography @ np.linalg.inv(self._canonical_from_reference)
        trusted = cv2.warpPerspective(trusted, into_image, (frame.shape[1], frame.shape[0]), flags=cv2.INTER_NEAREST)
        if exclusion_mask is not None:
            trusted[exclusion_mask > 0] = 0
        # Keep a margin in camera pixels as well: a downscaled canonical image
        # alone does not give a consistent patch margin at different distances.
        trusted = cv2.erode(trusted, np.ones((7, 7), dtype=np.uint8))
        return np.where(trusted > 0, 0, 255).astype(np.uint8)

    def estimate(self, frame: np.ndarray, exclusion_mask=None, *,
                 roi: tuple[int, int, int, int] | None = None,
                 max_side: int = 1280) -> np.ndarray | None:
        if not self.is_ready or self._reference_descriptors is None:
            return None
        gray = self._frame_gray(frame)
        height, width = gray.shape
        left, top, right, bottom = (0, 0, width, height) if roi is None else roi
        left, top = max(0, int(left)), max(0, int(top))
        right, bottom = min(width, int(right)), min(height, int(bottom))
        if right - left < 16 or bottom - top < 16:
            return None
        gray = gray[top:bottom, left:right]
        crop_height, crop_width = gray.shape
        factor = min(1.0, max(32, max_side) / max(gray.shape))
        if factor < 1:
            gray = cv2.resize(gray, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
        mask = None
        if exclusion_mask is not None:
            mask = np.where(exclusion_mask[top:bottom, left:right] > 0, 0, 255).astype(np.uint8)
            if factor < 1:
                mask = cv2.resize(mask, (gray.shape[1], gray.shape[0]), interpolation=cv2.INTER_NEAREST)
            mask = cv2.erode(mask, np.ones((5, 5), dtype=np.uint8))
        keypoints, descriptors = self._sift.detectAndCompute(gray, mask)
        if descriptors is None or len(keypoints) < 12:
            return None
        # Mutual nearest neighbours, without a ratio test. Frets repeat, so the
        # second-best descriptor is nearly as close as the best and a ratio
        # test discards most correct matches along with the wrong ones. The
        # robust fit below is what separates them.
        matches = cv2.BFMatcher(cv2.NORM_L2, crossCheck=True).match(self._reference_descriptors, descriptors)
        if len(matches) < 12:
            return None
        old = self._reference_points[[match.queryIdx for match in matches]]
        # Use actual resize dimensions, including rounding, and restore the
        # crop origin. All validation and returned poses use camera pixels.
        scale = np.asarray((gray.shape[1] / crop_width, gray.shape[0] / crop_height), dtype=np.float32)
        new = (np.asarray([keypoints[match.trainIdx].pt for match in matches], dtype=np.float32)
               / scale + np.asarray((left, top), dtype=np.float32))
        homography, inliers = cv2.findHomography(old, new, cv2.USAC_MAGSAC, 2.0, maxIters=10000, confidence=0.995)
        if homography is None or inliers is None:
            return None
        keep = inliers.ravel().astype(bool)
        # No inlier-ratio requirement: with repeating frets most matches being
        # outliers is normal for a correct pose (a 65% requirement once left a
        # plainly visible neck unfound for many seconds). The pose is accepted
        # on what follows instead: where the inliers lie on the neck, their
        # residual, and the image comparison with the calibration reference.
        if keep.sum() < 12:
            return None
        old, new = old[keep], new[keep]
        # Least-squares fits over the agreed matches, the second one over
        # those within a pixel, for sub-pixel accuracy.
        for limit in (None, 1.):
            if limit is not None:
                error = np.linalg.norm(cv2.perspectiveTransform(old[:, None], homography).reshape(-1, 2) - new, axis=1)
                if (error < limit).sum() < 12:
                    break
                old, new = old[error < limit], new[error < limit]
            refit, _ = cv2.findHomography(old, new, 0)
            if refit is None or not np.isfinite(refit).all():
                break
            homography = refit
        height, width = self.template.shape
        canonical = cv2.perspectiveTransform(old[:, None], self._canonical_from_reference).reshape(-1, 2) / (width - 1, height - 1)
        if np.any(np.ptp(canonical, axis=0) < (0.55, 0.5)) or abs(cv2.contourArea(cv2.convexHull(canonical.astype(np.float32)))) < 0.25:
            return None
        projected = cv2.perspectiveTransform(old[:, None], homography).reshape(-1, 2)
        if np.median(np.linalg.norm(projected - new, axis=1)) > 1.25:
            return None
        return homography if self.validate(frame, homography, exclusion_mask) else None
