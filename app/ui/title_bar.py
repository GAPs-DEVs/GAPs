from __future__ import annotations

import math
import sys
import warnings

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QMainWindow, QWidget

from app.ui.sidebar import paint_icon
from app.ui.theme import theme, themed_color

IS_MACOS = sys.platform == "darwin"

TITLE_BAR_HEIGHT = 28
TRAFFIC_LIGHTS_WIDTH = 72 if IS_MACOS else 8


def apply_native_title_bar(window: QMainWindow) -> None:
    if not IS_MACOS:
        return
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        window.setWindowFlag(Qt.WindowType.ExpandedClientAreaHint, True)
    window.setWindowFlag(Qt.WindowType.NoTitleBarBackgroundHint, True)
    window.setAttribute(Qt.WidgetAttribute.WA_ContentsMarginsRespectsSafeArea, False)


class TitleBarButton(QAbstractButton):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(QSize(26, 20))
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.isDown() or self.underMouse():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(themed_color("#dee2e6" if self.isDown() else "#e5e7eb"))
            painter.drawRoundedRect(QRectF(self.rect()), 6, 6)
        self.paint_glyph(painter, QPointF(self.width() / 2, self.height() / 2), themed_color("#4b5563", text=True))

    def paint_glyph(self, painter: QPainter, center: QPointF, color) -> None:
        raise NotImplementedError


class SidebarToggleButton(TitleBarButton):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.set_open(True)

    def set_open(self, open_: bool) -> None:
        self.setToolTip("사이드바 닫기 (⌃⌘S)" if open_ else "사이드바 열기 (⌃⌘S)")

    def paint_glyph(self, painter: QPainter, center: QPointF, color) -> None:
        paint_icon(painter, "sidebar", QRectF(center.x() - 8, center.y() - 8, 16, 16), color)


class ThemeToggleButton(TitleBarButton):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.clicked.connect(self._toggle)
        manager = theme()
        if manager is not None:
            manager.changed.connect(self._sync)
        self._sync()

    def _is_dark(self) -> bool:
        manager = theme()
        return manager is not None and manager.is_dark()

    def _toggle(self) -> None:
        manager = theme()
        if manager is not None:
            manager.toggle()

    def _sync(self, *_args) -> None:
        self.setToolTip("라이트 모드로 전환" if self._is_dark() else "다크 모드로 전환")
        self.update()

    def paint_glyph(self, painter: QPainter, center: QPointF, color) -> None:
        if self._is_dark():
            self._paint_sun(painter, center, color)
        else:
            self._paint_moon(painter, center, color)

    def _paint_sun(self, painter: QPainter, center: QPointF, color) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(center, 3.2, 3.2)
        pen = QPen(color, 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        for index in range(8):
            angle = index * math.pi / 4
            dx, dy = math.cos(angle), math.sin(angle)
            painter.drawLine(
                QPointF(center.x() + dx * 5.3, center.y() + dy * 5.3),
                QPointF(center.x() + dx * 7.0, center.y() + dy * 7.0),
            )

    def _paint_moon(self, painter: QPainter, center: QPointF, color) -> None:
        moon = QPainterPath()
        moon.addEllipse(center, 6.0, 6.0)
        cutout = QPainterPath()
        cutout.addEllipse(QPointF(center.x() + 3.4, center.y() - 2.6), 5.0, 5.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawPath(moon.subtracted(cutout))


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

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 10, 0)
        layout.addStretch(1)
        self.theme_button = ThemeToggleButton()
        layout.addWidget(self.theme_button)

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
