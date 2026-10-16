"""Calibration regressions: real fret IDs must survive missing YOLO detections."""
import os
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtGui import QImage
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest

from app.ui.main_window import MainWindow
from app.ui.tracking_panel import HelpButton, TrackingPanel
from vision.fretboard_mapping import DetectedLine


@pytest.fixture
def window():
    application = QApplication.instance() or QApplication([])
    with patch('app.ui.main_window.enumerate_camera_indices', return_value=[]), patch('app.ui.main_window.enumerate_audio_input_devices', return_value=[]):
        widget = MainWindow(model_path=Path('models/best06.pt'))
    widget.reference_fret_spin.setValue(7)
    widget.tracking_panel.middle_fret_spin.setValue(3)
    widget.video_worker = Mock()
    widget.video_worker.frozen_selection_lines = ()
    widget.map_label_click_to_image = lambda x, y: (x, y)
    frame = QImage(340, 380, QImage.Format.Format_RGB888)
    frame.fill(0)
    widget.video_label.set_current_image(frame)
    widget.selection_stage = 'select_nut'
    yield widget
    widget.video_worker = None
    widget.close()
    application.processEvents()


def test_selected_actual_number_is_not_replaced_by_detected_line_count(window):
    assert not window.reference_fret_spin.isHidden()
    nut = DetectedLine('nut', (80, 50), (180, 50), 1)
    seventh = DetectedLine('fret', (80, 310), (180, 310), 1)
    middle = DetectedLine('fret', (80, 170), (180, 170), 1)
    window.video_worker.frozen_selection_lines = (nut, middle, seventh)
    window.handle_video_click(130, 50)
    window.handle_video_click(130, 170)
    window.handle_video_click(130, 310)
    assert window.reference_fret_spin.value() == 7
    window.video_worker.apply_calibrated_selection.assert_called_once()
    grid, index = window.video_worker.apply_calibrated_selection.call_args.args
    assert index == 7
    assert grid.ordered_fret_lines[2].p0[1] == pytest.approx(170)
    assert grid.ordered_fret_lines[-1].p0[1] == pytest.approx(310)


def test_six_point_calibration_works_without_any_detected_lines(window):
    window.manual_point_selection = True
    window.handle_video_click(80, 50)
    window.handle_video_click(180, 50)
    window.handle_video_click(80, 170)
    window.handle_video_click(180, 170)
    window.handle_video_click(80, 310)
    window.video_worker.apply_calibrated_selection.assert_not_called()
    window.handle_video_click(180, 310)
    args = window.video_worker.apply_calibrated_selection.call_args.args
    assert args[0].nut_line.kind == 'nut'
    assert args[0].ordered_fret_lines[-1].p0 == (80, 310)
    assert args[1] == 7
    assert window.selection_stage == 'idle'


def test_near_duplicate_endpoint_click_is_rejected(window):
    window.manual_point_selection = True
    window.handle_video_click(80, 50)
    window.handle_video_click(81, 50)
    assert window.selection_stage == 'select_nut'
    assert window.selection_points == []
    window.video_worker.apply_calibrated_selection.assert_not_called()


def test_number_fields_start_empty_and_can_be_cleared(window):
    panel = TrackingPanel()
    for spin in (panel.reference_fret_spin, panel.middle_fret_spin):
        assert spin.value() == 0
        assert spin.text() == ''
        spin.setValue(12)
        spin.lineEdit().selectAll()
        QTest.keyClick(spin.lineEdit(), Qt.Key.Key_Backspace)
        spin.interpretText()
        assert spin.value() == 0
        assert spin.text() == ''
    panel.close()


@pytest.mark.parametrize('middle,last', [(0, 0), (0, 12), (5, 0), (12, 12), (13, 12)])
def test_missing_or_invalid_numbers_do_not_freeze_camera(window, middle, last):
    window.selection_stage = 'idle'
    window.reference_fret_spin.setValue(last)
    window.tracking_panel.middle_fret_spin.setValue(middle)
    window.begin_line_selection()
    window.video_worker.begin_line_selection.assert_not_called()
    assert window.selection_stage == 'idle'


def test_editing_reference_number_does_not_relabel_active_calibration(window):
    window.reference_fret_spin.setValue(12)
    window.video_worker.set_reference_fret_index.assert_not_called()


def test_invalid_middle_line_returns_to_middle_selection(window):
    window.manual_point_selection = True
    for point in ((80, 50), (180, 50), (80, 340), (180, 340), (80, 310), (180, 310)):
        window.handle_video_click(*point)
    window.video_worker.apply_calibrated_selection.assert_not_called()
    assert window.selection_stage == 'select_middle'
    assert len(window.selection_points) == 2


def test_calibration_fields_unlock_after_camera_stops(window):
    window.render_current_frame()
    assert not window.reference_fret_spin.isEnabled()
    assert not window.video_label.pixmap().isNull()
    window.on_video_worker_finished()
    assert window.reference_fret_spin.isEnabled()
    assert window.tracking_panel.middle_fret_spin.isEnabled()
    assert window.video_label.current_image is None
    assert window.video_label.pixmap().isNull()
    assert window.video_label.text() == window.video_label.placeholder_text
    window.render_current_frame()
    assert window.video_label.pixmap().isNull()


def test_camera_stop_clears_preview_and_ignores_queued_frames(window):
    window.render_current_frame()
    last_frame = window.video_label.current_image
    worker = window.video_worker
    window.stop_video()
    worker.stop.assert_called_once()
    assert window.video_label.current_image is None
    window.update_frame(last_frame, Mock())
    assert window.video_label.current_image is None
    assert window.video_label.text() == window.video_label.placeholder_text
    window.on_video_worker_finished()
    window.update_frame(last_frame, Mock())
    assert window.video_label.current_image is None
    assert window.video_label.pixmap().isNull()


def show_practice(window):
    window.selection_stage = 'idle'
    window.stacked_widget.setCurrentWidget(window.practice_root)
    window.show()
    QApplication.processEvents()


def test_start_commits_latest_two_digit_number_without_enter_or_focus_change(window):
    show_practice(window)
    spin = window.reference_fret_spin
    spin.setFocus()
    spin.lineEdit().selectAll()
    QTest.keyClicks(spin.lineEdit(), '1')
    assert spin.value() == 7
    QTest.keyClicks(spin.lineEdit(), '2')
    assert spin.text() == '12'
    assert spin.value() == 7
    # Programmatic activation bypasses focus changes and still must commit.
    window.select_lines_button.click()
    assert spin.value() == 12
    assert window.selection_stage == 'await_frame'
    window.video_worker.begin_line_selection.assert_called_once()


@pytest.mark.parametrize('destination', ['field', 'label', 'button'])
def test_input_commits_when_clicking_outside_without_enter(window, destination):
    show_practice(window)
    spin = window.reference_fret_spin
    spin.setFocus()
    spin.lineEdit().selectAll()
    QTest.keyClicks(spin.lineEdit(), '12')
    target = {
        'field': window.tracking_panel.middle_fret_spin,
        'label': window.tracking_panel.hint_label,
        'button': window.select_lines_button,
    }[destination]
    QTest.mouseClick(target, Qt.MouseButton.LeftButton)
    assert spin.value() == 12
    if destination == 'button':
        assert window.selection_stage == 'await_frame'


def test_invalid_number_displays_inline_error_and_can_be_corrected(window):
    show_practice(window)
    panel = window.tracking_panel
    panel.middle_fret_spin.setValue(12)
    window.select_lines_button.click()
    assert panel.middle_error.isVisible()
    assert '작아야' in panel.middle_error.text()
    window.video_worker.begin_line_selection.assert_not_called()
    panel.middle_fret_spin.setValue(3)
    assert panel.middle_error.isHidden()
    window.select_lines_button.click()
    window.video_worker.begin_line_selection.assert_called_once()


def test_cleared_pending_input_blocks_start_instead_of_reusing_old_number(window):
    show_practice(window)
    spin = window.reference_fret_spin
    spin.setFocus()
    spin.lineEdit().selectAll()
    QTest.keyClick(spin.lineEdit(), Qt.Key.Key_Backspace)
    window.select_lines_button.click()
    assert spin.value() == 0
    assert window.tracking_panel.reference_error.isVisible()
    window.video_worker.begin_line_selection.assert_not_called()


def test_manual_fallback_reuses_frozen_frame_and_guides_each_endpoint(window):
    window.render_current_frame()
    panel = window.tracking_panel
    assert '인식되지' in panel.hint_label.text()
    panel.manual_points_checkbox.setChecked(True)
    assert '1/3' in panel.hint_label.text()
    assert '첫 번째' in panel.hint_label.text()
    window.handle_video_click(80, 50)
    assert '반대쪽' in panel.hint_label.text()
    window.handle_video_click(180, 50)
    assert '2/3' in panel.hint_label.text()
    window.handle_video_click(80, 170)
    window.handle_video_click(180, 170)
    assert '3/3' in panel.hint_label.text()
    window.handle_video_click(80, 310)
    window.handle_video_click(180, 310)
    window.video_worker.apply_calibrated_selection.assert_called_once()
    window.video_worker.begin_line_selection.assert_not_called()
    assert '적용' in panel.hint_label.text()
    window.clear_manual_tracking()
    assert panel.hint_label.text() == panel.READY_HINT
    assert window.selection_points == []
    assert panel.select_lines_button.isEnabled()


def test_selection_error_survives_frame_refresh(window):
    window.manual_point_selection = True
    window.handle_video_click(80, 50)
    window.handle_video_click(81, 50)
    message = window.tracking_panel.hint_label.text()
    assert '떨어뜨려' in message
    window.render_current_frame()
    assert window.tracking_panel.hint_label.text() == message


def test_help_is_keyboard_accessible_and_click_opens_persistent_popup(window):
    show_practice(window)
    help_button = window.tracking_panel.help_button
    help_button.setFocus()
    assert help_button.hasFocus()
    QTest.mouseClick(help_button, Qt.MouseButton.LeftButton)
    assert help_button.popup.isVisible()
    QTest.keyClick(help_button.popup, Qt.Key.Key_Escape)
    assert not help_button.popup.isVisible()


def test_step_buttons_use_pending_input_and_respect_limits(window):
    show_practice(window)
    spin = window.reference_fret_spin
    spin.setFocus()
    spin.lineEdit().selectAll()
    QTest.keyClicks(spin.lineEdit(), '12')
    QTest.mouseClick(spin.increase_button, Qt.MouseButton.LeftButton)
    assert spin.value() == 13
    QTest.mouseClick(spin.decrease_button, Qt.MouseButton.LeftButton)
    assert spin.value() == 12
    spin.setValue(24)
    assert not spin.increase_button.isEnabled()
    spin.setValue(0)
    assert not spin.decrease_button.isEnabled()
    QTest.mouseClick(spin.increase_button, Qt.MouseButton.LeftButton)
    assert spin.value() == 1
    spin.setFocus()
    QTest.keyClick(spin, Qt.Key.Key_Up)
    assert spin.value() == 2


def test_help_stays_inside_window_and_scrolls_when_window_is_short(window):
    host = QWidget()
    host.resize(400, 480)
    help_button = HelpButton('지판 맞춤', TrackingPanel.HELP_TEXT)
    help_button.setParent(host)
    help_button.move(370, 440)
    host.show()
    QApplication.processEvents()
    help_button.show_help()
    QApplication.processEvents()
    bounds = QRect(host.mapToGlobal(QPoint()), host.size())
    assert bounds.contains(help_button.popup.frameGeometry())
    host.resize(380, 220)
    QApplication.processEvents()
    bounds = QRect(host.mapToGlobal(QPoint()), host.size())
    assert bounds.contains(help_button.popup.frameGeometry())
    assert help_button.help_scroll.verticalScrollBar().maximum() > 0
    help_button.popup.close()
    host.close()
