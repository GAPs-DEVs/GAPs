from __future__ import annotations

import sys

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

WINDOWS_FAMILIES = ("Pretendard", "Segoe UI", "Malgun Gothic")
BASE_PIXEL_SIZE = 13


def apply_app_font(app: QApplication) -> None:
    if sys.platform != "win32":
        return
    installed = set(QFontDatabase.families())
    families = [family for family in WINDOWS_FAMILIES if family in installed]
    if not families:
        return
    font = QFont(app.font())
    font.setFamilies(families)
    font.setPixelSize(BASE_PIXEL_SIZE)
    font.setHintingPreference(QFont.HintingPreference.PreferVerticalHinting)
    app.setFont(font)
