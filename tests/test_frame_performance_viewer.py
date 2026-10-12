import os
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from test_frame_performance import stats
from tools.frame_performance_viewer import FramePerformanceWindow, parse_args


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    with patch('app.ui.main_window.enumerate_camera_indices', return_value=[]), patch('app.ui.main_window.enumerate_audio_input_devices', return_value=[]):
        widget = FramePerformanceWindow(model_path=Path('models/best06.pt'), output_dir=tmp_path)
    widget._camera_live = True
    widget.refresh_timer.stop()
    yield widget
    widget._camera_live = False
    widget.close()
    app.processEvents()


def test_tool_enters_practice_screen_and_paint_event_drives_measurement(window):
    image = QImage(340, 380, QImage.Format.Format_RGB888)
    image.fill(0)
    assert window.stacked_widget.currentWidget() is window.practice_root
    with patch('tools.frame_performance_viewer.monotonic', return_value=10.02):
        window.update_frame(image, stats())
        assert len(window.monitor.records) == 1
        assert window.monitor.records[-1].painted_at is None
        window.eventFilter(window.video_label, QEvent(QEvent.Type.Paint))
        window.eventFilter(window.video_label, QEvent(QEvent.Type.Paint))
        assert window.monitor.snapshot(10.03)['summary']['painted_frames'] == 1
        window.refresh_metrics()
    assert '30.0' in window.metric_labels['capture'][0].text()


def test_pause_resume_excludes_already_queued_frames_and_export_is_unique(window):
    image = QImage(340, 380, QImage.Format.Format_RGB888)
    image.fill(0)
    with patch('tools.frame_performance_viewer.monotonic', return_value=10.02):
        window.update_frame(image, stats())
        window.monitor.present(10.03)
        window.toggle_measurement()
    assert window.monitor.paused
    with patch('tools.frame_performance_viewer.monotonic', return_value=20.):
        window.toggle_measurement()
        window.update_frame(image, stats(sequence=2, captured=19.))
    assert len(window.monitor.records) == 1
    with patch('tools.frame_performance_viewer.monotonic', return_value=20.02):
        window.update_frame(image, stats(sequence=3, captured=20.))
        window.monitor.present(20.03)
        window.save_report()
        window.save_report()
    assert len(list(window.output_dir.glob('fps-*/summary.json'))) == 2
    assert len(list(window.output_dir.glob('fps-*/frames.csv'))) == 2


def test_cli_camera_and_output_options():
    args = parse_args(['--camera', '2', '--output-dir', '/tmp/guitar-performance'])
    assert args.camera == 2
    assert args.output_dir == Path('/tmp/guitar-performance')


def test_synchronous_baseline_has_no_independent_capture_fps(window):
    sample = stats()
    sample.capture_fps = None
    sample.capture_sequence = 0
    sample.last_recovery_ms = None
    image = QImage(640, 360, QImage.Format.Format_RGB888)
    image.fill(0)
    with patch('tools.frame_performance_viewer.monotonic', return_value=10.02):
        window.update_frame(image, sample)
        window.monitor.present(10.03)
        window.refresh_metrics()
        window.save_report()
    assert window.metric_labels['capture'][0].text().endswith('—')
    import json
    report = json.loads(next(window.output_dir.glob('fps-*/summary.json')).read_text())
    assert report['summary']['skipped_capture_frames'] is None
    assert report['metadata']['frame_sizes'] == [[640, 360]]
    assert report['metadata']['git_commit']
    assert report['metadata']['source_sha256']
