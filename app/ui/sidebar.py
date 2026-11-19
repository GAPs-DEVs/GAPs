from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractButton, QFrame, QLabel, QMenu, QSizePolicy, QVBoxLayout, QWidget

from app.ui.theme import themed_color

SIDEBAR_WIDTH = 240


def paint_icon(painter: QPainter, name: str, rect: QRectF, color) -> None:
    painter.save()
    painter.translate(rect.topLeft())
    painter.scale(rect.width() / 16, rect.height() / 16)
    pen = QPen(color, 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    if name == "plus":
        painter.drawLine(QPointF(8, 3), QPointF(8, 13))
        painter.drawLine(QPointF(3, 8), QPointF(13, 8))
    elif name == "search":
        painter.drawEllipse(QPointF(7, 7), 4.5, 4.5)
        painter.drawLine(QPointF(10.4, 10.4), QPointF(13.5, 13.5))
    elif name == "library":
        painter.drawRoundedRect(QRectF(2.5, 6, 11, 7.5), 1.8, 1.8)
        painter.drawLine(QPointF(4, 3.8), QPointF(12, 3.8))
        painter.drawLine(QPointF(5.5, 1.8), QPointF(10.5, 1.8))
    elif name == "play":
        path = QPainterPath()
        path.moveTo(5, 3)
        path.lineTo(13, 8)
        path.lineTo(5, 13)
        path.closeSubpath()
        painter.drawPath(path)
    elif name == "edit":
        path = QPainterPath()
        path.moveTo(10.5, 2.5)
        path.lineTo(13.5, 5.5)
        path.lineTo(6, 13)
        path.lineTo(3, 13)
        path.lineTo(3, 10)
        path.closeSubpath()
        painter.drawPath(path)
        painter.drawLine(QPointF(8.8, 4.2), QPointF(11.8, 7.2))
    elif name == "history":
        painter.drawEllipse(QPointF(8, 8), 5.8, 5.8)
        painter.drawLine(QPointF(8, 8), QPointF(8, 4.8))
        painter.drawLine(QPointF(8, 8), QPointF(10.4, 9.4))
    elif name == "sidebar":
        painter.drawRoundedRect(QRectF(1.8, 2.8, 12.4, 10.4), 2.2, 2.2)
        painter.drawLine(QPointF(6, 3), QPointF(6, 13))
    elif name == "note":
        painter.setBrush(color)
        painter.drawEllipse(QPointF(5.6, 11.6), 2.2, 1.7)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(QPointF(7.6, 11.4), QPointF(7.6, 3.2))
        painter.drawLine(QPointF(7.6, 3.2), QPointF(12, 4.6))

    painter.restore()


def _font(widget: QWidget, pixel_size: int, weight: QFont.Weight) -> QFont:
    font = QFont(widget.font())
    font.setPixelSize(pixel_size)
    font.setWeight(weight)
    return font


class SidebarItem(QAbstractButton):
    def __init__(self, text: str, icon: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setText(text)
        self.icon_name = icon
        self.subtitle = ""
        self.badge = ""
        self.setFixedHeight(32)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def nextCheckState(self) -> None:
        pass

    def set_subtitle(self, subtitle: str) -> None:
        self.subtitle = subtitle
        self.setToolTip(subtitle)
        self.update()

    def set_badge(self, badge: str) -> None:
        self.badge = badge
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(SIDEBAR_WIDTH - 16, 32)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        enabled = self.isEnabled()

        background = None
        if self.isChecked():
            background = "#e2e8f0"
        elif enabled and self.isDown():
            background = "#dee2e6"
        elif enabled and self.underMouse():
            background = "#e9ecef"
        if background:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(themed_color(background))
            painter.drawRoundedRect(rect, 6, 6)

        if not enabled:
            text_color = themed_color("#adb5bd", text=True)
        elif self.isChecked():
            text_color = themed_color("#111827", text=True)
        else:
            text_color = themed_color("#374151", text=True)

        text_left = 12.0
        if self.icon_name:
            icon_color = text_color if self.isChecked() or not enabled else themed_color("#4b5563", text=True)
            paint_icon(painter, self.icon_name, QRectF(10, (rect.height() - 16) / 2, 16, 16), icon_color)
            text_left = 34.0

        right = rect.width() - 10
        if self.badge:
            badge_font = _font(self, 10, QFont.Weight.DemiBold)
            badge_width = QFontMetrics(badge_font).horizontalAdvance(self.badge) + 12
            badge_rect = QRectF(right - badge_width, (rect.height() - 18) / 2, badge_width, 18)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(themed_color("#e9ecef"))
            painter.drawRoundedRect(badge_rect, 9, 9)
            painter.setFont(badge_font)
            painter.setPen(themed_color("#6b7280", text=True))
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, self.badge)
            right = badge_rect.left() - 8

        title_font = _font(self, 13, QFont.Weight.DemiBold if self.isChecked() else QFont.Weight.Medium)
        title_metrics = QFontMetrics(title_font)
        title_width = min(title_metrics.horizontalAdvance(self.text()), int(right - text_left))
        painter.setFont(title_font)
        painter.setPen(text_color)
        title = title_metrics.elidedText(self.text(), Qt.TextElideMode.ElideRight, int(right - text_left))
        painter.drawText(
            QRectF(text_left, 0, right - text_left, rect.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            title,
        )

        if self.subtitle:
            sub_font = _font(self, 11, QFont.Weight.Normal)
            sub_left = text_left + title_width + 8
            if right - sub_left > 24:
                sub_metrics = QFontMetrics(sub_font)
                painter.setFont(sub_font)
                painter.setPen(themed_color("#9ca3af", text=True))
                painter.drawText(
                    QRectF(sub_left, 0, right - sub_left, rect.height()),
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                    sub_metrics.elidedText(self.subtitle, Qt.TextElideMode.ElideRight, int(right - sub_left)),
                )


class ProfileButton(QAbstractButton):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.name = "게스트"
        self.detail = ""
        self.setFixedHeight(44)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def set_user(self, name: str, detail: str) -> None:
        self.name = name or "게스트"
        self.detail = detail
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(SIDEBAR_WIDTH - 16, 44)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())

        if self.isDown() or self.underMouse():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(themed_color("#dee2e6" if self.isDown() else "#e9ecef"))
            painter.drawRoundedRect(rect, 8, 8)

        avatar = QRectF(8, (rect.height() - 28) / 2, 28, 28)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(themed_color("#2563eb"))
        painter.drawEllipse(avatar)
        painter.setPen(themed_color("#ffffff", text=True))
        painter.setFont(_font(self, 12, QFont.Weight.Bold))
        painter.drawText(avatar, Qt.AlignmentFlag.AlignCenter, self.name[:1].upper())

        text_left = avatar.right() + 10
        text_width = int(rect.width() - text_left - 28)
        name_font = _font(self, 13, QFont.Weight.DemiBold)
        painter.setFont(name_font)
        painter.setPen(themed_color("#111827", text=True))
        name_top = 6 if self.detail else 0
        name_height = 18 if self.detail else rect.height()
        painter.drawText(
            QRectF(text_left, name_top, text_width, name_height),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            QFontMetrics(name_font).elidedText(self.name, Qt.TextElideMode.ElideRight, text_width),
        )
        if self.detail:
            detail_font = _font(self, 11, QFont.Weight.Normal)
            painter.setFont(detail_font)
            painter.setPen(themed_color("#6b7280", text=True))
            painter.drawText(
                QRectF(text_left, 23, text_width, 15),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                QFontMetrics(detail_font).elidedText(self.detail, Qt.TextElideMode.ElideRight, text_width),
            )

        chevron_color = themed_color("#9ca3af", text=True)
        pen = QPen(chevron_color, 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        cx, cy = rect.width() - 16, rect.height() / 2
        painter.drawLine(QPointF(cx - 3.5, cy - 1.5), QPointF(cx, cy - 5))
        painter.drawLine(QPointF(cx, cy - 5), QPointF(cx + 3.5, cy - 1.5))
        painter.drawLine(QPointF(cx - 3.5, cy + 1.5), QPointF(cx, cy + 5))
        painter.drawLine(QPointF(cx, cy + 5), QPointF(cx + 3.5, cy + 1.5))


class Sidebar(QFrame):
    navigate = Signal(str)
    new_score_requested = Signal()
    search_requested = Signal()
    recent_selected = Signal(str)
    logout_requested = Signal()
    open_changed = Signal(bool)

    NAV_ITEMS = (
        ("library", "악보 보관함", "library"),
        ("practice", "연습", "play"),
        ("editor", "악보 편집", "edit"),
        ("history", "연습 기록", "history"),
    )

    def __init__(self, top_inset: int = 0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("""
            QFrame#sidebar {
                background-color: #f1f3f5;
                border: none;
                border-right: 1px solid #e5e7eb;
            }
            QWidget#sidebar_body {
                background-color: transparent;
            }
            QLabel#sidebar_section {
                background-color: transparent;
                color: #9ca3af;
                font-size: 11px;
                font-weight: 600;
                padding-left: 12px;
            }
            QFrame#sidebar_divider {
                background-color: #e5e7eb;
                border: none;
            }
        """)
        self.top_inset = top_inset
        self._open = True
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)
        self.setMaximumWidth(SIDEBAR_WIDTH)

        self.body = QWidget(self)
        self.body.setObjectName("sidebar_body")
        layout = QVBoxLayout(self.body)
        layout.setContentsMargins(8, top_inset + 10, 8, 10)
        layout.setSpacing(2)

        new_score = SidebarItem("새 악보 등록", "plus")
        new_score.clicked.connect(self.new_score_requested.emit)
        search = SidebarItem("악보 검색", "search")
        search.clicked.connect(self.search_requested.emit)
        layout.addWidget(new_score)
        layout.addWidget(search)
        layout.addSpacing(14)

        self.nav_buttons: dict[str, SidebarItem] = {}
        for key, text, icon in self.NAV_ITEMS:
            item = SidebarItem(text, icon)
            item.setCheckable(True)
            item.setAutoExclusive(False)
            item.clicked.connect(lambda _checked=False, nav_key=key: self._handle_nav(nav_key))
            self.nav_buttons[key] = item
            layout.addWidget(item)
        self.nav_buttons["history"].setEnabled(False)
        self.nav_buttons["history"].set_badge("준비 중")

        layout.addSpacing(18)
        self.recent_label = QLabel("최근 연습")
        self.recent_label.setObjectName("sidebar_section")
        self.recent_label.setFixedHeight(22)
        layout.addWidget(self.recent_label)
        self.recent_layout = QVBoxLayout()
        self.recent_layout.setContentsMargins(0, 0, 0, 0)
        self.recent_layout.setSpacing(2)
        layout.addLayout(self.recent_layout)

        layout.addStretch(1)

        divider = QFrame()
        divider.setObjectName("sidebar_divider")
        divider.setFixedHeight(1)
        layout.addWidget(divider)
        layout.addSpacing(6)

        self.profile_button = ProfileButton()
        self.profile_button.clicked.connect(self._show_profile_menu)
        layout.addWidget(self.profile_button)

        self._animation = QPropertyAnimation(self, b"maximumWidth", self)
        self._animation.setDuration(200)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.set_recent([])

    def is_open(self) -> bool:
        return self._open

    def set_open(self, open_: bool, animated: bool = True) -> None:
        open_ = bool(open_)
        changed = open_ != self._open
        self._open = open_
        target = SIDEBAR_WIDTH if open_ else 0
        self._animation.stop()
        if animated and self.isVisible():
            self._animation.setStartValue(self.maximumWidth())
            self._animation.setEndValue(target)
            self._animation.start()
        else:
            self.setMaximumWidth(target)
        if changed:
            self.open_changed.emit(open_)

    def toggle(self) -> None:
        self.set_open(not self._open)

    def set_current(self, key: str | None) -> None:
        for nav_key, button in self.nav_buttons.items():
            button.setChecked(nav_key == key)

    def set_practice_title(self, title: str | None) -> None:
        button = self.nav_buttons["practice"]
        button.setEnabled(bool(title))
        button.set_subtitle(title or "")

    def set_recent(self, scores: list[tuple[str, str]]) -> None:
        while self.recent_layout.count():
            widget = self.recent_layout.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        for title, path in scores:
            item = SidebarItem(title, "note")
            item.setToolTip(title)
            item.clicked.connect(lambda _checked=False, score_path=path: self.recent_selected.emit(score_path))
            self.recent_layout.addWidget(item)
        self.recent_label.setVisible(bool(scores))

    def set_user(self, name: str, detail: str) -> None:
        self.profile_button.set_user(name, detail)

    def sizeHint(self) -> QSize:
        return QSize(self.maximumWidth(), super().sizeHint().height())

    def minimumSizeHint(self) -> QSize:
        return QSize(0, 0)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.body.setGeometry(0, 0, SIDEBAR_WIDTH - 1, self.height())

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() < self.top_inset:
            handle = self.window().windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)

    def _handle_nav(self, key: str) -> None:
        self.navigate.emit(key)

    def _show_profile_menu(self) -> None:
        menu = QMenu(self)
        logout = menu.addAction("로그아웃")
        logout.triggered.connect(self.logout_requested.emit)
        anchor = self.profile_button.mapToGlobal(self.profile_button.rect().topLeft())
        anchor.setY(anchor.y() - menu.sizeHint().height() - 4)
        menu.exec(anchor)
