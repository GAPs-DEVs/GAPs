"""Reusable guide-point inference pipeline.

루트 패키지에서는 가벼운 추출 API만 노출합니다.
무거운 파이프라인 구현은 필요한 하위 모듈에서 직접 import합니다.
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
