"""Live diagnostics must count displayed images, not merely worker callbacks."""
import csv
import json

import pytest

from app.features.video import PracticeFrameStats
from tools.frame_performance import FramePerformanceMonitor


def stats(*, mode='selected_lines_motion', sequence=1, markers=1, expected=True, captured=10.):
    return PracticeFrameStats(
        fps=60., inference_ms=10., total_detections=0, class_counts={}, visible_fret_count=5,
        marker_count=markers, matched_marker_count=0, fingertip_count=0,
        hand_tracking_available=False, tracking_mode=mode,
        captured_at=captured, emitted_at=captured + .01, capture_sequence=sequence,
        capture_fps=30., ar_expected=expected, hand_mask_ms=3., tracking_ms=7.,
    )


def test_painted_fps_uses_elapsed_time_not_mean_of_instantaneous_worker_fps():
    monitor = FramePerformanceMonitor()
    for seq, at in enumerate((10., 10.033, 10.133), 1):
        monitor.receive(stats(sequence=seq, captured=at - .02), at - .001)
        monitor.present(at)
    summary = monitor.snapshot(10.14)['live']
    assert summary['paint_fps'] == pytest.approx(2 / .133)
    assert summary['long_frames'] == 1
    assert summary['processing_median_ms'] == 10.
    assert summary['capture_to_paint_p95_ms'] == pytest.approx(20.)


def test_multiple_worker_callbacks_before_one_paint_are_not_displayed_frames():
    monitor = FramePerformanceMonitor()
    monitor.receive(stats(sequence=1), 10.01)
    monitor.receive(stats(sequence=2), 10.02)
    assert monitor.present(10.03)
    assert not monitor.present(10.04), 'Window exposure/resize painted the same video image twice'
    summary = monitor.snapshot(10.05)['summary']
    assert summary['received_frames'] == 2 and summary['painted_frames'] == 1
    assert summary['paint_fps'] is None


def test_capture_skips_are_separate_from_ui_paints_and_restart_resets_sequence():
    monitor = FramePerformanceMonitor()
    monitor.receive(stats(sequence=4), 10.01)
    monitor.present(10.02)
    monitor.receive(stats(sequence=8), 10.04)
    monitor.present(10.05)
    assert monitor.snapshot(10.05)['summary']['skipped_capture_frames'] == 3
    monitor.receive(stats(sequence=1, captured=20.), 20.01)
    monitor.present(20.02)
    assert monitor.records[-1].skipped_capture_frames is None
    assert monitor.records[-1].paint_interval_ms is None


def test_calibration_freeze_and_measurement_pause_do_not_count_as_stalls():
    monitor = FramePerformanceMonitor()
    monitor.receive(stats(sequence=1), 10.01)
    monitor.present(10.02)
    assert monitor.receive(stats(mode='selection_ready', sequence=2), 10.03) is None
    assert not monitor.present(10.04)
    monitor.receive(stats(sequence=300, captured=20.), 20.01)
    monitor.present(20.02)
    assert monitor.records[-1].paint_interval_ms is None
    assert monitor.records[-1].skipped_capture_frames is None
    monitor.set_paused(True)
    assert monitor.receive(stats(sequence=301), 20.04) is None
    monitor.set_paused(False)
    monitor.receive(stats(sequence=600, captured=30.), 30.01)
    monitor.present(30.02)
    assert monitor.snapshot(30.03)['summary']['long_frames'] == 0


def test_ar_disappearance_counts_only_expected_visible_to_hidden_transitions():
    monitor = FramePerformanceMonitor()
    inputs = [
        stats(mode='calibration_required', sequence=1, markers=0, expected=False),
        stats(sequence=2),
        stats(mode='selected_lines_hold', sequence=3, markers=0),
        stats(mode='selected_lines_lost', sequence=4, markers=0),
        stats(mode='selected_lines_reacquired', sequence=5),
    ]
    for index, sample in enumerate(inputs):
        monitor.receive(sample, 10. + index * .1)
        monitor.present(10.01 + index * .1)
    report = monitor.snapshot(10.5)
    assert report['summary']['ar_visible_percent'] == 50.
    assert report['summary']['ar_disappearances'] == 1
    assert report['summary']['last_completed_ar_gap_ms'] == pytest.approx(200.)
    assert report['by_mode']['preview']['ar_visible_percent'] is None
    assert report['by_mode']['recovery']['painted_frames'] == 2


def test_complete_stall_does_not_keep_showing_last_healthy_fps():
    monitor = FramePerformanceMonitor()
    for sequence, at in enumerate((10., 10.033), 1):
        monitor.receive(stats(sequence=sequence), at)
        monitor.present(at)
    assert monitor.snapshot(10.1)['live']['paint_fps'] > 29.
    assert monitor.snapshot(11.)['live']['paint_fps'] == 0.
    assert monitor.snapshot(11.)['stalled']


def test_retention_clear_and_export_preserve_units_and_no_image_data(tmp_path):
    monitor = FramePerformanceMonitor(retention_seconds=2.)
    monitor.receive(stats(sequence=1), 10.01)
    monitor.present(10.02)
    monitor.receive(stats(sequence=2, captured=13.), 13.01)
    monitor.present(13.02)
    report_path, csv_path = monitor.export(tmp_path, now=13.03, metadata={'camera_index': 0})
    report = json.loads(report_path.read_text(encoding='utf-8'))
    assert report['summary']['received_frames'] == 1
    assert report['metadata']['camera_index'] == 0
    with csv_path.open(encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1 and rows[0]['capture_sequence'] == '2'
    assert float(rows[0]['capture_to_paint_ms']) == pytest.approx(20.)
    assert 'image' not in rows[0]
    monitor.clear()
    assert monitor.snapshot(13.1)['summary']['received_frames'] == 0
    assert not monitor.present(13.2)
