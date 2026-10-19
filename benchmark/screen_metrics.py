"""Screen-only metrics built on the production diagnostic frame recorder."""
from __future__ import annotations

import csv
import json
from math import isclose
from pathlib import Path

from tools.frame_performance import FramePerformanceMonitor, MODE_LABELS, percentile


CSV_FIELDS = (
    'received_at', 'mode', 'group', 'capture_fps', 'processing_ms',
    'captured_at', 'capture_sequence', 'skipped_capture_frames', 'ui_queue_ms',
    'painted_at', 'paint_interval_ms', 'capture_to_paint_ms',
    'hand_mask_ms', 'tracking_ms', 'last_recovery_ms', 'recovery_pending',
)
METRICS = (
    ('paint_fps', '화면 FPS', 'fps'),
    ('paint_interval_median_ms', '프레임 간격 중앙값', 'ms'),
    ('paint_interval_p95_ms', '프레임 간격 p95', 'ms'),
    ('paint_interval_p99_ms', '프레임 간격 p99', 'ms'),
    ('paint_interval_max_ms', '최대 프레임 간격', 'ms'),
    ('long_frame_percent', '긴 프레임 비율 (>50ms)', '%'),
    ('freeze_frames', '멈춤 간격 수 (>100ms)', '회'),
    ('capture_to_paint_median_ms', '입력→화면 중앙값', 'ms'),
    ('capture_to_paint_p95_ms', '입력→화면 p95', 'ms'),
    ('processing_p95_ms', '처리 시간 p95', 'ms'),
    ('ui_queue_p95_ms', 'UI 대기 p95', 'ms'),
)
DEFINITIONS = {
    'paint_fps': '1000 × 유효한 화면 갱신 간격 수 / 간격 합(ms). 순간 FPS의 평균이 아님.',
    'paint_interval': '서로 인접한 새 영상의 Qt paint 이벤트 진입 시각 차이. 같은 영상 재그리기는 제외.',
    'long_frame_percent': '50ms를 초과한 유효 간격 수 / 전체 유효 간격 수 × 100.',
    'freeze_frames': '100ms를 초과한 유효 간격 수. 50/100ms는 진단용 기준이며 합격 기준이 아님.',
    'capture_to_paint': '카메라 read 반환부터 Qt paint 이벤트 진입까지. 노출·장치 버퍼·실제 화면 출력·paint 실행 시간 제외.',
    'processing': '수신한 각 영상의 워커 처리 시간. 표시되지 않은 영상도 포함.',
    'ui_queue': '워커 emit부터 UI 수신까지. 표시되지 않은 영상도 포함.',
    'interval_scope': '선택·일시정지·재시작 구간 제외. 보관 범위 밖 첫 간격과 구간별 집계의 상태 전환 간격 제외.',
    'unfinished_stall': '마지막 paint 이후 아직 끝나지 않은 정지는 간격 통계에 포함되지 않음. pending_paint_gap_ms를 별도로 확인.',
    'empty': '표본이 부족한 값은 null. 지연 표본 수와 간격 표본 수는 다를 수 있음.',
}


def summarize_screen(records):
    records = list(records)
    painted = [r for r in records if r.painted_at is not None]
    # Do not pull in an interval from outside a window or across a mode filter.
    intervals = [current.paint_interval_ms for previous, current in zip(painted, painted[1:])
                 if current.paint_interval_ms is not None
                 and isclose(current.paint_interval_ms,
                             (current.painted_at - previous.painted_at) * 1000,
                             rel_tol=1e-8, abs_tol=1e-6)]
    latency = [r.capture_to_paint_ms for r in painted if r.capture_to_paint_ms is not None]
    queue = [r.ui_queue_ms for r in records if r.ui_queue_ms is not None]
    total = sum(intervals)
    # Subtracting monotonic timestamps can put an exact boundary a fraction
    # of a microsecond above 50/100 ms. Do not count that as a slow frame.
    def exceeds(value, threshold):
        return value > threshold and not isclose(value, threshold, rel_tol=0., abs_tol=1e-6)

    long_frames = sum(exceeds(value, 50) for value in intervals)
    return {
        'received_frames': len(records), 'painted_frames': len(painted),
        'measured_intervals': len(intervals), 'measured_interval_seconds': total / 1000,
        'latency_samples': len(latency),
        'paint_fps': 1000 * len(intervals) / total if total > 0 else None,
        'paint_interval_median_ms': percentile(intervals, 50),
        'paint_interval_p95_ms': percentile(intervals, 95),
        'paint_interval_p99_ms': percentile(intervals, 99),
        'paint_interval_max_ms': max(intervals, default=None),
        'long_frames': long_frames,
        'long_frame_percent': 100 * long_frames / len(intervals) if intervals else None,
        'freeze_frames': sum(exceeds(value, 100) for value in intervals),
        'capture_to_paint_median_ms': percentile(latency, 50),
        'capture_to_paint_p95_ms': percentile(latency, 95),
        'processing_p95_ms': percentile([r.processing_ms for r in records], 95),
        'ui_queue_p95_ms': percentile(queue, 95),
    }


def format_value(value):
    return '—' if value is None else f'{value:.2f}'


class ScreenPerformanceMonitor(FramePerformanceMonitor):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._reset_timed_run()

    def _reset_timed_run(self):
        self.started_at = None
        self.deadline = None
        self.finished_at = None
        self.finish_reason = None
        self._finished_gap_ms = None

    @property
    def timed_running(self):
        return self.deadline is not None and self.finished_at is None

    def clear(self):
        super().clear()
        self._reset_timed_run()

    def start_timed(self, seconds, now):
        if not 1 <= seconds <= self.retention_seconds:
            raise ValueError('측정 시간은 1초부터 보관 시간 이내여야 합니다.')
        self.clear()
        self.set_paused(False)
        self.started_at = now
        self.deadline = now + seconds

    def finish_timed(self, now, reason='completed'):
        if not self.timed_running:
            return False
        self.finished_at = max(self.started_at, min(now, self.deadline))
        self.finish_reason = reason
        if self._last_painted is not None:
            self._finished_gap_ms = max(0., (self.finished_at - self._last_painted.painted_at) * 1000)
        self.set_paused(True)
        return True

    def receive(self, stats, now):
        if self.timed_running and (now >= self.deadline or
                                  (stats.captured_at is not None and stats.captured_at < self.started_at)):
            return None
        # Keep a completed run available even when the camera keeps streaming.
        if self.finished_at is not None:
            return None
        return super().receive(stats, now)

    def present(self, now):
        if self.timed_running and now >= self.deadline:
            return False
        return super().present(now)

    def screen_snapshot(self, now):
        if self.finished_at is not None:
            now = self.finished_at
        self._prune(now)
        records = list(self.records)
        last = self._last_painted
        gap = max(0., (now - last.painted_at) * 1000) if last is not None and not self.paused else None
        if self.finished_at is not None:
            gap = self._finished_gap_ms
        live = summarize_screen([r for r in records if r.received_at >= now - 5])
        if gap is not None and gap > 500:
            live['paint_fps'] = 0.
        return {
            'schema_version': 1, 'benchmark': 'screen', 'paused': self.paused,
            'retention_seconds': self.retention_seconds,
            'timed_run': None if self.deadline is None else {
                'requested_seconds': self.deadline - self.started_at,
                'elapsed_seconds': max(0., min(now, self.deadline) - self.started_at),
                'status': self.finish_reason or 'running',
                'clock': 'wall time (monotonic)',
            },
            'pending_paint_gap_ms': gap,
            'stalled': gap is not None and gap > 500,
            'live': live, 'summary': summarize_screen(records),
            'by_mode': {group: summarize_screen([r for r in records if r.group == group])
                        for group in MODE_LABELS},
            'definitions': DEFINITIONS,
        }

    def export(self, directory: Path, *, now: float, metadata=None):
        report = self.screen_snapshot(now)
        report['metadata'] = metadata or {}
        directory.mkdir(parents=True, exist_ok=True)
        summary_path, frames_path = directory / 'summary.json', directory / 'frames.csv'
        summary_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        with frames_path.open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows({key: getattr(record, key) for key in CSV_FIELDS} for record in self.records)
        lines = ['# 화면 성능 평가', '',
                 f"- 수신/표시 프레임: {report['summary']['received_frames']} / {report['summary']['painted_frames']}",
                 f"- 유효 간격 표본: {report['summary']['measured_intervals']}",
                 f"- 지연 표본: {report['summary']['latency_samples']}",
                 f"- 마지막 표시 이후 경과: {format_value(report['pending_paint_gap_ms'])} ms", '',
                 '| 지표 | 전체 | ' + ' | '.join(MODE_LABELS.values()) + ' |',
                 '|---|' + '---:|' * (1 + len(MODE_LABELS))]
        if report['timed_run'] is not None:
            run = report['timed_run']
            lines[2:2] = [f"- 지정 시간 / 경과 시간: {run['requested_seconds']:.1f} / {run['elapsed_seconds']:.1f} 초",
                          f"- 측정 종료 상태: {run['status']}"]
        for key, title, unit in METRICS:
            values = [report['summary'][key]] + [report['by_mode'][group][key] for group in MODE_LABELS]
            lines.append(f'| {title} ({unit}) | ' + ' | '.join(format_value(v) for v in values) + ' |')
        lines.extend(['', '## 구간별 표본 수', '', '| 구간 | 표시 프레임 | 유효 간격 | 지연 표본 |', '|---|---:|---:|---:|'])
        for group, title in MODE_LABELS.items():
            sample = report['by_mode'][group]
            lines.append(f"| {title} | {sample['painted_frames']} | {sample['measured_intervals']} | {sample['latency_samples']} |")
        lines.extend(['', '## 측정 정의', ''] + [f'- {value}' for value in DEFINITIONS.values()])
        (directory / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
        return summary_path, frames_path
