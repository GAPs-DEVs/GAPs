"""Run the tracker again over a recorded session and explain every dropout.

Run: python -m tools.replay_session artifacts/performance/current/recording-...

Frames are fed at their recorded pace because background recovery and the
brief-blur allowance depend on real elapsed time. ``--fast`` removes the
waiting; the result then no longer represents live recovery timing.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
from time import monotonic, perf_counter, sleep

import cv2
import numpy as np

from tools.session_recording import INDEX_NAME, grid_from_json
from vision.fretboard_mapping import SelectedLineTracker
from vision.fretboard_motion import HybridSelectedLineTracker, grid_quad
from vision.fretboard_recovery import BackgroundFretboardRecovery
from vision.hand_tracking import AsyncHandOcclusionMasker, HandOcclusionMasker


HIDDEN_MODES = {'selected_lines_hold', 'selected_lines_lost', 'selected_lines_reference_weak'}


def replay(directory: Path, *, realtime: bool = True, hand_mask: str = 'async', background_recovery: bool = True):
    """Return one record per recorded frame: sequence, mode, detail, processing ms."""
    directory = Path(directory)
    index = json.loads((directory / INDEX_NAME).read_text(encoding='utf-8'))
    events: dict[int, list[dict]] = {}
    for event in index['events']:
        events.setdefault(event['after_sequence'], []).append(event)
    selected = SelectedLineTracker(max_history=5)
    tracker = HybridSelectedLineTracker(
        selected, recovery=BackgroundFretboardRecovery() if background_recovery else None,
    )
    masker = {'async': AsyncHandOcclusionMasker, 'sync': HandOcclusionMasker}[hand_mask]() if hand_mask != 'off' else None
    video = cv2.VideoCapture(str(directory / index['video']))
    records = []
    started = monotonic()
    first_timestamp = index['frames'][0]['timestamp'] if index['frames'] else 0.
    try:
        for frame_info in index['frames']:
            ok, frame = video.read()
            if not ok:
                break
            if realtime:
                sleep(max(0., frame_info['timestamp'] - first_timestamp - (monotonic() - started)))
            begin = perf_counter()
            mask = masker.process(frame) if masker is not None else None
            mode, detail, quad = 'calibration_required', '', None
            if tracker.anchor_grid is not None:
                result = tracker.update(frame, lambda roi: (), mask, timestamp=frame_info['timestamp'])
                mode, detail = result.result.tracking_mode, tracker.last_decision
                if result.homography is not None:
                    # Board corners actually drawn on this frame.
                    quad = grid_quad(result.result.stable_grid).tolist()
            records.append({'sequence': frame_info['sequence'], 'timestamp': frame_info['timestamp'],
                            'mode': mode, 'detail': detail, 'quad': quad, 'ms': (perf_counter() - begin) * 1000,
                            'live_mode': frame_info.get('mode'), 'live_detail': frame_info.get('detail')})
            for event in events.get(frame_info['sequence'], ()):
                if event['type'] == 'clear':
                    selected.clear()
                    tracker.clear()
                    continue
                # Seed from the lossless calibration image, exactly as the app did.
                seed_image = cv2.imread(str(directory / event['image']))
                seed_mask = (cv2.imread(str(directory / event['mask']), cv2.IMREAD_GRAYSCALE)
                             if event.get('mask') else None)
                selected.clear()
                selected.reference_fret_index = event['reference_fret_index']
                selected.accept_registered_grid(grid_from_json(event['grid']))
                tracker.seed(seed_image, seed_mask)
    finally:
        video.release()
        tracker.close()
        if masker is not None:
            masker.close()
    return records


def simplify(detail: str) -> str:
    """Drop the per-frame numbers so identical causes are counted together."""
    return re.sub(r'[:=(][\d./-]+', lambda match: match.group(0)[0] + '#', detail)


def summarize(records, mode_key: str = 'mode', detail_key: str = 'detail') -> dict:
    tracked = [record for record in records
               if (record[mode_key] or '').startswith('selected_lines_')]
    hidden = [record[mode_key] in HIDDEN_MODES for record in tracked]
    runs, length = [], 0
    for value in hidden:
        if value:
            length += 1
        elif length:
            runs.append(length)
            length = 0
    if length:
        runs.append(length)
    causes = Counter(simplify(record[detail_key] or '') for record, value in zip(tracked, hidden) if value)
    return {
        'tracked_frames': len(tracked),
        'hidden_frames': int(sum(hidden)),
        'dropouts': len(runs),
        'single_frame_dropouts': sum(run == 1 for run in runs),
        'longest_dropout_frames': max(runs, default=0),
        'modes': dict(Counter(record[mode_key] for record in tracked)),
        'hidden_causes': dict(causes.most_common(12)),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('recording', type=Path)
    parser.add_argument('--fast', action='store_true', help='Do not wait between frames (recovery timing is then unrealistic)')
    parser.add_argument('--hand-mask', choices=('async', 'sync', 'off'), default='async')
    parser.add_argument('--frames', type=Path, help='Also write every frame record to this JSON file')
    args = parser.parse_args(argv)
    records = replay(args.recording, realtime=not args.fast, hand_mask=args.hand_mask)
    times = [record['ms'] for record in records if record['mode'].startswith('selected_lines_')]
    report = {
        'replay': summarize(records),
        'live': summarize(records, 'live_mode', 'live_detail'),
        'processing_ms': {'median': float(np.median(times)), 'p95': float(np.percentile(times, 95))} if times else None,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.frames:
        args.frames.write_text(json.dumps(records, ensure_ascii=False), encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
