"""선택한 baseline 모델 하나를 전체 검증 데이터셋에서 실행합니다.

사용 예:
    uv run python tools/valid/models/run.py best06
    uv run python tools/valid/models/run.py rot_v2
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    # 파일 경로로 직접 실행해도 ``tools.valid`` 패키지를 찾도록 프로젝트 루트를 추가합니다.
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.valid.models.base import run_pipeline_validation
from tools.valid.models.line import LineGuidePipeline
from tools.valid.models.rot import RotGuidePipeline


# 모델 이름은 가중치 파일명과 동일합니다. 종류에 따라 필요한 최소 변환 파이프라인만 선택합니다.
MODEL_KINDS = {
    "best06": "line",
    "best07": "line",
    "best08_v1": "line",
    "best08_v2": "line",
    "best09": "line",
    "rot_v1": "rot",
    "rot_v2": "rot",
}


def build_pipeline(model_name: str) -> LineGuidePipeline | RotGuidePipeline:
    """등록된 모델 이름으로 가중치와 출력 변환 방식을 함께 선택합니다."""
    kind = MODEL_KINDS.get(model_name)
    if kind is None:
        supported = ", ".join(MODEL_KINDS)
        raise ValueError(f"Unknown model: {model_name}. Supported models: {supported}")

    model_path = PROJECT_ROOT / "models" / "weights" / f"{model_name}.pt"
    if not model_path.exists():
        raise FileNotFoundError(f"Model weights not found: {model_path}")

    if kind == "line":
        return LineGuidePipeline(model_path=model_path)
    return RotGuidePipeline(model_path=model_path)


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] in {"-h", "--help"}:
        models = " | ".join(MODEL_KINDS)
        print(f"Usage: uv run python tools/valid/models/run.py <{models}>")
        return

    run_pipeline_validation(build_pipeline(sys.argv[1]))


if __name__ == "__main__":
    main()
