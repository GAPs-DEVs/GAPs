from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QSizePolicy


class CameraPanel(QGroupBox):
    def __init__(self) -> None:
        super().__init__("카메라")

        self.camera_combo = QComboBox()
        self.camera_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.camera_combo.setMinimumContentsLength(6)
        self.camera_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.camera_combo.currentTextChanged.connect(self.camera_combo.setToolTip)
        self.refresh_button = QPushButton("새로고침")
        self.start_button = QPushButton("카메라 시작")
        self.stop_button = QPushButton("카메라 끄기")
        self.stop_button.setEnabled(False)
        self.status_label = QLabel("카메라 대기")
        self.marker_status_label = QLabel()
        self.status_label.setStyleSheet("font-weight: 700;")
        self.marker_status_label.setStyleSheet("color: #4b5563;")
        for label in (self.status_label, self.marker_status_label):
            label.setWordWrap(True)
            policy = label.sizePolicy()
            policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
            label.setSizePolicy(policy)

        layout = QGridLayout(self)
        layout.setColumnStretch(1, 1)
        layout.addWidget(QLabel("입력"), 0, 0)
        layout.addWidget(self.camera_combo, 0, 1)
        layout.addWidget(self.refresh_button, 0, 2)

        button_row = QHBoxLayout()
        button_row.addWidget(self.start_button)
        button_row.addWidget(self.stop_button)
        layout.addLayout(button_row, 2, 0, 1, 3)
        layout.addWidget(self.status_label, 3, 0, 1, 3)
        layout.addWidget(self.marker_status_label, 4, 0, 1, 3)

    def set_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
