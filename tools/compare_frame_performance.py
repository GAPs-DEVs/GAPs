"""Compare two saved live-camera reports (baseline first, candidate second)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.frame_performance import MODE_LABELS


CONDITIONS = {
    'measurement_version': '측정 도구 버전', 'camera_index': '카메라 번호',
    'model_sha256': '모델', 'score_sha256': '악보', 'calibration': '카메라 보정',
    'video_widget_size': '영상 위젯 크기', 'frame_sizes': '입력 영상 크기',
    'reverse_strings': '줄 방향', 'reference_fret_index': '끝 기준 프렛',
    'python': 'Python', 'opencv': 'OpenCV', 'qt': 'Qt',
}


def load_report(path):
    path = Path(path)
    if path.is_dir():
        path /= 'summary.json'
    report = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(report.get('summary'), dict) or not isinstance(report.get('by_mode'), dict):
        raise ValueError(f'Not a frame performance report: {path}')
    return report


def value(number, digits=1):
    return '—' if number is None else f'{number:.{digits}f}'


def change(before, after, *, percent=False):
    if before is None or after is None or (percent and before == 0):
        return '—'
    return f'{(after / before - 1) * 100:+.1f}%' if percent else f'{after - before:+.1f}'


def describe(report):
    metadata = report.get('metadata', {})
    return f"{metadata.get('label') or '이름 없음'} · {(metadata.get('git_commit') or '커밋 미기록')[:12]} · {metadata.get('created_at', '시각 미기록')}"


def compare_reports(baseline, candidate):
    lines = [f'기준: {describe(baseline)}', f'비교: {describe(candidate)}', '',
             '| 구간 | 기준 FPS | 비교 FPS | FPS 변화 | 기준 간격 p95(ms) | 비교 간격 p95(ms) |',
             '|---|---:|---:|---:|---:|---:|']
    for group, title in [('summary', '전체'), *MODE_LABELS.items()]:
        old = baseline['summary'] if group == 'summary' else baseline['by_mode'].get(group, {})
        new = candidate['summary'] if group == 'summary' else candidate['by_mode'].get(group, {})
        fps_old, fps_new = old.get('paint_fps'), new.get('paint_fps')
        lines.append(f"| {title} | {value(fps_old)} | {value(fps_new)} | {change(fps_old, fps_new, percent=True)} | "
                     f"{value(old.get('paint_interval_p95_ms'))} | {value(new.get('paint_interval_p95_ms'))} |")
    lines.extend(['', '| 전체 지표 | 기준 | 비교 | 변화 |', '|---|---:|---:|---:|'])
    for key, title in (
        ('painted_frames', '표시 프레임 수'),
        ('measured_interval_seconds', '유효 프레임 간격 합계(s)'),
        ('long_frame_percent', '50ms 초과 비율(%, 변화는 %p)'),
        ('processing_median_ms', '처리 시간 중앙값(ms)'),
        ('capture_to_paint_p95_ms', '읽기 완료→화면 p95(ms)'),
        ('ar_visible_percent', 'AR 표시율(%, 변화는 %p)'),
        ('ar_disappearances', 'AR 사라짐 횟수'),
    ):
        old, new = baseline['summary'].get(key), candidate['summary'].get(key)
        lines.append(f'| {title} | {value(old)} | {value(new)} | {change(old, new)} |')
    lines.extend(['', 'FPS·AR 표시율은 높을수록, 간격·처리 시간·지연은 낮을수록 좋습니다.',
                  '전체 평균은 구간 비중에 영향을 받습니다. 정상 추적 FPS를 우선 비교하세요.',
                  'AR 사라짐 횟수는 측정 길이·가림 동작에 영향을 받습니다. 미표시 시간에는 화면 밖에 둔 시간도 포함됩니다.',
                  '읽기 완료→화면 지연은 카메라 내부 버퍼 대기를 포함하지 않습니다. main의 동기 읽기와 최신 프레임 방식의 전체 지연 차이를 나타내지는 않습니다.'])
    missing, differences = [], []
    for key, label in CONDITIONS.items():
        old, new = baseline.get('metadata', {}), candidate.get('metadata', {})
        if key not in old or key not in new or (key != 'calibration' and (old[key] is None or new[key] is None)):
            missing.append(label)
        elif old[key] != new[key]:
            differences.append(label)
    if baseline.get('slow_frame_threshold_ms') != candidate.get('slow_frame_threshold_ms'):
        differences.append('긴 프레임 판정 기준')
    if differences:
        lines.append('조건 불일치: ' + ', '.join(differences))
    if missing:
        lines.append('기록이 없어 확인할 수 없는 조건: ' + ', '.join(missing))
    lines.append('동일한 조명·움직임·가림 순서로 번갈아 3회 이상 측정해야 반복성을 확인할 수 있습니다.')
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline', type=Path, help='main report directory or summary.json')
    parser.add_argument('candidate', type=Path, help='current report directory or summary.json')
    parser.add_argument('--output', type=Path, help='Also save the comparison as Markdown')
    args = parser.parse_args(argv)
    try:
        comparison = compare_reports(load_report(args.baseline), load_report(args.candidate))
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(comparison, encoding='utf-8')
        print(comparison)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
