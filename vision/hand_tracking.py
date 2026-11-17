from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Condition, Thread
from time import perf_counter
from typing import Sequence

import numpy as np
import cv2

from vision.fretboard_mapping import NoteMarker


DEFAULT_MATCH_RADIUS_PX = 22.0
MIN_MATCH_RADIUS_PX = 14.0
MAX_MATCH_RADIUS_PX = 34.0
FINGERTIP_LANDMARK_IDS = (4, 8, 12, 16, 20)
DEFAULT_MODEL_PATH = Path(__file__).resolve().parents[1] / "models" / "hand_landmarker.task"


@dataclass(frozen=True)
class Fingertip:
    point_xy: tuple[float, float]
    tip_id: int
    handedness: str | None = None


@dataclass(frozen=True)
class HandTrackingResult:
    fingertips: tuple[Fingertip, ...]
    matched_marker_indices: tuple[int, ...]
    match_radius_px: float
    backend_available: bool
    hand_polygons: tuple[tuple[tuple[float, float], ...], ...] = ()


class MediaPipeHandTracker:
    def __init__(
        self,
        model_asset_path: Path | None = None,
        max_num_hands: int = 2,
        min_detection_confidence: float = 0.5,
        min_tracking_confidence: float = 0.5,
    ) -> None:
        self.model_asset_path = (model_asset_path or DEFAULT_MODEL_PATH).expanduser().resolve()
        self.max_num_hands = max_num_hands
        self.min_detection_confidence = min_detection_confidence
        self.min_tracking_confidence = min_tracking_confidence
        self._hands = None
        self._load_error: Exception | None = None

    @property
    def is_available(self) -> bool:
        self._ensure_backend()
        return self._hands is not None and self._load_error is None

    def close(self) -> None:
        if self._hands is not None:
            self._hands.close()
            self._hands = None

    def process(
        self,
        frame_bgr: np.ndarray,
        note_markers: Sequence[NoteMarker] = (),
    ) -> HandTrackingResult:
        self._ensure_backend()
        if self._hands is None:
            return HandTrackingResult(
                fingertips=(),
                matched_marker_indices=(),
                match_radius_px=DEFAULT_MATCH_RADIUS_PX,
                backend_available=False,
            )

        import mediapipe as mp

        frame_rgb = frame_bgr[:, :, ::-1].copy()
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
        results = self._hands.detect(mp_image)
        fingertips = extract_fingertips(results, frame_width=frame_bgr.shape[1], frame_height=frame_bgr.shape[0])
        match_radius_px = estimate_marker_match_radius(note_markers)
        matched_marker_indices = match_fingertips_to_markers(
            note_markers=note_markers,
            fingertips=fingertips,
            match_radius_px=match_radius_px,
        )
        return HandTrackingResult(
            fingertips=fingertips,
            matched_marker_indices=matched_marker_indices,
            match_radius_px=match_radius_px,
            backend_available=True,
            hand_polygons=extract_hand_polygons(results, frame_bgr.shape[1], frame_bgr.shape[0]),
        )

    def _ensure_backend(self) -> None:
        if self._hands is not None or self._load_error is not None:
            return
        if not self.model_asset_path.exists():
            self._load_error = FileNotFoundError(
                f"MediaPipe hand model not found: {self.model_asset_path}"
            )
            return
        try:
            from mediapipe.tasks.python import vision
            from mediapipe.tasks.python.core.base_options import BaseOptions
        except Exception as exc:  # pragma: no cover - depends on local runtime
            self._load_error = exc
            return

        try:
            options = vision.HandLandmarkerOptions(
                base_options=BaseOptions(
                    model_asset_path=str(self.model_asset_path),
                    delegate=BaseOptions.Delegate.CPU,
                ),
                num_hands=self.max_num_hands,
                min_hand_detection_confidence=self.min_detection_confidence,
                min_tracking_confidence=self.min_tracking_confidence,
            )
            self._hands = vision.HandLandmarker.create_from_options(options)
        except Exception as exc:  # pragma: no cover - depends on local runtime
            self._load_error = exc
            self._hands = None


def extract_hand_polygons(results, width: int, height: int):
    """Conservative hulls include fingers, not just the five fingertips."""
    polygons = []
    for landmarks in getattr(results, "hand_landmarks", ()) or ():
        points = np.asarray([(p.x * width, p.y * height) for p in landmarks], dtype=np.float32)
        if len(points) < 3 or not np.isfinite(points).all():
            continue
        hull = cv2.convexHull(points).reshape(-1, 2)
        polygons.append(tuple((float(x), float(y)) for x, y in hull))
    return tuple(polygons)


def hand_exclusion_mask(polygons, shape: tuple[int, ...]) -> np.ndarray:
    mask = np.zeros(shape[:2], dtype=np.uint8)
    for polygon in polygons:
        points = np.asarray(polygon, dtype=np.float32)
        if len(points) < 3 or not np.isfinite(points).all():
            continue
        hand_mask = np.zeros_like(mask)
        cv2.fillConvexPoly(hand_mask, np.rint(points).astype(np.int32), 255)
        padding = int(np.clip(np.sqrt(abs(cv2.contourArea(points))) * 0.12, 6, 24))
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * padding + 1, 2 * padding + 1))
        mask = cv2.bitwise_or(mask, cv2.dilate(hand_mask, kernel))
    return mask


class HandOcclusionMasker:
    """Local hand inference for board tracking, separate from note judging."""

    def __init__(self) -> None:
        self.tracker = MediaPipeHandTracker()
        self.available = False
        self.previous_mask: np.ndarray | None = None

    @staticmethod
    def shrink(frame: np.ndarray) -> np.ndarray:
        """The inference-sized image; the result never aliases a camera buffer."""
        height, width = frame.shape[:2]
        scale = min(1.0, 640 / max(height, width))
        return cv2.resize(frame, (round(width * scale), round(height * scale))) if scale < 1 else frame.copy()

    def process(self, frame: np.ndarray) -> np.ndarray | None:
        return self.mask(self.shrink(frame), frame.shape[:2])

    def mask(self, small: np.ndarray, frame_size: tuple[int, int]) -> np.ndarray | None:
        height, width = frame_size
        result = self.tracker.process(small)
        self.available = result.backend_available
        if not self.available:
            self.previous_mask = None
            return None
        mask = hand_exclusion_mask(result.hand_polygons, small.shape)
        mask = cv2.resize(mask, (width, height), interpolation=cv2.INTER_NEAREST)
        combined = mask
        if self.previous_mask is not None and self.previous_mask.shape == mask.shape:
            combined = cv2.bitwise_or(mask, self.previous_mask)
        self.previous_mask = mask
        return combined

    def close(self) -> None:
        self.tracker.close()


class AsyncHandOcclusionMasker:
    """Hand masks from a worker thread, so tracking never waits for inference.

    ``process`` queues the current frame and returns the newest finished mask,
    normally the previous frame's. Each mask already includes the frame before
    it, so the returned region covers the hand over the two preceding frames.
    A hand that enters faster than the mask padding is hidden one frame late;
    the tracker's own appearance check against the reference image still
    rejects such pixels. Only the very first call waits, so the caller learns
    at once whether hand detection works at all.

    One replaceable pending image: a slow detector skips frames, never queues.
    All inference, including model loading, stays on the worker thread.
    """

    def __init__(self, masker: HandOcclusionMasker | None = None) -> None:
        self._masker = masker or HandOcclusionMasker()
        self._condition = Condition()
        self._thread: Thread | None = None
        self._pending: tuple[np.ndarray, tuple[int, int]] | None = None
        self._mask: np.ndarray | None = None
        self._completed = 0
        self._closed = False
        self.available = False
        self.last_inference_ms = 0.

    def process(self, frame: np.ndarray) -> np.ndarray | None:
        frame_size = frame.shape[:2]
        small = self._masker.shrink(frame)
        with self._condition:
            if self._closed:
                return None
            self._pending = (small, frame_size)
            if self._thread is None:
                self._thread = Thread(target=self._run, name="hand-occlusion-mask", daemon=True)
                self._thread.start()
            self._condition.notify_all()
            if self._completed == 0:
                self._condition.wait_for(lambda: self._completed > 0 or self._closed, timeout=10.)
            mask = self._mask
        # Never hand back a mask made for a different camera resolution.
        return mask if mask is not None and mask.shape == frame_size else None

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._pending = None
            self._condition.notify_all()
            thread = self._thread
        if thread is not None:
            thread.join()
        self._masker.close()

    def _run(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._pending is not None)
                if self._closed:
                    return
                (small, frame_size), self._pending = self._pending, None
            started = perf_counter()
            try:
                mask = self._masker.mask(small, frame_size)
                available = self._masker.available
            except Exception:  # Tracking continues without a hand mask.
                mask, available = None, False
            with self._condition:
                self._mask = mask
                self.available = available
                self.last_inference_ms = (perf_counter() - started) * 1000
                self._completed += 1
                self._condition.notify_all()


def extract_fingertips(
    hands_result,
    frame_width: int,
    frame_height: int,
) -> tuple[Fingertip, ...]:
    if hands_result is None or getattr(hands_result, "hand_landmarks", None) is None:
        return ()

    fingertips: list[Fingertip] = []
    handedness_labels: list[str | None] = []
    if getattr(hands_result, "handedness", None) is not None:
        for handedness in hands_result.handedness:
            label = None
            if handedness:
                top_category = handedness[0]
                label = getattr(top_category, "category_name", None)
            handedness_labels.append(label)

    for hand_index, hand_landmarks in enumerate(hands_result.hand_landmarks):
        handedness = handedness_labels[hand_index] if hand_index < len(handedness_labels) else None
        landmarks = hand_landmarks
        for tip_id in FINGERTIP_LANDMARK_IDS:
            if tip_id >= len(landmarks):
                continue
            landmark = landmarks[tip_id]
            fingertips.append(
                Fingertip(
                    point_xy=(float(landmark.x) * frame_width, float(landmark.y) * frame_height),
                    tip_id=tip_id,
                    handedness=handedness,
                )
            )

    return tuple(fingertips)


def estimate_marker_match_radius(note_markers: Sequence[NoteMarker]) -> float:
    if len(note_markers) < 2:
        return DEFAULT_MATCH_RADIUS_PX

    marker_points = np.asarray([marker.point_xy for marker in note_markers], dtype=np.float32)
    nearest_distances: list[float] = []
    for index, point in enumerate(marker_points):
        deltas = marker_points - point
        distances = np.linalg.norm(deltas, axis=1)
        distances = distances[np.arange(len(distances)) != index]
        if distances.size == 0:
            continue
        nearest_distances.append(float(np.min(distances)))

    if not nearest_distances:
        return DEFAULT_MATCH_RADIUS_PX

    estimated_radius = float(np.median(np.asarray(nearest_distances, dtype=np.float32)) * 0.42)
    return float(min(MAX_MATCH_RADIUS_PX, max(MIN_MATCH_RADIUS_PX, estimated_radius)))


def match_fingertips_to_markers(
    note_markers: Sequence[NoteMarker],
    fingertips: Sequence[Fingertip],
    match_radius_px: float,
) -> tuple[int, ...]:
    if not note_markers or not fingertips:
        return ()

    matched_indices: list[int] = []
    radius_sq = match_radius_px * match_radius_px
    for marker_index, marker in enumerate(note_markers):
        marker_point = np.asarray(marker.point_xy, dtype=np.float32)
        for fingertip in fingertips:
            fingertip_point = np.asarray(fingertip.point_xy, dtype=np.float32)
            delta = fingertip_point - marker_point
            if float(np.dot(delta, delta)) <= radius_sq:
                matched_indices.append(marker_index)
                break
    return tuple(matched_indices)
