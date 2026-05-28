from __future__ import annotations

from dataclasses import dataclass

import numpy as np



@dataclass(frozen=True)
class PoseLine:
    # 모델이 검출한 "한 줄" 단위 결과입니다.
    # 보통 kind는 nut 또는 fret이며,
    # 6개 keypoint를 통해 한 프렛/너트 선분을 표현합니다.
    kind: str
    # 이 line 전체에 대한 모델 confidence입니다.
    # 점별 confidence가 아니라 line 단위 신뢰도입니다.
    confidence: float
    # 6개 keypoint의 (x, y) 좌표입니다.
    # shape은 보통 (6, 2)이며, 각 점은 줄 위치를 따라 배치됩니다.
    points_xy: np.ndarray
    # 각 keypoint를 실제 계산에 사용할지 여부입니다.
    # True인 점만 line fitting, 정렬, 보정 등에 사용합니다.
    visibility: np.ndarray
    # 이 line의 대표 중심 좌표입니다.
    # 정렬, 거리 비교, overlap suppression 등에 자주 사용됩니다.
    center_xy: np.ndarray


@dataclass(frozen=True)
class GuidePoint:
    # 이 guide point가 몇 번째 fret interval에 속하는지 나타냅니다.
    fret_index: int
    # 최종 guide point를 구성하는 keypoint 좌표들입니다.
    # 보통 fret interval 중간선 성격의 점 집합입니다.
    keypoints_xy: tuple[tuple[float, float], ...]
    # 각 guide keypoint가 유효한지 나타냅니다.
    visibility: tuple[bool, ...]
    # guide point 전체를 감싸는 bbox의 중심입니다.
    bbox_center_xy: tuple[float, float]
    # guide point 전체를 감싸는 bbox의 크기입니다.
    bbox_size_xy: tuple[float, float]


@dataclass(frozen=True)
class PoseLineStageConfig:
    # 모델 raw keypoint에서 실제로 읽을 점 개수입니다.
    keypoint_count: int = 6
    # 한 line을 유효하다고 보기 위한 최소 visible keypoint 개수입니다.
    min_visible_keypoints: int = 4
    # nut와 첫 fret가 거의 같은 위치일 때 overlap으로 볼 거리 기준입니다.
    nut_fret_overlap_px: float = 18.0
    # 인접 fret끼리 사실상 같은 선일 때 overlap으로 볼 거리 기준입니다.
    adjacent_fret_overlap_px: float = 16.0
