from __future__ import annotations

import sys
import warnings

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QMainWindow, QWidget

IS_MACOS = sys.platform == "darwin"

TITLE_BAR_HEIGHT = 28


def apply_native_title_bar(window: QMainWindow) -> None:
    if not IS_MACOS:
        return
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        window.setWindowFlag(Qt.WindowType.ExpandedClientAreaHint, True)
    window.setWindowFlag(Qt.WindowType.NoTitleBarBackgroundHint, True)
    window.setAttribute(Qt.WidgetAttribute.WA_ContentsMarginsRespectsSafeArea, False)


class TitleBar(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("title_bar")
        self.setFixedHeight(TITLE_BAR_HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("""
            QWidget#title_bar {
                background-color: #f8f9fa;
                border-bottom: 1px solid #e9ecef;
            }
        """)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            window = self.window()
            if window.isMaximized():
                window.showNormal()
            else:
                window.showMaximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.window().installEventFilter(self)
        self._sync_fullscreen_state()

    def eventFilter(self, watched, event) -> bool:
        if watched is self.window() and event.type() == QEvent.Type.WindowStateChange:
            self._sync_fullscreen_state()
        return super().eventFilter(watched, event)

    def _sync_fullscreen_state(self) -> None:
        self.setVisible(not self.window().isFullScreen())
