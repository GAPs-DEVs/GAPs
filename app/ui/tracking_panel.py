from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QGridLayout, QGroupBox, QLabel, QPushButton, QSpinBox, QToolButton, QWidget
from PySide6.QtGui import QValidator
from PySide6.QtCore import Qt


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
        self.reference_fret_spin.setMinimumWidth(76)
        self.reference_fret_spin.setToolTip("클릭할 기준 프렛의 실제 번호입니다. 검출된 선 개수가 아닙니다.")
        self.middle_fret_spin = OptionalFretSpinBox()
        self.middle_fret_spin.setMinimumWidth(76)
        self.middle_fret_spin.setToolTip("너트와 끝 기준 프렛 사이 중앙 부근의 실제 프렛 번호를 입력하세요.")

        self.select_lines_button = QPushButton("지판 맞추기")
        self.clear_tracking_button = QPushButton("맞춤 초기화")

        layout = QGridLayout(self)
        layout.setVerticalSpacing(10)
        layout.addWidget(self.select_lines_button, 0, 0)
        layout.addWidget(self.clear_tracking_button, 0, 1)

        # 평소에는 주 동작 두 개만 크게 표시하고, 번호·수동 지정은 펼쳐서 설정한다.
        # 위젯 자체를 재생성하지 않으므로 접었다 펴도 입력값과 신호 연결이 유지된다.
        self.settings_toggle = QToolButton()
        self.settings_toggle.setText("프렛 설정")
        self.settings_toggle.setCheckable(True)
        self.settings_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.settings_toggle.setArrowType(Qt.ArrowType.RightArrow)
        layout.addWidget(self.settings_toggle, 1, 0, 1, 2)
        self.settings_widget = QWidget()
        settings_layout = QGridLayout(self.settings_widget)
        settings_layout.setContentsMargins(0, 4, 0, 0)
        # 긴 설명은 아래 안내문에 두고, 입력 행은 고정 너비 패널 안에 들어오게 한다.
        # 실제 작업 순서대로 번호 입력을 먼저 배치한다.
        settings_layout.addWidget(QLabel("중간 기준 프렛 번호"), 0, 0)
        settings_layout.addWidget(self.middle_fret_spin, 0, 1)
        settings_layout.addWidget(QLabel("끝 기준 프렛 번호"), 1, 0)
        settings_layout.addWidget(self.reference_fret_spin, 1, 1)
        hint = QLabel("번호 입력 → 지판 맞추기 → 너트·중간·끝 순서로 선택하세요.")
        hint.setWordWrap(True)
        hint.setToolTip("처음에는 손을 떼고 전체 구간을 선명하게 보여주세요. 끝 기준은 화면에서 사용할 구간의 끝이며 기타의 마지막 프렛일 필요는 없습니다.")
        settings_layout.addWidget(hint, 2, 0, 1, 2)
        self.manual_points_checkbox = QCheckBox("선이 인식되지 않으면 양 끝점 직접 지정")
        self.manual_points_checkbox.setToolTip("너트·중간 프렛·끝 기준 프렛의 양 끝점을 총 여섯 번 클릭합니다.")
        settings_layout.addWidget(self.manual_points_checkbox, 3, 0, 1, 2)
        layout.addWidget(self.settings_widget, 2, 0, 1, 2)
        self.settings_toggle.toggled.connect(self.set_settings_visible)
        self.set_settings_visible(False)

    def set_settings_visible(self, visible: bool) -> None:
        self.settings_widget.setVisible(visible)
        self.settings_toggle.setChecked(visible)
        self.settings_toggle.setArrowType(Qt.ArrowType.DownArrow if visible else Qt.ArrowType.RightArrow)
