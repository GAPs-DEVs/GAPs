from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audio.audio_worker import AudioWorker
from audio.music_math import SOLFEGE_TEST_NOTE_FREQUENCIES, frequency_tolerance_range


STATUS_TEXT = {
    "idle": "대기",
    "listening": "듣는 중",
    "correct": "정답",
    "incorrect": "오답",
    "no_signal": "신호 없음",
    "error": "오류",
}

STATUS_STYLES = {
    "idle": "color: #6b7280;",
    "listening": "color: #2563eb;",
    "correct": "color: #15803d; font-weight: 700;",
    "incorrect": "color: #b91c1c; font-weight: 700;",
    "no_signal": "color: #a16207;",
    "error": "color: #b91c1c; font-weight: 700;",
}


class PitchJudgeWidget(QWidget):
    result_changed = Signal(dict)

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
        parent: QWidget | None = None,
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
        self.worker: AudioWorker | None = None
        self.expected_note_name = self._note_name_for_frequency(self.expected_frequency)

        self.note_combo = QComboBox()
        self.backend_value = QLabel(self.pitch_backend)
        self.detected_value = QLabel("-")
        self.expected_value = QLabel(f"{self.expected_frequency:.2f} Hz")
        self.range_value = QLabel("")
        self.cents_value = QLabel("-")
        self.status_value = QLabel(STATUS_TEXT["idle"])
        self.message_label = QLabel("")
        self.message_label.setWordWrap(True)
        self.message_label.setStyleSheet("color: #b91c1c;")

        self.start_button = QPushButton("음 판정 시작")
        self.stop_button = QPushButton("중지")
        self.stop_button.setEnabled(False)
        self.start_button.clicked.connect(self.start_listening)
        self.stop_button.clicked.connect(self.stop_listening)

        self._build_ui()
        self._apply_status("idle")
        self._refresh_expected_display()

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(10)

        title = QLabel("Pitch Judgement")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        root_layout.addWidget(title)

        values_frame = QFrame()
        values_frame.setFrameShape(QFrame.Shape.StyledPanel)
        values_layout = QGridLayout(values_frame)
        values_layout.setColumnStretch(1, 1)
        self._populate_note_combo()
        self.note_combo.currentIndexChanged.connect(self._handle_note_selection)
        values_layout.addWidget(QLabel("테스트 음"), 0, 0)
        values_layout.addWidget(self.note_combo, 0, 1)
        values_layout.addWidget(QLabel("피치 엔진"), 1, 0)
        values_layout.addWidget(self.backend_value, 1, 1)
        values_layout.addWidget(QLabel("감지 주파수"), 2, 0)
        values_layout.addWidget(self.detected_value, 2, 1)
        values_layout.addWidget(QLabel("목표 주파수"), 3, 0)
        values_layout.addWidget(self.expected_value, 3, 1)
        values_layout.addWidget(QLabel("정답 주파수 범위"), 4, 0)
        values_layout.addWidget(self.range_value, 4, 1)
        values_layout.addWidget(QLabel("cents 오차"), 5, 0)
        values_layout.addWidget(self.cents_value, 5, 1)
        values_layout.addWidget(QLabel("판정 상태"), 6, 0)
        values_layout.addWidget(self.status_value, 6, 1)
        root_layout.addWidget(values_frame)

        button_row = QHBoxLayout()
        button_row.addWidget(self.start_button)
        button_row.addWidget(self.stop_button)
        root_layout.addLayout(button_row)
        root_layout.addWidget(self.message_label)

    def set_expected_frequency(self, expected_frequency: float) -> None:
        if expected_frequency <= 0:
            raise ValueError("expected_frequency must be greater than 0 Hz.")
        self.expected_frequency = expected_frequency
        self.expected_note_name = self._note_name_for_frequency(expected_frequency)
        self._refresh_expected_display()
        self._sync_note_combo_to_expected_frequency()
        if self.worker is not None and self.worker.isRunning():
            self.stop_listening()

    def set_tolerance_cents(self, tolerance_cents: float) -> None:
        if tolerance_cents < 0:
            raise ValueError("tolerance_cents must be greater than or equal to 0.")
        self.tolerance_cents = tolerance_cents
        self._refresh_expected_display()
        if self.worker is not None and self.worker.isRunning():
            self.stop_listening()

    def _populate_note_combo(self) -> None:
        self.note_combo.blockSignals(True)
        self.note_combo.clear()
        for note_name, frequency in SOLFEGE_TEST_NOTE_FREQUENCIES.items():
            self.note_combo.addItem(f"{note_name} ({frequency:.2f} Hz)", (note_name, frequency))
        if self.expected_note_name is None:
            self.note_combo.addItem(f"사용자 지정 ({self.expected_frequency:.2f} Hz)", (None, self.expected_frequency))
        self._sync_note_combo_to_expected_frequency(block_signals=False)
        self.note_combo.blockSignals(False)

    def _handle_note_selection(self, index: int) -> None:
        data = self.note_combo.itemData(index)
        if data is None:
            return
        note_name, frequency = data
        self.expected_note_name = note_name
        self.expected_frequency = float(frequency)
        self._refresh_expected_display()
        if self.worker is not None and self.worker.isRunning():
            self.stop_listening()

    def _sync_note_combo_to_expected_frequency(self, block_signals: bool = True) -> None:
        if block_signals:
            self.note_combo.blockSignals(True)
        try:
            expected_index = -1
            for index in range(self.note_combo.count()):
                data = self.note_combo.itemData(index)
                if data is None:
                    continue
                _note_name, frequency = data
                if abs(float(frequency) - self.expected_frequency) < 0.01:
                    expected_index = index
                    break

            if expected_index < 0:
                custom_index = self.note_combo.findText("사용자 지정", Qt.MatchFlag.MatchStartsWith)
                if custom_index < 0:
                    self.note_combo.addItem(
                        f"사용자 지정 ({self.expected_frequency:.2f} Hz)",
                        (None, self.expected_frequency),
                    )
                    custom_index = self.note_combo.count() - 1
                else:
                    self.note_combo.setItemText(custom_index, f"사용자 지정 ({self.expected_frequency:.2f} Hz)")
                    self.note_combo.setItemData(custom_index, (None, self.expected_frequency))
                expected_index = custom_index

            self.note_combo.setCurrentIndex(expected_index)
        finally:
            if block_signals:
                self.note_combo.blockSignals(False)

    def _refresh_expected_display(self) -> None:
        note_prefix = f"{self.expected_note_name} / " if self.expected_note_name else ""
        lower_frequency, upper_frequency = frequency_tolerance_range(
            self.expected_frequency,
            self.tolerance_cents,
        )
        self.expected_value.setText(f"{note_prefix}{self.expected_frequency:.2f} Hz")
        self.range_value.setText(
            f"{lower_frequency:.2f} ~ {upper_frequency:.2f} Hz (±{self.tolerance_cents:.0f} cents)"
        )

    def _note_name_for_frequency(self, frequency: float) -> str | None:
        for note_name, note_frequency in SOLFEGE_TEST_NOTE_FREQUENCIES.items():
            if abs(note_frequency - frequency) < 0.01:
                return note_name
        return None

    def start_listening(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            return

        self.message_label.clear()
        self.worker = AudioWorker(
            expected_frequency=self.expected_frequency,
            tolerance_cents=self.tolerance_cents,
            sample_rate=self.sample_rate,
            block_size=self.block_size,
            min_freq=self.min_freq,
            max_freq=self.max_freq,
            silence_threshold=self.silence_threshold,
            update_interval_ms=self.update_interval_ms,
            result_hold_ms=self.result_hold_ms,
            pitch_backend=self.pitch_backend,
            device=self.device,
            parent=self,
        )
        self.worker.result_ready.connect(self._handle_result)
        self.worker.error_message.connect(self._handle_error)
        self.worker.finished.connect(self._handle_finished)

        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self._handle_result(self._idle_result(status="listening"))
        self.worker.start()

    def stop_listening(self) -> None:
        if self.worker is None:
            self._handle_result(self._idle_result(status="idle"))
            return

        self.stop_button.setEnabled(False)
        self.worker.stop()
        self._handle_result(self._idle_result(status="idle"))

    def _handle_result(self, result: dict) -> None:
        result = self._with_widget_context(result)
        detected_frequency = result.get("detected_frequency")
        cents = result.get("cents_difference")
        status = str(result.get("status", "idle"))
        held = bool(result.get("held", False))
        held_ms = int(result.get("held_ms", 0) or 0)

        if detected_frequency is None:
            self.detected_value.setText("-")
        else:
            self.detected_value.setText(f"{float(detected_frequency):.2f} Hz")

        if cents is None:
            self.cents_value.setText("-")
        else:
            self.cents_value.setText(f"{float(cents):+.1f} cents")

        self._apply_status(status, held=held, held_ms=held_ms)
        message = result.get("message") or result.get("stream_status") or ""
        self.message_label.setText(str(message))
        self.result_changed.emit(result)

    def _handle_error(self, message: str) -> None:
        self.message_label.setText(message)

    def _handle_finished(self) -> None:
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.worker = None

    def _apply_status(self, status: str, held: bool = False, held_ms: int = 0) -> None:
        status_text = STATUS_TEXT.get(status, status)
        if held and status in {"correct", "incorrect"}:
            status_text = f"{status_text} (유지 {held_ms}ms)"
        self.status_value.setText(status_text)
        self.status_value.setStyleSheet(STATUS_STYLES.get(status, STATUS_STYLES["idle"]))

    def _idle_result(self, status: str = "idle") -> dict:
        return self._with_widget_context({
            "detected_frequency": None,
            "expected_frequency": self.expected_frequency,
            "cents_difference": None,
            "is_correct": False,
            "status": status,
            "rms": 0.0,
            "peak": 0.0,
            "clarity": 0.0,
            "message": "",
            "stream_status": "",
            "held": False,
            "held_ms": 0,
            "pitch_backend": self.pitch_backend,
        })

    def _with_widget_context(self, result: dict) -> dict:
        lower_frequency, upper_frequency = frequency_tolerance_range(
            self.expected_frequency,
            self.tolerance_cents,
        )
        enriched = dict(result)
        enriched["expected_frequency"] = self.expected_frequency
        enriched["expected_note"] = self.expected_note_name
        enriched["tolerance_cents"] = self.tolerance_cents
        enriched["accepted_min_frequency"] = lower_frequency
        enriched["accepted_max_frequency"] = upper_frequency
        enriched["result_hold_ms"] = self.result_hold_ms
        enriched["pitch_backend"] = self.pitch_backend
        return enriched

    def closeEvent(self, event: QCloseEvent) -> None:
        self.stop_listening()
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    widget = PitchJudgeWidget(expected_frequency=196.0, tolerance_cents=30.0)
    widget.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
    widget.resize(360, 240)
    widget.show()
    sys.exit(app.exec())
