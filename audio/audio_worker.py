from __future__ import annotations

from queue import Empty, Full, Queue
from time import perf_counter

import numpy as np
from PySide6.QtCore import QObject, QThread, Signal

from audio.music_math import cents_difference, is_within_tolerance
from audio.pitch_detection import estimate_pitch
from audio.input_settings import select_input_sample_rate


class AudioWorker(QThread):
    result_ready = Signal(dict)
    input_level_ready = Signal(float)
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
        self._running = False

    def stop(self, timeout_ms: int = 2000) -> None:
        self._running = False
        self.requestInterruption()
        if self.isRunning() and QThread.currentThread() is not self:
            self.wait(timeout_ms)

    def run(self) -> None:
        try:
            import sounddevice as sd
        except Exception as exc:  # pragma: no cover - depends on local environment
            self._emit_error_result(f"sounddevice import failed: {exc}")
            return

        audio_blocks: Queue[np.ndarray] = Queue(maxsize=3)
        stream_status_message = ""
        next_level_at = 0.0

        def audio_callback(indata, _frames, _time_info, status) -> None:
            nonlocal stream_status_message, next_level_at
            if status:
                stream_status_message = str(status)
            block = np.asarray(indata[:, 0], dtype=np.float32).copy()
            # 음정 분석 초기화·결과 유지와 무관하게 실제 입력을 전달한다.
            # 콜백에서는 작은 RMS 계산과 Signal 전송만 하고 UI를 건드리지 않는다.
            # 화면 이벤트가 쌓이지 않도록 초당 최대 10회만 전송한다.
            now = perf_counter()
            if block.size and now >= next_level_at:
                self.input_level_ready.emit(float(np.sqrt(np.mean(np.square(block, dtype=np.float64)))))
                next_level_at = now + 0.1
            try:
                audio_blocks.put_nowait(block)
            except Full:
                try:
                    audio_blocks.get_nowait()
                except Empty:
                    pass
                audio_blocks.put_nowait(block)

        self._running = True
        self.result_ready.emit(self._build_result(status="listening"))
        last_voiced_result: dict | None = None
        last_voiced_at = 0.0

        try:
            # 스트림과 검출기에 같은 실제 샘플레이트를 전달해야 음정이 틀어지지 않는다.
            sample_rate = select_input_sample_rate(sd, self.sample_rate, self.device)
            with sd.InputStream(
                samplerate=sample_rate,
                blocksize=self.block_size,
                channels=1,
                dtype="float32",
                device=self.device,
                callback=audio_callback,
            ):
                next_emit_at = 0.0
                while self._running and not self.isInterruptionRequested():
                    try:
                        block = audio_blocks.get(timeout=0.1)
                    except Empty:
                        continue

                    now = perf_counter()
                    if now < next_emit_at:
                        continue

                    detection = estimate_pitch(
                        block,
                        sample_rate=sample_rate,
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
                        self.result_ready.emit(live_result)
                    elif (
                        last_voiced_result is not None
                        and (now - last_voiced_at) * 1000.0 <= self.result_hold_ms
                    ):
                        held_result = dict(last_voiced_result)
                        held_result["held"] = True
                        held_result["held_ms"] = int((now - last_voiced_at) * 1000.0)
                        held_result["stream_status"] = stream_status_message
                        # 음정 표시를 잠깐 유지해도 입력 미터는 현재 소리 크기를 보여준다.
                        held_result["rms"] = live_result["rms"]
                        held_result["peak"] = live_result["peak"]
                        self.result_ready.emit(held_result)
                    else:
                        self.result_ready.emit(live_result)
                    next_emit_at = now + self.update_interval_ms / 1000.0
        except Exception as exc:  # pragma: no cover - hardware and permission dependent
            self._emit_error_result(f"Microphone input failed: {exc}")
        finally:
            self._running = False

    def _emit_error_result(self, message: str) -> None:
        self.error_message.emit(message)
        self.result_ready.emit(self._build_result(status="error", message=message))

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
