"""Reproduce paper figures from completed screen benchmark runs.

Run: .venv/bin/python -m benchmark.publication_figures
Dependencies: numpy, scipy, matplotlib (analysis-only; not imported by the app).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from collections import defaultdict
from pathlib import Path

os.environ.setdefault('MPLCONFIGDIR', '/tmp/guitar-benchmark-matplotlib')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np
from scipy.stats import t

ROOT = Path(__file__).resolve().parents[1]
METRICS = [
    ('overall_fps', 'summary', 'paint_fps', 'Overall display FPS', 'FPS'),
    ('tracking_fps', 'tracking', 'paint_fps', 'Normal-tracking FPS', 'FPS'),
    ('interval_p95', 'summary', 'paint_interval_p95_ms', 'Frame interval, run p95', 'ms'),
    ('latency_median', 'summary', 'capture_to_paint_median_ms', 'Read-to-paint latency, run median', 'ms'),
    ('latency_p95', 'summary', 'capture_to_paint_p95_ms', 'Read-to-paint latency, run p95', 'ms'),
    ('slow_percent', 'summary', 'long_frame_percent', 'Frame intervals >50 ms', '%'),
    ('freeze_count', 'summary', 'freeze_frames', 'Frame intervals >100 ms', 'count / 60 s'),
]
COLORS = ['#0072B2', '#D55E00', '#7A5195']
MARKERS = ['o', 's', '^']


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def recompute(records):
    painted = [r for r in records if r['painted_at']]
    intervals = [float(b['paint_interval_ms']) for a, b in zip(painted, painted[1:])
                 if b['paint_interval_ms'] and math.isclose(float(b['paint_interval_ms']),
                 (float(b['painted_at']) - float(a['painted_at'])) * 1000, rel_tol=1e-8, abs_tol=1e-6)]
    latencies = [float(r['capture_to_paint_ms']) for r in painted if r['capture_to_paint_ms']]
    def exceed(x, threshold):
        return x > threshold and not math.isclose(x, threshold, rel_tol=0, abs_tol=1e-6)
    return {
        'paint_fps': 1000 * len(intervals) / sum(intervals),
        'paint_interval_p95_ms': float(np.percentile(intervals, 95)),
        'capture_to_paint_median_ms': float(np.median(latencies)),
        'capture_to_paint_p95_ms': float(np.percentile(latencies, 95)),
        'long_frame_percent': 100 * sum(exceed(x, 50) for x in intervals) / len(intervals),
        'freeze_frames': sum(exceed(x, 100) for x in intervals),
    }


def load_runs(source):
    runs, manifest, seen = [], [], set()
    for p in sorted(source.glob('*/summary.json')):
        d = json.loads(p.read_text())
        assert d['timed_run']['status'] == 'completed', f'Incomplete run: {p}'
        assert d['timed_run']['elapsed_seconds'] == 60, f'Unequal duration: {p}'
        frame_path = p.with_name('frames.csv')
        h = digest(frame_path)
        assert h not in seen, f'Duplicate recording: {p}'
        seen.add(h)
        with frame_path.open(encoding='utf-8-sig', newline='') as f:
            frames = list(csv.DictReader(f))
        checked = {}
        for scope in ('summary', 'tracking'):
            raw = frames if scope == 'summary' else [r for r in frames if r['group'] == 'tracking']
            actual = recompute(raw)
            stored = d['summary'] if scope == 'summary' else d['by_mode']['tracking']
            for key, value in actual.items():
                assert math.isclose(value, stored[key], rel_tol=1e-8, abs_tol=1e-6), (p, scope, key, value, stored[key])
            checked[scope] = actual
        meta = d['metadata']
        assert meta['video_widget_size_at_start'] == meta['video_widget_size_at_end']
        row = {'run': p.parent.name, 'order': len(runs) + 1,
               'width': meta['video_widget_size'][0], 'height': meta['video_widget_size'][1],
               'dpr': meta['device_pixel_ratio'], 'duration_seconds': d['timed_run']['elapsed_seconds']}
        for key, scope, field, _, _ in METRICS:
            row[key] = checked[scope][field]
        runs.append(row)
        manifest.append({'run': p.parent.name, 'summary': str(p.relative_to(ROOT)),
                         'summary_sha256': digest(p), 'frames_sha256': h,
                         'source_sha256': meta['source_sha256'], 'model_sha256': meta['model_sha256'],
                         'score_sha256': meta['score_sha256'], 'input_sizes': meta['frame_sizes'],
                         'measurement_profile': meta['measurement_profile']})
    assert len(runs) >= 2, 'At least two completed runs required'
    for key in ('source_sha256', 'model_sha256', 'score_sha256', 'input_sizes', 'measurement_profile'):
        assert len({json.dumps(m[key], sort_keys=True) for m in manifest}) == 1, f'Mixed {key}'
    conditions = list(dict.fromkeys((r['width'], r['height'], r['dpr']) for r in runs))
    assert len(conditions) <= len(COLORS), 'Add explicit styles for new conditions'
    for r in runs:
        r['condition'] = f'G{conditions.index((r["width"], r["height"], r["dpr"])) + 1}'
    return runs, conditions, manifest


def stats(values):
    a = np.asarray(values, dtype=float)
    sd = float(np.std(a, ddof=1)) if len(a) > 1 else None
    half = float(t.ppf(.975, len(a) - 1)) * sd / math.sqrt(len(a)) if len(a) > 1 else None
    return {'n': len(a), 'mean': float(np.mean(a)), 'sd': sd,
            'min': float(a.min()), 'max': float(a.max()),
            'ci_low': float(a.mean()) - half if half is not None else None,
            'ci_high': float(a.mean()) + half if half is not None else None}


def write_csv(path, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def save_figure(fig, output, name):
    fig.savefig(output / f'{name}.png', dpi=600, facecolor='white')
    fig.savefig(output / f'{name}.svg', facecolor='white')
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=ROOT / 'benchmark/results/screen')
    parser.add_argument('--output', type=Path, default=ROOT / 'benchmark/results/publication')
    args = parser.parse_args()
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    runs, conditions, manifest = load_runs(args.source)
    groups = [[r for r in runs if r['condition'] == f'G{i+1}'] for i in range(len(conditions))]
    aggregate = {key: stats([r[key] for r in runs]) for key, *_ in METRICS}
    write_csv(output / 'run_metrics.csv', runs)
    write_csv(output / 'summary_statistics.csv', [
        {'metric': label, 'unit': unit, **{k: v for k, v in aggregate[key].items() if not k.startswith('ci_')}}
        for key, _, _, label, unit in METRICS])
    grouped = []
    for i, group in enumerate(groups):
        for key in ('overall_fps', 'tracking_fps'):
            grouped.append({'condition': f'G{i+1}', 'metric': key, **stats([r[key] for r in group])})
    write_csv(output / 'fps_condition_intervals.csv', grouped)
    (output / 'source_manifest.json').write_text(json.dumps({
        'analysis_unit': 'one completed 60-second run; equal run weighting',
        'n_runs': len(runs), 'analysis_script_sha256': digest(Path(__file__)), 'independent_frame_observations_assumed': False,
        'csv_recomputation': 'All six stored metrics checked in both overall and tracking scopes for every run.',
        'runs': manifest}, ensure_ascii=False, indent=2) + '\n')

    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 8.5,
                         'axes.labelsize': 8.5, 'axes.titlesize': 9,
                         'axes.linewidth': .65, 'xtick.labelsize': 8, 'ytick.labelsize': 8,
                         'svg.fonttype': 'none', 'savefig.bbox': None})
    fig, axes = plt.subplots(2, 3, figsize=(7.2, 5.65))
    fig.subplots_adjust(left=.085, right=.985, top=.85, bottom=.21, wspace=.36, hspace=.48)
    panels = [('overall_fps', 'Overall display rate', 'FPS', (0, 32)),
              ('tracking_fps', 'Normal-tracking rate', 'FPS', (0, 32)),
              ('interval_p95', 'Frame interval: run p95', 'ms', (0, 80)),
              ('latency_p95', 'Read-to-paint: run p95', 'ms', (0, 110)),
              ('slow_percent', 'Frame intervals >50 ms', '%', (0, 100)),
              ('freeze_count', 'Frame intervals >100 ms', 'Count per 60-s run', (0, 14))]
    for panel, (key, title, unit, ylim) in zip(axes.flat, panels):
        idx = panels.index((key, title, unit, ylim))
        panel.set_title(f'({chr(97+idx)}) {title}', loc='left', pad=9)
        for i, group in enumerate(groups):
            vals = np.asarray([r[key] for r in group])
            # Fixed offsets preserve run identity and keep coincident counts visible.
            offsets = np.linspace(-.22, .10, len(vals))
            panel.scatter(i + offsets, vals, s=22, marker=MARKERS[i], facecolors='white',
                          edgecolors=COLORS[i], linewidths=.9, zorder=3, clip_on=False)
            st = stats(vals)
            panel.errorbar(i+.23, st['mean'], yerr=st['sd'] if key != 'freeze_count' else None,
                           fmt='D', color='#242424', markersize=3.4, capsize=3,
                           elinewidth=.9, capthick=.9, zorder=4)
        panel.set_xlim(-.5, len(groups)-.4)
        panel.set_ylim(*ylim)
        panel.set_xticks(range(len(groups)), [f'G{i+1}\n(n={len(g)})' for i,g in enumerate(groups)])
        panel.set_ylabel(unit, labelpad=3)
        panel.spines[['top','right']].set_visible(False)
        panel.grid(axis='y', color='#E5E7EB', linewidth=.5, zorder=0)
        panel.tick_params(axis='both', length=3, width=.65)
    fig.text(.085, .965, f'Screen performance across {len(runs)} repeated runs', fontsize=12, weight='bold')
    fig.text(.085, .925, '60 s per run  |  1280 × 720 camera input  |  one marker per run', color='#4B5563', fontsize=8.5)
    fig.text(.085, .115, 'Open markers: observed runs. Black diamonds: means; whiskers: ±1 sample SD (a–e).', fontsize=7.8)
    fig.text(.085, .083, 'G1: 940 × 682, DPR 1   •   G2: 2060 × 1164, DPR 1   •   G3: 1012 × 703, DPR 2', fontsize=7.6)
    fig.text(.085, .051, 'Viewport dimensions are logical pixels. Conditions were not randomized; differences are descriptive.', fontsize=7.4, color='#4B5563')
    save_figure(fig, output, 'figure1_screen_performance')

    # Journal-style table: no vertical rules, no decorative panels.
    fig = plt.figure(figsize=(7.2, 3.55))
    ax = fig.add_axes([0,0,1,1]); ax.set_axis_off(); ax.set_xlim(0,1); ax.set_ylim(0,1)
    ax.text(.04,.94, 'Table 1. Screen performance summary', fontsize=11, weight='bold')
    ax.text(.04,.882, f'{len(runs)} completed 60-s runs; all display conditions combined', fontsize=8.5, color='#4B5563')
    xpos = [.04,.60,.79,.955]
    heads = ['Metric', 'Mean ± SD', 'Minimum', 'Maximum']
    for x,label in zip(xpos,heads): ax.text(x,.795,label,ha='left' if x==xpos[0] else 'right',weight='bold',fontsize=8.5)
    ax.hlines([.837,.77],.04,.96,colors='#222222',linewidths=[1,.65])
    for i,(key,_,_,label,unit) in enumerate(METRICS):
        s=aggregate[key]; y=.715-i*.068
        ax.text(.04,y,f'{label} ({unit})',fontsize=8.1)
        for x,value in zip(xpos[1:],[f"{s['mean']:.2f} ± {s['sd']:.2f}",f"{s['min']:.2f}",f"{s['max']:.2f}"]):
            ax.text(x,y,value,ha='right',fontsize=8.5)
    ax.hlines(.272,.04,.96,color='#222222',linewidth=1)
    ax.text(.04,.21,'SD: sample standard deviation across runs (n − 1 denominator). Each run has equal weight.',fontsize=7.5)
    ax.text(.04,.158,'Percentile rows summarize the distribution of per-run percentiles, not a pooled frame percentile.',fontsize=7.5)
    ax.text(.04,.106,'Normal tracking is a subset of each run. Overall values also include recovery and other states.',fontsize=7.5)
    ax.text(.04,.054,'Read-to-paint excludes camera exposure/buffering, paint execution and physical display scanout.',fontsize=7.5,color='#4B5563')
    save_figure(fig, output, 'table1_screen_performance')

    # Conditional confidence intervals are separate from descriptive SD bars.
    fig, ax = plt.subplots(figsize=(7.2,3.9))
    fig.subplots_adjust(left=.12,right=.97,bottom=.35,top=.79)
    for i,g in enumerate(groups):
        for j,key in enumerate(('overall_fps','tracking_fps')):
            s=stats([r[key] for r in g]); y=i+(j-.5)*.23
            ax.errorbar(s['mean'],y,xerr=[[s['mean']-s['ci_low']],[s['ci_high']-s['mean']]],
                        fmt='o' if j==0 else 's',mfc='white' if j==0 else COLORS[i],
                        mec=COLORS[i],ecolor=COLORS[i],capsize=3,markersize=5,elinewidth=1)
    ax.set_yticks(range(len(groups)),[f'G{i+1} (n={len(g)})' for i,g in enumerate(groups)])
    ax.invert_yaxis(); ax.set_xlabel('Mean display rate (FPS)'); ax.set_xlim(15,30)
    ax.spines[['top','right']].set_visible(False); ax.grid(axis='x',color='#E5E7EB',linewidth=.5)
    fig.text(.12,.94,'Figure 2. Conditional 95% confidence intervals for mean FPS',fontsize=11,weight='bold')
    fig.legend(handles=[Line2D([],[],marker='o',mfc='white',color='#444444',linestyle='none',label='Overall'),
                        Line2D([],[],marker='s',color='#444444',linestyle='none',label='Normal tracking')],
               loc='upper center',bbox_to_anchor=(.57,.90),ncol=2,frameon=False,fontsize=8)
    fig.text(.12,.15,'Student-t intervals assume independent, approximately normal run-level observations within each group.',fontsize=7.2)
    fig.text(.12,.095,'Session independence is unverified; group sizes are small (4 / 12 / 4). Interpret as exploratory intervals.',fontsize=7.2)
    fig.text(.12,.04,'G1: 940 × 682, DPR 1   •   G2: 2060 × 1164, DPR 1   •   G3: 1012 × 703, DPR 2 (logical pixels).',fontsize=7.2)
    save_figure(fig, output, 'figure2_fps_intervals')

    tex = ['% Requires \\usepackage{booktabs}', '\\begin{table}[t]', '\\centering',
           '\\caption{Screen performance over 20 completed 60-s runs under mixed display conditions.}',
           '\\label{tab:screen-performance}', '\\begin{tabular}{lrrr}', '\\toprule',
           'Metric & Mean $\\pm$ SD & Min. & Max. \\\\', '\\midrule']
    for key,_,_,label,unit in METRICS:
        s=aggregate[key]; label=label.replace('>', '$>$'); unit=unit.replace('%',r'\%')
        tex.append(f"{label} ({unit}) & {s['mean']:.2f} $\\pm$ {s['sd']:.2f} & {s['min']:.2f} & {s['max']:.2f} \\\\")
    tex += ['\\bottomrule','\\end{tabular}',
            '\\par\\smallskip\\footnotesize SD is the sample standard deviation across runs. Percentile rows summarize per-run percentiles, not pooled frame percentiles.', '\\end{table}']
    (output/'table1.tex').write_text('\n'.join(tex)+'\n')

    group_lines=[]
    for i,g in enumerate(groups):
        c=conditions[i]; s=stats([r['tracking_fps'] for r in g])
        group_lines.append(f"| G{i+1} | {c[0]} × {c[1]} | {c[2]:g} | {len(g)} | {s['mean']:.2f} ± {s['sd']:.2f} | {s['ci_low']:.2f}–{s['ci_high']:.2f} |")
    notes = f'''# 화면 성능 평가: 논문 삽입용 자료

분석 단위는 **60초 실행 1회**입니다. {len(runs)}회, 총 {sum(r['duration_seconds'] for r in runs)/60:g}분이며 프레임 CSV 해시가 모두 다릅니다. 20회 모두 같은 소스·모델·악보 해시, 1280×720 입력, 같은 측정 프로파일을 사용합니다. 실행별 요약은 원시 CSV에서 전체/정상 추적 구간의 지표 6개씩을 독립적으로 다시 계산하여 대조했습니다. 결과 파일은 수정하지 않았습니다.

## 그림과 표 사용

- `figure1_screen_performance.svg`: 논문 본문에 권장하는 6패널 벡터 그림. 점은 실행 1회, 검은 마름모는 평균, 오차막대는 표본 표준편차(±1 SD)입니다. (f)는 분포가 치우친 횟수 자료라 평균과 모든 점만 표시합니다.
- `table1_screen_performance.svg`: 전체 조건을 합친 평균±표준편차와 최솟값·최댓값 표.
- 같은 이름의 PNG는 600 dpi이며 슬라이드·문서 삽입에 사용할 수 있습니다. 영문 표기로 작성하여 논문 본문에 넣기 쉽게 했습니다.
- `table1.tex`: booktabs 기반 LaTeX 표. 필요 시 두 단 문서에서는 table*로 바꾸세요.
- `figure2_fps_intervals.svg`: **참고용** 조건별 FPS 평균의 95% Student-t 신뢰구간. 주된 결론보다 보조 분석에 적합합니다.
- `run_metrics.csv`, `summary_statistics.csv`, `fps_condition_intervals.csv`, `source_manifest.json`: 재현·수치 검토 자료.

## 표본과 통계

각 실행에 같은 가중치를 주어 산술평균을 구합니다. 표준편차는 ddof=1입니다. 중앙값/p95 행은 **실행별 중앙값/p95의 평균과 SD**이며 모든 프레임을 합친 중앙값/p95가 아닙니다. 프레임 수를 독립 표본 수로 사용하지 않습니다. 정상 추적 지표는 같은 실행 중 정상 추적으로 분류된 프레임의 부분집합이고 추적 정확도나 성공률을 의미하지 않습니다.

전체 평균은 서로 다른 표시 조건을 섞은 **기술통계**입니다. 조건 구성비가 달라지면 전체 평균도 달라집니다. 전체 혼합 평균에는 신뢰구간을 붙이지 않았습니다.

| 조건 | 영상 위젯 크기(논리 픽셀) | DPR | 실행 수 | 정상 추적 FPS: 평균±SD | 조건부 95% CI |
|---|---|---:|---:|---:|---:|
{chr(10).join(group_lines)}

조건별 FPS 신뢰구간은 mean ± t(0.975, n−1) × SD/√n으로 계산했습니다. [NIST의 평균 신뢰구간 정의](https://www.itl.nist.gov/div898/handbook/eda/section3/eda352.htm)를 따릅니다. 이 식은 그룹 내 독립적인, 근사 정규분포의 실행 관측을 가정합니다. 현재 자료는 같은 날짜에 연속 측정한 반복 실행이며 독립성·정규성은 입증되지 않았고 n=4인 조건도 있습니다. 따라서 CI는 **해당 가정 아래의 탐색적 참고값**입니다. 그룹 간 유의성 검정이나 화면 크기의 인과 효과를 주장하지 않습니다. 서로 다른 조건의 실행 순서·열 상태·손 가림·추적 상실 비중도 통제되지 않았습니다.

## 논문용 캡션

**Figure 1 (English).** Screen performance measured in 20 completed 60-s runs with 1280×720 camera input. Open markers represent individual runs; black diamonds indicate group means and whiskers indicate ±1 sample standard deviation in panels (a–e). Panel (f) shows individual counts and means without dispersion bars. G1, G2 and G3 denote viewport sizes of 940×682 (DPR 1), 2060×1164 (DPR 1), and 1012×703 (DPR 2), with 4, 12 and 4 runs, respectively. Percentiles are computed within each run. Display conditions were not randomized; group differences are descriptive.

**그림 1 (한국어).** 1280×720 카메라 입력에서 60초씩 20회 수행한 화면 성능 측정 결과. 빈 기호는 개별 실행, 검은 마름모는 조건별 평균, (a)–(e)의 오차막대는 표본 표준편차를 나타낸다. (f)는 개별 발생 횟수와 평균을 표시한다. 백분위수는 각 실행에서 계산하였다. 화면 표시 조건은 무작위 배정되지 않았으므로 조건 간 차이는 기술적으로 해석한다.

**Table 1.** Descriptive statistics of per-run screen-performance measures across 20 completed 60-s runs under mixed display conditions. Values are equally weighted across runs; SD denotes the sample standard deviation. Per-run percentile summaries must not be interpreted as percentiles of pooled frames.

## 결과 문장 예시

“60초씩 총 20회 측정한 결과, 전체 화면 갱신 속도는 {aggregate['overall_fps']['mean']:.2f} ± {aggregate['overall_fps']['sd']:.2f} FPS였으며 정상 추적 구간은 {aggregate['tracking_fps']['mean']:.2f} ± {aggregate['tracking_fps']['sd']:.2f} FPS였다(평균±표준편차). 이 값은 세 가지 화면 표시 조건을 포함한 반복 실행의 기술통계이다.”

입력→화면 지연은 카메라 read 반환부터 Qt paint 이벤트 진입까지의 **소프트웨어 구간 지연**입니다. 카메라 노출·장치 버퍼·paint 실행·물리적 모니터 출력 시간을 포함하지 않습니다. 50ms/100ms는 진단 임계값이며 검증된 사용자 체감 또는 합격 기준이 아닙니다.

## 연구 보고 시 추가로 명시할 항목

CPU/메모리/OS/카메라 모델과 실제 설정, 전원 모드, 조명·가림·이동 절차, 연습/마이크 동작 여부를 적으세요. 현재 파일은 이 항목을 모두 증명하지 않습니다. 일반화 가능한 성능 주장을 하려면 같은 조건의 통제 실험, 무작위 또는 교차 실행 순서, 다른 날짜·장비의 독립 반복이 필요합니다. 지금의 시각적 완성도는 이런 실험 설계를 대신하지 않습니다.

## 재생성

프로젝트 루트에서 `.venv/bin/python -m benchmark.publication_figures`를 실행합니다. 분석 전용 의존성은 numpy, scipy, matplotlib입니다. 입력 파일/중복/기간/원시 CSV 대조 검증에 실패하면 생성이 중단됩니다.
'''
    (output/'README.md').write_text(notes,encoding='utf-8')
    print(json.dumps({'output':str(output),'runs':len(runs),'summary':aggregate},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
