"""Calibration regressions: real fret IDs must survive missing YOLO detections."""
import os
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from app.ui.main_window import MainWindow
from app.ui.tracking_panel import TrackingPanel
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
    # 원근 변환에서 발생하는 부동소수점 반올림 오차는 허용하되,
    # 실제 선택한 끝 프렛의 좌표가 보존되는지는 계속 검사한다.
    assert args[0].ordered_fret_lines[-1].p0 == pytest.approx((80, 310))
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
    window.on_video_worker_finished()
    assert window.reference_fret_spin.isEnabled()
    assert window.tracking_panel.middle_fret_spin.isEnabled()
