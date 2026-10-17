"""모델별 출력을 공통 선 형식으로 변환하고 정렬하는 baseline 어댑터입니다.

이 패키지는 실제 앱 추적 파이프라인이 아니라 ``tools/valid/models`` 실행기의
단일 모델 baseline 검증에서만 사용합니다.
"""

from .extractors import extract_pose_lines
from .geometry import order_pose_lines
from .types import PoseLine, PoseLineStageConfig

__all__ = [
    "PoseLine",
    "PoseLineStageConfig",
    "extract_pose_lines",
    "order_pose_lines",
]
