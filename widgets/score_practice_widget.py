from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
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
from audio.chord_audio_worker import ChordAudioWorker
from practice.score_steps import (
    PracticeStep,
    build_practice_steps,
    load_parsed_notes_from_json,
    practice_steps_have_same_expected_frequencies,
)


STATUS_TEXT = {
    "idle": "대기",
    "listening": "듣는 중",
    "correct": "정답",
    "incorrect": "오답",
    "no_signal": "신호 없음",
    "error": "오류",
    "complete": "완료",
}

STATUS_STYLES = {
    "idle": "color: #6b7280;",
    "listening": "color: #2563eb;",
    "correct": "color: #15803d; font-weight: 700;",
    "incorrect": "color: #b91c1c; font-weight: 700;",
    "no_signal": "color: #a16207;",
    "error": "color: #b91c1c; font-weight: 700;",
    "complete": "color: #15803d; font-weight: 700;",
}


class ScorePracticeWidget(QWidget):
    result_changed = Signal(dict)
    step_changed = Signal(dict)
    practice_completed = Signal()

    def __init__(
        self,
        parsed_notes: list[dict],
        single_tolerance_cents: float = 30.0,
        chord_tolerance_cents: float = 35.0,
        auto_advance: bool = True,
        auto_advance_delay_ms: int = 800,
        require_release_for_repeated_steps: bool = True,
        sample_rate: int = 44100,
        device: int | str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if auto_advance_delay_ms < 0:
            raise ValueError("auto_advance_delay_ms must be greater than or equal to 0.")
        self.single_tolerance_cents = single_tolerance_cents
        self.chord_tolerance_cents = chord_tolerance_cents
        self.auto_advance = auto_advance
        self.auto_advance_delay_ms = auto_advance_delay_ms
        self.require_release_for_repeated_steps = require_release_for_repeated_steps
        self.sample_rate = sample_rate
        self.device = device

        self.practice_steps: list[PracticeStep] = build_practice_steps(parsed_notes)
        if not self.practice_steps:
            raise ValueError("parsed_notes must contain at least one note.")

        self.current_index = 0
        self.worker: AudioWorker | ChordAudioWorker | None = None
        self.is_running = False
        self._advanced_step_keys: set[int] = set()
        self._release_required_for_current_step = False
        self._release_observed_for_current_step = True
        self._pending_auto_advance_index: int | None = None

        self._auto_advance_timer = QTimer(self)
        self._auto_advance_timer.setSingleShot(True)
        self._auto_advance_timer.timeout.connect(self._handle_auto_advance_timeout)

        self.progress_value = QLabel("")
        self.mode_value = QLabel("")
        self.expected_value = QLabel("")
        self.detected_value = QLabel("-")
        self.missing_value = QLabel("-")
        self.status_value = QLabel(STATUS_TEXT["idle"])
        self.message_label = QLabel("")
        self.message_label.setWordWrap(True)
        self.message_label.setStyleSheet("color: #b91c1c;")

        self.load_json_button = QPushButton("JSON 열기")
        self.start_button = QPushButton("연습 시작")
        self.stop_button = QPushButton("중지")
        self.prev_button = QPushButton("이전")
        self.next_button = QPushButton("다음")
        self.reset_button = QPushButton("처음")
        self.stop_button.setEnabled(False)

        self.load_json_button.clicked.connect(self.choose_score_json_file)
        self.start_button.clicked.connect(self.start_practice)
        self.stop_button.clicked.connect(self.stop_practice)
        self.prev_button.clicked.connect(self.previous_step)
        self.next_button.clicked.connect(self.next_step)
        self.reset_button.clicked.connect(self.reset_practice)

        self._build_ui()
        self._refresh_step_display()
        self._apply_status("idle")

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(10)

        title = QLabel("test driver")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        root_layout.addWidget(title)

        values_frame = QFrame()
        values_frame.setFrameShape(QFrame.Shape.StyledPanel)
        values_layout = QGridLayout(values_frame)
        values_layout.setColumnStretch(1, 1)
        values_layout.addWidget(QLabel("진행"), 0, 0)
        values_layout.addWidget(self.progress_value, 0, 1)
        values_layout.addWidget(QLabel("판정 방식"), 1, 0)
        values_layout.addWidget(self.mode_value, 1, 1)
        values_layout.addWidget(QLabel("기대 음"), 2, 0)
        values_layout.addWidget(self.expected_value, 2, 1)
        values_layout.addWidget(QLabel("감지"), 3, 0)
        values_layout.addWidget(self.detected_value, 3, 1)
        values_layout.addWidget(QLabel("빠진 음"), 4, 0)
        values_layout.addWidget(self.missing_value, 4, 1)
        values_layout.addWidget(QLabel("상태"), 5, 0)
        values_layout.addWidget(self.status_value, 5, 1)
        root_layout.addWidget(values_frame)

        button_row = QHBoxLayout()
        button_row.addWidget(self.load_json_button)
        button_row.addWidget(self.start_button)
        button_row.addWidget(self.stop_button)
        button_row.addWidget(self.prev_button)
        button_row.addWidget(self.next_button)
        button_row.addWidget(self.reset_button)
        root_layout.addLayout(button_row)
        root_layout.addWidget(self.message_label)

    def set_parsed_notes(self, parsed_notes: list[dict]) -> None:
        practice_steps = build_practice_steps(parsed_notes)
        if not practice_steps:
            raise ValueError("parsed_notes must contain at least one note.")

        was_running = self.is_running
        self._cancel_pending_auto_advance()
        self.stop_practice()
        self.practice_steps = practice_steps
        self.current_index = 0
        self._advanced_step_keys.clear()
        self._configure_retrigger_gate(None, self.current_step())
        self._refresh_step_display()
        self._apply_status("idle")
        if was_running:
            self.start_practice()

    @classmethod
    def from_json_file(cls, json_path: str | Path, **kwargs: object) -> "ScorePracticeWidget":
        return cls(load_parsed_notes_from_json(json_path), **kwargs)

    def choose_score_json_file(self) -> None:
        file_name, _ = QFileDialog.getOpenFileName(
            self,
            "Open Parsed Score JSON",
            str(Path.home()),
            "JSON files (*.json);;All files (*)",
        )
        if not file_name:
            return

        try:
            self.set_parsed_notes(load_parsed_notes_from_json(file_name))
        except Exception as exc:
            self.message_label.setText(f"JSON 파일을 불러오지 못했습니다: {exc}")
            self._apply_status("error")
            return

        self.message_label.clear()

    def start_practice(self) -> None:
        if self.is_running:
            return
        self._cancel_pending_auto_advance()
        self.is_running = True
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self._advanced_step_keys.clear()
        self._configure_retrigger_gate(None, self.current_step())
        self._start_worker_for_current_step()

    def stop_practice(self) -> None:
        self.is_running = False
        self._cancel_pending_auto_advance()
        self._stop_worker()
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self._apply_status("idle")

    def reset_practice(self) -> None:
        was_running = self.is_running
        self._cancel_pending_auto_advance()
        self._stop_worker()
        self.current_index = 0
        self._advanced_step_keys.clear()
        self._configure_retrigger_gate(None, self.current_step())
        self._refresh_step_display()
        self._apply_status("idle")
        if was_running:
            self.is_running = True
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(True)
            self._start_worker_for_current_step()

    def previous_step(self) -> None:
        if self.current_index <= 0:
            return
        self._move_to_step(self.current_index - 1)

    def next_step(self) -> None:
        if self.current_index >= len(self.practice_steps) - 1:
            self._complete_practice()
            return
        self._move_to_step(self.current_index + 1)

    def current_step(self) -> PracticeStep:
        return self.practice_steps[self.current_index]

    def _move_to_step(self, index: int) -> None:
        was_running = self.is_running
        previous_step = self.current_step()
        self._cancel_pending_auto_advance()
        self._stop_worker()
        self.current_index = max(0, min(index, len(self.practice_steps) - 1))
        self._configure_retrigger_gate(previous_step, self.current_step())
        self._refresh_step_display()
        self._apply_status("listening" if was_running else "idle")
        if was_running:
            self.is_running = True
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(True)
            self._start_worker_for_current_step()

    def _start_worker_for_current_step(self) -> None:
        self._stop_worker()
        step = self.current_step()
        self._refresh_step_display()
        self._apply_status("listening")
        self.detected_value.setText("-")
        self.missing_value.setText("-")
        self.message_label.clear()

        if step.mode == "single":
            expected = step.expected_frequencies[0]
            self.worker = AudioWorker(
                expected_frequency=expected.frequency,
                tolerance_cents=self.single_tolerance_cents,
                sample_rate=self.sample_rate,
                device=self.device,
                parent=self,
            )
        else:
            self.worker = ChordAudioWorker(
                expected_frequencies=list(step.expected_frequencies),
                sample_rate=self.sample_rate,
                tolerance_cents=self.chord_tolerance_cents,
                device=self.device,
                parent=self,
            )

        self.worker.result_ready.connect(self._handle_result)
        self.worker.error_message.connect(self._handle_error)
        self.worker.finished.connect(self._handle_worker_finished)
        self.worker.start()

    def _stop_worker(self) -> None:
        if self.worker is None:
            return
        worker = self.worker
        self.worker = None
        try:
            worker.result_ready.disconnect(self._handle_result)
            worker.error_message.disconnect(self._handle_error)
            worker.finished.disconnect(self._handle_worker_finished)
        except RuntimeError:
            pass
        worker.stop()
        worker.deleteLater()

    def _handle_result(self, result: dict) -> None:
        if self._pending_auto_advance_index is not None:
            return

        step = self.current_step()
        enriched = self._with_step_context(result, step)
        enriched = self._apply_retrigger_gate(enriched)
        self._render_result(enriched)
        self.result_changed.emit(enriched)

        if (
            self.auto_advance
            and enriched.get("is_correct")
            and not enriched.get("held")
            and step.step not in self._advanced_step_keys
        ):
            self._advanced_step_keys.add(step.step)
            self._schedule_auto_advance_after_correct()

    def _schedule_auto_advance_after_correct(self) -> None:
        self._pending_auto_advance_index = self.current_index
        if self.auto_advance_delay_ms == 0:
            self._handle_auto_advance_timeout()
            return
        self._auto_advance_timer.start(self.auto_advance_delay_ms)

    def _handle_auto_advance_timeout(self) -> None:
        pending_index = self._pending_auto_advance_index
        self._pending_auto_advance_index = None
        if pending_index is None:
            return
        if not self.is_running or pending_index != self.current_index:
            return
        self._advance_after_correct()

    def _cancel_pending_auto_advance(self) -> None:
        if self._auto_advance_timer.isActive():
            self._auto_advance_timer.stop()
        self._pending_auto_advance_index = None

    def _configure_retrigger_gate(
        self,
        previous_step: PracticeStep | None,
        current_step: PracticeStep,
    ) -> None:
        self._release_required_for_current_step = (
            self.require_release_for_repeated_steps
            and previous_step is not None
            and practice_steps_have_same_expected_frequencies(previous_step, current_step)
        )
        self._release_observed_for_current_step = not self._release_required_for_current_step

    def _apply_retrigger_gate(self, result: dict) -> dict:
        if not self._release_required_for_current_step:
            result["retrigger_required"] = False
            result["retrigger_ready"] = True
            return result

        status = str(result.get("status", "idle"))
        if status == "no_signal":
            self._release_observed_for_current_step = True

        gated = dict(result)
        gated["retrigger_required"] = True
        gated["retrigger_ready"] = self._release_observed_for_current_step

        if self._release_observed_for_current_step:
            return gated

        if gated.get("is_correct"):
            gated["is_correct"] = False
            gated["status"] = "listening"
            gated["message"] = "같은 음 반복 step입니다. 이전 소리가 끊긴 뒤 다시 연주하면 정답 처리됩니다."
        return gated

    def _render_result(self, result: dict) -> None:
        status = str(result.get("status", "idle"))
        held = bool(result.get("held", False))
        held_ms = int(result.get("held_ms", 0) or 0)

        if result.get("practice_mode") == "single":
            detected_frequency = result.get("detected_frequency")
            cents = result.get("cents_difference")
            if detected_frequency is None:
                self.detected_value.setText("-")
            elif cents is None:
                self.detected_value.setText(f"{float(detected_frequency):.2f} Hz")
            else:
                self.detected_value.setText(f"{float(detected_frequency):.2f} Hz / {float(cents):+.1f} cents")
            self.missing_value.setText("-" if result.get("is_correct") else result.get("expected_label", "-"))
        else:
            matched_notes = result.get("matched_notes") or []
            missing_notes = result.get("missing_notes") or []
            self.detected_value.setText(", ".join(matched_notes) if matched_notes else "-")
            self.missing_value.setText(", ".join(missing_notes) if missing_notes else "-")

        self._apply_status(status, held=held, held_ms=held_ms)
        message = result.get("message") or result.get("stream_status") or ""
        self.message_label.setText(str(message))

    def _with_step_context(self, result: dict, step: PracticeStep) -> dict:
        enriched = dict(result)
        enriched["practice_step"] = step.step
        enriched["practice_index"] = self.current_index
        enriched["practice_total"] = len(self.practice_steps)
        enriched["practice_mode"] = step.mode
        enriched["expected_notes"] = [
            {
                "label": note.label,
                "string": note.string_number,
                "fret": note.fret_number,
                "frequency": note.frequency,
            }
            for note in step.notes
        ]
        if step.mode == "single":
            note = step.notes[0]
            enriched["expected_label"] = note.label
            enriched["expected_frequency"] = note.frequency
        return enriched

    def _advance_after_correct(self) -> None:
        if self.current_index >= len(self.practice_steps) - 1:
            self._complete_practice()
            return
        self._move_to_step(self.current_index + 1)

    def _complete_practice(self) -> None:
        self.is_running = False
        self._stop_worker()
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self._apply_status("complete")
        self.practice_completed.emit()

    def _handle_error(self, message: str) -> None:
        self.message_label.setText(message)
        self._apply_status("error")

    def _handle_worker_finished(self) -> None:
        if self.worker is not None and not self.worker.isRunning():
            self.worker = None

    def _refresh_step_display(self) -> None:
        step = self.current_step()
        mode_text = "단일음" if step.mode == "single" else "다중음"
        self.progress_value.setText(f"{self.current_index + 1} / {len(self.practice_steps)} (step {step.step})")
        self.mode_value.setText(mode_text)
        self.expected_value.setText(
            ", ".join(
                f"{note.label} {note.frequency:.2f}Hz"
                for note in step.notes
            )
        )
        self.step_changed.emit(
            {
                "practice_step": step.step,
                "practice_index": self.current_index,
                "practice_total": len(self.practice_steps),
                "practice_mode": step.mode,
                "expected_notes": [
                    {
                        "label": note.label,
                        "string": note.string_number,
                        "fret": note.fret_number,
                        "frequency": note.frequency,
                    }
                    for note in step.notes
                ],
            }
        )

    def _apply_status(self, status: str, held: bool = False, held_ms: int = 0) -> None:
        status_text = STATUS_TEXT.get(status, status)
        if held and status in {"correct", "incorrect"}:
            status_text = f"{status_text} (유지 {held_ms}ms)"
        self.status_value.setText(status_text)
        self.status_value.setStyleSheet(STATUS_STYLES.get(status, STATUS_STYLES["idle"]))

    def closeEvent(self, event: QCloseEvent) -> None:
        self.stop_practice()
        super().closeEvent(event)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the parsed-score pitch practice widget.")
    parser.add_argument("--score-json", type=Path, help="Path to a parsed score JSON file.")
    parser.add_argument(
        "--auto-advance-delay-ms",
        type=int,
        default=800,
        help="Delay before moving to the next step after a correct judgement.",
    )
    args, qt_args = parser.parse_known_args()

    sample_parsed_notes = [
        {"step": 1, "string": 1, "fret": 7},
        {"step": 1, "string": 6, "fret": 0},
        {"step": 2, "string": 2, "fret": 0},
        {"step": 3, "string": 3, "fret": 0},
        {"step": 4, "string": 1, "fret": 5},
        {"step": 5, "string": 2, "fret": 0},
        {"step": 6, "string": 3, "fret": 0},
    ]
    app = QApplication([sys.argv[0], *qt_args])
    if args.score_json:
        widget = ScorePracticeWidget.from_json_file(
            args.score_json,
            auto_advance_delay_ms=args.auto_advance_delay_ms,
        )
    else:
        widget = ScorePracticeWidget(
            sample_parsed_notes,
            auto_advance_delay_ms=args.auto_advance_delay_ms,
        )
    widget.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
    widget.resize(620, 320)
    widget.show()
    sys.exit(app.exec())
