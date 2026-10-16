"""Read the camera independently of tracking, retaining only its newest frame."""
from __future__ import annotations

from dataclasses import dataclass
from collections import deque
from threading import Condition, Event, Thread
from time import monotonic

import cv2
import numpy as np

from vision.camera_capture import open_camera


@dataclass(frozen=True)
class CapturedFrame:
    image: np.ndarray
    timestamp: float
    sequence: int = 0
    capture_fps: float = 0.


class LatestFrameCapture:
    """Single producer/single consumer with a one-frame, replacing mailbox.

    Only the capture thread touches VideoCapture, including release. Tracking
    may pause for calibration or recovery without accumulating camera frames.
    The timestamp belongs to capture, not to when the consumer becomes free.
    """

    def __init__(self, camera_index: int) -> None:
        self.camera_index = camera_index
        self._condition = Condition()
        self._stop = Event()
        self._latest: CapturedFrame | None = None
        self._finished = False
        self._error: str | None = None
        self._thread = Thread(target=self._run, name=f"practice-camera-{camera_index}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def request_stop(self) -> None:
        self._stop.set()
        with self._condition:
            self._condition.notify_all()

    def close(self) -> None:
        self.request_stop()
        # Do not release a device concurrently with a backend read. The video
        # worker stays alive until its capture thread has released the camera.
        self._thread.join()

    def read(self) -> CapturedFrame | None:
        with self._condition:
            self._condition.wait_for(
                lambda: self._latest is not None or self._finished or self._stop.is_set(),
            )
            if self._stop.is_set():
                return None
            if self._latest is not None:
                latest, self._latest = self._latest, None
                return latest
            if self._error is not None:
                raise RuntimeError(self._error)
            return None

    def _run(self) -> None:
        capture = None
        try:
            capture = open_camera(self.camera_index)
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            if not capture.isOpened():
                raise RuntimeError(
                    f"Could not open webcam index {self.camera_index}. "
                    "Check camera permission or another camera index."
                )
            sequence = 0
            capture_times = deque(maxlen=60)
            while not self._stop.is_set():
                ok, frame = capture.read()
                timestamp = monotonic()
                if self._stop.is_set():
                    break
                if not ok:
                    raise RuntimeError("Failed to read frame from webcam.")
                # Backends may reuse their array on the next read. Own pixels
                # before publishing so a frozen calibration frame stays frozen.
                sequence += 1
                capture_times.append(timestamp)
                elapsed = capture_times[-1] - capture_times[0]
                capture_fps = (len(capture_times) - 1) / elapsed if elapsed > 0 else 0.
                packet = CapturedFrame(frame.copy(), timestamp, sequence, capture_fps)
                with self._condition:
                    self._latest = packet
                    self._condition.notify_all()
        except Exception as exc:
            with self._condition:
                self._error = str(exc)
        finally:
            try:
                if capture is not None:
                    capture.release()
            finally:
                with self._condition:
                    self._finished = True
                    self._condition.notify_all()
