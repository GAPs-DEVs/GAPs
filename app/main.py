from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import DEFAULT_SCORE_PATH, PROJECT_ROOT
from app.ui.main_window import MainWindow
from tools.fretboard_detection_viewer import resolve_camera_calibration_path, resolve_model_path
from vision.camera_calibration import load_camera_calibration


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Main guitar practice support application")
    parser.add_argument("--model", type=str, default=None, help="Path to the YOLO weight file")
    parser.add_argument("--camera", type=int, default=0, help="Webcam index to open first")
    parser.add_argument("--score-json", type=Path, default=DEFAULT_SCORE_PATH, help="Practice score JSON path")
    parser.add_argument(
        "--camera-calibration",
        type=str,
        default=None,
        help="Path to a camera calibration .npz file. If omitted, camera_calibration.npz is auto-loaded when present.",
    )
    parser.add_argument('--benchmark-screen', action='store_true', help='Enable optional screen measurement (F8 settings, F9 start/stop)')
    parser.add_argument('--duration', type=int, default=30, help='Screen benchmark duration in seconds (1–600)')
    parser.add_argument('--output-dir', type=Path, default=PROJECT_ROOT / 'benchmark' / 'results' / 'screen')
    parser.add_argument('--label', default='', help='Benchmark run label')
    args = parser.parse_args(argv)
    if not 1 <= args.duration <= 600:
        parser.error('--duration은 1~600초여야 합니다.')
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    model_path = resolve_model_path(args.model)
    calibration_path = resolve_camera_calibration_path(args.camera_calibration)
    camera_calibration = load_camera_calibration(calibration_path) if calibration_path is not None else None

    app = QApplication(sys.argv)
    window_type = MainWindow
    extra = {}
    if args.benchmark_screen:
        from benchmark.screen import ScreenBenchmarkWindow
        window_type = ScreenBenchmarkWindow
        extra = dict(duration=args.duration, output_dir=args.output_dir, label=args.label)
    window = window_type(
        model_path=model_path,
        score_path=Path(args.score_json).expanduser().resolve(),
        camera_index=args.camera,
        camera_calibration=camera_calibration,
        **extra,
    )
    window.show()
    if args.benchmark_screen:
        window.show_benchmark_settings()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
