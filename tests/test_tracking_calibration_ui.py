"""Calibration regressions: real fret IDs must survive missing YOLO detections."""
import os
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage

from app.ui.main_window import MainWindow
from vision.fretboard_mapping import DetectedLine


@pytest.fixture
def window():
    application = QApplication.instance() or QApplication([])
    with patch('app.ui.main_window.enumerate_camera_indices', return_value=[]), patch('app.ui.main_window.enumerate_audio_input_devices', return_value=[]):
        widget = MainWindow(model_path=Path('models/best06.pt'))
    widget.reference_fret_spin.setValue(7)
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
    window.video_worker.frozen_selection_lines = (nut, seventh)
    window.handle_video_click(130, 50)
    window.handle_video_click(130, 310)
    assert window.reference_fret_spin.value() == 7
    window.video_worker.apply_line_selection.assert_called_once_with(nut, seventh, 7)


def test_four_point_calibration_works_without_any_detected_lines(window):
    window.manual_point_selection = True
    window.handle_video_click(80, 50)
    window.handle_video_click(180, 50)
    window.handle_video_click(80, 310)
    window.video_worker.apply_line_selection.assert_not_called()
    window.handle_video_click(180, 310)
    args = window.video_worker.apply_line_selection.call_args.args
    assert args[0].kind == 'nut'
    assert args[1].p0 == (80, 310)
    assert args[2] == 7
    assert window.selection_stage == 'idle'


def test_near_duplicate_endpoint_click_is_rejected(window):
    window.manual_point_selection = True
    window.handle_video_click(80, 50)
    window.handle_video_click(81, 50)
    assert window.selection_stage == 'select_nut'
    assert window.selection_points == []
    window.video_worker.apply_line_selection.assert_not_called()
