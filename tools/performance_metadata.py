"""Capture reproducible identities outside the measured frame loop."""
from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess


def file_hash(path):
    path = Path(path)
    if not path.is_file():
        return None
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def git_text(root, *args):
    try:
        return subprocess.check_output(['git', '-C', str(root), *args], text=True,
                                       stderr=subprocess.DEVNULL, timeout=10).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def source_hash(root):
    digest = hashlib.sha256()
    for folder in ('app', 'vision', 'practice', 'audio', 'tools', 'benchmark'):
        for path in sorted((root / folder).rglob('*.py')):
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def report_identity(root, model, score, calibration):
    calibration_data = None if calibration is None else {
        'camera_matrix': calibration.camera_matrix.tolist(),
        'dist_coeffs': calibration.dist_coeffs.tolist(),
        'image_size': list(calibration.image_size) if calibration.image_size else None,
    }
    return {
        'measurement_version': 1,
        'git_commit': git_text(root, 'rev-parse', 'HEAD'),
        'git_branch': git_text(root, 'symbolic-ref', '--short', '-q', 'HEAD'),
        'git_status': git_text(root, 'status', '--short', '--untracked-files=no'),
        'source_sha256': source_hash(root),
        'model_sha256': file_hash(model), 'score_sha256': file_hash(score),
        'score': str(score), 'calibration': calibration_data,
    }
