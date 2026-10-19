from __future__ import annotations

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLayout,
    QProgressBar, QPushButton, QSizePolicy, QSlider, QToolButton, QWidget,
)

from practice.score_steps import ParsedPracticeNote, PracticeStep


class PracticeNoteCard(QFrame):
    def __init__(self, note: ParsedPracticeNote, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.note = note
        self.setObjectName("practiceNote")
        self.setFixedHeight(36)
        self.string_label = QLabel(f"{note.string_number}번 줄")
        self.string_label.setStyleSheet("font-size: 13px; font-weight: 500;")
        separator = QLabel("·")
        separator.setStyleSheet("font-size: 13px;")
        self.match_label = QLabel()
        self.match_label.setFixedWidth(10)
        self.match_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.match_label.setStyleSheet("font-size: 11px; font-weight: 600;")
        self.fret_label = QLabel("개방현" if note.fret_number == 0 else f"{note.fret_number}프렛")
        self.fret_label.setStyleSheet("font-size: 13px; font-weight: 600;")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(7, 0, 7, 0)
        layout.setSpacing(3)
        layout.addWidget(self.string_label)
        layout.addWidget(separator)
        layout.addWidget(self.fret_label)
        layout.addStretch()
        layout.addWidget(self.match_label)
        self._match_state = None
        self.set_match_state("neutral")

    def set_match_state(self, state: str) -> None:
        if state == self._match_state:
            return
        self._match_state = state
        matched = state == "matched"
        background, border, foreground = (
            ("#ecfdf3", "#86cfa3", "#166534") if matched
            else ("#ffffff", "#dfe3e8", "#374151")
        )
        self.match_label.setText("✓" if matched else "")
        self.setStyleSheet(f"""
            QFrame#practiceNote {{ background: {background}; border: 1px solid {border}; border-radius: 8px; }}
            QFrame#practiceNote QLabel {{ background: transparent; color: {foreground}; border: none; }}
        """)
        state_text = "확인" if matched else "대기"
        self.setAccessibleName(f"{self.string_label.text()} {self.fret_label.text()} {state_text}")


class PracticePanel(QGroupBox):
    def __init__(self) -> None:
        super().__init__("연습")

        self.microphone_combo = QComboBox()
        self.refresh_microphone_button = QPushButton("새로고침")

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("인터랙티브 연습", "interactive")
        self.mode_combo.addItem("자동 흐름", "auto")
        for combo in (self.microphone_combo, self.mode_combo):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(6)
            combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            combo.currentTextChanged.connect(combo.setToolTip)
            combo.setToolTip(combo.currentText())

        self.auto_speed_label = QLabel("배속")
        self.auto_speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.auto_speed_slider.setRange(5, 200)
        self.auto_speed_slider.setSingleStep(5)
        self.auto_speed_slider.setPageStep(10)
        self.auto_speed_slider.setTickInterval(50)
        self.auto_speed_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.auto_speed_slider.setValue(100)
        self.auto_speed_value_label = QLabel("1.00x")
        self.auto_speed_value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.start_button = QPushButton("연습 시작")
        self.stop_button = QPushButton("연습 중지")
        self.stop_button.setEnabled(False)
        self.prev_button = QPushButton("이전")
        self.next_button = QPushButton("다음")
        self.reset_button = QPushButton("처음")

        self.progress_label = QLabel()
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(5)
        self.progress_bar.setStyleSheet("QProgressBar { background: #e5e7eb; border: none; border-radius: 2px; } QProgressBar::chunk { background: #374151; border-radius: 2px; }")
        self.notes_widget = QWidget()
        self.notes_layout = QGridLayout(self.notes_widget)
        self.notes_layout.setContentsMargins(0, 4, 0, 0)
        self.notes_layout.setSpacing(6)
        # Let the container shrink before reflowing, instead of retaining the
        # minimum width of its previous three-column layout.
        self.notes_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.notes_widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.note_cards: list[PracticeNoteCard] = []
        self._note_columns = 0
        self.notes_widget.installEventFilter(self)
        self.status_label = QLabel()
        self.detected_label = QLabel("-")
        self.missing_label = QLabel("-")
        self.details_button = QToolButton()
        self.details_button.setText("입력 소리 확인")
        self.details_button.setCheckable(True)
        self.details_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.details_button.setIconSize(QSize(10, 10))
        self.details_button.setArrowType(Qt.ArrowType.RightArrow)
        self.details_button.setStyleSheet("QToolButton { background: transparent; border: none; color: #6b7280; padding: 5px 0; font-size: 12px; } QToolButton:hover, QToolButton:focus { color: #111827; }")
        self.details_widget = QWidget()
        self.details_button.toggled.connect(self.set_details_visible)
        self._configure_dynamic_labels()
        self._build_layout()
        self.set_details_visible(False)
        self.set_feedback("idle")

    def _configure_dynamic_labels(self) -> None:
        for label in (self.progress_label, self.status_label,
                      self.detected_label, self.missing_label):
            label.setWordWrap(True)
            policy = label.sizePolicy()
            policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
            label.setSizePolicy(policy)
        self.progress_label.setStyleSheet("font-size: 13px; font-weight: 600; color: #374151;")
        self.progress_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        for label in (self.detected_label, self.missing_label):
            label.setStyleSheet("font-size: 12px; color: #6b7280;")

    def _build_layout(self) -> None:
        layout = QGridLayout(self)
        layout.setColumnStretch(1, 1)
        layout.addWidget(QLabel("마이크"), 0, 0)
        layout.addWidget(self.microphone_combo, 0, 1, 1, 3)
        layout.addWidget(self.refresh_microphone_button, 0, 4)
        layout.addWidget(QLabel("모드"), 1, 0)
        layout.addWidget(self.mode_combo, 1, 1, 1, 4)
        layout.addWidget(self.auto_speed_label, 2, 0)
        layout.addWidget(self.auto_speed_slider, 2, 1, 1, 3)
        layout.addWidget(self.auto_speed_value_label, 2, 4)

        button_row = QHBoxLayout()
        button_row.addWidget(self.start_button)
        button_row.addWidget(self.stop_button)
        layout.addLayout(button_row, 3, 0, 1, 5)

        nav_row = QHBoxLayout()
        nav_row.addWidget(self.prev_button)
        nav_row.addWidget(self.next_button)
        nav_row.addWidget(self.reset_button)
        layout.addLayout(nav_row, 4, 0, 1, 5)

        progress_row = QHBoxLayout()
        progress_title = QLabel("진행")
        progress_title.setStyleSheet("font-size: 12px; color: #6b7280;")
        progress_row.addWidget(progress_title)
        progress_row.addWidget(self.progress_label, 1)
        layout.addLayout(progress_row, 5, 0, 1, 5)
        layout.addWidget(self.progress_bar, 6, 0, 1, 5)
        layout.addWidget(self.notes_widget, 7, 0, 1, 5)
        layout.addWidget(self.status_label, 8, 0, 1, 5)
        layout.addWidget(self.details_button, 9, 0, 1, 5, Qt.AlignmentFlag.AlignLeft)
        details_layout = QGridLayout(self.details_widget)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setColumnStretch(1, 1)
        for row, title, label in ((0, "감지", self.detected_label), (1, "미감지", self.missing_label)):
            title_label = QLabel(title)
            title_label.setStyleSheet("font-size: 12px; color: #6b7280;")
            details_layout.addWidget(title_label, row, 0)
            details_layout.addWidget(label, row, 1)
        layout.addWidget(self.details_widget, 10, 0, 1, 5)
        self.set_auto_speed_controls_visible(False)

    def set_details_visible(self, visible: bool) -> None:
        self.details_widget.setVisible(visible)
        self.details_button.setArrowType(Qt.ArrowType.DownArrow if visible else Qt.ArrowType.RightArrow)

    def show_step(self, step: PracticeStep, index: int, total: int) -> None:
        self.progress_label.setText(f"{index + 1} / {total}")
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(index + 1)
        self.progress_bar.setAccessibleName(f"연습 진행 {index + 1} / {total}")
        while self.notes_layout.count():
            item = self.notes_layout.takeAt(0)
            item.widget().hide()
            item.widget().deleteLater()
        self.note_cards = [PracticeNoteCard(note, self.notes_widget) for note in step.notes]
        self._note_columns = 0
        self.layout_note_cards()
        self.reset_feedback("idle")

    def eventFilter(self, watched, event):
        if watched is self.notes_widget and event.type() == QEvent.Type.Resize:
            self.layout_note_cards()
        return super().eventFilter(watched, event)

    def layout_note_cards(self) -> None:
        if not self.note_cards:
            return
        for card in self.note_cards:
            card.ensurePolished()
        chip_width = max(card.sizeHint().width() for card in self.note_cards)
        gap = self.notes_layout.horizontalSpacing()
        columns = max(1, min(3, (self.notes_widget.width() + gap) // (chip_width + gap)))
        if columns == self._note_columns:
            return
        self._note_columns = columns
        while self.notes_layout.count():
            self.notes_layout.takeAt(0)
        for column in range(3):
            self.notes_layout.setColumnStretch(column, 1 if column < columns else 0)
        for i, card in enumerate(self.note_cards):
            self.notes_layout.addWidget(card, i // columns, i % columns)
            card.show()
        rows = (len(self.note_cards) + columns - 1) // columns
        self.notes_widget.setFixedHeight(4 + rows * 36 + (rows - 1) * self.notes_layout.verticalSpacing())

    def reset_feedback(self, status: str) -> None:
        for card in self.note_cards:
            card.set_match_state("neutral")
        self.detected_label.setText("-")
        self.missing_label.setText("-")
        self.set_feedback(status)

    def set_feedback(self, status: str, message: str | None = None) -> None:
        texts = {
            "idle": "대기", "listening": "듣는 중", "no_signal": "소리 대기 중",
            "correct": "정확해요!", "incorrect": "다시 확인해주세요", "partial": "일부 음 확인",
            "error": "마이크 오류", "complete": "연습 완료", "auto": "자동 진행 중",
            "retrigger": "소리를 끊고 다시 연주해주세요",
        }
        if status in {"correct", "complete"}:
            background, foreground = "#ecfdf3", "#166534"
        elif status in {"incorrect", "partial"}:
            background, foreground = "#fff7ed", "#9a3412"
        elif status == "error":
            background, foreground = "#fef2f2", "#b42318"
        else:
            background, foreground = "#eef2f6", "#4b5563"
        self.status_label.setText(message or texts.get(status, "듣는 중"))
        self.status_label.setStyleSheet(f"background: {background}; color: {foreground}; border-radius: 6px; padding: 10px; font-size: 13px; font-weight: 600;")

    def show_audio_result(self, result: dict) -> None:
        status = str(result.get("status", "idle"))
        correct = bool(result.get("is_correct"))
        retrigger = result.get("retrigger_required") and not result.get("retrigger_ready")
        matched = set(result.get("matched_notes") or [])
        if result.get("practice_mode") == "single":
            matched = {card.note.label for card in self.note_cards} if correct else set()
            frequency, cents = result.get("detected_frequency"), result.get("cents_difference")
            detail = "-" if frequency is None else f"{float(frequency):.2f} Hz"
            if frequency is not None and cents is not None:
                detail += f" / {float(cents):+.1f} cents"
            self.detected_label.setText(detail)
            self.missing_label.setText("-" if correct else str(result.get("expected_label", "-")))
        else:
            self.detected_label.setText(", ".join(result.get("matched_notes") or []) or "-")
            self.missing_label.setText(", ".join(result.get("missing_notes") or []) or "-")
        if retrigger or status in {"idle", "listening", "no_signal", "error"}:
            matched = set()
        elif correct:
            matched = {card.note.label for card in self.note_cards}
        count = sum(card.note.label in matched for card in self.note_cards)
        for card in self.note_cards:
            state = "matched" if card.note.label in matched else "waiting" if count else "neutral"
            card.set_match_state(state)
        if status == "error":
            self.set_feedback("error", f"마이크 오류 · {result['message']}" if result.get("message") else None)
        elif retrigger:
            self.set_feedback("retrigger")
        elif correct:
            self.set_feedback("correct")
        elif count:
            self.set_feedback("partial", f"{count} / {len(self.note_cards)}음 확인")
        else:
            self.set_feedback(status)

    def set_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)

    def set_auto_speed_controls_visible(self, visible: bool) -> None:
        self.auto_speed_label.setVisible(visible)
        self.auto_speed_slider.setVisible(visible)
        self.auto_speed_value_label.setVisible(visible)
        self.auto_speed_slider.setEnabled(visible)
