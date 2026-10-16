"""Camera-free timing/AR-continuity comparison; not a measurement of webcam FPS.

Run with ``python -m tools.benchmark_tracking_recovery``. The same seeded scene
and motion run through synchronous and live background recovery at a nominal
30 Hz. Includes normal motion, moving occlusion, brief blur, loss and return.
"""
from __future__ import annotations

import json
from collections import defaultdict
from time import monotonic, perf_counter, sleep

import cv2
import numpy as np

from vision.fretboard_mapping import DetectedLine, SelectedLineTracker, build_grid_from_reference_lines
from vision.fretboard_motion import HybridSelectedLineTracker, grid_quad
from vision.fretboard_recovery import BackgroundFretboardRecovery


def scene():
    rng = np.random.default_rng(2026)
    background = cv2.GaussianBlur(rng.integers(25, 210, (720, 1280), dtype=np.uint8), (5, 5), .8)
    image = background.copy()
    image[50:471, 90:251] = 25
    image[60:461, 100:241] = cv2.GaussianBlur(rng.integers(35, 175, (401, 141), dtype=np.uint8), (3, 3), .6)
    grid = build_grid_from_reference_lines(
        DetectedLine('nut', (100, 60), (240, 60), 1.),
        DetectedLine('fret', (100, 460), (240, 460), 1.), 9,
    )
    for x in np.linspace(108, 232, 6):
        cv2.line(image, (round(x), 60), (round(x), 460), 190, 1)
    for line in (grid.nut_line, *grid.ordered_fret_lines):
        cv2.line(image, tuple(map(round, line.p0)), tuple(map(round, line.p1)), 225, 2)
    for index, y in enumerate((121, 237, 341, 419)):
        cv2.circle(image, (164 + index * 3, y), 5, 220, -1)
    return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR), cv2.cvtColor(background, cv2.COLOR_GRAY2BGR), grid


def sequence(frame, background):
    for stage, count in (('motion', 24), ('hand', 24), ('brief_blur', 12), ('lost', 30), ('return', 45)):
        for index in range(count):
            x = 180 + index * .6 if stage == 'return' else index * .6
            pose = np.asarray(((1., 0, x), (0, 1, index * .2), (0, 0, 1)))
            current = cv2.warpPerspective(frame, pose, (1280, 720))
            mask = None
            if stage == 'hand':
                mask = np.zeros(frame.shape[:2], dtype=np.uint8)
                left = round(160 + x)
                current[190:310, left:left + 24] = (70, 130, 180)
                mask[190:310, left:left + 12] = 255
            elif stage == 'brief_blur' and index % 3:
                current = cv2.GaussianBlur(current, (11, 11), 0)
            elif stage == 'lost':
                current = background.copy()
            yield stage, current, mask, pose


def measure(background_recovery: bool):
    frame, background, grid = scene()
    selected = SelectedLineTracker()
    selected.reference_fret_index = 9
    selected.accept_registered_grid(grid)
    tracker = HybridSelectedLineTracker(
        selected, recovery=BackgroundFretboardRecovery() if background_recovery else None,
    )
    tracker.seed(frame)
    samples = defaultdict(list)
    previous_visible = {}
    disappearances = defaultdict(int)
    return_started = return_visible = None
    try:
        for stage, current, mask, expected in sequence(frame, background):
            captured_at = monotonic()
            if stage == 'return' and return_started is None:
                return_started = captured_at
            started = perf_counter()
            result = tracker.update(current, lambda roi: (), mask, timestamp=captured_at)
            processing_ms = (perf_counter() - started) * 1000
            visible = result.homography is not None
            error = None
            if visible and stage != 'lost':
                target = cv2.perspectiveTransform(grid_quad(grid)[:, None], expected).reshape(-1, 2)
                error = float(np.max(np.linalg.norm(grid_quad(result.result.stable_grid) - target, axis=1)))
            if previous_visible.get(stage, False) and not visible:
                disappearances[stage] += 1
            previous_visible[stage] = visible
            if stage == 'return' and visible and return_visible is None:
                return_visible = monotonic()
            samples[stage].append((processing_ms, visible, error))
            sleep(max(0., 1 / 30 - (monotonic() - captured_at)))
    finally:
        tracker.close()
    report = {}
    for stage, records in samples.items():
        times = [record[0] for record in records]
        errors = [record[2] for record in records if record[2] is not None]
        report[stage] = {
            'median_ms': round(float(np.median(times)), 2),
            'p95_ms': round(float(np.percentile(times, 95)), 2),
            'visible_frames': sum(record[1] for record in records),
            'frames': len(records),
            'disappearances': disappearances[stage],
            'max_position_error_px': round(max(errors), 2) if errors else None,
        }
    report['return_latency_ms'] = round((return_visible - return_started) * 1000, 2) if return_visible else None
    return report


if __name__ == '__main__':
    print(json.dumps({'synchronous': measure(False), 'background': measure(True)}, indent=2))
