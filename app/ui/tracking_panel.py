from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QGridLayout, QGroupBox, QLabel, QPushButton, QSpinBox
from PySide6.QtGui import QValidator


class OptionalFretSpinBox(QSpinBox):
    """Zero is an internal unset value, displayed as a genuinely empty field."""
    def __init__(self) -> None:
        super().__init__()
        self.setRange(0, 24)
        self.setKeyboardTracking(False)

    def textFromValue(self, value: int) -> str:
        return str(value) if value else ""

    def valueFromText(self, text: str) -> int:
        return int(text) if text.strip() else 0

    def validate(self, text: str, pos: int):
        if not text.strip():
            return QValidator.State.Acceptable, text, pos
        return super().validate(text, pos)

    def clear(self) -> None:
        self.setValue(0)
        super().clear()


class TrackingPanel(QGroupBox):
    def __init__(self) -> None:
        super().__init__("지판 맞춤")

        self.reference_fret_spin = OptionalFretSpinBox()
        self.reference_fret_spin.setToolTip("클릭할 기준 프렛의 실제 번호입니다. 검출된 선 개수가 아닙니다.")
        self.middle_fret_spin = OptionalFretSpinBox()
        self.middle_fret_spin.setToolTip("너트와 끝 기준 프렛 사이 중앙 부근의 실제 프렛 번호를 입력하세요.")

        self.select_lines_button = QPushButton("지판 맞추기")
        self.clear_tracking_button = QPushButton("맞춤 초기화")

        layout = QGridLayout(self)
        layout.addWidget(self.select_lines_button, 0, 0)
        layout.addWidget(self.clear_tracking_button, 0, 1)
        layout.addWidget(QLabel("끝 기준 프렛 번호 (기타의 전체 프렛 수 아님)"), 1, 0)
        layout.addWidget(self.reference_fret_spin, 1, 1)
        layout.addWidget(QLabel("중간 기준 프렛 번호"), 2, 0)
        layout.addWidget(self.middle_fret_spin, 2, 1)
        hint = QLabel("두 번호를 입력하고 너트 → 중간 프렛 → 끝 기준 프렛을 선택하세요. 처음에는 손을 떼고 전체 구간을 선명하게 보여주세요. 끝 기준은 화면에서 사용할 구간의 끝이며 기타의 마지막 프렛일 필요는 없습니다.")
        hint.setWordWrap(True)
        layout.addWidget(hint, 3, 0, 1, 2)
        self.manual_points_checkbox = QCheckBox("선이 인식되지 않으면 양 끝점 직접 지정")
        self.manual_points_checkbox.setToolTip("너트·중간 프렛·끝 기준 프렛의 양 끝점을 총 여섯 번 클릭합니다.")
        layout.addWidget(self.manual_points_checkbox, 4, 0, 1, 2)
