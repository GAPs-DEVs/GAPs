"""Opt-in measurement of the normal app window; aggregate only after a run."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import platform
import sys
from time import monotonic
from zoneinfo import ZoneInfo

import cv2
from PySide6.QtCore import QEvent, QTimer, qVersion
from PySide6.QtGui import QKeySequence, QShortcut

from app.config import PROJECT_ROOT
from app.ui.main_window import MainWindow
from app.ui.benchmark_dialog import ScreenBenchmarkDialog
from benchmark.screen_metrics import ScreenPerformanceMonitor
from tools.performance_metadata import report_identity


class ScreenBenchmarkWindow(MainWindow):
    """Keep MainWindow's layout, startup flow, camera and rendering unchanged."""

    def __init__(self, *, duration=30, output_dir=None, label='', **kwargs):
        super().__init__(**kwargs)
        self.monitor = ScreenPerformanceMonitor()
        self.monitor.set_paused(True)
        self.output_dir = Path(output_dir or PROJECT_ROOT / 'benchmark' / 'results' / 'screen')
        self.run_label = label
        self._frame_sizes = set()
        self._run_metadata = {}
        self._original_title = self.windowTitle()
        self._closing = False
        self.last_report_directory = None
        self.benchmark_dialog = ScreenBenchmarkDialog(self, duration=duration)
        self.benchmark_dialog.start_button.clicked.connect(self.toggle_timed_measurement)
        self.benchmark_dialog.save_button.clicked.connect(self.save_report)
        self.duration_spin = self.benchmark_dialog.duration_spin
        self.save_status = self.benchmark_dialog.status_label
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(1000)
        self.refresh_timer.timeout.connect(self.refresh_metrics)
        self.video_label.installEventFilter(self)
        self.settings_shortcut = QShortcut(QKeySequence('F8'), self)
        self.settings_shortcut.activated.connect(self.show_benchmark_settings)
        self.start_shortcut = QShortcut(QKeySequence('F9'), self)
        self.start_shortcut.activated.connect(self.toggle_timed_measurement)
        # No dock, minimum video size, practice-page bypass or forced resize.

    def show_benchmark_settings(self):
        if self.monitor.timed_running:
            self.finish_timed_measurement('cancelled')
        self.benchmark_dialog.show()
        self.benchmark_dialog.raise_()

    def toggle_timed_measurement(self):
        if self.monitor.timed_running:
            self.finish_timed_measurement('cancelled')
            return
        if (self.video_worker is None or self._video_stopping or self.selection_stage != 'idle'
                or self.stacked_widget.currentWidget() is not self.practice_root):
            self.save_status.setText('연습 화면에서 카메라를 시작하고 지판 맞추기를 마친 뒤 측정하세요.')
            self.benchmark_dialog.show()
            return
        self.benchmark_dialog.hide()
        self._original_title = self.windowTitle()
        self._frame_sizes.clear()
        self.last_report_directory = None
        self._run_metadata = {
            'video_widget_size_at_start': [self.video_label.width(), self.video_label.height()],
            'window_size_at_start': [self.width(), self.height()],
            'device_pixel_ratio': self.video_label.devicePixelRatioF(),
        }
        self.monitor.start_timed(self.duration_spin.value(), monotonic())
        self.benchmark_dialog.save_button.setEnabled(False)
        self.save_status.setText('측정 중 · 종료 후 통계 계산 및 자동 저장')
        self.refresh_timer.start()
        self.refresh_metrics()

    def update_frame(self, image, stats):
        if self.monitor.timed_running and self.video_worker is not None and not self._video_stopping:
            if stats.tracking_mode == 'selection_ready':
                self.finish_timed_measurement('interrupted')
            elif self.monitor.receive(stats, monotonic()) is not None:
                self._frame_sizes.add((image.width(), image.height()))
        super().update_frame(image, stats)

    def eventFilter(self, watched, event):
        if watched is self.video_label and self.monitor.timed_running:
            if event.type() == QEvent.Type.Paint and not self._video_stopping:
                self.monitor.present(monotonic())
            elif event.type() == QEvent.Type.Resize:
                # A changed render area would invalidate the comparison.
                self.finish_timed_measurement('resized')
        return super().eventFilter(watched, event)

    def refresh_metrics(self):
        """Constant-time deadline/countdown check; never scan frame history."""
        if not self.monitor.timed_running:
            return
        now = monotonic()
        if now >= self.monitor.deadline:
            self.finish_timed_measurement('completed')
        elif self.video_worker is None or self._video_stopping or self.selection_stage != 'idle':
            self.finish_timed_measurement('interrupted')
        else:
            self.setWindowTitle(f'{self._original_title} · 측정 {self.monitor.deadline - now:.0f}초 남음 (F9 중단)')

    def finish_timed_measurement(self, reason):
        now = monotonic()
        if self.monitor.timed_running and now >= self.monitor.deadline:
            reason = 'completed'
        if not self.monitor.finish_timed(now, reason):
            return
        self.refresh_timer.stop()
        self.setWindowTitle(self._original_title)
        self._run_metadata['video_widget_size_at_end'] = [self.video_label.width(), self.video_label.height()]
        self.benchmark_dialog.save_button.setEnabled(True)
        self.save_report()
        if not self._closing:
            self.benchmark_dialog.show()

    def save_report(self):
        if self.monitor.timed_running or self.monitor.finished_at is None:
            return
        now = datetime.now(ZoneInfo('Asia/Seoul'))
        directory = self.output_dir / now.strftime('fps-%Y%m%d-%H%M%S-%f')
        try:
            self.monitor.export(directory, now=monotonic(), metadata={
                **report_identity(PROJECT_ROOT, self.model_path, self.score_path, self.camera_calibration),
                **self._run_metadata,
                'measurement_profile': 'main_window_deferred_summary_v1',
                'live_statistics': False, 'countdown_interval_ms': 1000,
                'label': self.run_label,
                'frame_sizes': [list(size) for size in sorted(self._frame_sizes)],
                'video_widget_size': self._run_metadata.get('video_widget_size_at_start'),
                'reverse_strings': self.reverse_checkbox.isChecked(),
                'reference_fret_index': self.reference_fret_spin.value(),
                'created_at': now.isoformat(), 'python': platform.python_version(),
                'opencv': cv2.__version__, 'qt': qVersion(),
                'camera_index': self.camera_combo.currentData(), 'model': str(self.model_path),
            })
            self.last_report_directory = directory
            import json
            report = json.loads((directory / 'summary.json').read_text(encoding='utf-8'))
            self.benchmark_dialog.show_result(report, directory)
        except OSError as exc:
            self.save_status.setText(f'저장 실패: {exc}\n결과 저장 버튼으로 재시도할 수 있습니다.')

    def stop_video(self):
        self.finish_timed_measurement('interrupted')
        super().stop_video()

    def on_video_worker_finished(self):
        self.finish_timed_measurement('interrupted')
        super().on_video_worker_finished()

    def begin_line_selection(self):
        super().begin_line_selection()
        if self.selection_stage != 'idle':
            self.finish_timed_measurement('interrupted')

    def closeEvent(self, event):
        self._closing = True
        self.finish_timed_measurement('interrupted')
        self.refresh_timer.stop()
        self.benchmark_dialog.close()
        super().closeEvent(event)


def parse_args(argv=None):
    from app.main import parse_args as app_args
    return app_args(['--benchmark-screen', *(sys.argv[1:] if argv is None else argv)])


def main(argv=None):
    from app.main import main as app_main
    return app_main(['--benchmark-screen', *(sys.argv[1:] if argv is None else argv)])


if __name__ == '__main__':
    raise SystemExit(main())
