from __future__ import annotations

# 이 파일의 책임:
# - best06~best09 같은 2-keypoint line 모델을
#   검증용 최소 파이프라인 형태로 감싸기
# - 입력: 이미지 1장
# - 출력: list[GuidePoint]
#
# 여기서도 models/output_adapter의 모델 출력 변환 계층만 사용합니다.
# 즉:
#   raw result -> 2-keypoint PoseLine -> order_pose_lines
# 까지만 공용 레이어를 타고,
# 인접 line midpoint 계산만 여기서 수행합니다.

from pathlib import Path

import numpy as np
from ultralytics import YOLO

from tools.valid.models.base import load_input_image
from tools.valid.models.output_adapter import PoseLineStageConfig, extract_pose_lines, order_pose_lines
from tools.valid.models.output_adapter.types import PoseLine
from tools.valid.module import GuidePoint


DEFAULT_CONF = 0.20
DEFAULT_IMGSZ = 960
DEFAULT_POINT_CONF = 0.15


class LineGuidePipeline:
    # 2-keypoint line 모델용 검증 파이프라인입니다.
    # 핵심 흐름:
    #   image
    #   -> YOLO pose inference
    #   -> 2-keypoint PoseLine 추출
    #   -> 단일 프레임 line 정렬
    #   -> 인접 line의 midpoint
    #   -> list[GuidePoint]
    def __init__(
        self,
        model_path: str | Path,
        conf: float = DEFAULT_CONF,
        imgsz: int = DEFAULT_IMGSZ,
        point_conf: float = DEFAULT_POINT_CONF,
        device: str | None = None,
    ) -> None:
        self.model_path = Path(model_path)
        self.conf = float(conf)
        self.imgsz = int(imgsz)
        self.point_conf = float(point_conf)
        self.device = device
        self.model = YOLO(str(self.model_path))
        # best 계열은 양 끝점 2개만 쓰는 모델이므로
        # base pose-line layer도 2-keypoint 기준으로 동작하게 맞춥니다.
        self.pose_line_config = PoseLineStageConfig(
            keypoint_count=2,
            min_visible_keypoints=2,
            point_conf_threshold=self.point_conf,
        )
        print(f"LineGuidePipeline initialized: {self.model_path}", flush=True)

    def reset(self) -> None:
        # 현재 파이프라인은 프레임 간 상태를 유지하지 않습니다.
        return

    @staticmethod
    def _lines_to_guide_points(ordered_lines: tuple[PoseLine, ...] | list[PoseLine]) -> list[GuidePoint]:
        # 2-keypoint line 모델은 각 line이 선분 자체이므로,
        # 먼저 각 line을 6등분하고 같은 index끼리 midpoint를 계산해 guide point로 씁니다.
        points: list[GuidePoint] = []
        for fret_index in range(1, len(ordered_lines)):
            previous_line = ordered_lines[fret_index - 1]
            current_line = ordered_lines[fret_index]
            if current_line.kind != "fret":
                continue

            previous_start = np.asarray(previous_line.points_xy[0], dtype=np.float32)
            previous_end = np.asarray(previous_line.points_xy[-1], dtype=np.float32)
            current_start = np.asarray(current_line.points_xy[0], dtype=np.float32)
            current_end = np.asarray(current_line.points_xy[-1], dtype=np.float32)

            previous_samples = [
                previous_start + (previous_end - previous_start) * float(ratio)
                for ratio in np.linspace(0.0, 1.0, 6, dtype=np.float32)
            ]
            current_samples = [
                current_start + (current_end - current_start) * float(ratio)
                for ratio in np.linspace(0.0, 1.0, 6, dtype=np.float32)
            ]

            for string_index, (previous_point, current_point) in enumerate(
                zip(previous_samples, current_samples, strict=True),
                start=1,
            ):
                midpoint = (previous_point + current_point) * 0.5
                points.append(
                    GuidePoint(
                        string=string_index,
                        fret=fret_index,
                        x=round(float(midpoint[0]), 4),
                        y=round(float(midpoint[1]), 4),
                        visible=True,
                    )
                )
        return points

    def run(self, image: str | Path | object) -> list[GuidePoint]:
        image_bgr = load_input_image(image)
        result = self.model.predict(
            source=image_bgr,
            conf=self.conf,
            imgsz=self.imgsz,
            device=self.device,
            verbose=False,
        )[0]
        pose_lines = extract_pose_lines(
            result,
            self.model.names,
            config=self.pose_line_config,
        )
        ordered_pose_lines = order_pose_lines(
            pose_lines,
            config=self.pose_line_config,
        )
        return self._lines_to_guide_points(ordered_pose_lines)
