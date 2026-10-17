from __future__ import annotations

# 이 파일의 책임:
# - rot_v1 / rot_v2 같은 6-keypoint pose 모델을
#   검증용 최소 파이프라인 형태로 감싸기
# - 입력: 이미지 1장
# - 출력: list[GuidePoint]
#
# 여기서는 models/output_adapter의 모델 출력 변환 계층만 사용합니다.
# 즉:
#   raw result -> extract_pose_lines -> order_pose_lines
# 까지만 공용 레이어를 타고,
# 마지막 midpoint 계산만 여기서 수행합니다.

from pathlib import Path

import numpy as np
from ultralytics import YOLO

from tools.valid.models.base import load_input_image
from tools.valid.models.output_adapter import PoseLineStageConfig, extract_pose_lines, order_pose_lines
from tools.valid.module import GuidePoint


DEFAULT_CONF = 0.20
DEFAULT_IMGSZ = 960


class RotGuidePipeline:
    # rot 계열의 공통 최소 파이프라인입니다.
    # 핵심 흐름:
    #   image
    #   -> YOLO pose inference
    #   -> PoseLine 추출
    #   -> PoseLine 정렬
    #   -> 인접 line 사이 midpoint 계산
    #   -> list[GuidePoint]
    def __init__(
        self,
        model_path: str | Path,
        conf: float = DEFAULT_CONF,
        imgsz: int = DEFAULT_IMGSZ,
        device: str | None = None,
    ) -> None:
        self.model_path = Path(model_path)
        self.conf = float(conf)
        self.imgsz = int(imgsz)
        self.device = device
        self.model = YOLO(str(self.model_path))
        self.pose_line_config = PoseLineStageConfig()
        print(f"RotGuidePipeline initialized: {self.model_path}", flush=True)

    def reset(self) -> None:
        # 현재 파이프라인은 프레임 간 상태를 유지하지 않습니다.
        return

    def _extract_ordered_pose_lines(self, result):
        # 복구된 base layer를 그대로 사용합니다.
        # 1. raw result -> PoseLine
        # 2. PoseLine -> ordered PoseLine
        pose_lines = extract_pose_lines(
            result,
            self.model.names,
            config=self.pose_line_config,
        )
        return order_pose_lines(
            pose_lines,
            config=self.pose_line_config,
        )

    @staticmethod
    def _sample_line_points(line, sample_count: int = 6) -> list[tuple[float, float]]:
        # 정렬된 PoseLine에서 "실제로 보이는 첫 점과 마지막 점"을 잡고,
        # 그 선분을 sample_count개 점으로 균등 분할합니다.
        #
        # 즉 rot 모델이 6개 keypoint를 직접 냈더라도,
        # 검증용 비교 기준은 "line을 6등분한 점"으로 통일합니다.
        visible_indices = np.flatnonzero(line.visibility)
        if visible_indices.size < 2:
            return []

        start_index = int(visible_indices[0])
        end_index = int(visible_indices[-1])
        start_xy = np.asarray(line.points_xy[start_index], dtype=np.float32)
        end_xy = np.asarray(line.points_xy[end_index], dtype=np.float32)

        sampled_points: list[tuple[float, float]] = []
        for ratio in np.linspace(0.0, 1.0, sample_count, dtype=np.float32):
            point_xy = start_xy + (end_xy - start_xy) * float(ratio)
            sampled_points.append((float(point_xy[0]), float(point_xy[1])))
        return sampled_points

    def _lines_to_guide_points(self, ordered_lines) -> list[GuidePoint]:
        # 정렬된 PoseLine들에서 인접 두 선 사이의 midpoint를 guide point로 바꿉니다.
        # 여기서 각 line은 먼저 6등분한 뒤, 같은 index끼리 midpoint를 계산합니다.
        # current line이 fret일 때만 fret interval 하나로 간주합니다.
        points: list[GuidePoint] = []
        for fret_index in range(1, len(ordered_lines)):
            previous_line = ordered_lines[fret_index - 1]
            current_line = ordered_lines[fret_index]
            if current_line.kind != "fret":
                continue

            previous_samples = self._sample_line_points(previous_line, sample_count=6)
            current_samples = self._sample_line_points(current_line, sample_count=6)
            if len(previous_samples) < 6 or len(current_samples) < 6:
                continue

            # GT는 6점 모두가 이상적이지만,
            # 현재 비교 포맷은 string/fret 단위이므로
            # 6등분한 같은 위치끼리 midpoint를 계산해 6개 점을 만듭니다.
            for sample_index, (previous_point, current_point) in enumerate(
                zip(previous_samples, current_samples, strict=True),
                start=1,
            ):
                midpoint = (
                    (previous_point[0] + current_point[0]) * 0.5,
                    (previous_point[1] + current_point[1]) * 0.5,
                )
                # rot 계열은 현재 정렬된 선의 점 방향이
                # GT의 string 번호 방향과 반대로 들어오므로
                # 검증 키를 맞추기 위해 string 번호를 뒤집어 매깁니다.
                string_index = 7 - sample_index
                points.append(
                    GuidePoint(
                        string=int(string_index),
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
        ordered_lines = self._extract_ordered_pose_lines(result)
        return self._lines_to_guide_points(ordered_lines)
