from __future__ import annotations

import re
from functools import lru_cache

from PySide6.QtCore import QEvent, QObject, QSettings, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QWidget

SURFACE_DARK = {
    "#ffffff": "#181a20",
    "#f8f9fa": "#0f1115",
    "#fbfbfc": "#15171c",
    "#f1f3f5": "#22252d",
    "#f3f4f6": "#20232b",
    "#f1f5f9": "#20232b",
    "#eef2f6": "#20232b",
    "#e9ecef": "#2a2e37",
    "#e5e7eb": "#2a2e37",
    "#dee2e6": "#323641",
    "#dfe3e8": "#323641",
    "#e2e8f0": "#323641",
    "#ced4da": "#3a3f4b",
    "#d1d1d6": "#3a3f4b",
    "#cbd5e1": "#3a3f4b",
    "#adb5bd": "#4b5260",
    "#94a3b8": "#5b6270",
    "#6b7280": "#6b7383",
    "#111827": "#eceef1",
    "#333333": "#eceef1",
    "#27272a": "#d4d7dc",
    "#374151": "#d4d7dc",
    "#555555": "#d4d7dc",
    "#09090b": "#bfc3c9",
    "#fef2f2": "#2a1618",
    "#fecaca": "#4a2326",
    "#fca5a5": "#7f2d2d",
    "#ecfdf3": "#13261b",
    "#86cfa3": "#1f5f3a",
    "#fff7ed": "#2b1d10",
}

TEXT_DARK = {
    "#111827": "#f3f4f6",
    "#111111": "#f3f4f6",
    "#212529": "#e6e8eb",
    "#303033": "#e6e8eb",
    "#374151": "#d1d5db",
    "#4b5563": "#b4bcc8",
    "#475569": "#b4bcc8",
    "#6b7280": "#9aa3b2",
    "#6e6e73": "#9aa3b2",
    "#8e8e93": "#8b93a1",
    "#94a3b8": "#8b93a1",
    "#9ca3af": "#7c8594",
    "#adb5bd": "#5b6270",
    "#f1f3f5": "#2a2e37",
    "#b91c1c": "#f87171",
    "#dc2626": "#f87171",
    "#b42318": "#f87171",
    "#15803d": "#4ade80",
    "#166534": "#4ade80",
    "#a16207": "#facc15",
    "#9a3412": "#fb923c",
    "#2563eb": "#60a5fa",
    "#1d4ed8": "#93c5fd",
}

INVERTED_SURFACES = {"#111827", "#333333", "#27272a", "#374151", "#555555", "#09090b"}
TEXT_ON_INVERTED = "#111318"

_NAMED = {"white": "#ffffff", "black": "#000000"}
_BLOCK = re.compile(r"\{([^{}]*)\}")
_DECLARATION = re.compile(
    r"(?P<prop>(?<![\w-])[a-z-]+)(?P<sep>\s*:\s*)(?P<value>[^;{}]*)", re.IGNORECASE
)
_COLOR = re.compile(r"#[0-9a-fA-F]{6}\b|\bwhite\b|\bblack\b", re.IGNORECASE)
_BACKGROUND = re.compile(r"background(?:-color)?\s*:\s*[^;{}]*?(#[0-9a-fA-F]{6}\b|\bwhite\b)", re.IGNORECASE)


def _normalize(color: str) -> str:
    color = color.lower()
    return _NAMED.get(color, color)


def _convert_declarations(body: str) -> str:
    background = _BACKGROUND.search(body)
    on_inverted = background is not None and _normalize(background.group(1)) in INVERTED_SURFACES

    def replace_declaration(match: re.Match) -> str:
        prop = match.group("prop").lower()
        is_text = prop == "color"

        def replace_color(color_match: re.Match) -> str:
            color = _normalize(color_match.group(0))
            if is_text:
                if color == "#ffffff":
                    return TEXT_ON_INVERTED if on_inverted else color
                return TEXT_DARK.get(color, color)
            return SURFACE_DARK.get(color, color)

        value = _COLOR.sub(replace_color, match.group("value"))
        return f"{match.group('prop')}{match.group('sep')}{value}"

    return _DECLARATION.sub(replace_declaration, body)


@lru_cache(maxsize=2048)
def convert_stylesheet(qss: str) -> str:
    if "{" not in qss:
        return _convert_declarations(qss)
    return _BLOCK.sub(lambda m: "{" + _convert_declarations(m.group(1)) + "}", qss)


class ThemeManager(QObject):
    changed = Signal(bool)

    _LIGHT_QSS = "_theme_light_qss"
    _THEMED_QSS = "_theme_applied_qss"

    def __init__(self, app: QApplication) -> None:
        super().__init__(app)
        self._app = app
        self._settings = QSettings("GAPs", "GuitarPractice")
        self._dark = False
        self._filter_installed = False
        self.set_dark(self._settings.value("dark_mode", False, type=bool))

    def is_dark(self) -> bool:
        return self._dark

    def toggle(self) -> None:
        self.set_dark(not self._dark)

    def set_dark(self, dark: bool) -> None:
        dark = bool(dark)
        changed = dark != self._dark
        self._dark = dark
        self._settings.setValue("dark_mode", dark)
        self._app.styleHints().setColorScheme(Qt.ColorScheme.Dark if dark else Qt.ColorScheme.Light)

        if dark and not self._filter_installed:
            self._app.installEventFilter(self)
            self._filter_installed = True
        elif not dark and self._filter_installed:
            self._app.removeEventFilter(self)
            self._filter_installed = False

        for widget in self._app.allWidgets():
            if dark:
                self._restyle(widget)
            else:
                self._restore(widget)
            widget.update()

        if changed:
            self.changed.emit(dark)

    def color(self, light: str, text: bool = False) -> QColor:
        if not self._dark:
            return QColor(light)
        key = _normalize(light)
        mapped = (TEXT_DARK if text else SURFACE_DARK).get(key, key)
        return QColor(mapped)

    def _restyle(self, widget: QWidget) -> None:
        qss = widget.styleSheet()
        if not qss or qss == widget.property(self._THEMED_QSS):
            return
        widget.setProperty(self._LIGHT_QSS, qss)
        themed = convert_stylesheet(qss) if self._dark else qss
        widget.setProperty(self._THEMED_QSS, themed)
        if themed != qss:
            widget.setStyleSheet(themed)

    def _restore(self, widget: QWidget) -> None:
        light = widget.property(self._LIGHT_QSS)
        if light is None or widget.styleSheet() != widget.property(self._THEMED_QSS):
            return
        widget.setProperty(self._THEMED_QSS, None)
        if widget.styleSheet() != light:
            widget.setStyleSheet(light)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() in (QEvent.Type.StyleChange, QEvent.Type.Polish) and isinstance(watched, QWidget):
            self._restyle(watched)
        return False


_manager: ThemeManager | None = None


def install_theme(app: QApplication) -> ThemeManager:
    global _manager
    if _manager is None:
        _manager = ThemeManager(app)
    return _manager


def theme() -> ThemeManager | None:
    return _manager


def themed_color(light: str, text: bool = False) -> QColor:
    return _manager.color(light, text) if _manager is not None else QColor(light)
