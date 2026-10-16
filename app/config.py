from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCORE_PATH = PROJECT_ROOT / "examples" / "score_practice_sample.json"
DEFAULT_AUTO_ADVANCE_DELAY_MS = 500
DEFAULT_CONF_THRESHOLD = 0.25
DEFAULT_IMAGE_SIZE = 960
