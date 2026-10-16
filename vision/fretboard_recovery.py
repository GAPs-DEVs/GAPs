"""Bounded background descriptor search for a calibrated guitar neck."""
from __future__ import annotations

from dataclasses import dataclass
from threading import Condition, Thread
from time import perf_counter

import cv2
import numpy as np

from vision.fretboard_reference import FretboardVisualReference


@dataclass(frozen=True)
class RecoveryRequest:
    generation: int
    frame_index: int
    timestamp: float
    frame: np.ndarray
    exclusion_mask: np.ndarray | None
    reference: FretboardVisualReference
    roi: tuple[int, int, int, int] | None
    max_side: int
    retry_native: bool = False


@dataclass(frozen=True)
class RecoveryResult:
    request: RecoveryRequest
    pose: np.ndarray | None
    elapsed_ms: float
    error: str | None = None


class BackgroundFretboardRecovery:
    """One active search, one replaceable pending image, one completed result.

    The live tracker never waits for SIFT. Each calibration has an independent
    reference/cache; results from an invalidated generation are discarded here.
    A worker starts lazily, so merely constructing a video widget owns no thread.
    """

    def __init__(self) -> None:
        self._condition = Condition()
        self._thread: Thread | None = None
        self._closed = False
        self._generation = 0
        self._reference: FretboardVisualReference | None = None
        self._pending: RecoveryRequest | None = None
        self._completed: RecoveryResult | None = None
        self._active = False
        self._last_local = float('-inf')
        self._last_global = float('-inf')
        self._global_due = False
        self._global_scale = 640
        self._global_preview = None

    def reset(self, reference: FretboardVisualReference | None = None) -> None:
        snapshot = reference.recovery_snapshot() if reference is not None else None
        with self._condition:
            self._generation += 1
            self._reference = snapshot
            self._pending = self._completed = None
            self._last_local = self._last_global = float('-inf')
            self._global_due = False
            self._global_scale = 640
            self._global_preview = None

    @property
    def busy(self) -> bool:
        with self._condition:
            return self._active or self._pending is not None

    def poll(self) -> RecoveryResult | None:
        with self._condition:
            result, self._completed = self._completed, None
            return result

    def submit(self, frame, exclusion_mask, pose, *, timestamp: float,
               frame_index: int, confirmation: bool = False) -> bool:
        with self._condition:
            reference = self._reference
            if self._closed or reference is None or not reference.is_ready:
                return False
            # Confirmation gets a prompt second independent image. Ordinary
            # failures cannot turn a high camera FPS into more global searches.
            preview = cv2.cvtColor(cv2.resize(frame, (80, 45), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
            difference = cv2.absdiff(preview, self._global_preview) if self._global_preview is not None else None
            changed = difference is not None and (float(difference.mean()) > 4. or float((difference > 12).mean()) > .03)
            # Do not wait an entire idle-scene interval when the guitar returns
            # or the camera moves. Still cap changed-scene full searches so
            # continuously moving hands cannot flood the worker.
            interval = .15 if changed else .5
            full = not confirmation and self._global_due and timestamp - self._last_global >= interval
            if not full and timestamp - self._last_local < (.05 if confirmation else .2):
                return False
            quad = cv2.perspectiveTransform(reference._reference_quad[:, None], pose).reshape(-1, 2)
            if not np.isfinite(quad).all():
                return False
            if full:
                roi = None
                # Preserve at least ~40 pixels across a small neck. Failed
                # global attempts also progress 640 -> 960 -> native resolution.
                neck_width = min(np.linalg.norm(quad[1] - quad[0]), np.linalg.norm(quad[2] - quad[3]))
                required = max(frame.shape[:2]) * 40 / max(neck_width, 1)
                max_side = min(1280, max(self._global_scale, int(required)))
                self._last_global = timestamp
                self._global_preview = preview
            else:
                low, high = quad.min(axis=0), quad.max(axis=0)
                padding = np.maximum((high - low) * .5, 72.)
                roi = (*np.floor(low - padding).astype(int), *np.ceil(high + padding).astype(int))
                max_side = 1280  # Crop first; keep local string/inlay detail.
                self._last_local = timestamp
            # Own input pixels: both the camera and caller may reuse arrays.
            self._pending = RecoveryRequest(
                self._generation, frame_index, timestamp, frame.copy(),
                None if exclusion_mask is None else exclusion_mask.copy(), reference, roi, max_side,
                retry_native=full and changed and max_side < 1280,
            )
            if self._thread is None:
                self._thread = Thread(target=self._run, name='fretboard-recovery', daemon=True)
                self._thread.start()
            self._condition.notify_all()
            return True

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._generation += 1
            self._pending = self._completed = None
            self._reference = None
            self._condition.notify_all()
            thread = self._thread
        if thread is not None:
            thread.join()

    def _search(self, request: RecoveryRequest) -> RecoveryResult:
        started = perf_counter()
        pose, error = None, None
        try:
            with request.reference.frame_scope(request.frame, request.exclusion_mask):
                pose = request.reference.estimate(
                    request.frame, request.exclusion_mask, roi=request.roi, max_side=request.max_side,
                )
                if pose is None and request.retry_native:
                    # A returning neck can lack enough descriptors at 640 px.
                    # Retry this same fresh image in the background instead of
                    # waiting another entire global-search interval to upscale.
                    pose = request.reference.estimate(request.frame, request.exclusion_mask, max_side=1280)
                if pose is not None:
                    pose = request.reference.refine(request.frame, pose, request.exclusion_mask)
        except Exception as exc:
            error = str(exc)
        return RecoveryResult(request, pose, (perf_counter() - started) * 1000, error)

    def _run(self) -> None:
        while True:
            with self._condition:
                ready = self._condition.wait_for(lambda: self._closed or self._pending is not None, timeout=2.)
                if not ready:
                    self._thread = None
                    return
                if self._closed:
                    return
                request, self._pending = self._pending, None
                self._active = True
            result = self._search(request)
            with self._condition:
                self._active = False
                if not self._closed and request.generation == self._generation:
                    self._completed = result
                    self._global_due = result.pose is None
                    if request.roi is None and result.pose is None:
                        self._global_scale = min(1280, self._global_scale + 320)
                    elif result.pose is not None:
                        self._global_scale = 640
                self._condition.notify_all()
