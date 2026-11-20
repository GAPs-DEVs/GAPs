from __future__ import annotations

import ctypes
from ctypes import c_double, c_long, c_void_p

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QMainWindow

NSWindowTitleHidden = 1
NSWindowCloseButton = 0
NSWindowZoomButton = 2


class NSPoint(ctypes.Structure):
    _fields_ = [("x", c_double), ("y", c_double)]


class NSSize(ctypes.Structure):
    _fields_ = [("width", c_double), ("height", c_double)]


class NSRect(ctypes.Structure):
    _fields_ = [("origin", NSPoint), ("size", NSSize)]


_objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
_objc.sel_registerName.restype = c_void_p
_objc.sel_registerName.argtypes = [ctypes.c_char_p]


def _send(receiver: int, selector: str, restype=c_void_p, argtypes=(), *args):
    function = ctypes.CFUNCTYPE(restype, c_void_p, c_void_p, *argtypes)(("objc_msgSend", _objc))
    return function(receiver, _objc.sel_registerName(selector.encode()), *args)


def _ns_window(window: QMainWindow) -> int | None:
    if QGuiApplication.platformName() != "cocoa":
        return None
    return _send(int(window.winId()), "window")


def _frame_in_window(view: int) -> NSRect:
    return _send(view, "convertRect:toView:", NSRect, (NSRect, c_void_p), _send(view, "bounds", NSRect), None)


def hide_window_title(window: QMainWindow) -> None:
    ns_window = _ns_window(window)
    if ns_window:
        _send(ns_window, "setTitleVisibility:", None, (c_long,), NSWindowTitleHidden)


def traffic_light_metrics(window: QMainWindow) -> tuple[int, float, float] | None:
    ns_window = _ns_window(window)
    if not ns_window:
        return None
    close = _send(ns_window, "standardWindowButton:", c_void_p, (c_long,), NSWindowCloseButton)
    zoom = _send(ns_window, "standardWindowButton:", c_void_p, (c_long,), NSWindowZoomButton)
    if not close or not zoom:
        return None
    window_height = _send(ns_window, "frame", NSRect).size.height
    layout = _send(ns_window, "contentLayoutRect", NSRect)
    title_bar_height = round(window_height - layout.origin.y - layout.size.height)
    close_frame = _frame_in_window(close)
    zoom_frame = _frame_in_window(zoom)
    center_y = window_height - close_frame.origin.y - close_frame.size.height / 2
    return title_bar_height, zoom_frame.origin.x + zoom_frame.size.width, center_y
