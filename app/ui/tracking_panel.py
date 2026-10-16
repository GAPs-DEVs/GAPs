from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRect, Qt
from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QCheckBox, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QPushButton, QScrollArea, QSpinBox, QStyle, QStyleOptionGroupBox, QToolButton, QToolTip, QVBoxLayout, QWidget,
)
from PySide6.QtGui import QValidator


class OptionalFretSpinBox(QSpinBox):
    """Zero is an internal unset value, displayed as a genuinely empty field."""
    def __init__(self) -> None:
        super().__init__()
        self.setRange(0, 24)
        self.setKeyboardTracking(False)
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.setMinimumWidth(148)
        self.setStyleSheet("""
            QSpinBox {
                background: white; color: #212529; border: 1px solid #ced4da;
                border-radius: 6px; padding: 5px 36px 5px 10px; min-height: 24px;
            }
            QSpinBox:focus { border-color: #2563eb; }
            QSpinBox:disabled { background: #f1f3f5; color: #6b7280; }
            QToolButton {
                background: #f8f9fa; color: #4b5563; border: 1px solid #e5e7eb;
                border-radius: 3px; padding: 0; font-size: 12px; font-weight: 500;
            }
            QToolButton:hover { background: #e9ecef; border-color: #adb5bd; color: #111827; }
            QToolButton:pressed { background: #dee2e6; }
            QToolButton:disabled { background: #f8f9fa; color: #adb5bd; border-color: #e9ecef; }
        """)
        self.decrease_button = QToolButton(self)
        self.increase_button = QToolButton(self)
        for button, text, description, step in (
            (self.decrease_button, "−", "프렛 번호 줄이기", -1),
            (self.increase_button, "+", "프렛 번호 늘리기", 1),
        ):
            button.setText(text)
            button.setAccessibleName(description)
            button.setToolTip(description)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setFixedSize(22, 14)
            button.setAutoRepeat(True)
            button.clicked.connect(lambda _checked=False, step=step: self.change_number(step))
        self.lineEdit().textChanged.connect(self.refresh_step_buttons)
        self.valueChanged.connect(self.refresh_step_buttons)
        self.refresh_step_buttons()
        QApplication.instance().installEventFilter(self)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        top = (self.height() - 30) // 2
        self.increase_button.move(self.width() - 28, top)
        self.decrease_button.move(self.width() - 28, top + 16)

    def change_number(self, step: int) -> None:
        self.interpretText()
        self.stepBy(step)

    def refresh_step_buttons(self, *_args) -> None:
        text = self.lineEdit().text().strip()
        value = int(text) if text.isdecimal() else 0
        self.decrease_button.setEnabled(value > self.minimum())
        self.increase_button.setEnabled(value < self.maximum())

    def eventFilter(self, watched, event):
        # Labels and macOS buttons need not take focus when clicked. Commit even
        # in that case, without committing a partially typed two-digit number.
        if (event.type() == QEvent.Type.MouseButtonPress and self.hasFocus()
                and isinstance(watched, QWidget)
                and watched is not self and not self.isAncestorOf(watched)):
            self.interpretText()
            self.clearFocus()
        return super().eventFilter(watched, event)

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


class HelpButton(QToolButton):
    """Hover/focus for a quick hint; click for a persistent, dismissible popup."""

    def __init__(self, title: str, description: str) -> None:
        super().__init__()
        self.setText("?")
        self.setFixedSize(24, 24)
        self.setStyleSheet("""
            QToolButton {
                background: #ffffff; color: #6b7280; border: 1px solid #adb5bd;
                border-radius: 12px; padding: 0; font-size: 14px; font-weight: 600;
            }
            QToolButton:hover, QToolButton:focus {
                background: #f1f3f5; color: #111827; border-color: #6b7280;
            }
            QToolButton:pressed { background: #e9ecef; }
            QToolTip { background: white; color: #374151; border: 1px solid #ced4da; padding: 10px; }
        """)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(f"{title} 도움말")
        self.setToolTip(description)
        self.popup = QFrame(self, Qt.WindowType.Popup)
        self.popup.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.popup.setStyleSheet("QFrame { background: white; color: #374151; border: 1px solid #ced4da; border-radius: 6px; }")
        layout = QVBoxLayout(self.popup)
        self.help_label = QLabel(description)
        self.help_label.setWordWrap(True)
        self.help_label.setStyleSheet("border: none; padding: 4px; background: white; color: #374151; font-size: 13px;")
        self.help_scroll = QScrollArea()
        self.help_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.help_scroll.setStyleSheet("QScrollArea { border: none; background: white; }")
        self.help_scroll.setWidgetResizable(True)
        self.help_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.help_scroll.setWidget(self.help_label)
        layout.addWidget(self.help_scroll)
        self.popup.installEventFilter(self)
        self.clicked.connect(self.show_help)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        QToolTip.showText(self.mapToGlobal(QPoint(0, self.height())), self.toolTip(), self)

    def focusOutEvent(self, event) -> None:
        QToolTip.hideText()
        super().focusOutEvent(event)

    def show_help(self) -> None:
        QToolTip.hideText()
        self.window().installEventFilter(self)
        self.position_help()
        self.popup.show()
        self.popup.setFocus()

    def position_help(self) -> None:
        owner = self.window()
        bounds = QRect(owner.mapToGlobal(QPoint(0, 0)), owner.size())
        bounds = bounds.intersected(self.screen().availableGeometry()).adjusted(8, 8, -8, -8)
        width = min(334, bounds.width())
        margins = self.popup.layout().contentsMargins()
        content_width = max(1, width - margins.left() - margins.right() - 2)
        content_height = self.help_label.heightForWidth(content_width)
        height = min(content_height + margins.top() + margins.bottom() + 4, bounds.height())
        self.popup.resize(width, height)
        # Right-align beneath the question mark, then keep every edge inside
        # the app's client area. Short windows scroll instead of overflowing.
        position = self.mapToGlobal(QPoint(self.width() - width, self.height() + 6))
        position.setX(max(bounds.left(), min(position.x(), bounds.right() - width + 1)))
        position.setY(max(bounds.top(), min(position.y(), bounds.bottom() - height + 1)))
        self.popup.move(position)

    def eventFilter(self, watched, event):
        if watched is self.window() and self.popup.isVisible() and event.type() in {QEvent.Type.Resize, QEvent.Type.Move}:
            self.position_help()
        if watched is self.popup and event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self.popup.hide()
            self.setFocus()
            QToolTip.hideText()
            return True
        return super().eventFilter(watched, event)


class TrackingPanel(QGroupBox):
    READY_HINT = "손을 잠시 떼고 사용할 지판 구간을 선명하게 보여주세요."
    HELP_TEXT = (
        "<b>지판 맞춤 방법</b>"
        "<p><b>1. 프렛 번호 입력</b><br>"
        "중간: 너트와 끝 프렛 사이 중앙 부근의 실제 프렛 번호입니다.<br>"
        "끝: 화면에서 사용할 구간의 마지막 프렛 번호입니다. 기타의 전체 프렛 수와는 달라도 됩니다.<br>"
        "1~24 사이에서 중간 번호가 끝 번호보다 작도록 입력하세요. Enter 없이 입력칸 밖을 클릭하거나 ‘지판 맞추기’를 누르면 반영됩니다.</p>"
        "<p><b>2. 지판 맞추기</b><br>"
        "카메라를 켜고 손을 잠시 떼어 사용할 지판 구간을 선명하게 보여주세요. ‘지판 맞추기’를 누른 뒤 너트 → 중간 프렛 → 끝 프렛 순서로 선을 선택하세요.</p>"
        "<p><b>선이 인식되지 않을 때</b><br>"
        "‘수동으로 양 끝점 지정’을 선택하세요. 너트부터 각 선의 양 끝점을 총 여섯 번 클릭합니다. 선택 도중 방식을 바꾸면 너트부터 다시 지정합니다.</p>"
        "<p><b>다시 맞추려면</b><br>‘맞춤 초기화’를 누르면 맞춤 결과와 선택을 지우고 다시 시작할 수 있습니다. 입력한 프렛 번호는 유지됩니다.</p>"
    )

    def __init__(self) -> None:
        super().__init__("지판 맞춤")

        self.middle_fret_spin = OptionalFretSpinBox()
        self.reference_fret_spin = OptionalFretSpinBox()
        self.middle_fret_spin.setAccessibleName("중간 기준 프렛 번호")
        self.reference_fret_spin.setAccessibleName("끝 기준 프렛 번호")
        self.help_button = HelpButton("지판 맞춤", self.HELP_TEXT)
        self.help_button.setParent(self)

        self.select_lines_button = QPushButton("지판 맞추기")
        self.clear_tracking_button = QPushButton("맞춤 초기화")
        self.select_lines_button.setStyleSheet("QPushButton { background: #111827; color: white; border: 1px solid #111827; } QPushButton:hover { background: #374151; } QPushButton:disabled { background: #e9ecef; color: #6b7280; border-color: #e9ecef; }")
        self.clear_tracking_button.setFlat(True)
        self.clear_tracking_button.setStyleSheet("QPushButton { background: transparent; border: none; color: #6b7280; padding: 5px 6px; font-size: 12px; } QPushButton:hover { color: #111827; background: #f1f3f5; }")

        layout = QGridLayout(self)
        layout.setColumnStretch(1, 1)
        self.middle_error = QLabel()
        self.reference_error = QLabel()
        for row, title, spin, error in (
            (0, "중간 기준 프렛 번호", self.middle_fret_spin, self.middle_error),
            (2, "끝 기준 프렛 번호", self.reference_fret_spin, self.reference_error),
        ):
            label = QLabel(title)
            label.setBuddy(spin)
            layout.addWidget(label, row, 0)
            layout.addWidget(spin, row, 1)
            error.setWordWrap(True)
            error.setStyleSheet("color: #b42318; font-size: 12px;")
            error.hide()
            layout.addWidget(error, row + 1, 0, 1, 2)
            spin.lineEdit().textEdited.connect(self.clear_errors)
            spin.valueChanged.connect(self.clear_errors)

        button_row = QHBoxLayout()
        button_row.addWidget(self.select_lines_button, 1)
        button_row.addWidget(self.clear_tracking_button)
        layout.addLayout(button_row, 4, 0, 1, 2)
        self.hint_label = QLabel(self.READY_HINT)
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet("color: #4b5563; font-size: 12px;")
        layout.addWidget(self.hint_label, 5, 0, 1, 2)
        self.manual_points_checkbox = QCheckBox("수동으로 양 끝점 지정")
        self.manual_points_checkbox.setToolTip("너트·중간 프렛·끝 기준 프렛의 양 끝점을 총 여섯 번 클릭합니다.")
        layout.addWidget(self.manual_points_checkbox, 6, 0, 1, 2)
        QWidget.setTabOrder(self.middle_fret_spin, self.reference_fret_spin)
        QWidget.setTabOrder(self.reference_fret_spin, self.select_lines_button)
        QWidget.setTabOrder(self.select_lines_button, self.clear_tracking_button)
        QWidget.setTabOrder(self.clear_tracking_button, self.manual_points_checkbox)
        QWidget.setTabOrder(self.manual_points_checkbox, self.help_button)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        option = QStyleOptionGroupBox()
        self.initStyleOption(option)
        title_rect = self.style().subControlRect(
            QStyle.ComplexControl.CC_GroupBox, option, QStyle.SubControl.SC_GroupBoxLabel, self,
        )
        self.help_button.move(self.width() - self.help_button.width() - 12,
                              max(0, title_rect.center().y() - self.help_button.height() // 2))

    def clear_errors(self, *_args) -> None:
        for error in (self.middle_error, self.reference_error):
            error.clear()
            error.hide()

    def commit_and_validate(self) -> bool:
        self.middle_fret_spin.interpretText()
        self.reference_fret_spin.interpretText()
        self.clear_errors()
        middle, last = self.middle_fret_spin.value(), self.reference_fret_spin.value()
        if not middle:
            self.middle_error.setText("중간 프렛 번호를 입력해주세요.")
        if not last:
            self.reference_error.setText("끝 프렛 번호를 입력해주세요.")
        if middle and last and middle >= last:
            self.middle_error.setText("중간 프렛 번호는 끝 프렛 번호보다 작아야 합니다.")
        for error in (self.middle_error, self.reference_error):
            error.setVisible(bool(error.text()))
        return 0 < middle < last
