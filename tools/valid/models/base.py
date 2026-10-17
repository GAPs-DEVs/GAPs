from __future__ import annotations

# 이 파일의 책임:
# - 검증 루프를 한 곳에만 모아두기
# - 각 모델 실행 파일이 같은 검증 흐름을 재사용하게 만들기
#
# 사용 예:
#   pipeline = SomePipeline()
#   run_pipeline_validation(pipeline)

from pathlib import Path

import cv2

from tools.valid.module import ValidLoader, ValidResultManager, log_validation_progress


def load_input_image(image: str | Path | object):
    """모델 실행기가 경로와 이미 읽힌 OpenCV 프레임을 모두 받을 수 있게 정규화합니다."""
    if not isinstance(image, (str, Path)):
        return image

    image_bgr = cv2.imread(str(image))
    if image_bgr is None:
        raise FileNotFoundError(f"Could not read image: {image}")
    return image_bgr


def run_pipeline_validation(pipeline) -> Path:
    # Step 1. 검증 데이터셋 준비
    loader = ValidLoader()
    total_frames = len(loader)

    # Step 2. 결과 저장 관리자 준비
    manager = ValidResultManager()

    # Step 3. 전체 프레임에 대해 동일한 검증 루프 수행
    for frame_index, (input_image, answer_points) in enumerate(loader):
        # 파이프라인은 이미지 1장을 받아 GuidePoint 목록을 출력한다.
        out = pipeline.run(input_image)

        # 정답 객체가 예측 결과와의 L2 기반 비교를 수행한다.
        result = answer_points.L2_check(out)
        log_validation_progress(frame_index, total_frames, result)

        # 프레임별 예측 결과와 검증 결과를 저장한다.
        manager.add_frame_result(frame_index, out, result)

    # Step 4. 모델 검증도 앱 파이프라인과 동일하게 summary.json을 남깁니다.
    manager.save_summary()
    print(f"Validation run saved to: {manager.result_dir}", flush=True)
    return manager.result_dir
