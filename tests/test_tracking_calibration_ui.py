"""Calibration regressions: real fret IDs must survive missing YOLO detections."""
import os
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace

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


def test_audio_error_message_is_not_replaced_by_empty_detection(window):
    window._render_audio_result({'status': 'error', 'practice_mode': 'single',
                                 'message': 'Microphone input failed: device unavailable'})
    assert window.practice_status_label.text() == '오류'
    assert 'device unavailable' in window.detected_label.text()


def test_audio_level_distinguishes_input_from_matching_notes(window):
    window.practice_running = True
    window.audio_controller.input_level_ready.emit(0.003)
    window._render_audio_result({'status': 'incorrect', 'rms': 0.003,
                                 'matched_notes': [], 'missing_notes': ['1번줄 7프렛']})
    assert window.practice_panel.input_level.value() > 0
    assert window.practice_panel.input_level.format() == '입력 감지'
    assert '목표음과 다름' in window.detected_label.text()
    # 지연된 무음 분석 결과가 최신 콜백의 입력 미터를 0으로 되돌리면 안 된다.
    window._render_audio_result({'status': 'no_signal', 'rms': 0.0})
    assert window.practice_panel.input_level.value() > 0
    window.audio_controller.input_level_ready.emit(0.0)
    assert window.practice_panel.input_level.value() == 0


def test_input_meter_updates_while_waiting_for_next_step(window):
    window.practice_running = True
    window._pending_advance_index = window.current_index
    window.audio_controller.input_level_ready.emit(0.02)
    assert window.practice_panel.input_level.value() > 0
    window.stop_practice()
    window.audio_controller.input_level_ready.emit(0.02)
    assert window.practice_panel.input_level.value() == 0


@pytest.mark.parametrize('mode', ['single', 'chord'])
def test_input_meter_receives_thread_signal_before_analysis_finishes(window, monkeypatch, mode):
    """실제 QThread 분석을 막아도 콜백→컨트롤러→화면의 입력 미터는 갱신된다."""
    import sys
    from threading import Event
    from unittest.mock import MagicMock
    import numpy as np
    from audio import audio_worker, chord_audio_worker
    from practice.score_steps import build_practice_steps

    notes = [{'step': 1, 'string': 1, 'fret': 0}]
    if mode == 'chord':
        notes.append({'step': 1, 'string': 6, 'fret': 0})
    window.practice_steps = build_practice_steps(notes)
    sd = Mock()
    sd.InputStream.return_value = MagicMock()
    monkeypatch.setitem(sys.modules, 'sounddevice', sd)
    entered, release = Event(), Event()

    def feed():
        worker = window.audio_controller.worker
        callback = sd.InputStream.call_args.kwargs['callback']
        block = np.full((worker.block_size, 1), 0.02, dtype=np.float32)
        callback(block, len(block), None, None)
        callback(block, len(block), None, None)

    def analyze(*args, **kwargs):
        entered.set()
        release.wait(3)
        window.audio_controller.worker._running = False
        if mode == 'single':
            return SimpleNamespace(frequency=None, rms=0., peak=0., clarity=0.)
        return SimpleNamespace(matches=(), rms=0., peak=0., noise_floor_db=-120.,
                               is_correct=False, status='no_signal', backend='librosa_cqt')

    sd.InputStream.return_value.__enter__.side_effect = feed
    module = audio_worker if mode == 'single' else chord_audio_worker
    name = 'estimate_pitch' if mode == 'single' else 'detect_expected_frequencies_cqt'
    monkeypatch.setattr(module, name, analyze)
    try:
        window.start_practice()
        assert entered.wait(2)
        QTest.qWait(50)
        assert not release.is_set()
        assert window.practice_panel.input_level.value() > 0
        assert window.practice_panel.input_level.format() == '입력 감지'
    finally:
        # 컨트롤러가 worker 참조를 지우기 전에 분석 종료를 기다려 안전하게 정리한다.
        worker = window.audio_controller.worker
        release.set()
        if worker is not None:
            worker.wait(2000)
        window.stop_practice()


def test_selected_input_sensitivity_reaches_practice_worker(window):
    window.audio_controller.start_for_step = Mock()
    window.practice_panel.input_sensitivity_combo.setCurrentIndex(1)
    window._start_audio_worker_for_current_step()
    assert window.audio_controller.start_for_step.call_args.kwargs['silence_threshold'] == 0.0005


def test_sensitivity_change_keeps_the_active_microphone_stream(window):
    window.practice_running = True
    window.audio_controller.worker = Mock()
    worker = window.audio_controller.worker
    window.practice_panel.input_sensitivity_combo.setCurrentIndex(1)
    assert window.audio_controller.worker is worker
    assert worker.silence_threshold == 0.0005
    worker.stop.assert_not_called()
    window.audio_controller.worker = None


@pytest.mark.parametrize('device_available', [True, False])
def test_microphone_refresh_reopens_stream_with_displayed_device(window, device_available):
    """목록 갱신 후 실제 스트림도 선택 표시와 같은 장치로 복구해야 한다."""
    window.microphone_combo.addItem('테스트 마이크', 18)
    window.microphone_combo.setCurrentIndex(1)
    window.audio_controller.start_for_step = Mock()
    window.practice_running = True
    window._pending_advance_index = window.current_index
    window.interactive_advance_timer.start(10000)
    devices = [('테스트 마이크', 18)] if device_available else []
    with patch('app.ui.main_window.enumerate_audio_input_devices', return_value=devices):
        window.refresh_microphone_button.click()
    selected = 18 if device_available else None
    assert window.current_microphone_device() == selected
    window.audio_controller.start_for_step.assert_called_once_with(
        window.current_step(), selected, silence_threshold=0.002,
    )
    assert window._pending_advance_index is None
    assert not window.interactive_advance_timer.isActive()


def test_microphone_refresh_does_not_start_audio_when_stopped_or_in_auto_mode(window):
    """연습 중지·자동 흐름 상태에서는 목록만 갱신하고 마이크를 열지 않는다."""
    window.audio_controller.start_for_step = Mock()
    with patch('app.ui.main_window.enumerate_audio_input_devices', return_value=[]):
        window.refresh_microphone_button.click()
        window.mode_combo.setCurrentIndex(1)
        window.practice_running = True
        window.refresh_microphone_button.click()
    window.audio_controller.start_for_step.assert_not_called()
