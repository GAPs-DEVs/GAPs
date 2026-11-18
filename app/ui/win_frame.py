from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QPoint, Qt
from PySide6.QtWidgets import QAbstractButton, QApplication, QMainWindow

WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084
WM_NCMOUSEMOVE = 0x00A0
WM_NCLBUTTONDOWN = 0x00A1
WM_NCLBUTTONUP = 0x00A2
WM_NCLBUTTONDBLCLK = 0x00A3
WM_NCMOUSELEAVE = 0x02A2

HTCLIENT = 1
HTCAPTION = 2
HTMAXBUTTON = 9
HTLEFT = 10
HTRIGHT = 11
HTTOP = 12
HTTOPLEFT = 13
HTTOPRIGHT = 14
HTBOTTOM = 15
HTBOTTOMLEFT = 16
HTBOTTOMRIGHT = 17

GWL_STYLE = -16
WS_CAPTION = 0x00C00000
WS_SYSMENU = 0x00080000
WS_THICKFRAME = 0x00040000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020

SM_CXSIZEFRAME = 32
SM_CYSIZEFRAME = 33
SM_CXPADDEDBORDER = 92

MONITOR_DEFAULTTONEAREST = 2
TME_LEAVE = 0x00000002
TME_NONCLIENT = 0x00000010


class MARGINS(ctypes.Structure):
    _fields_ = [
        ("cxLeftWidth", ctypes.c_int),
        ("cxRightWidth", ctypes.c_int),
        ("cyTopHeight", ctypes.c_int),
        ("cyBottomHeight", ctypes.c_int),
    ]


class NCCALCSIZE_PARAMS(ctypes.Structure):
    _fields_ = [("rgrc", wintypes.RECT * 3), ("lppos", ctypes.c_void_p)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


class TRACKMOUSEEVENT(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("hwndTrack", wintypes.HWND),
        ("dwHoverTime", wintypes.DWORD),
    ]


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_dwmapi = ctypes.WinDLL("dwmapi")

_user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
_user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT,
]
_user32.IsZoomed.argtypes = [wintypes.HWND]
_user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
_user32.MonitorFromWindow.restype = wintypes.HMONITOR
_user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
_user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
_user32.TrackMouseEvent.argtypes = [ctypes.POINTER(TRACKMOUSEEVENT)]
_dwmapi.DwmExtendFrameIntoClientArea.argtypes = [wintypes.HWND, ctypes.POINTER(MARGINS)]

try:
    _user32.GetDpiForWindow.restype = wintypes.UINT
    _user32.GetDpiForWindow.argtypes = [wintypes.HWND]
    _user32.GetSystemMetricsForDpi.argtypes = [ctypes.c_int, wintypes.UINT]
    _HAS_DPI_METRICS = True
except AttributeError:
    _HAS_DPI_METRICS = False


def _metric(hwnd: int, index: int) -> int:
    if _HAS_DPI_METRICS:
        return _user32.GetSystemMetricsForDpi(index, _user32.GetDpiForWindow(hwnd))
    return _user32.GetSystemMetrics(index)


def _frame_thickness(hwnd: int) -> tuple[int, int]:
    padding = _metric(hwnd, SM_CXPADDEDBORDER)
    return _metric(hwnd, SM_CXSIZEFRAME) + padding, _metric(hwnd, SM_CYSIZEFRAME) + padding


def _signed_word(value: int) -> int:
    return ctypes.c_short(value & 0xFFFF).value


class _CustomFrameFilter(QAbstractNativeEventFilter):
    def __init__(self, window: QMainWindow) -> None:
        super().__init__()
        self.window = window
        self.hwnd = int(window.winId())

    def nativeEventFilter(self, event_type, message):
        if event_type != b"windows_generic_MSG":
            return False, 0
        msg = wintypes.MSG.from_address(int(message))
        if msg.hWnd is None or int(msg.hWnd) != self.hwnd:
            return False, 0

        if msg.message == WM_NCCALCSIZE and msg.wParam:
            if _user32.IsZoomed(self.hwnd):
                self._fit_to_work_area(NCCALCSIZE_PARAMS.from_address(msg.lParam).rgrc[0])
            return True, 0
        if msg.message == WM_NCHITTEST:
            result = self._hit_test(_signed_word(msg.lParam), _signed_word(msg.lParam >> 16))
            if result != HTMAXBUTTON:
                self._set_max_hover(False)
            return True, result

        if msg.wParam != HTMAXBUTTON:
            if msg.message in (WM_NCMOUSEMOVE, WM_NCMOUSELEAVE):
                self._set_max_hover(False)
            return False, 0
        if msg.message == WM_NCMOUSEMOVE:
            self._set_max_hover(True)
            self._track_leave()
            return True, 0
        if msg.message in (WM_NCLBUTTONDOWN, WM_NCLBUTTONDBLCLK):
            button = self._max_button()
            if button is not None:
                button.setDown(True)
            return True, 0
        if msg.message == WM_NCLBUTTONUP:
            button = self._max_button()
            if button is not None and button.isDown():
                button.setDown(False)
                button.click()
            return True, 0
        if msg.message == WM_NCMOUSELEAVE:
            self._set_max_hover(False)
        return False, 0

    def _fit_to_work_area(self, rect: wintypes.RECT) -> None:
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        monitor = _user32.MonitorFromWindow(self.hwnd, MONITOR_DEFAULTTONEAREST)
        if not monitor or not _user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            frame_x, frame_y = _frame_thickness(self.hwnd)
            rect.left += frame_x
            rect.top += frame_y
            rect.right -= frame_x
            rect.bottom -= frame_y
            return
        work = info.rcWork
        rect.left = max(rect.left, work.left)
        rect.top = max(rect.top, work.top)
        rect.right = min(rect.right, work.right)
        rect.bottom = min(rect.bottom, work.bottom)

    def _hit_test(self, screen_x: int, screen_y: int) -> int:
        point = wintypes.POINT(screen_x, screen_y)
        _user32.ScreenToClient(self.hwnd, ctypes.byref(point))
        client = wintypes.RECT()
        _user32.GetClientRect(self.hwnd, ctypes.byref(client))

        if not _user32.IsZoomed(self.hwnd) and not self.window.isFullScreen():
            frame_x, frame_y = _frame_thickness(self.hwnd)
            left = point.x < frame_x
            right = point.x >= client.right - frame_x
            top = point.y < frame_y
            bottom = point.y >= client.bottom - frame_y
            if top and left:
                return HTTOPLEFT
            if top and right:
                return HTTOPRIGHT
            if bottom and left:
                return HTBOTTOMLEFT
            if bottom and right:
                return HTBOTTOMRIGHT
            if left:
                return HTLEFT
            if right:
                return HTRIGHT
            if top:
                return HTTOP
            if bottom:
                return HTBOTTOM

        title_bar = getattr(self.window, "title_bar", None)
        if title_bar is None or not title_bar.isVisible():
            return HTCLIENT
        ratio = self.window.devicePixelRatioF()
        local = QPoint(round(point.x / ratio), round(point.y / ratio))
        if local.y() >= title_bar.height():
            return HTCLIENT

        widget = self.window.childAt(local)
        while widget is not None and widget is not self.window:
            if isinstance(widget, QAbstractButton) and widget.isVisible():
                return HTMAXBUTTON if widget is self._max_button() else HTCLIENT
            widget = widget.parentWidget()
        return HTCAPTION

    def _max_button(self) -> QAbstractButton | None:
        title_bar = getattr(self.window, "title_bar", None)
        return getattr(title_bar, "maximize_button", None)

    def _set_max_hover(self, hovered: bool) -> None:
        button = self._max_button()
        if button is None:
            return
        if not hovered and button.isDown():
            button.setDown(False)
        button.set_native_hover(hovered)

    def _track_leave(self) -> None:
        track = TRACKMOUSEEVENT()
        track.cbSize = ctypes.sizeof(TRACKMOUSEEVENT)
        track.dwFlags = TME_LEAVE | TME_NONCLIENT
        track.hwndTrack = self.hwnd
        _user32.TrackMouseEvent(ctypes.byref(track))


def install_custom_frame(window: QMainWindow) -> None:
    window.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
    frame_filter = _CustomFrameFilter(window)
    QApplication.instance().installNativeEventFilter(frame_filter)
    window._custom_frame_filter = frame_filter

    hwnd = frame_filter.hwnd
    style = _user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
    _user32.SetWindowLongPtrW(
        hwnd, GWL_STYLE, style | WS_CAPTION | WS_SYSMENU | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX,
    )
    margins = MARGINS(1, 1, 1, 1)
    _dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
    _user32.SetWindowPos(
        hwnd, None, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
    )
