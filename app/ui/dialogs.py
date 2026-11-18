from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

_KINDS = {
    "info": ("i", "#2563eb", "#f1f5f9"),
    "success": ("✓", "#15803d", "#ecfdf3"),
    "warning": ("!", "#a16207", "#fff7ed"),
    "danger": ("!", "#dc2626", "#fef2f2"),
    "question": ("?", "#374151", "#f1f3f5"),
}

CARD_WIDTH = 400
CARD_MARGIN = 24
ICON_SIZE = 36
BODY_SPACING = 14
TEXT_WIDTH = CARD_WIDTH - CARD_MARGIN * 2 - ICON_SIZE - BODY_SPACING

_STYLE = """
QDialog#app_dialog {
    background-color: rgba(15, 23, 42, 70);
}
QFrame#dialog_card {
    background-color: #ffffff;
    border: 1px solid #e5e7eb;
    border-radius: 14px;
}
QLabel {
    background-color: transparent;
}
QLabel#dialog_title {
    font-size: 15px;
    font-weight: 700;
    color: #111827;
}
QLabel#dialog_message {
    font-size: 13px;
    color: #4b5563;
}
QLabel#dialog_detail {
    font-size: 12px;
    color: #6b7280;
    background-color: #f8f9fa;
    border: 1px solid #e9ecef;
    border-radius: 8px;
    padding: 8px 10px;
}
QPushButton {
    font-size: 13px;
    font-weight: 600;
    border-radius: 8px;
    padding: 8px 18px;
    min-width: 64px;
}
QPushButton#dialog_secondary {
    background-color: #ffffff;
    color: #374151;
    border: 1px solid #ced4da;
}
QPushButton#dialog_secondary:hover {
    background-color: #f8f9fa;
    border-color: #adb5bd;
}
QPushButton#dialog_primary {
    background-color: #111827;
    color: #ffffff;
    border: 1px solid #111827;
}
QPushButton#dialog_primary:hover {
    background-color: #27272a;
    border-color: #27272a;
}
QPushButton#dialog_danger {
    background-color: #dc2626;
    color: #ffffff;
    border: 1px solid #dc2626;
}
QPushButton#dialog_danger:hover {
    background-color: #b91c1c;
    border-color: #b91c1c;
}
"""


class AppDialog(QDialog):
    def __init__(self, parent: QWidget | None, title: str, message: str, kind: str = "info",
                 detail: str = "", confirm_text: str = "확인", cancel_text: str | None = None,
                 danger: bool = False) -> None:
        self._host = parent.window() if parent is not None else None
        super().__init__(self._host)
        if self._host is not None:
            self.setWindowFlags(Qt.WindowType.Widget)
            self._host.installEventFilter(self)
        self.setObjectName("app_dialog")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setModal(True)
        self.setStyleSheet(_STYLE)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)

        card = QFrame()
        card.setObjectName("dialog_card")
        card.setFixedWidth(CARD_WIDTH)
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(32)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(15, 23, 42, 60))
        card.setGraphicsEffect(shadow)
        outer.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(CARD_MARGIN, CARD_MARGIN, CARD_MARGIN, 20)
        layout.setSpacing(16)

        body = QHBoxLayout()
        body.setSpacing(BODY_SPACING)
        glyph, fg, bg = _KINDS.get(kind, _KINDS["info"])
        icon = QLabel(glyph)
        icon.setFixedSize(ICON_SIZE, ICON_SIZE)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(
            f"background-color: {bg}; color: {fg}; border-radius: 18px; font-size: 16px; font-weight: 800;"
        )
        body.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)

        texts = QVBoxLayout()
        texts.setSpacing(6)
        title_label = QLabel(title)
        title_label.setObjectName("dialog_title")
        title_label.setWordWrap(True)
        texts.addWidget(title_label)
        if message:
            message_label = QLabel(message)
            message_label.setObjectName("dialog_message")
            message_label.setWordWrap(True)
            texts.addWidget(message_label)
        if detail:
            detail_label = QLabel(detail)
            detail_label.setObjectName("dialog_detail")
            detail_label.setWordWrap(True)
            detail_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            texts.addWidget(detail_label)
        body.addLayout(texts, 1)
        layout.addLayout(body)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        if cancel_text:
            cancel = QPushButton(cancel_text)
            cancel.setObjectName("dialog_secondary")
            cancel.setCursor(Qt.CursorShape.PointingHandCursor)
            cancel.clicked.connect(self.reject)
            buttons.addWidget(cancel)
        confirm = QPushButton(confirm_text)
        confirm.setObjectName("dialog_danger" if danger else "dialog_primary")
        confirm.setCursor(Qt.CursorShape.PointingHandCursor)
        confirm.setDefault(True)
        confirm.clicked.connect(self.accept)
        buttons.addWidget(confirm)
        layout.addLayout(buttons)

        for label in card.findChildren(QLabel):
            if label.wordWrap():
                label.ensurePolished()
                label.setFixedWidth(TEXT_WIDTH)
                label.setMinimumHeight(label.heightForWidth(TEXT_WIDTH))
        self._confirm = confirm

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._host is not None:
            self.setGeometry(self._host.rect())
            self.raise_()
        self._confirm.setFocus()

    def eventFilter(self, watched, event) -> bool:
        if watched is self._host and event.type() == QEvent.Type.Resize:
            self.setGeometry(self._host.rect())
        return super().eventFilter(watched, event)

    def done(self, result: int) -> None:
        if self._host is not None:
            self._host.removeEventFilter(self)
        super().done(result)


def show_message(parent: QWidget | None, title: str, message: str = "", kind: str = "info",
                 detail: str = "") -> None:
    AppDialog(parent, title, message, kind=kind, detail=detail).exec()


def ask_confirm(parent: QWidget | None, title: str, message: str = "", confirm_text: str = "확인",
                cancel_text: str = "취소", kind: str = "question", danger: bool = False) -> bool:
    dialog = AppDialog(parent, title, message, kind=kind, confirm_text=confirm_text,
                       cancel_text=cancel_text, danger=danger)
    return dialog.exec() == QDialog.DialogCode.Accepted
