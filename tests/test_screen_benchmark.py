import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from benchmark.screen_metrics import ScreenPerformanceMonitor, summarize_screen
from test_frame_performance import stats


def feed(monitor, times, modes=None):
    for sequence, at in enumerate(times, 1):
        mode = modes[sequence - 1] if modes else 'selected_lines_motion'
        monitor.receive(stats(sequence=sequence, captured=at - .02, mode=mode), at - .001)
        monitor.present(at)


def test_screen_metrics_elapsed_fps_percentiles_and_latency():
    monitor = ScreenPerformanceMonitor()
    feed(monitor, [10., 10.04, 10.10, 10.30])
    summary = monitor.screen_snapshot(10.31)['summary']
    assert summary['paint_fps'] == pytest.approx(10.)
    assert summary['paint_interval_p95_ms'] == pytest.approx(186.)
    assert summary['paint_interval_p99_ms'] == pytest.approx(197.2)
    assert summary['paint_interval_max_ms'] == pytest.approx(200.)
    assert summary['long_frame_percent'] == pytest.approx(200 / 3)
    assert summary['freeze_frames'] == 1
    assert summary['latency_samples'] == 4
    assert summary['capture_to_paint_median_ms'] == pytest.approx(20.)


def test_filtered_modes_and_retention_do_not_include_outside_intervals():
    monitor = ScreenPerformanceMonitor(retention_seconds=1)
    feed(monitor, [10., 10.1, 10.2, 10.4], [
        'selected_lines_motion', 'selected_lines_lost',
        'selected_lines_motion', 'selected_lines_motion',
    ])
    report = monitor.screen_snapshot(10.5)
    assert report['summary']['measured_intervals'] == 3
    assert report['by_mode']['tracking']['measured_intervals'] == 1
    assert report['by_mode']['tracking']['paint_fps'] == pytest.approx(5.)
    assert report['by_mode']['recovery']['paint_fps'] is None
    assert monitor.screen_snapshot(11.25)['summary']['measured_intervals'] == 0


def test_overwritten_frame_repaint_and_pause_do_not_inflate_counts():
    monitor = ScreenPerformanceMonitor()
    monitor.receive(stats(sequence=1), 10.01)
    monitor.receive(stats(sequence=2), 10.02)
    assert monitor.present(10.03)
    assert not monitor.present(10.04)
    monitor.set_paused(True)
    monitor.set_paused(False)
    monitor.receive(stats(sequence=3, captured=20), 20.01)
    monitor.present(20.02)
    result = monitor.screen_snapshot(20.03)['summary']
    assert result['received_frames'] == 3
    assert result['painted_frames'] == 2
    assert result['measured_intervals'] == 0
    assert result['paint_fps'] is None


def test_export_reports_open_stall_and_contains_screen_data_only(tmp_path):
    monitor = ScreenPerformanceMonitor()
    feed(monitor, [10, 10.04])
    summary_path, csv_path = monitor.export(tmp_path, now=11, metadata={'label': 'test'})
    report = json.loads(summary_path.read_text())
    assert report['stalled']
    assert report['live']['paint_fps'] == 0
    assert report['summary']['paint_fps'] == pytest.approx(25.)
    assert report['pending_paint_gap_ms'] == pytest.approx(960)
    assert report['metadata']['label'] == 'test'
    assert 'ar_visible_percent' not in summary_path.read_text()
    assert 'marker_count' not in csv_path.read_text(encoding='utf-8-sig')
    assert '화면 FPS' in (tmp_path / 'report.md').read_text()


def test_empty_missing_timestamps_and_exact_thresholds():
    assert summarize_screen([])['paint_fps'] is None
    monitor = ScreenPerformanceMonitor()
    feed(monitor, [10, 10.05, 10.15])
    records = list(monitor.records)
    records[1] = replace(records[1], capture_to_paint_ms=None)
    records[2] = replace(records[2], capture_to_paint_ms=None)
    result = summarize_screen(records)
    assert result['long_frames'] == 1
    assert result['freeze_frames'] == 0
    assert result['latency_samples'] == 1


def test_timed_run_rejects_prestart_and_late_frames_and_freezes_result(tmp_path):
    monitor = ScreenPerformanceMonitor()
    feed(monitor, [1, 1.1])
    monitor.start_timed(2, now=10)
    assert not monitor.records
    assert monitor.receive(stats(captured=9.9), 10.01) is None
    feed(monitor, [10.1, 10.2])
    monitor.receive(stats(sequence=3, captured=11.98), 11.99)
    assert not monitor.present(12), 'A pending frame painted at the deadline must be excluded'
    assert monitor.receive(stats(sequence=4, captured=12), 12.01) is None
    assert monitor.finish_timed(13)
    report_path, _ = monitor.export(tmp_path, now=2000)
    report = json.loads(report_path.read_text())
    assert report['timed_run']['requested_seconds'] == 2
    assert report['timed_run']['elapsed_seconds'] == 2
    assert report['timed_run']['status'] == 'completed'
    assert report['summary']['painted_frames'] == 2
    assert report['summary']['received_frames'] == 3
    assert report['pending_paint_gap_ms'] == pytest.approx(1800)
    assert monitor.receive(stats(sequence=5, captured=3000), 3000) is None
    assert monitor.screen_snapshot(4000)['summary'] == report['summary']
    assert not monitor.finish_timed(4000)


def test_timed_run_cancel_and_restart():
    monitor = ScreenPerformanceMonitor()
    monitor.start_timed(30, now=10)
    feed(monitor, [10.1, 10.2])
    monitor.finish_timed(15, reason='cancelled')
    report = monitor.screen_snapshot(20)
    assert report['timed_run']['elapsed_seconds'] == 5
    assert report['timed_run']['status'] == 'cancelled'
    monitor.start_timed(10, now=25)
    assert not monitor.paused and not monitor.records
    assert monitor.finished_at is None and monitor.deadline == 35
    for seconds in (0, -1, 601):
        with pytest.raises(ValueError):
            monitor.start_timed(seconds, now=25)


@pytest.fixture
def timed_window(tmp_path, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from benchmark.screen import ScreenBenchmarkWindow

    app = QApplication.instance() or QApplication([])
    with patch('app.ui.main_window.enumerate_camera_indices', return_value=[]), patch('app.ui.main_window.enumerate_audio_input_devices', return_value=[]):
        window = ScreenBenchmarkWindow(model_path=Path('models/best06.pt'), output_dir=tmp_path, duration=2)
    window.show()
    app.processEvents()
    yield window
    window.close()
    app.processEvents()


def prepare_camera(window):
    from unittest.mock import Mock
    window.stacked_widget.setCurrentWidget(window.practice_root)
    window.video_worker = Mock()
    from PySide6.QtWidgets import QApplication
    QApplication.instance().processEvents()


def test_benchmark_preserves_normal_app_layout_and_startup(timed_window):
    from app.ui.main_window import MainWindow
    from PySide6.QtWidgets import QDockWidget, QApplication
    with patch('app.ui.main_window.enumerate_camera_indices', return_value=[]), patch('app.ui.main_window.enumerate_audio_input_devices', return_value=[]):
        plain = MainWindow(model_path=Path('models/best06.pt'))
    try:
        plain.show()
        QApplication.instance().processEvents()
        assert timed_window.stacked_widget.currentIndex() == plain.stacked_widget.currentIndex()
        assert timed_window.size() == plain.size()
        assert timed_window.video_label.minimumSize() == plain.video_label.minimumSize()
        plain.stacked_widget.setCurrentWidget(plain.practice_root)
        timed_window.stacked_widget.setCurrentWidget(timed_window.practice_root)
        QApplication.instance().processEvents()
        assert timed_window.video_label.size() == plain.video_label.size()
        assert not timed_window.findChildren(QDockWidget)
        assert not timed_window.refresh_timer.isActive()
    finally:
        plain.close()


def test_recording_never_aggregates_or_writes_and_uses_normal_rendering(timed_window):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QImage
    from app.ui.main_window import MainWindow
    window = timed_window
    prepare_camera(window)
    image = QImage(640, 360, QImage.Format.Format_RGB888)
    image.fill(0)
    with patch('benchmark.screen.monotonic', return_value=10):
        window.toggle_timed_measurement()
    assert not window.benchmark_dialog.isVisible()
    with patch.object(window.monitor, 'screen_snapshot', side_effect=AssertionError('Aggregated during recording')), patch.object(window.monitor, 'export', side_effect=AssertionError('Exported during recording')):
        with patch('benchmark.screen.monotonic', return_value=10.03), patch.object(MainWindow, 'update_frame', autospec=True, wraps=MainWindow.update_frame) as render:
            window.update_frame(image, stats(captured=10.01))
            render.assert_called_once_with(window, image, stats(captured=10.01))
            window.eventFilter(window.video_label, QEvent(QEvent.Type.Paint))
            window.eventFilter(window.video_label, QEvent(QEvent.Type.Paint))
        with patch('benchmark.screen.monotonic', return_value=11):
            window.refresh_metrics()
        assert len(window.monitor.records) == 1
        assert window.monitor.records[0].painted_at == 10.03
        assert not list(window.output_dir.glob('fps-*'))
    with patch('benchmark.screen.monotonic', return_value=12.5):
        window.refresh_metrics()
        window.refresh_metrics()
    reports = list(window.output_dir.glob('fps-*/summary.json'))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text())
    assert report['timed_run']['elapsed_seconds'] == 2
    assert report['summary']['painted_frames'] == 1
    assert report['metadata']['measurement_profile'] == 'main_window_deferred_summary_v1'
    assert report['metadata']['live_statistics'] is False
    assert report['metadata']['frame_sizes'] == [[640, 360]]
    assert report['metadata']['video_widget_size_at_start'] == report['metadata']['video_widget_size_at_end']
    assert not window.refresh_timer.isActive()
    with patch.object(window.monitor, 'screen_snapshot', side_effect=AssertionError('Repeated summary')):
        window.refresh_metrics()


def test_timed_window_requires_camera_and_saves_empty_interrupted_run(timed_window):
    window = timed_window
    window.toggle_timed_measurement()
    assert not window.monitor.timed_running
    assert '카메라를 시작' in window.save_status.text()
    prepare_camera(window)
    with patch('benchmark.screen.monotonic', return_value=10):
        window.toggle_timed_measurement()
    with patch('benchmark.screen.monotonic', return_value=10.5):
        window.stop_video()
    report = json.loads(next(window.output_dir.glob('fps-*/summary.json')).read_text())
    assert report['timed_run']['status'] == 'interrupted'
    assert report['timed_run']['elapsed_seconds'] == .5
    assert report['summary']['paint_fps'] is None


def test_resize_interrupts_measurement_and_restart_clears_records(timed_window):
    from PySide6.QtCore import QEvent
    window = timed_window
    prepare_camera(window)
    with patch('benchmark.screen.monotonic', return_value=10):
        window.toggle_timed_measurement()
    feed(window.monitor, [10.1, 10.2])
    with patch('benchmark.screen.monotonic', return_value=11):
        window.eventFilter(window.video_label, QEvent(QEvent.Type.Resize))
    report = json.loads(next(window.output_dir.glob('fps-*/summary.json')).read_text())
    assert report['timed_run']['status'] == 'resized'
    with patch('benchmark.screen.monotonic', return_value=20):
        window.toggle_timed_measurement()
    assert not window.monitor.records
    assert window.monitor.deadline == 22
    with patch('benchmark.screen.monotonic', return_value=21):
        window.toggle_timed_measurement()
    assert window.monitor.finish_reason == 'cancelled'


def test_duration_cli_and_main_app_share_arguments():
    from benchmark.screen import parse_args
    from app.main import parse_args as app_args
    assert not app_args([]).benchmark_screen
    assert parse_args([]).benchmark_screen
    assert parse_args([]).duration == 30
    assert parse_args(['--duration', '60']) == app_args(['--benchmark-screen', '--duration', '60'])
    for value in ('0', '-5', '601', '1.5'):
        with pytest.raises(SystemExit):
            parse_args(['--duration', value])
