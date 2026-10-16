from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton


class CameraPanel(QGroupBox):
    def __init__(self) -> None:
        super().__init__("카메라")

        self.camera_combo = QComboBox()
        # 긴 장치명이 패널 전체의 최소 너비를 늘리지 않도록 한다.
        self.camera_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.camera_combo.setMinimumContentsLength(8)
        self.refresh_button = QPushButton("새로고침")
        self.start_button = QPushButton("카메라 시작")
        self.stop_button = QPushButton("카메라 끄기")
        self.stop_button.setEnabled(False)
        self.reverse_checkbox = QCheckBox("줄 방향 반전")
        self.status_label = QLabel("카메라 대기")
        self.marker_status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.marker_status_label.setWordWrap(True)
        self.status_label.setStyleSheet("font-weight: 700;")
        self.marker_status_label.setStyleSheet("color: #4b5563;")

        layout = QGridLayout(self)
        layout.setVerticalSpacing(10)
        layout.setColumnStretch(1, 1)
        layout.addWidget(QLabel("입력"), 0, 0)
        layout.addWidget(self.camera_combo, 0, 1)
        layout.addWidget(self.refresh_button, 0, 2)
        layout.addWidget(self.reverse_checkbox, 1, 1, 1, 2)

        button_row = QHBoxLayout()
        button_row.addWidget(self.start_button)
        button_row.addWidget(self.stop_button)
        layout.addLayout(button_row, 2, 0, 1, 3)
        layout.addWidget(self.status_label, 3, 0, 1, 3)
        layout.addWidget(self.marker_status_label, 4, 0, 1, 3)

    def set_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
