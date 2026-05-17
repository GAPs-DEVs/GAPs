from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
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

from audio.chord_audio_worker import ChordAudioWorker
from audio.music_math import SOLFEGE_TEST_NOTE_FREQUENCIES
from audio.polyphonic_detection import ExpectedFrequency


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


class ChordJudgeWidget(QWidget):
    result_changed = Signal(dict)

    def __init__(
        self,
        expected_notes: list[str] | None = None,
        tolerance_cents: float = 50.0,
        sample_rate: int = 44100,
        block_size: int = 2048,
        analysis_window_ms: int = 650,
        silence_threshold: float = 0.006,
        update_interval_ms: int = 120,
        result_hold_ms: int = 1000,
        device: int | str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.tolerance_cents = tolerance_cents
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.analysis_window_ms = analysis_window_ms
        self.silence_threshold = silence_threshold
        self.update_interval_ms = update_interval_ms
        self.result_hold_ms = result_hold_ms
        self.device = device
        self.worker: ChordAudioWorker | None = None

        self.default_expected_notes = expected_notes or ["낮은 도", "미", "솔"]
        self.note_checkboxes: dict[str, QCheckBox] = {}
        self.expected_value = QLabel("")
        self.matched_value = QLabel("-")
        self.missing_value = QLabel("-")
        self.status_value = QLabel(STATUS_TEXT["idle"])
        self.message_label = QLabel("")
        self.message_label.setWordWrap(True)
        self.message_label.setStyleSheet("color: #b91c1c;")

        self.start_button = QPushButton("다중음 판정 시작")
        self.stop_button = QPushButton("중지")
        self.stop_button.setEnabled(False)
        self.start_button.clicked.connect(self.start_listening)
        self.stop_button.clicked.connect(self.stop_listening)

        self._build_ui()
        self._refresh_expected_display()
        self._apply_status("idle")

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(10)

        title = QLabel("Multi Note Judgement")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        root_layout.addWidget(title)

        selector_frame = QFrame()
        selector_frame.setFrameShape(QFrame.Shape.StyledPanel)
        selector_layout = QGridLayout(selector_frame)
        for index, (note_name, frequency) in enumerate(SOLFEGE_TEST_NOTE_FREQUENCIES.items()):
            checkbox = QCheckBox(f"{note_name} ({frequency:.2f} Hz)")
            checkbox.setChecked(note_name in self.default_expected_notes)
            checkbox.stateChanged.connect(self._handle_note_selection_change)
            self.note_checkboxes[note_name] = checkbox
            selector_layout.addWidget(checkbox, index // 2, index % 2)
        root_layout.addWidget(selector_frame)

        values_frame = QFrame()
        values_frame.setFrameShape(QFrame.Shape.StyledPanel)
        values_layout = QGridLayout(values_frame)
        values_layout.setColumnStretch(1, 1)
        values_layout.addWidget(QLabel("기대 음"), 0, 0)
        values_layout.addWidget(self.expected_value, 0, 1)
        values_layout.addWidget(QLabel("감지된 기대 음"), 1, 0)
        values_layout.addWidget(self.matched_value, 1, 1)
        values_layout.addWidget(QLabel("빠진 음"), 2, 0)
        values_layout.addWidget(self.missing_value, 2, 1)
        values_layout.addWidget(QLabel("판정 상태"), 3, 0)
        values_layout.addWidget(self.status_value, 3, 1)
        root_layout.addWidget(values_frame)

        button_row = QHBoxLayout()
        button_row.addWidget(self.start_button)
        button_row.addWidget(self.stop_button)
        root_layout.addLayout(button_row)
        root_layout.addWidget(self.message_label)

    def selected_expected_frequencies(self) -> list[ExpectedFrequency]:
        selected: list[ExpectedFrequency] = []
        for note_name, checkbox in self.note_checkboxes.items():
            if checkbox.isChecked():
                selected.append(
                    ExpectedFrequency(
                        name=note_name,
                        frequency=SOLFEGE_TEST_NOTE_FREQUENCIES[note_name],
                    )
                )
        return selected

    def start_listening(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            return

        expected_frequencies = self.selected_expected_frequencies()
        if not expected_frequencies:
            self.message_label.setText("테스트할 음을 하나 이상 선택하세요.")
            return

        self.message_label.clear()
        self.worker = ChordAudioWorker(
            expected_frequencies=expected_frequencies,
            sample_rate=self.sample_rate,
            block_size=self.block_size,
            analysis_window_ms=self.analysis_window_ms,
            tolerance_cents=self.tolerance_cents,
            silence_threshold=self.silence_threshold,
            update_interval_ms=self.update_interval_ms,
            result_hold_ms=self.result_hold_ms,
            device=self.device,
            parent=self,
        )
        self.worker.result_ready.connect(self._handle_result)
        self.worker.error_message.connect(self._handle_error)
        self.worker.finished.connect(self._handle_finished)

        self._set_selection_enabled(False)
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self._handle_result(self._idle_result("listening"))
        self.worker.start()

    def stop_listening(self) -> None:
        if self.worker is None:
            self._handle_result(self._idle_result("idle"))
            return
        self.stop_button.setEnabled(False)
        self.worker.stop()
        self._handle_result(self._idle_result("idle"))

    def _handle_note_selection_change(self, _: int) -> None:
        self._refresh_expected_display()

    def _handle_result(self, result: dict) -> None:
        status = str(result.get("status", "idle"))
        held = bool(result.get("held", False))
        held_ms = int(result.get("held_ms", 0) or 0)
        matched_notes = result.get("matched_notes") or []
        missing_notes = result.get("missing_notes") or []

        self.matched_value.setText(", ".join(matched_notes) if matched_notes else "-")
        self.missing_value.setText(", ".join(missing_notes) if missing_notes else "-")
        self._apply_status(status, held=held, held_ms=held_ms)
        message = result.get("message") or result.get("stream_status") or ""
        self.message_label.setText(str(message))
        self.result_changed.emit(result)

    def _handle_error(self, message: str) -> None:
        self.message_label.setText(message)

    def _handle_finished(self) -> None:
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self._set_selection_enabled(True)
        self.worker = None

    def _apply_status(self, status: str, held: bool = False, held_ms: int = 0) -> None:
        status_text = STATUS_TEXT.get(status, status)
        if held and status == "correct":
            status_text = f"{status_text} (유지 {held_ms}ms)"
        self.status_value.setText(status_text)
        self.status_value.setStyleSheet(STATUS_STYLES.get(status, STATUS_STYLES["idle"]))

    def _refresh_expected_display(self) -> None:
        selected = self.selected_expected_frequencies()
        if not selected:
            self.expected_value.setText("-")
            return
        self.expected_value.setText(
            ", ".join(f"{note.name} {note.frequency:.2f}Hz" for note in selected)
        )

    def _set_selection_enabled(self, enabled: bool) -> None:
        for checkbox in self.note_checkboxes.values():
            checkbox.setEnabled(enabled)

    def _idle_result(self, status: str = "idle") -> dict:
        expected = self.selected_expected_frequencies()
        return {
            "status": status,
            "is_correct": False,
            "matches": [],
            "matched_notes": [],
            "missing_notes": [note.name for note in expected],
            "rms": 0.0,
            "peak": 0.0,
            "noise_floor_db": -120.0,
            "message": "",
            "stream_status": "",
            "held": False,
            "held_ms": 0,
            "backend": "librosa_cqt",
            "expected_frequencies": [
                {"name": note.name, "frequency": note.frequency}
                for note in expected
            ],
        }

    def closeEvent(self, event: QCloseEvent) -> None:
        self.stop_listening()
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    widget = ChordJudgeWidget()
    widget.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
    widget.resize(480, 320)
    widget.show()
    sys.exit(app.exec())

