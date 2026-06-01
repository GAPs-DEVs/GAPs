from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QSpinBox

from app.config import DEFAULT_BPM


class PracticePanel(QGroupBox):
    def __init__(self) -> None:
        super().__init__("연습")

        self.microphone_combo = QComboBox()
        self.refresh_microphone_button = QPushButton("새로고침")

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("인터랙티브 연습", "interactive")
        self.mode_combo.addItem("자동 흐름", "auto")

        self.bpm_spin = QSpinBox()
        self.bpm_spin.setRange(30, 240)
        self.bpm_spin.setValue(DEFAULT_BPM)

        self.start_button = QPushButton("연습 시작")
        self.stop_button = QPushButton("연습 중지")
        self.stop_button.setEnabled(False)
        self.prev_button = QPushButton("이전")
        self.next_button = QPushButton("다음")
        self.reset_button = QPushButton("처음")

        self.progress_label = QLabel()
        self.expected_label = QLabel()
        self.expected_label.setWordWrap(True)
        self.status_label = QLabel()
        self.detected_label = QLabel("-")
        self.missing_label = QLabel("-")
        self._configure_dynamic_labels()
        self._build_layout()

    def _configure_dynamic_labels(self) -> None:
        self.progress_label.setStyleSheet("font-size: 18px; font-weight: 700;")
        self.expected_label.setStyleSheet("font-size: 16px;")
        self.status_label.setStyleSheet("font-size: 16px; font-weight: 700;")

    def _build_layout(self) -> None:
        layout = QGridLayout(self)
        layout.addWidget(QLabel("마이크"), 0, 0)
        layout.addWidget(self.microphone_combo, 0, 1, 1, 3)
        layout.addWidget(self.refresh_microphone_button, 0, 4)
        layout.addWidget(QLabel("모드"), 1, 0)
        layout.addWidget(self.mode_combo, 1, 1, 1, 2)
        layout.addWidget(QLabel("BPM"), 1, 3)
        layout.addWidget(self.bpm_spin, 1, 4)

        button_row = QHBoxLayout()
        button_row.addWidget(self.start_button)
        button_row.addWidget(self.stop_button)
        layout.addLayout(button_row, 2, 0, 1, 5)

        nav_row = QHBoxLayout()
        nav_row.addWidget(self.prev_button)
        nav_row.addWidget(self.next_button)
        nav_row.addWidget(self.reset_button)
        layout.addLayout(nav_row, 3, 0, 1, 5)

        layout.addWidget(QLabel("진행"), 4, 0)
        layout.addWidget(self.progress_label, 4, 1, 1, 4)
        layout.addWidget(QLabel("누를 위치"), 5, 0)
        layout.addWidget(self.expected_label, 5, 1, 1, 4)
        layout.addWidget(QLabel("상태"), 6, 0)
        layout.addWidget(self.status_label, 6, 1, 1, 4)
        layout.addWidget(QLabel("감지된 소리"), 7, 0)
        layout.addWidget(self.detected_label, 7, 1, 1, 4)
        layout.addWidget(QLabel("아직 부족한 음"), 8, 0)
        layout.addWidget(self.missing_label, 8, 1, 1, 4)

    def set_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
