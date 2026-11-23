from __future__ import annotations

from collections.abc import Mapping
from queue import Empty, Full, Queue
from threading import Lock
from time import perf_counter

import numpy as np
from PySide6.QtCore import QObject, QThread, Signal

from audio.audio_worker import open_input_stream
from audio.polyphonic_detection import (
    ChordJudgeGate,
    ExpectedFrequency,
    detect_expected_frequencies_cqt,
    recent_tone_energy_db,
)


RESTRIKE_MESSAGE = "이미 울리고 있던 음은 다시 쳐야 인정됩니다."


class ChordAudioWorker(QThread):
    result_ready = Signal(dict)
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
        onset_rise_db: float = 3.0,
        confirm_frames: int = 2,
        target_id: int = 0,
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
        self.target_id = target_id
        self.onset_rise_db = onset_rise_db
        self.confirm_frames = confirm_frames
        self._judge_gate = self._new_judge_gate()
        self._running = True
        self._target_lock = Lock()
        self._pending_target: tuple[list[ExpectedFrequency], int] | None = None

    def request_stop(self) -> None:
        self._running = False
        self.requestInterruption()

    def stop(self, timeout_ms: int = 2000) -> None:
        self.request_stop()
        if self.isRunning() and QThread.currentThread() is not self:
            self.wait(timeout_ms)

    def set_target(
        self,
        expected_frequencies: list[ExpectedFrequency],
        target_id: int,
    ) -> None:
        if not expected_frequencies:
            raise ValueError("expected_frequencies must not be empty.")
        with self._target_lock:
            self._pending_target = (list(expected_frequencies), target_id)

    def _new_judge_gate(self, baseline_db: Mapping[str, float] | None = None) -> ChordJudgeGate:
        return ChordJudgeGate(
            restrike_names=[expected.name for expected in self.expected_frequencies],
            onset_rise_db=self.onset_rise_db,
            confirm_frames=self.confirm_frames,
            baseline_db=baseline_db,
        )

    def _apply_pending_target(self, audio_buffer: np.ndarray | None = None, sample_rate: int = 0) -> bool:
        with self._target_lock:
            pending = self._pending_target
            self._pending_target = None
        if pending is None:
            return False
        self.expected_frequencies, self.target_id = pending
        baseline_db = None
        if audio_buffer is not None and audio_buffer.size > 0:
            baseline_db = {
                expected.name: recent_tone_energy_db(audio_buffer, sample_rate, expected.frequency)
                for expected in self.expected_frequencies
            }
        self._judge_gate = self._new_judge_gate(baseline_db)
        return True

    def run(self) -> None:
        try:
            import sounddevice as sd
        except Exception as exc:  # pragma: no cover - depends on local environment
            self._emit_error_result(f"sounddevice import failed: {exc}")
            return
        if not self._running:
            return

        audio_blocks: Queue[np.ndarray] = Queue(maxsize=8)
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
        self._emit(self._idle_result("listening"))
        audio_buffer = np.asarray([], dtype=np.float32)
        last_correct_result: dict | None = None
        last_correct_at = 0.0

        try:
            with open_input_stream(sd, self.device, self.sample_rate, self.block_size, audio_callback) as stream:
                sample_rate = int(stream.samplerate)
                window_samples = max(1, int(sample_rate * self.analysis_window_ms / 1000.0))
                next_emit_at = 0.0
                while self._running and not self.isInterruptionRequested():
                    if self._apply_pending_target(audio_buffer, sample_rate):
                        _drain(audio_blocks)
                        audio_buffer = np.asarray([], dtype=np.float32)
                        last_correct_result = None
                        next_emit_at = 0.0
                        self._emit(self._idle_result("listening"))
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
                    detection = self._judge_gate.update(detection)
                    result = self._build_result(detection, stream_status_message)
                    if detection.is_correct:
                        last_correct_result = result
                        last_correct_at = now
                        self._emit(result)
                    elif (
                        last_correct_result is not None
                        and (now - last_correct_at) * 1000.0 <= self.result_hold_ms
                    ):
                        held_result = dict(last_correct_result)
                        held_result["held"] = True
                        held_result["held_ms"] = int((now - last_correct_at) * 1000.0)
                        held_result["stream_status"] = stream_status_message
                        self._emit(held_result)
                    else:
                        self._emit(result)
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
        result = self._idle_result("error")
        result["message"] = message
        self._emit(result)

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
        awaiting_restrike = any(
            match["rejection_reason"] == "awaiting_restrike" for match in matches
        )
        return {
            # "confirming" is internal: the chord looked right but still needs
            # another consecutive window before it is accepted.
            "status": "listening" if detection.status == "confirming" else detection.status,
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
            "message": RESTRIKE_MESSAGE if awaiting_restrike else "",
            "stream_status": stream_status_message,
            "held": False,
            "held_ms": 0,
            "backend": detection.backend,
            "expected_frequencies": [
                {"name": expected.name, "frequency": expected.frequency}
                for expected in self.expected_frequencies
            ],
        }


def _drain(blocks: Queue) -> None:
    while True:
        try:
            blocks.get_nowait()
        except Empty:
            return
