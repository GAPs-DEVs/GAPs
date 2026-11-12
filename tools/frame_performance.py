"""Pure metrics for the live camera tester, without owning a camera or a GUI."""
from __future__ import annotations

import csv
import json
from collections import deque
from dataclasses import asdict, dataclass, fields
from pathlib import Path

import numpy as np


MODE_LABELS = {
    'preview': '맞추기 전',
    'tracking': '정상 추적',
    'continuity': '흐림 보완',
    'recovery': '추적 상실',
    'weak_reference': '기준 영상 부족',
}


def mode_group(mode: str) -> str:
    if mode == 'selected_lines_continuity':
        return 'continuity'
    if mode in {'selected_lines_hold', 'selected_lines_lost'}:
        return 'recovery'
    if mode == 'selected_lines_reference_weak':
        return 'weak_reference'
    if mode.startswith('selected_lines_'):
        return 'tracking'
    return 'preview'


@dataclass
class FrameRecord:
    received_at: float
    mode: str
    group: str
    capture_fps: float | None
    processing_ms: float
    hand_mask_ms: float
    tracking_ms: float
    last_recovery_ms: float | None
    recovery_pending: bool
    marker_count: int
    ar_expected: bool
    captured_at: float | None
    capture_sequence: int
    skipped_capture_frames: int | None
    ui_queue_ms: float | None
    painted_at: float | None = None
    paint_interval_ms: float | None = None
    capture_to_paint_ms: float | None = None
    ar_disappearance: bool = False
    completed_ar_gap_ms: float | None = None
    tracking_detail: str = ''


def percentile(values, percent):
    return float(np.percentile(values, percent)) if values else None


def summarize(records, *, slow_frame_ms=50.):
    painted = [record for record in records if record.painted_at is not None]
    intervals = [record.paint_interval_ms for record in painted if record.paint_interval_ms is not None]
    eligible = [record for record in painted if record.ar_expected]
    processing = [record.processing_ms for record in records]
    latency = [record.capture_to_paint_ms for record in painted if record.capture_to_paint_ms is not None]
    queue = [record.ui_queue_ms for record in records if record.ui_queue_ms is not None]
    gaps = [record.completed_ar_gap_ms for record in painted if record.completed_ar_gap_ms is not None]
    return {
        'received_frames': len(records),
        'painted_frames': len(painted),
        'measured_interval_seconds': sum(intervals) / 1000,
        'measured_intervals': len(intervals),
        'paint_fps': 1000 * len(intervals) / sum(intervals) if intervals and sum(intervals) > 0 else None,
        'paint_interval_p95_ms': percentile(intervals, 95),
        'long_frames': sum(interval > slow_frame_ms for interval in intervals),
        'long_frame_percent': 100 * sum(interval > slow_frame_ms for interval in intervals) / len(intervals) if intervals else None,
        'processing_median_ms': percentile(processing, 50),
        'processing_p95_ms': percentile(processing, 95),
        'capture_to_paint_p95_ms': percentile(latency, 95),
        'ui_queue_p95_ms': percentile(queue, 95),
        'skipped_capture_frames': (sum(record.skipped_capture_frames or 0 for record in records)
                                   if any(record.capture_sequence > 0 for record in records) else None),
        'ar_visible_percent': 100 * sum(record.marker_count > 0 for record in eligible) / len(eligible) if eligible else None,
        'ar_disappearances': sum(record.ar_disappearance for record in painted),
        'last_completed_ar_gap_ms': gaps[-1] if gaps else None,
    }


class FramePerformanceMonitor:
    """Keep up to ten minutes of samples, with intentional freezes excluded.

    Presentation means a new video image reached a Qt paint event, not monitor
    refresh/scanout. Worker emissions overwritten before paint are not counted
    as displayed frames. Selection freezes and user pauses break intervals.
    """

    def __init__(self, *, retention_seconds=600., slow_frame_ms=50.) -> None:
        self.retention_seconds = retention_seconds
        self.slow_frame_ms = slow_frame_ms
        self.paused = False
        self.records: deque[FrameRecord] = deque()
        self.break_stream()

    def break_stream(self) -> None:
        self._pending = None
        self._last_painted = None
        self._last_capture = None
        self._ar_gap_started = None

    def clear(self) -> None:
        self.records.clear()
        self.break_stream()

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        self.break_stream()

    def _prune(self, now):
        while self.records and self.records[0].received_at < now - self.retention_seconds:
            self.records.popleft()

    def receive(self, stats, now: float) -> FrameRecord | None:
        self._prune(now)
        if self.paused or stats.tracking_mode == 'selection_ready':
            self.break_stream()
            return None
        skipped = None
        if self._last_capture is not None and stats.capture_sequence > 0:
            if stats.capture_sequence <= self._last_capture:
                self.break_stream()  # Camera restart / new sequence.
            else:
                skipped = stats.capture_sequence - self._last_capture - 1
        self._last_capture = stats.capture_sequence if stats.capture_sequence > 0 else None
        queue_ms = max(0., (now - stats.emitted_at) * 1000) if stats.emitted_at is not None else None
        record = FrameRecord(
            now, stats.tracking_mode, mode_group(stats.tracking_mode), stats.capture_fps,
            stats.inference_ms, stats.hand_mask_ms, stats.tracking_ms,
            stats.last_recovery_ms, stats.recovery_pending, stats.marker_count, stats.ar_expected,
            stats.captured_at, stats.capture_sequence, skipped, queue_ms,
            # Absent on the instrumented ``main`` baseline worker.
            tracking_detail=getattr(stats, 'tracking_detail', ''),
        )
        self.records.append(record)
        self._pending = record
        return record

    def present(self, now: float) -> bool:
        record = self._pending
        if self.paused or record is None:
            return False
        self._pending = None
        record.painted_at = now
        previous = self._last_painted
        if previous is not None and now > previous.painted_at:
            record.paint_interval_ms = (now - previous.painted_at) * 1000
        if record.captured_at is not None:
            record.capture_to_paint_ms = max(0., (now - record.captured_at) * 1000)
        if not record.ar_expected:
            self._ar_gap_started = None
        elif record.marker_count == 0:
            record.ar_disappearance = previous is not None and previous.ar_expected and previous.marker_count > 0
            if self._ar_gap_started is None:
                self._ar_gap_started = now
        elif self._ar_gap_started is not None:
            record.completed_ar_gap_ms = (now - self._ar_gap_started) * 1000
            self._ar_gap_started = None
        self._last_painted = record
        return True

    def snapshot(self, now: float):
        self._prune(now)
        records = list(self.records)
        recent = [record for record in records if record.received_at >= now - 5.]
        live = summarize(recent, slow_frame_ms=self.slow_frame_ms)
        # A stalled stream must not keep advertising its last healthy FPS.
        stalled = self._last_painted is not None and now - self._last_painted.painted_at > .5
        if stalled and not self.paused:
            live['paint_fps'] = 0.
        return {
            'paused': self.paused,
            'stalled': stalled,
            'retention_seconds': self.retention_seconds,
            'slow_frame_threshold_ms': self.slow_frame_ms,
            'live': live,
            'summary': summarize(records, slow_frame_ms=self.slow_frame_ms),
            'by_mode': {group: summarize([record for record in records if record.group == group],
                                         slow_frame_ms=self.slow_frame_ms) for group in MODE_LABELS},
        }

    def export(self, directory: Path, *, now: float, metadata=None) -> tuple[Path, Path]:
        report = self.snapshot(now)
        directory.mkdir(parents=True, exist_ok=True)
        summary_path, frames_path = directory / 'summary.json', directory / 'frames.csv'
        report['metadata'] = metadata or {}
        report['definitions'] = {
            'paint_fps': 'New video frames reaching Qt paint events; excludes deliberate selection/measurement pauses.',
            'skipped_capture_frames': 'Camera frames replaced in the latest-frame mailbox; not camera hardware drops.',
            'ar_visible_percent': 'Painted frames with >=1 marker / painted frames expecting an in-range practice note.',
            'completed_ar_gap_ms': 'Entire AR-hidden interval, including time the guitar is outside the image; not isolated reacquisition latency.',
            'capture_to_paint_ms': 'From VideoCapture.read return to Qt paint event; excludes exposure/device buffer and physical display scanout.',
        }
        with summary_path.open('w', encoding='utf-8') as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
        with frames_path.open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=[field.name for field in fields(FrameRecord)])
            writer.writeheader()
            writer.writerows(asdict(record) for record in self.records)
        return summary_path, frames_path
