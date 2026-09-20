"""An immutable image reference for a calibrated, approximately planar neck.

Fret endpoints are often endpoints of an *occlusion*, not of the neck. This
module consequently accepts poses only against the image saved at calibration.
Neither detected fret lines nor later frames can replace that image. A modest
amount of hidden texture is allowed, but one matching patch cannot validate the
whole board. Repetitive frets without distributed texture can be insufficient;
in that case recovery deliberately fails rather than guessing a fret number.
"""
from __future__ import annotations

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
        for row in range(_TILE_ROWS):
            for col in range(_TILE_COLUMNS):
                ys = slice(row * height // _TILE_ROWS, (row + 1) * height // _TILE_ROWS)
                xs = slice(col * width // _TILE_COLUMNS, (col + 1) * width // _TILE_COLUMNS)
                support = valid[ys, xs]
                if (support.mean() >= 0.6 and template[ys, xs][support].std() >= 2.0
                        and template_dx[ys, xs][support].std() >= 3.0):
                    tiles.append((ys, xs))
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

    def validate(self, frame: np.ndarray, homography: np.ndarray, exclusion_mask=None) -> bool:
        if not self.is_ready or not self._valid_projection(homography, frame.shape):
            return False
        try:
            current_to_canonical = self._canonical_from_reference @ np.linalg.inv(homography)
        except np.linalg.LinAlgError:
            return False
        height, width = self.template.shape
        current = cv2.warpPerspective(_gray(frame), current_to_canonical, (width, height)).astype(np.float32)
        current = cv2.GaussianBlur(current, (5, 5), 0.85)
        current_dx = cv2.Sobel(current, cv2.CV_32F, 1, 0, ksize=3)
        current_mask = np.full(frame.shape[:2], 255, dtype=np.uint8)
        if exclusion_mask is not None:
            current_mask[np.asarray(exclusion_mask) > 0] = 0
            # Avoid comparing pixels blurred across the boundary of a hand.
            current_mask = cv2.erode(current_mask, np.ones((5, 5), dtype=np.uint8))
        valid = self._template_valid & (cv2.warpPerspective(
            current_mask, current_to_canonical, (width, height), flags=cv2.INTER_NEAREST,
        ) > 0)
        available = 0
        accepted = []
        for ys, xs in self._tiles:
            support = valid[ys, xs]
            if support.mean() < 0.55:
                continue
            available += 1
            reference_pixels = self.template[ys, xs][support]
            current_pixels = current[ys, xs][support]
            # Local centering and normalization make this insensitive to additive
            # illumination and moderate contrast changes, without moving pixels.
            # Horizontal fret bars can remain perfectly aligned after an unsafe
            # width change. Require transverse texture (strings/wood/inlays) as
            # independent evidence of lateral position and width as well.
            if (reference_pixels.std() >= 2.0 and current_pixels.std() >= 1.5
                    and _correlation(reference_pixels, current_pixels) >= 0.72
                    and _correlation(self._template_dx[ys, xs][support], current_dx[ys, xs][support]) >= 0.55):
                accepted.append(((xs.start + xs.stop) / (2 * width), (ys.start + ys.stop) / (2 * height)))
        if len(accepted) < max(10, int(np.ceil(len(self._tiles) * 0.45))) or len(accepted) < available * 0.6:
            return False
        support_points = np.asarray(accepted, dtype=np.float32)
        # A hand can carry one matching patch or repeated frets; the unchanged
        # template must still be supported across both dimensions of the neck.
        return bool(np.ptp(support_points, axis=0)[0] >= 0.7
                    and np.ptp(support_points, axis=0)[1] >= 0.5
                    and abs(cv2.contourArea(cv2.convexHull(support_points))) >= 0.28)

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
        into_template = canonical @ np.linalg.inv(homography)
        current = cv2.warpPerspective(_gray(frame), into_template, (width, height)).astype(np.float32)
        current = cv2.GaussianBlur(current, (5, 5), .85)
        current_dx = cv2.Sobel(current, cv2.CV_32F, 1, 0, ksize=3)
        valid = np.full(frame.shape[:2], 255, dtype=np.uint8)
        if exclusion_mask is not None:
            valid[exclusion_mask > 0] = 0
        valid = cv2.warpPerspective(valid, into_template, (width, height), flags=cv2.INTER_NEAREST)
        valid[~self._template_valid] = 0
        # The mask may miss a finger. Reject visibly different template tiles
        # too, instead of asking the dense optimizer to align that moving skin.
        supported = np.zeros_like(valid)
        supported_centers = []
        for ys, xs in self._tiles:
            selected = valid[ys, xs] > 0
            if (selected.mean() >= .55
                    and _correlation(self.template[ys, xs][selected], current[ys, xs][selected]) > .5
                    and _correlation(self._template_dx[ys, xs][selected], current_dx[ys, xs][selected]) > .25):
                supported[ys, xs] = valid[ys, xs]
                supported_centers.append(((xs.start + xs.stop) / (2 * width), (ys.start + ys.stop) / (2 * height)))
        if len(supported_centers) < max(10, int(np.ceil(len(self._tiles) * .45))):
            return fallback
        centers = np.asarray(supported_centers, dtype=np.float32)
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
                (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 35, 1e-5), supported, 3,
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
        if not self.is_ready or not self._valid_projection(homography, frame.shape):
            return {}
        height, width = self.template.shape
        into_template = self._canonical_from_reference @ np.linalg.inv(homography)
        current = cv2.warpPerspective(_gray(frame), into_template, (width, height)).astype(np.float32)
        current = cv2.GaussianBlur(current, (5, 5), .85)
        dx = cv2.Sobel(current, cv2.CV_32F, 1, 0, ksize=3)
        image_mask = np.full(frame.shape[:2], 255, dtype=np.uint8)
        if exclusion_mask is not None:
            image_mask[exclusion_mask > 0] = 0
        valid = self._template_valid & (cv2.warpPerspective(
            image_mask, into_template, (width, height), flags=cv2.INTER_NEAREST,
        ) > 0)
        scores = {}
        for index, (ys, xs) in enumerate(self._tiles):
            use = valid[ys, xs]
            if use.mean() < .6:
                continue
            intensity = _correlation(self.template[ys, xs][use], current[ys, xs][use])
            transverse = _correlation(self._template_dx[ys, xs][use], dx[ys, xs][use])
            if intensity >= .65 and transverse >= .4:
                scores[index] = .5 * (intensity + transverse)
        return scores

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

    def tracking_exclusion(self, frame: np.ndarray, homography: np.ndarray,
                           exclusion_mask=None) -> np.ndarray:
        """Restrict new LK points to pixels matching the immutable neck image.

        This also works when MediaPipe misses a finger: a textured new hand must
        not become a source of neck features on the following frame.
        """
        height, width = self.template.shape
        into_template = self._canonical_from_reference @ np.linalg.inv(homography)
        current = cv2.warpPerspective(_gray(frame), into_template, (width, height)).astype(np.float32)
        current = cv2.GaussianBlur(current, (5, 5), .85)
        trusted = np.zeros((height, width), dtype=np.uint8)
        scores = self._visible_tile_scores(frame, homography, exclusion_mask)
        for index in scores:
            ys, xs = self._tiles[index]
            first, second = self.template[ys, xs], current[ys, xs]
            a = (first - first.mean()) / max(float(first.std()), 2.)
            b = (second - second.mean()) / max(float(second.std()), 2.)
            trusted[ys, xs] = np.where(abs(a - b) < .8, 255, 0).astype(np.uint8)
        trusted[~self._template_valid] = 0
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

    def estimate(self, frame: np.ndarray, exclusion_mask=None) -> np.ndarray | None:
        if not self.is_ready or self._reference_descriptors is None:
            return None
        gray = _gray(frame)
        factor = min(1.0, 1280.0 / max(gray.shape))
        if factor < 1:
            gray = cv2.resize(gray, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
        mask = None
        if exclusion_mask is not None:
            mask = np.where(exclusion_mask > 0, 0, 255).astype(np.uint8)
            if factor < 1:
                mask = cv2.resize(mask, (gray.shape[1], gray.shape[0]), interpolation=cv2.INTER_NEAREST)
            mask = cv2.erode(mask, np.ones((5, 5), dtype=np.uint8))
        keypoints, descriptors = self._sift.detectAndCompute(gray, mask)
        if descriptors is None or len(keypoints) < 12:
            return None
        matcher = cv2.BFMatcher(cv2.NORM_L2)
        forward = matcher.knnMatch(self._reference_descriptors, descriptors, k=2)
        backward = matcher.knnMatch(descriptors, self._reference_descriptors, k=2)
        reverse = {pair[0].queryIdx: pair[0].trainIdx for pair in backward
                   if len(pair) == 2 and pair[0].distance < 0.75 * pair[1].distance}
        matches = [pair[0] for pair in forward if len(pair) == 2 and pair[0].distance < 0.75 * pair[1].distance
                   and reverse.get(pair[0].trainIdx) == pair[0].queryIdx]
        if len(matches) < 12:
            return None
        old = self._reference_points[[match.queryIdx for match in matches]]
        new = np.asarray([keypoints[match.trainIdx].pt for match in matches], dtype=np.float32) / factor
        homography, inliers = cv2.findHomography(old, new, cv2.RANSAC, 2.0, maxIters=2500, confidence=0.995)
        if homography is None or inliers is None:
            return None
        keep = inliers.ravel().astype(bool)
        if keep.sum() < 12 or keep.mean() < 0.65:
            return None
        old, new = old[keep], new[keep]
        height, width = self.template.shape
        canonical = cv2.perspectiveTransform(old[:, None], self._canonical_from_reference).reshape(-1, 2) / (width - 1, height - 1)
        if np.any(np.ptp(canonical, axis=0) < (0.55, 0.5)) or abs(cv2.contourArea(cv2.convexHull(canonical.astype(np.float32)))) < 0.25:
            return None
        projected = cv2.perspectiveTransform(old[:, None], homography).reshape(-1, 2)
        if np.median(np.linalg.norm(projected - new, axis=1)) > 1.25:
            return None
        return homography if self.validate(frame, homography, exclusion_mask) else None
