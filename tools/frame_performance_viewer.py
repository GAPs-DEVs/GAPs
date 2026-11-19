"""Live FPS/AR diagnostic window using the production practice video pipeline.

Run: python -m tools.frame_performance_viewer --camera 0
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import platform
import sys
from time import monotonic
from zoneinfo import ZoneInfo

import cv2
from PySide6.QtCore import QEvent, Qt, QTimer, qVersion
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication, QDockWidget, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QPushButton, QSizePolicy, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from app.config import DEFAULT_SCORE_PATH, PROJECT_ROOT
from app.ui.main_window import MainWindow
from tools.frame_performance import FramePerformanceMonitor, MODE_LABELS, mode_group
from tools.performance_metadata import report_identity
from tools.session_recording import SessionRecorder
from tools.fretboard_detection_viewer import resolve_camera_calibration_path, resolve_model_path
from vision.camera_calibration import load_camera_calibration


def number(value, suffix='', digits=1):
    return '—' if value is None else f'{value:.{digits}f}{suffix}'


class FrameIntervalGraph(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.values = []
        self.setMinimumHeight(85)

    def set_values(self, values):
        self.values = values[-150:]
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#f4f7fb'))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        left, top, width, height = 48., 18., max(1., self.width() - 62.), max(1., self.height() - 28.)
        ceiling = max(100., min(250., max(self.values, default=100.)))
        for value in (33.3, 50.):
            y = top + height * (1 - value / ceiling)
            painter.setPen(QPen(QColor('#dc8b28' if value == 50 else '#b6c4d2'), 1, Qt.PenStyle.DashLine))
            painter.drawLine(int(left), int(y), int(left + width), int(y))
            painter.drawText(2, int(y + 4), f'{value:g}ms')
        if len(self.values) > 1:
            path = QPainterPath()
            for index, value in enumerate(self.values):
                x = left + width * index / (len(self.values) - 1)
                y = top + height * (1 - min(value, ceiling) / ceiling)
                path.moveTo(x, y) if index == 0 else path.lineTo(x, y)
            painter.setPen(QPen(QColor('#2478c7'), 2))
            painter.drawPath(path)
        painter.setPen(QColor('#536579'))
        painter.drawText(50, 13, '최근 프레임 간격 · 낮을수록 부드러움')
        painter.end()


class FramePerformanceWindow(MainWindow):
    def __init__(self, *, output_dir: Path = PROJECT_ROOT / 'artifacts' / 'performance', label: str = '',
                 record: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.run_label = label
        self.record = record
        self.recorder: SessionRecorder | None = None
        self.setWindowTitle('Guitar Practice · 실시간 프레임 테스트' + (f' · {label}' if label else ''))
        # Enter the actual practice screen directly, retaining its camera,
        # calibration and marker implementation. No second detector pipeline.
        self.stacked_widget.setCurrentWidget(self.practice_root)
        self.video_label.setMinimumSize(640, 360)
        self.video_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.monitor = FramePerformanceMonitor()
        self.output_dir = Path(output_dir)
        self._camera_live = False
        self._accept_after = 0.
        self._frame_sizes = set()
        self._build_monitor()
        self.video_label.installEventFilter(self)
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(500)
        self.refresh_timer.timeout.connect(self.refresh_metrics)
        self.refresh_timer.start()
        self.layout().activate()
        self.resize(1280, 900)

    def _build_monitor(self):
        dock = QDockWidget('실시간 성능 측정', self)
        dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable | QDockWidget.DockWidgetFeature.DockWidgetFloatable)
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(5)
        controls = QHBoxLayout()
        self.mode_label = QLabel('카메라 시작 버튼을 눌러주세요.')
        controls.addWidget(self.mode_label, 1)
        self.pause_button = QPushButton('측정 일시정지')
        self.pause_button.clicked.connect(self.toggle_measurement)
        controls.addWidget(self.pause_button)
        reset = QPushButton('통계 초기화')
        reset.clicked.connect(self.reset_metrics)
        controls.addWidget(reset)
        save = QPushButton('결과 저장')
        save.clicked.connect(self.save_report)
        controls.addWidget(save)
        layout.addLayout(controls)
        hint = QLabel('카메라 시작 → 맞추기 전 10초 → 지판 맞추기 → 손 가림·흔들기 → 화면 밖으로 뺐다가 복귀')
        hint.setWordWrap(True)
        layout.addWidget(hint)

        tabs = QTabWidget()
        live = QWidget()
        live_layout = QVBoxLayout(live)
        cards = QGridLayout()
        self.metric_labels = {}
        for index, (key, title) in enumerate((
            ('capture', '카메라 입력 FPS'), ('paint', '화면 갱신 FPS'), ('p95', '프레임 간격 p95'),
            ('long', '긴 프레임 >50ms'), ('latency', '입력→화면 p95'), ('ar', 'AR 표시율'),
        )):
            label = QLabel(f'{title}\n—')
            label.setStyleSheet('QLabel {background:#edf3f9; color:#163c5c; padding:6px; font-size:14px;}')
            cards.addWidget(label, 0, index)
            self.metric_labels[key] = (label, title)
        live_layout.addLayout(cards)
        self.detail_label = QLabel('카메라 대기')
        self.detail_label.setWordWrap(True)
        self.detail_label.setToolTip('입력 생략은 최신 프레임 처리 정책으로 건너뛴 수입니다. 카메라 하드웨어 손실을 뜻하지 않습니다.\nAR 미표시 시간에는 기타가 화면 밖에 있던 시간도 포함됩니다.')
        live_layout.addWidget(self.detail_label)
        self.graph = FrameIntervalGraph()
        live_layout.addWidget(self.graph, 1)
        tabs.addTab(live, '실시간 · 최근 5초')
        self.comparison = QTableWidget(len(MODE_LABELS), 6)
        self.comparison.setHorizontalHeaderLabels(['구간', '표시 프레임', '화면 FPS', '간격 p95', 'AR 표시율', 'AR 사라짐'])
        self.comparison.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.comparison.verticalHeader().hide()
        self.comparison.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tabs.addTab(self.comparison, '구간 비교 · 최근 10분')
        layout.addWidget(tabs)
        self.save_status = QLabel('지판 선택 정지·측정 일시정지는 통계에서 제외됩니다. 화면 FPS는 Qt 영상 다시그리기 기준입니다.')
        self.save_status.setWordWrap(True)
        self.save_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.save_status)
        dock.setWidget(panel)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)
        self.resizeDocks([dock], [290], Qt.Orientation.Vertical)

    def start_video(self):
        self.monitor.break_stream()
        self._accept_after = monotonic()
        super().start_video()
        self._camera_live = self.video_worker is not None
        if self.record and self.video_worker is not None and hasattr(self.video_worker, 'session_recorder'):
            self._finish_recording()
            stamp = datetime.now(ZoneInfo('Asia/Seoul')).strftime('recording-%Y%m%d-%H%M%S')
            self.recorder = SessionRecorder(self.output_dir / stamp)
            self.video_worker.session_recorder = self.recorder
            self.save_status.setText(f'녹화 중: {self.recorder.directory} · 카메라를 끄면 저장됩니다.')

    def _finish_recording(self):
        recorder, self.recorder = self.recorder, None
        if recorder is not None:
            directory = recorder.close()
            self.save_status.setText(f'녹화 저장됨: {directory} (누락 {recorder.dropped_frames}장)')

    def stop_video(self):
        self._camera_live = False
        self.monitor.break_stream()
        super().stop_video()

    def on_video_worker_finished(self):
        self._camera_live = False
        self.monitor.break_stream()
        # The worker has stopped, so no frame can still be handed to the recorder.
        self._finish_recording()
        super().on_video_worker_finished()

    def update_frame(self, image, stats):
        now = monotonic()
        if self._camera_live and (stats.captured_at is None or stats.captured_at >= self._accept_after):
            if self.monitor.receive(stats, now) is not None:
                self._frame_sizes.add((image.width(), image.height()))
        super().update_frame(image, stats)

    def eventFilter(self, watched, event):
        if watched is self.video_label and event.type() == QEvent.Type.Paint:
            self.monitor.present(monotonic())
        return super().eventFilter(watched, event)

    def toggle_measurement(self):
        self.monitor.set_paused(not self.monitor.paused)
        self._accept_after = monotonic()
        self.pause_button.setText('측정 재개' if self.monitor.paused else '측정 일시정지')
        self.refresh_metrics()

    def reset_metrics(self):
        self.monitor.clear()
        self._frame_sizes.clear()
        self._accept_after = monotonic()
        self.refresh_metrics()

    def refresh_metrics(self):
        now = monotonic()
        snapshot = self.monitor.snapshot(now)
        live = snapshot['live']
        last = self.monitor.records[-1] if self.monitor.records else None
        if self.monitor.paused:
            status = '측정 일시정지 · 카메라는 계속 실행됩니다.'
        elif not self._camera_live:
            status = '카메라 대기 · 시작 버튼을 눌러주세요.'
        elif self.selection_stage != 'idle':
            status = '지판 맞추기 중 · 선택 정지는 측정에서 제외'
        elif snapshot['stalled']:
            status = '새 영상 갱신 대기 중'
        else:
            status = MODE_LABELS.get(mode_group(last.mode), '준비 중') if last else '측정 준비 중'
            if last and last.recovery_pending:
                status += ' · 백그라운드 재탐색 중'
        self.mode_label.setText(status)
        active = self._camera_live and not self.monitor.paused and self.selection_stage == 'idle'
        capture_fps = last.capture_fps if last and active and now - last.received_at < .5 else None
        values = {
            'capture': number(capture_fps), 'paint': number(live['paint_fps'] if active else None),
            'p95': number(live['paint_interval_p95_ms'], 'ms'), 'long': str(live['long_frames']),
            'latency': number(live['capture_to_paint_p95_ms'], 'ms'), 'ar': number(live['ar_visible_percent'], '%'),
        }
        for key, (label, title) in self.metric_labels.items():
            label.setText(f'{title}\n{values[key]}')
        if last:
            self.detail_label.setText(
                f"처리 중앙값 {number(live['processing_median_ms'], 'ms')} · "
                f"손 가림 {last.hand_mask_ms:.1f}ms · 추적/그리기 {last.tracking_ms:.1f}ms · "
                f"최근 재탐색 {number(last.last_recovery_ms, 'ms')} · UI 대기 p95 {number(live['ui_queue_p95_ms'], 'ms')}\n"
                f"입력 생략 {number(live['skipped_capture_frames'], '장', digits=0)} · AR 사라짐 {live['ar_disappearances']}회 · "
                f"최근 AR 미표시 {number(live['last_completed_ar_gap_ms'], 'ms')}"
            )
        else:
            self.detail_label.setText('카메라 시작 후 측정됩니다. 지판 맞추기 전에는 AR 표시율을 계산하지 않습니다.')
        intervals = [record.paint_interval_ms for record in self.monitor.records
                     if record.paint_interval_ms is not None and record.received_at >= now - 5.]
        self.graph.set_values(intervals)
        for row, (group, title) in enumerate(MODE_LABELS.items()):
            summary = snapshot['by_mode'][group]
            cells = (title, str(summary['painted_frames']), number(summary['paint_fps']),
                     number(summary['paint_interval_p95_ms'], 'ms'), number(summary['ar_visible_percent'], '%'),
                     str(summary['ar_disappearances']))
            for column, value in enumerate(cells):
                self.comparison.setItem(row, column, QTableWidgetItem(value))

    def save_report(self):
        if not self.monitor.records:
            self.save_status.setText('저장할 측정값이 없습니다. 카메라를 시작해주세요.')
            return
        now = datetime.now(ZoneInfo('Asia/Seoul'))
        directory = self.output_dir / now.strftime('fps-%Y%m%d-%H%M%S-%f')
        self.monitor.break_stream()
        try:
            self.monitor.export(directory, now=monotonic(), metadata={
                **report_identity(PROJECT_ROOT, self.model_path, self.score_path, self.camera_calibration),
                'label': self.run_label,
                'frame_sizes': [list(size) for size in sorted(self._frame_sizes)],
                'reverse_strings': False,
                'string_margin_percent': self.tracking_panel.string_margin_slider.value(),
                'reference_fret_index': self.reference_fret_spin.value(),
                'created_at': now.isoformat(), 'python': platform.python_version(), 'opencv': cv2.__version__,
                'qt': qVersion(), 'camera_index': self.camera_combo.currentData(),
                'model': str(self.model_path), 'video_widget_size': [self.video_label.width(), self.video_label.height()],
            })
            self.save_status.setText(f'저장됨: {directory} (summary.json · frames.csv, 영상 저장 없음)')
        except OSError as exc:
            self.save_status.setText(f'저장 실패: {exc}')
        finally:
            # Saving is an intentional diagnostic pause, not a tracking stall.
            self._accept_after = monotonic()
            self.monitor.break_stream()

    def closeEvent(self, event):
        self.refresh_timer.stop()
        super().closeEvent(event)
        self._finish_recording()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description='Live camera FPS and AR continuity tester using the main app pipeline')
    parser.add_argument('--camera', type=int, default=0)
    parser.add_argument('--model', default=None)
    parser.add_argument('--score-json', type=Path, default=DEFAULT_SCORE_PATH)
    parser.add_argument('--camera-calibration', default=None)
    parser.add_argument('--output-dir', type=Path, default=PROJECT_ROOT / 'artifacts' / 'performance')
    parser.add_argument('--label', default='', help='Label saved with the commit and measurement results')
    parser.add_argument('--record', action='store_true',
                        help='Also save the tracked camera frames (roughly 5 MB per second) for tools.replay_session')
    return parser.parse_args(argv)


def main():
    args = parse_args()
    model_path = resolve_model_path(args.model)
    calibration_path = resolve_camera_calibration_path(args.camera_calibration)
    calibration = load_camera_calibration(calibration_path) if calibration_path else None
    application = QApplication(sys.argv)
    window = FramePerformanceWindow(
        model_path=model_path, camera_index=args.camera, score_path=args.score_json,
        camera_calibration=calibration, output_dir=args.output_dir, label=args.label, record=args.record,
    )
    window.show()
    # Dock layouts settle on first show; fit the combined view afterwards.
    application.processEvents()
    window.resize(1280, 900)
    return application.exec()


if __name__ == '__main__':
    raise SystemExit(main())
