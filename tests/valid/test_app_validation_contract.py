"""실제 앱과 검증 어댑터가 공유하는 출력 계약을 확인합니다."""

from types import SimpleNamespace

import numpy as np

from app.features.video import visible_calibrated_grid
from tools.valid.models.output_adapter import PoseLineStageConfig, extract_pose_lines, order_pose_lines


def tracking_frame(mode: str, grid: object):
    # 함수가 사용하는 최소 계약만 가진 가짜 tracking frame입니다.
    # 무거운 카메라·모델 없이 앱의 출력 허용 규칙만 검증합니다.
    return SimpleNamespace(result=SimpleNamespace(tracking_mode=mode, stable_grid=grid))


def test_hold_grid_is_internal_state_not_visible_app_output() -> None:
    stale_grid = object()

    assert visible_calibrated_grid(tracking_frame("selected_lines_hold", stale_grid)) is None


def test_verified_grid_is_visible_app_output() -> None:
    verified_grid = object()

    assert visible_calibrated_grid(tracking_frame("selected_lines_motion", verified_grid)) is verified_grid


def test_model_output_adapter_public_api_is_available() -> None:
    # baseline 모델 실행기에서 사용하는 공개 API가 이동 후에도 한 패키지에서 제공됩니다.
    assert PoseLineStageConfig is not None
    assert callable(extract_pose_lines)
    assert callable(order_pose_lines)


class _ArrayStub:
    """extractor가 사용하는 tensor 최소 인터페이스만 제공하는 테스트 대역입니다."""

    def __init__(self, values) -> None:
        self.values = np.asarray(values)

    def __len__(self) -> int:
        return len(self.values)

    def detach(self):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self.values


class _BoxesStub:
    """Ultralytics Boxes에서 extractor가 읽는 필드와 길이를 흉내 냅니다."""

    def __init__(self, classes, confidences) -> None:
        self.cls = _ArrayStub(classes)
        self.conf = _ArrayStub(confidences)

    def __len__(self) -> int:
        return len(self.cls)


def test_model_output_adapter_applies_point_confidence_threshold() -> None:
    result = SimpleNamespace(
        boxes=_BoxesStub([0], [0.9]),
        keypoints=SimpleNamespace(
            data=_ArrayStub([[[10.0, 20.0, 0.10], [30.0, 40.0, 0.90]]]),
        ),
    )
    config = PoseLineStageConfig(
        keypoint_count=2,
        min_visible_keypoints=2,
        point_conf_threshold=0.15,
    )

    # 첫 점이 threshold보다 낮으므로 visible 점 두 개 조건을 만족하지 못합니다.
    assert extract_pose_lines(result, {0: "fret"}, config=config) == []
