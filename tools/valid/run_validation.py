import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from demo_pipeline import RotV2DemoPipeline
from tools.valid.valid_module import ValidLoader, ValidResultManager


def main() -> None:
    # Step 1. 예측 파이프라인 준비
    pipeline = RotV2DemoPipeline()

    # Step 2. 검증 데이터셋 준비
    loader = ValidLoader()

    # Step 3. 결과 관리자 준비
    manager = ValidResultManager()

    # Step 4. 전체 프레임 검증 실행
    for frame_index, (input_image, answer_points) in enumerate(loader):
        out = pipeline.run(input_image)
        result = answer_points.L2_check(out)
        manager.add_frame_result(frame_index, out, result)

    # Step 5. 실행 결과 폴더 안내
    print(f"Validation run saved to: {manager.result_dir}", flush=True)


if __name__ == "__main__":
    main()
