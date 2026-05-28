from __future__ import annotations

import numpy as np

from .types import PoseLine, PoseLineStageConfig


def _resolve_class_name(names: dict[int, str] | list[str], class_id: int) -> str:
    """YOLO class id를 사람이 읽는 class name으로 바꿉니다.

    이 프로젝트에서는 결국 `fret`, `nut`만 쓰기 때문에
    이후 단계가 단순해지도록 여기서 이름을 정규화합니다.
    """
    if isinstance(names, list):
        return names[class_id]
    return names.get(class_id, str(class_id))


def _extract_pose_arrays(result):
    """YOLO result에서 class / confidence / keypoint 배열만 안전하게 꺼냅니다.

    모델 출력 형식이 비어 있거나 일부 필드가 없는 경우가 있으므로,
    여기서 한 번에 검사한 뒤 실패하면 `None`을 돌려줍니다.
    """
    boxes = getattr(result, "boxes", None)
    keypoints = getattr(result, "keypoints", None)
    if boxes is None or keypoints is None or boxes.cls is None or keypoints.data is None:
        return None
    if len(boxes) == 0 or len(keypoints.data) == 0:
        return None

    classes = boxes.cls.detach().cpu().numpy().astype(int)
    confidences = boxes.conf.detach().cpu().numpy()
    kp_data = keypoints.data.detach().cpu().numpy()
    return classes, confidences, kp_data


def _build_pose_line(
    *,
    class_name: str,
    confidence: float,
    points,
    pipeline_config: PoseLineStageConfig,
) -> PoseLine | None:
    """한 검출 결과를 `PoseLine` 하나로 바꿉니다.

    여기서 하는 일:
    1. 필요한 keypoint 개수만 자르기
    2. visibility를 bool 배열로 만들기
    3. 실제로 보이는 점들만 사용해 중심을 계산하기
    """
    if len(points) < pipeline_config.keypoint_count:
        return None

    xy = np.asarray(points[: pipeline_config.keypoint_count, :2], dtype=np.float32)
    visibility = np.asarray(points[: pipeline_config.keypoint_count, 2] > 0.0, dtype=bool)
    if int(np.count_nonzero(visibility)) < pipeline_config.min_visible_keypoints:
        return None

    visible_xy = xy[visibility] if np.any(visibility) else xy
    center_xy = np.mean(visible_xy, axis=0)
    return PoseLine(
        kind=class_name,
        confidence=float(confidence),
        points_xy=xy,
        visibility=visibility,
        center_xy=np.asarray(center_xy, dtype=np.float32),
    )


def extract_pose_lines(
    result,
    names: dict[int, str] | list[str],
    config: PoseLineStageConfig | None = None,
) -> list[PoseLine]:
    """YOLO pose result에서 `fret`, `nut` PoseLine만 추출합니다.

    의도:
    - 모델 raw 결과를 가능한 한 빨리 프로젝트 공용 자료형인 `PoseLine`으로 바꿉니다.
    - 여기서는 tracking / prior / 정렬을 하지 않습니다.
    - 즉 이 함수는 순수하게 "모델 출력 -> PoseLine 변환"만 담당합니다.
    """
    pipeline_config = config or PoseLineStageConfig()

    extracted = _extract_pose_arrays(result)
    if extracted is None:
        return []
    classes, confidences, kp_data = extracted

    lines: list[PoseLine] = []
    for index, class_id in enumerate(classes):
        if index >= len(kp_data):
            continue

        class_name = _resolve_class_name(names, int(class_id))
        if class_name not in {"fret", "nut"}:
            continue

        pose_line = _build_pose_line(
            class_name=class_name,
            confidence=float(confidences[index]),
            points=kp_data[index],
            pipeline_config=pipeline_config,
        )
        if pose_line is not None:
            lines.append(pose_line)
    return lines
