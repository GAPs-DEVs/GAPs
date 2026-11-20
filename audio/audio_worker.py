from __future__ import annotations

from queue import Empty, Full, Queue
from threading import Lock
from time import perf_counter

import numpy as np
from PySide6.QtCore import QObject, QThread, Signal

from audio.music_math import cents_difference, is_within_tolerance
from audio.pitch_detection import estimate_pitch


class AudioWorker(QThread):
    result_ready = Signal(dict)
    error_message = Signal(str)

    def __init__(
        self,
        expected_frequency: float,
        tolerance_cents: float = 30.0,
        sample_rate: int = 44100,
        block_size: int = 4096,
        min_freq: float = 70.0,
        max_freq: float = 1000.0,
        silence_threshold: float = 0.006,
        update_interval_ms: int = 50,
        result_hold_ms: int = 900,
        pitch_backend: str = "librosa_yin",
        device: int | str | None = None,
        target_id: int = 0,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        if expected_frequency <= 0:
            raise ValueError("expected_frequency must be greater than 0 Hz.")
        if tolerance_cents < 0:
            raise ValueError("tolerance_cents must be greater than or equal to 0.")
        if result_hold_ms < 0:
            raise ValueError("result_hold_ms must be greater than or equal to 0.")

        self.expected_frequency = expected_frequency
        self.tolerance_cents = tolerance_cents
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.min_freq = min_freq
        self.max_freq = max_freq
        self.silence_threshold = silence_threshold
        self.update_interval_ms = update_interval_ms
        self.result_hold_ms = result_hold_ms
        self.pitch_backend = pitch_backend
        self.device = device
        self.target_id = target_id
        self._running = True
        self._target_lock = Lock()
        self._pending_target: tuple[float, int] | None = None

    def request_stop(self) -> None:
        self._running = False
        self.requestInterruption()

    def stop(self, timeout_ms: int = 2000) -> None:
        self.request_stop()
        if self.isRunning() and QThread.currentThread() is not self:
            self.wait(timeout_ms)

    def set_target(self, expected_frequency: float, target_id: int) -> None:
        if expected_frequency <= 0:
            raise ValueError("expected_frequency must be greater than 0 Hz.")
        with self._target_lock:
            self._pending_target = (expected_frequency, target_id)

    def _apply_pending_target(self) -> bool:
        with self._target_lock:
            pending = self._pending_target
            self._pending_target = None
        if pending is None:
            return False
        self.expected_frequency, self.target_id = pending
        return True

    def run(self) -> None:
        try:
            import sounddevice as sd
        except Exception as exc:  # pragma: no cover - depends on local environment
            self._emit_error_result(f"sounddevice import failed: {exc}")
            return
        if not self._running:
            return

        audio_blocks: Queue[np.ndarray] = Queue(maxsize=3)
        stream_status_message = ""

        def audio_callback(indata, _frames, _time_info, status) -> None:
            nonlocal stream_status_message
            if status:
                stream_status_message = str(status)
            block = np.asarray(indata[:, 0], dtype=np.float32).copy()
            try:
                audio_blocks.put_nowait(block)
            except Full:
                try:
                    audio_blocks.get_nowait()
                except Empty:
                    pass
                audio_blocks.put_nowait(block)

        self._apply_pending_target()
        self._emit(self._build_result(status="listening"))
        last_voiced_result: dict | None = None
        last_voiced_at = 0.0

        try:
            with sd.InputStream(
                samplerate=self.sample_rate,
                blocksize=self.block_size,
                channels=1,
                dtype="float32",
                device=self.device,
                callback=audio_callback,
            ):
                next_emit_at = 0.0
                while self._running and not self.isInterruptionRequested():
                    if self._apply_pending_target():
                        _drain(audio_blocks)
                        last_voiced_result = None
                        next_emit_at = 0.0
                        self._emit(self._build_result(status="listening"))
                    try:
                        block = audio_blocks.get(timeout=0.1)
                    except Empty:
                        continue

                    now = perf_counter()
                    if now < next_emit_at:
                        continue

                    detection = estimate_pitch(
                        block,
                        sample_rate=self.sample_rate,
                        min_freq=self.min_freq,
                        max_freq=self.max_freq,
                        silence_threshold=self.silence_threshold,
                        backend=self.pitch_backend,
                    )
                    live_result = self._build_result(
                        detected_frequency=detection.frequency,
                        rms=detection.rms,
                        peak=detection.peak,
                        clarity=detection.clarity,
                        stream_status=stream_status_message,
                    )
                    if detection.frequency is not None:
                        last_voiced_result = live_result
                        last_voiced_at = now
                        self._emit(live_result)
                    elif (
                        last_voiced_result is not None
                        and (now - last_voiced_at) * 1000.0 <= self.result_hold_ms
                    ):
                        held_result = dict(last_voiced_result)
                        held_result["held"] = True
                        held_result["held_ms"] = int((now - last_voiced_at) * 1000.0)
                        held_result["stream_status"] = stream_status_message
                        self._emit(held_result)
                    else:
                        self._emit(live_result)
                    next_emit_at = now + self.update_interval_ms / 1000.0
        except Exception as exc:  # pragma: no cover - hardware and permission dependent
            self._emit_error_result(f"Microphone input failed: {exc}")
        finally:
            self._running = False

    def _emit(self, result: dict) -> None:
        result["target_id"] = self.target_id
        self.result_ready.emit(result)

    def _emit_error_result(self, message: str) -> None:
        self.error_message.emit(message)
        self._emit(self._build_result(status="error", message=message))

    def _build_result(
        self,
        detected_frequency: float | None = None,
        status: str | None = None,
        message: str = "",
        rms: float = 0.0,
        peak: float = 0.0,
        clarity: float = 0.0,
        stream_status: str = "",
        held: bool = False,
        held_ms: int = 0,
        pitch_backend: str | None = None,
    ) -> dict:
        cents = None
        is_correct = False

        if status is None:
            if detected_frequency is None:
                status = "no_signal"
            else:
                cents = cents_difference(detected_frequency, self.expected_frequency)
                is_correct = is_within_tolerance(
                    detected_frequency,
                    self.expected_frequency,
                    self.tolerance_cents,
                )
                status = "correct" if is_correct else "incorrect"

        return {
            "detected_frequency": detected_frequency,
            "expected_frequency": self.expected_frequency,
            "cents_difference": cents,
            "is_correct": is_correct,
            "status": status,
            "rms": rms,
            "peak": peak,
            "clarity": clarity,
            "message": message,
            "stream_status": stream_status,
            "held": held,
            "held_ms": held_ms,
            "pitch_backend": pitch_backend or self.pitch_backend,
        }


def _drain(blocks: Queue) -> None:
    while True:
        try:
            blocks.get_nowait()
        except Empty:
            return
