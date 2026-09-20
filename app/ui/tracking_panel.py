from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QGridLayout, QGroupBox, QLabel, QPushButton, QSpinBox


class TrackingPanel(QGroupBox):
    def __init__(self) -> None:
        super().__init__("지판 맞춤")

        self.reference_fret_spin = QSpinBox()
        self.reference_fret_spin.setRange(1, 24)
        self.reference_fret_spin.setValue(12)
        self.reference_fret_spin.setSuffix(" 프렛")
        self.reference_fret_spin.setToolTip("클릭할 기준 프렛의 실제 번호입니다. 검출된 선 개수가 아닙니다.")

        self.select_lines_button = QPushButton("지판 맞추기")
        self.clear_tracking_button = QPushButton("맞춤 초기화")

        layout = QGridLayout(self)
        layout.addWidget(self.select_lines_button, 0, 0)
        layout.addWidget(self.clear_tracking_button, 0, 1)
        layout.addWidget(QLabel("기준 프렛의 실제 번호"), 1, 0)
        layout.addWidget(self.reference_fret_spin, 1, 1)
        hint = QLabel("처음에는 손을 잠깐 떼고 지판을 선명하게 보여주세요. 번호를 지정한 뒤 너트와 해당 프렛의 금속선을 선택하세요.")
        hint.setWordWrap(True)
        layout.addWidget(hint, 2, 0, 1, 2)
        self.manual_points_checkbox = QCheckBox("선이 인식되지 않으면 양 끝점 직접 지정")
        self.manual_points_checkbox.setToolTip("너트 양 끝점과 기준 프렛 양 끝점을 총 네 번 클릭합니다.")
        layout.addWidget(self.manual_points_checkbox, 3, 0, 1, 2)
