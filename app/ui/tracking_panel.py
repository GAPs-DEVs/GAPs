from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QGroupBox, QPushButton, QSpinBox


class TrackingPanel(QGroupBox):
    def __init__(self) -> None:
        super().__init__("지판 맞춤")

        self.reference_fret_spin = QSpinBox()
        self.reference_fret_spin.setRange(1, 24)
        self.reference_fret_spin.setValue(12)
        self.reference_fret_spin.hide()

        self.select_lines_button = QPushButton("지판 맞추기")
        self.clear_tracking_button = QPushButton("맞춤 초기화")

        layout = QGridLayout(self)
        layout.addWidget(self.select_lines_button, 0, 0)
        layout.addWidget(self.clear_tracking_button, 0, 1)
