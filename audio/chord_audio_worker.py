from __future__ import annotations

from queue import Empty, Full, Queue
from time import perf_counter

import numpy as np
from PySide6.QtCore import QObject, QThread, Signal

from audio.polyphonic_detection import ExpectedFrequency, detect_expected_frequencies_cqt
from audio.input_settings import select_input_sample_rate


class ChordAudioWorker(QThread):
    result_ready = Signal(dict)
    input_level_ready = Signal(float)
    error_message = Signal(str)

    def __init__(
        self,
        expected_frequencies: list[ExpectedFrequency],
        sample_rate: int = 44100,
        block_size: int = 2048,
        analysis_window_ms: int = 650,
        tolerance_cents: float = 35.0,
        silence_threshold: float = 0.006,
        update_interval_ms: int = 120,
        result_hold_ms: int = 600,
        device: int | str | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        if not expected_frequencies:
            raise ValueError("expected_frequencies must not be empty.")
        if analysis_window_ms <= 0:
            raise ValueError("analysis_window_ms must be greater than 0.")
        if result_hold_ms < 0:
            raise ValueError("result_hold_ms must be greater than or equal to 0.")

        self.expected_frequencies = expected_frequencies
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.analysis_window_ms = analysis_window_ms
        self.tolerance_cents = tolerance_cents
        self.silence_threshold = silence_threshold
        self.update_interval_ms = update_interval_ms
        self.result_hold_ms = result_hold_ms
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

        audio_blocks: Queue[np.ndarray] = Queue(maxsize=8)
        stream_status_message = ""
        next_level_at = 0.0

        def audio_callback(indata, _frames, _time_info, status) -> None:
            nonlocal stream_status_message, next_level_at
            if status:
                stream_status_message = str(status)
            block = np.asarray(indata[:, 0], dtype=np.float32).copy()
            # CQT 분석 창을 채우거나 첫 분석을 초기화하는 중에도 입력 미터는
            # 실제 콜백의 소리 크기를 표시한다. UI 변경은 수신 측에서 처리한다.
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
        self.result_ready.emit(self._idle_result("listening"))
        audio_buffer = np.asarray([], dtype=np.float32)
        last_correct_result: dict | None = None
        last_correct_at = 0.0

        try:
            # 대체 샘플레이트를 사용하면 분석 창의 샘플 수도 함께 조정한다.
            sample_rate = select_input_sample_rate(sd, self.sample_rate, self.device)
            window_samples = max(1, int(sample_rate * self.analysis_window_ms / 1000.0))
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

                    audio_buffer = np.concatenate((audio_buffer, block))
                    if audio_buffer.size > window_samples:
                        audio_buffer = audio_buffer[-window_samples:]
                    if audio_buffer.size < min(window_samples, self.block_size * 2):
                        continue

                    now = perf_counter()
                    if now < next_emit_at:
                        continue

                    detection = detect_expected_frequencies_cqt(
                        audio_buffer,
                        sample_rate=sample_rate,
                        expected_frequencies=self.expected_frequencies,
                        tolerance_cents=self.tolerance_cents,
                        silence_threshold=self.silence_threshold,
                    )
                    result = self._build_result(detection, stream_status_message)
                    if detection.is_correct:
                        last_correct_result = result
                        last_correct_at = now
                        self.result_ready.emit(result)
                    elif (
                        last_correct_result is not None
                        and (now - last_correct_at) * 1000.0 <= self.result_hold_ms
                    ):
                        held_result = dict(last_correct_result)
                        held_result["held"] = True
                        held_result["held_ms"] = int((now - last_correct_at) * 1000.0)
                        held_result["stream_status"] = stream_status_message
                        held_result["rms"] = result["rms"]
                        held_result["peak"] = result["peak"]
                        self.result_ready.emit(held_result)
                    else:
                        self.result_ready.emit(result)
                    next_emit_at = now + self.update_interval_ms / 1000.0
        except Exception as exc:  # pragma: no cover - hardware and permission dependent
            self._emit_error_result(f"Microphone input failed: {exc}")
        finally:
            self._running = False

    def _emit_error_result(self, message: str) -> None:
        self.error_message.emit(message)
        result = self._idle_result("error")
        result["message"] = message
        self.result_ready.emit(result)

    def _idle_result(self, status: str = "idle") -> dict:
        return {
            "status": status,
            "is_correct": False,
            "matches": [],
            "matched_notes": [],
            "missing_notes": [expected.name for expected in self.expected_frequencies],
            "rejected_notes": [],
            "rms": 0.0,
            "peak": 0.0,
            "noise_floor_db": -120.0,
            "message": "",
            "stream_status": "",
            "held": False,
            "held_ms": 0,
            "backend": "librosa_cqt",
            "expected_frequencies": [
                {"name": expected.name, "frequency": expected.frequency}
                for expected in self.expected_frequencies
            ],
        }

    def _build_result(self, detection, stream_status_message: str) -> dict:
        matches = [
            {
                "name": match.name,
                "expected_frequency": match.expected_frequency,
                "detected_frequency": match.detected_frequency,
                "cents_difference": match.cents_difference,
                "energy_db": match.energy_db,
                "is_present": match.is_present,
                "rejection_reason": match.rejection_reason,
            }
            for match in detection.matches
        ]
        return {
            "status": detection.status,
            "is_correct": detection.is_correct,
            "matches": matches,
            "matched_notes": [match["name"] for match in matches if match["is_present"]],
            "missing_notes": [match["name"] for match in matches if not match["is_present"]],
            "rejected_notes": [
                match["name"] for match in matches if match["rejection_reason"]
            ],
            "rms": detection.rms,
            "peak": detection.peak,
            "noise_floor_db": detection.noise_floor_db,
            "message": "",
            "stream_status": stream_status_message,
            "held": False,
            "held_ms": 0,
            "backend": detection.backend,
            "expected_frequencies": [
                {"name": expected.name, "frequency": expected.frequency}
                for expected in self.expected_frequencies
            ],
        }
