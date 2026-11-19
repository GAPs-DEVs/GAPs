from __future__ import annotations

import os
import sys
from pathlib import Path


APP_NAME = "GAPs"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _user_data_dir() -> Path:
    if not getattr(sys, "frozen", False):
        return PROJECT_ROOT / "data"
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / APP_NAME


USER_DATA_DIR = _user_data_dir()
DEFAULT_SCORE_PATH = PROJECT_ROOT / "examples" / "score_practice_sample.json"
DEFAULT_AUTO_ADVANCE_DELAY_MS = 500
DEFAULT_CONF_THRESHOLD = 0.25
DEFAULT_IMAGE_SIZE = 960
