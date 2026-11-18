from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import DEFAULT_SCORE_PATH
from app.ui.main_window import MainWindow
from tools.fretboard_detection_viewer import resolve_camera_calibration_path, resolve_model_path
from vision.camera_calibration import load_camera_calibration


def parse_args() -> argparse.Namespace:
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
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    model_path = resolve_model_path(args.model)
    calibration_path = resolve_camera_calibration_path(args.camera_calibration)
    camera_calibration = load_camera_calibration(calibration_path) if calibration_path is not None else None

    app = QApplication(sys.argv)
    app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    window = MainWindow(
        model_path=model_path,
        score_path=Path(args.score_json).expanduser().resolve(),
        camera_index=args.camera,
        camera_calibration=camera_calibration,
    )
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
