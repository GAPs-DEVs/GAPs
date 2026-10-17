"""저장된 보정 프레임부터 메인 앱 파이프라인을 검증합니다."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.valid.pipeline.app import AppValidationPipeline
from tools.valid.module import ValidLoader, ValidResultManager, log_validation_progress


def main() -> None:
    pipeline = AppValidationPipeline()
    loader = ValidLoader()
    total_frames = len(loader)
    manager = ValidResultManager()

    try:
        for frame_index, (input_image, answer_points) in enumerate(loader):
            out = pipeline.run(frame_index, input_image)
            # 보정 전 프레임은 파이프라인의 출력 실패가 아니므로 평가에서 제외합니다.
            if out is None:
                continue
            result = answer_points.L2_check(out)
            log_validation_progress(frame_index, total_frames, result)
            manager.add_frame_result(frame_index, out, result)
    finally:
        pipeline.close()

    summary = manager.save_summary()
    print(f"Validation summary: {summary}", flush=True)
    print(f"Validation run saved to: {manager.result_dir}", flush=True)


if __name__ == "__main__":
    main()

