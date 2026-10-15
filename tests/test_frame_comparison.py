import json

import pytest

from tools.compare_frame_performance import compare_reports, load_report
from tools.performance_metadata import source_hash
from tools.run_frame_performance import instrument_baseline


def report(fps=20.):
    summary = {'paint_fps': fps, 'paint_interval_p95_ms': 60., 'ar_visible_percent': 90.}
    return {'summary': summary, 'by_mode': {'tracking': summary.copy()}, 'metadata': {
        'label': 'main', 'git_commit': '123456789abcdef', 'measurement_version': 1,
        'camera_index': 0, 'model_sha256': 'model', 'score_sha256': 'score',
        'calibration': None, 'video_widget_size': [780, 360], 'frame_sizes': [[1280, 720]],
        'reverse_strings': False, 'reference_fret_index': 12,
        'python': '3.13.9', 'opencv': '4.13.0', 'qt': '6.11.0',
    }}


def test_comparison_uses_same_group_and_relative_fps_change():
    old, new = report(), report(25.)
    new['summary']['ar_visible_percent'] = 95.
    output = compare_reports(old, new)
    assert '| 정상 추적 | 20.0 | 25.0 | +25.0%' in output
    assert '| AR 표시율(%, 변화는 %p) | 90.0 | 95.0 | +5.0 |' in output
    assert '| 흐림 보완 | — | — | —' in output
    assert '조건 불일치:' not in output
    assert '확인할 수 없는 조건:' not in output


def test_comparison_reports_condition_mismatch_and_old_metadata():
    old, new = report(), report()
    new['metadata']['frame_sizes'] = [[640, 480]]
    del new['metadata']['model_sha256']
    output = compare_reports(old, new)
    assert '조건 불일치: 입력 영상 크기' in output
    assert '기록이 없어 확인할 수 없는 조건: 모델' in output


def test_zero_and_missing_metrics_are_not_false_improvements():
    old, new = report(0.), report(None)
    output = compare_reports(old, new)
    assert '| 정상 추적 | 0.0 | — | —' in output
    assert 'nan' not in output and 'inf' not in output


def test_report_loader_accepts_directory_or_json_and_rejects_other_json(tmp_path):
    path = tmp_path / 'summary.json'
    path.write_text(json.dumps(report()))
    assert load_report(path) == load_report(tmp_path)
    path.write_text('{}')
    with pytest.raises(ValueError, match='Not a frame performance report'):
        load_report(path)


def test_baseline_patch_refuses_unknown_worker_instead_of_guessing():
    with pytest.raises(ValueError, match='video worker has changed'):
        instrument_baseline('class OtherWorker: pass')


def test_source_identity_includes_uncommitted_and_untracked_python(tmp_path):
    (tmp_path / 'vision').mkdir()
    source = tmp_path / 'vision' / 'tracker.py'
    source.write_text('x = 1\n')
    before = source_hash(tmp_path)
    source.write_text('x = 2\n')
    assert source_hash(tmp_path) != before
    before = source_hash(tmp_path)
    (tmp_path / 'vision' / 'new.py').write_text('x = 3\n')
    assert source_hash(tmp_path) != before
