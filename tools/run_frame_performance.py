"""Run current/main with the same measurement UI and Python environment.

The baseline worktree is pinned at creation. Only timing fields are added to
its video worker; camera acquisition, tracking and recovery remain untouched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / 'artifacts' / 'performance' / 'main-worktree'
MEASUREMENT_FILES = ('frame_performance.py', 'frame_performance_viewer.py', 'performance_metadata.py')


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError('main video worker has changed; review the measurement-only patch before using this baseline.')
    return source.replace(old, new, 1)


def instrument_baseline(source):
    """Add timestamps/stats only to the synchronous main video loop."""
    source = replace_once(source,
        '    hand_mask_available: bool = False\n\n\nclass PracticeVideoWorker',
        '    hand_mask_available: bool = False\n'
        '    hand_mask_ms: float = 0.\n'
        '    tracking_ms: float = 0.\n'
        '    last_recovery_ms: float | None = None\n'
        '    recovery_pending: bool = False\n'
        '    captured_at: float | None = None\n'
        '    emitted_at: float | None = None\n'
        '    capture_sequence: int = 0\n'
        '    capture_fps: float | None = None\n'
        '    ar_expected: bool = False\n\n\nclass PracticeVideoWorker')
    source = replace_once(source,
        '            self.latest_exclusion_mask = self.occlusion_masker.process(frame_bgr)\n',
        '            self.latest_exclusion_mask = self.occlusion_masker.process(frame_bgr)\n'
        '            hand_mask_ms = (perf_counter() - inference_start) * 1000\n'
        '            tracking_start = perf_counter()\n')
    source = replace_once(source,
        '                hand_mask_available=self.occlusion_masker.available,\n',
        '                hand_mask_available=self.occlusion_masker.available,\n'
        '                hand_mask_ms=hand_mask_ms,\n'
        '                tracking_ms=(perf_counter() - tracking_start) * 1000,\n'
        '                captured_at=frame_timestamp,\n'
        '                ar_expected=(self.hybrid_tracker.anchor_grid is not None and self.current_step is not None\n'
        '                             and any(note.fret_number <= (self.selected_line_tracker.reference_fret_index or 0)\n'
        '                                     for note in self.current_step.notes)),\n')
    return replace_once(source,
        '            self.frame_ready.emit(bgr_to_qimage(annotated), stats)\n',
        '            image = bgr_to_qimage(annotated)\n'
        '            stats.emitted_at = monotonic()\n'
        '            self.frame_ready.emit(image, stats)\n')


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


def prepare_baseline(root=ROOT, target=BASELINE):
    # Validate the patch before creating any worktree.
    if not target.exists():
        instrument_baseline(git(root, 'show', 'main:app/features/video.py') + '\n')
        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['git', '-C', str(root), 'worktree', 'add', '--detach', str(target), 'main'], check=True)
    if not (target / '.git').is_file() or Path(git(target, 'rev-parse', '--show-toplevel')).resolve() != target.resolve():
        raise ValueError(f'Not a dedicated worktree: {target}')
    if git(target, 'rev-parse', '--git-common-dir') != git(root, 'rev-parse', '--absolute-git-dir'):
        raise ValueError('The baseline belongs to a different repository.')
    commit = git(target, 'rev-parse', 'HEAD')
    changed = set(git(target, 'diff', '--name-only', 'HEAD').splitlines())
    if changed - {'app/features/video.py'}:
        raise ValueError('Baseline has tracked changes outside the measurement patch; refusing to mix versions.')
    original = subprocess.check_output(['git', '-C', str(target), 'show', 'HEAD:app/features/video.py'], text=True)
    patched = instrument_baseline(original)
    video = target / 'app' / 'features' / 'video.py'
    if video.read_text() not in (original, patched):
        raise ValueError('Baseline video.py has user edits; refusing to overwrite them.')
    manifest_path = target / '.performance-baseline.json'
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    if manifest and manifest['commit'] != commit:
        raise ValueError('Baseline HEAD has changed; use a fresh comparison worktree.')
    for name in MEASUREMENT_FILES:
        destination = target / 'tools' / name
        if destination.exists():
            digest = hashlib.sha256(destination.read_bytes()).hexdigest()
            if digest != manifest.get('tools', {}).get(name):
                raise ValueError(f'Baseline measurement file has user edits: {destination}')
    video.write_text(patched)
    tool_hashes = {}
    for name in MEASUREMENT_FILES:
        destination = target / 'tools' / name
        shutil.copyfile(root / 'tools' / name, destination)
        tool_hashes[name] = hashlib.sha256(destination.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps({'commit': commit, 'tools': tool_hashes}, indent=2) + '\n')
    return commit


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', choices=('current', 'main'), default='current')
    parser.add_argument('--prepare-only', action='store_true', help='Prepare the worktree without opening the camera UI')
    parser.add_argument('--camera', type=int, default=0)
    parser.add_argument('--model', type=Path, default=ROOT / 'models' / 'best06.pt')
    parser.add_argument('--score-json', type=Path, default=ROOT / 'examples' / 'score_practice_sample.json')
    parser.add_argument('--camera-calibration', type=Path)
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.version == 'main':
            commit = prepare_baseline()
            project = BASELINE
        else:
            commit, project = git(ROOT, 'rev-parse', 'HEAD'), ROOT
        print(f'{args.version}: {commit[:12]} · {project}', flush=True)
        if args.prepare_only:
            return 0
        output = args.output_dir or ROOT / 'artifacts' / 'performance' / args.version
        command = [sys.executable, '-m', 'tools.frame_performance_viewer',
                   '--camera', str(args.camera), '--model', str(args.model.resolve()),
                   '--score-json', str(args.score_json.resolve()),
                   '--output-dir', str(output.resolve()), '--label', args.version]
        if args.camera_calibration:
            command.extend(['--camera-calibration', str(args.camera_calibration.resolve())])
        return subprocess.call(command, cwd=project)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f'준비 실패: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
