from __future__ import annotations

import numpy as np

from fretboard_mapping import DetectedLine, StableFretboardGrid

# from .types import GuidePoint, PipelineConfig, PoseLine, PoseLineStageConfig
from .types import PoseLine, PoseLineStageConfig


def _normalize_vector(vector: np.ndarray) -> np.ndarray:
    '''
        # 방향 벡터를 길이 1로 맞춥니다.
        # 길이가 거의 0이면 임시 기본축을 반환해 후속 계산이 깨지지 않게 합니다.
    '''
    norm = float(np.linalg.norm(vector))
    if norm <= 1e-6:
        return np.asarray([1.0, 0.0], dtype=np.float32)
    return (vector / norm).astype(np.float32)


def perpendicular(vector: np.ndarray) -> np.ndarray:
    '''
        2차원 벡터 (x, y)에 대해 (y, -x)를 만들면 원래 벡터와 직교합니다.
        즉 fret 선이 화면에서 길게 놓인 방향이 `vector`라면,
        이 함수는 그 fret 선을 가로질러 fret 번호가 증가하는 쪽의
        "neck 방향 후보축"을 하나 만들어 줍니다.
        
        주의:
        - 여기서 만들어지는 축은 "수직"만 보장합니다.
        - 아직 nut -> high fret 방향인지, 그 반대 방향인지는 확정되지 않습니다.
        - 그래서 이후 단계에서 nut/fret 분포를 보고 필요하면 부호를 뒤집습니다.
    # '''
    return np.asarray([float(vector[1]), -float(vector[0])], dtype=np.float32)


def estimate_fret_axis(lines: tuple[PoseLine, ...] | list[PoseLine]) -> np.ndarray:
    '''
        현재 프레임의 line들만 보고 "각 선 자체가 길게 뻗는 방향"의 대표축을 구합니다.
        
        여기서 구하는 축은
        - fret들을 나중에 뒤집지 않도록 점 순서를 통일할 때 쓰고
        - neck_axis = perpendicular(fret_axis) 를 만들 때의 기준축이 됩니다.
        
        동작 의도:
        1. 각 line마다 "첫 visible 점 -> 마지막 visible 점" 방향벡터를 구한다.
        2. 첫 번째 유효 line의 방향을 reference로 삼는다.
        3. 이후 line이 reference와 반대 방향이면 부호를 뒤집어 같은 방향계로 맞춘다.
        즉 [->, <-, ->] 처럼 섞여 들어온 방향을 모두 [->, ->, ->] 로 맞추는 단계다.
        4. confidence를 가중치로 주어 더 자신 있는 line이 대표축에 더 많이 기여하게 한다.
        5. 마지막에 가중 평균한 결과를 다시 정규화해 대표축 하나로 만든다.
        
        왜 필요한가:
        - 모델은 같은 fret를 프레임마다 앞뒤 반대로 낼 수 있다.
        - 그런 상태로는 정렬과 neck 방향 판정이 불안정해진다.
        - 그래서 먼저 "현재 프레임에서 선들이 대체로 어느 방향으로 놓였는가"를
        하나의 축으로 정리해두려는 목적이다.
    # '''
    reference: np.ndarray | None = None
    vectors: list[np.ndarray] = []
    for line in lines:
        visible_indices = np.flatnonzero(line.visibility)
        if visible_indices.size < 2:
            continue
        start = line.points_xy[int(visible_indices[0])]
        end = line.points_xy[int(visible_indices[-1])]
        vector = _normalize_vector(end - start)
        if reference is None:
            reference = vector
        elif float(np.dot(reference, vector)) < 0.0:
            vector = -vector
        vectors.append(vector * max(float(line.confidence), 1e-3))
    if not vectors:
        return np.asarray([0.0, 1.0], dtype=np.float32)
    return _normalize_vector(np.sum(np.asarray(vectors, dtype=np.float32), axis=0))


def pose_line_to_detected_line(line: PoseLine) -> DetectedLine | None:
    '''
        6개 keypoint로 표현된 PoseLine을 화면 표시용 단순 선분으로 바꿉니다.
        첫 visible 점과 마지막 visible 점을 line의 양끝으로 사용합니다.
    '''
    visible_indices = np.flatnonzero(line.visibility)
    if visible_indices.size < 2:
        return None
    start_index = int(visible_indices[0])
    end_index = int(visible_indices[-1])
    return DetectedLine(
        kind=line.kind,
        p0=(float(line.points_xy[start_index, 0]), float(line.points_xy[start_index, 1])),
        p1=(float(line.points_xy[end_index, 0]), float(line.points_xy[end_index, 1])),
        confidence=float(line.confidence),
    )


def pose_lines_to_detected_lines(lines: tuple[PoseLine, ...] | list[PoseLine]) -> tuple[DetectedLine, ...]:
    """PoseLine 집합을 화면 표시용 DetectedLine 집합으로 바꿉니다."""
    detected_lines: list[DetectedLine] = []
    for line in lines:
        detected = pose_line_to_detected_line(line)
        if detected is not None:
            detected_lines.append(detected)
    return tuple(detected_lines)


def orient_pose_line(line: PoseLine, reference_axis: np.ndarray) -> PoseLine:
    # line 내부 keypoint 순서를 대표축 방향으로 통일합니다.
    # 같은 선이 프레임마다 반대로 들어와도 start->end 방향이 일관되게 되도록 맞춥니다.
    visible_indices = np.flatnonzero(line.visibility)
    if visible_indices.size < 2:
        return line
    start = line.points_xy[int(visible_indices[0])]
    end = line.points_xy[int(visible_indices[-1])]
    if float(np.dot(end - start, reference_axis)) >= 0.0:
        return line

    return PoseLine(
        kind=line.kind,
        confidence=float(line.confidence),
        points_xy=np.ascontiguousarray(line.points_xy[::-1], dtype=np.float32),
        visibility=np.ascontiguousarray(line.visibility[::-1], dtype=bool),
        center_xy=np.asarray(line.center_xy, dtype=np.float32),
    )


def estimate_neck_axis(
    lines: tuple[PoseLine, ...] | list[PoseLine],
) -> np.ndarray:
    # fret_axis에 직교하는 축을 neck 방향 후보로 사용합니다.
    fret_axis = estimate_fret_axis(lines)
    return perpendicular(fret_axis)


def _coerce_pose_line_stage_config(
    config: PoseLineStageConfig | None,
) -> PoseLineStageConfig:
    """입력 정리 단계에서 필요한 최소 config만 꺼냅니다.

    `order_pose_lines()`는 여러 곳에서 호출되므로,
    기존 `PipelineConfig`가 들어와도 현재 단계에서 필요한 값만 추려
    `PoseLineStageConfig`로 통일합니다.
    """
    if config is None:
        return PoseLineStageConfig()
    if isinstance(config, PoseLineStageConfig):
        return config
    return PoseLineStageConfig(
        keypoint_count=config.keypoint_count,
        min_visible_keypoints=config.min_visible_keypoints,
        nut_fret_overlap_px=config.nut_fret_overlap_px,
        adjacent_fret_overlap_px=config.adjacent_fret_overlap_px,
    )


def _choose_nut_line(
    nut_lines: list[PoseLine],
    fret_lines: list[PoseLine],
    neck_axis: np.ndarray,
) -> PoseLine | None:
    """현재 프레임 nut 후보 중 대표 nut 하나를 고릅니다."""
    # 단일 프레임 후처리이므로,
    # 이전 프레임 정보는 쓰지 않습니다.
    #
    # 대신 현재 fret 분포를 같이 보면서 nut를 고릅니다.
    # 좋은 nut 후보의 성질은 대체로 이렇습니다.
    # 1. 대부분의 fret가 neck_axis의 같은 쪽(앞쪽)에 있어야 합니다.
    # 2. nut와 가장 가까운 첫 fret 간격이 너무 0에 가깝지 않아야 합니다.
    # 3. 기본적으로 confidence가 높을수록 좋습니다.
    if not nut_lines:
        return None
    if not fret_lines:
        return max(nut_lines, key=lambda line: line.confidence)

    scored: list[tuple[float, float, float, PoseLine]] = []
    for nut_line in nut_lines:
        projections = sorted(
            float(np.dot(fret_line.center_xy - nut_line.center_xy, neck_axis))
            for fret_line in fret_lines
        )
        positive_projections = [value for value in projections if value > 0.0]
        negative_count = len(projections) - len(positive_projections)

        # 기본적으로 fret는 nut의 "앞쪽"에 있어야 하므로
        # negative가 많을수록 나쁜 nut 후보입니다.
        side_penalty = float(negative_count)

        # 첫 fret와 nut가 사실상 같은 위치면 좋지 않으므로
        # 가장 가까운 양의 투영값이 너무 작으면 penalty를 줍니다.
        overlap_penalty = 1.0
        gap_penalty = 0.0
        if positive_projections:
            first_positive = positive_projections[0]
            overlap_penalty = max(0.0, 12.0 - first_positive)

            # 첫 fret 이후 간격들이 어느 정도 안정적이면 더 자연스러운 nut 후보로 봅니다.
            if len(positive_projections) >= 3:
                positive_array = np.asarray(positive_projections[:6], dtype=np.float32)
                positive_gaps = np.diff(positive_array)
                valid_gaps = positive_gaps[positive_gaps > 1e-6]
                if valid_gaps.size >= 2:
                    gap_penalty = float(np.std(valid_gaps))
        else:
            # nut 앞쪽에 fret가 하나도 없으면 거의 잘못된 후보입니다.
            overlap_penalty = 1000.0
            gap_penalty = 1000.0

        scored.append(
            (
                side_penalty + overlap_penalty + gap_penalty * 0.1,
                -float(len(positive_projections)),
                -float(nut_line.confidence),
                nut_line,
            )
        )

    scored.sort(key=lambda item: (item[0], item[1], item[2]))
    return scored[0][3]

def _sort_frets(
    fret_lines: list[PoseLine],
    nut_line: PoseLine | None,
    neck_axis: np.ndarray,
) -> list[PoseLine]:
    # fret를 neck 방향으로 정렬합니다.
    # nut가 있으면 nut 기준 상대거리로,
    # nut가 없으면 절대 투영값으로 순서를 잡습니다.
    if not fret_lines:
        return []

    if nut_line is None:
        return sorted(fret_lines, key=lambda line: float(np.dot(line.center_xy, neck_axis)))
    return sorted(
        fret_lines,
        key=lambda line: float(np.dot(line.center_xy - nut_line.center_xy, neck_axis)),
    )


def _deduplicate_oriented_lines(
    lines: list[PoseLine],
    neck_axis: np.ndarray,
    config: PoseLineStageConfig,
) -> list[PoseLine]:
    """정렬 전에 raw 선들에서 같은 자리에 겹친 후보를 먼저 줄입니다.

    의도:
    - nut/fret 역할 해석 전에 위치 중복부터 줄입니다.
    - 이후 nut 선택과 fret 정렬이 더 안정된 입력을 받게 합니다.
    """
    if len(lines) <= 1:
        return lines

    # 정렬 전 단계이지만, overlap 비교를 위해 neck 방향으로 대략 한 번 정렬합니다.
    # 이렇게 하면 "같은 자리에 겹친 선"을 인접 비교만으로 줄일 수 있습니다.
    prelim_ordered = sorted(
        lines,
        key=lambda line: float(np.dot(line.center_xy, neck_axis)),
    )
    overlap_threshold = float(
        min(config.nut_fret_overlap_px, config.adjacent_fret_overlap_px)
    )

    kept_lines: list[PoseLine] = [prelim_ordered[0]]
    for candidate_line in prelim_ordered[1:]:
        previous_line = kept_lines[-1]
        center_delta = (
            np.asarray(candidate_line.center_xy, dtype=np.float32)
            - np.asarray(previous_line.center_xy, dtype=np.float32)
        )
        neck_distance = abs(
            float(np.dot(center_delta, np.asarray(neck_axis, dtype=np.float32)))
        )
        center_distance = float(np.linalg.norm(center_delta))

        # neck 방향 거리와 전체 center 거리가 모두 충분히 작으면
        # 사실상 같은 자리에 나온 중복 선으로 보고 confidence 높은 쪽만 남깁니다.
        if neck_distance <= overlap_threshold and center_distance <= overlap_threshold:
            if float(candidate_line.confidence) > float(previous_line.confidence):
                kept_lines[-1] = candidate_line
            continue
        kept_lines.append(candidate_line)

    return kept_lines



def order_pose_lines(
    lines: tuple[PoseLine, ...] | list[PoseLine],
    config: PoseLineStageConfig | None = None,
) -> tuple[PoseLine, ...]:
    """raw PoseLine들을 입력 정리용 순서로 정렬합니다.

    현재 단계 순서:
    1. fret axis 기준으로 점 방향 통일
    2. neck axis 기준으로 겹친 선 먼저 제거
    3. nut 후보 선택
    4. 남은 fret 정렬
    """
    if not lines:
        return ()
    active_config = _coerce_pose_line_stage_config(config)

    # 1. 현재 프레임 line들에서 fret 대표축을 계산합니다.
    frets_axis = estimate_fret_axis(lines)
    # 2. 모든 line 내부 keypoint 순서를 대표축 방향으로 통일합니다.
    oriented_lines = [orient_pose_line(line, frets_axis) for line in lines]
    if not oriented_lines:
        return ()

    # 3. fret 축에 수직인 neck 방향 후보축을 만듭니다.
    neck_axis = perpendicular(frets_axis)

    # 4. nut/fret 역할을 해석하기 전에, 같은 자리에 겹친 raw 선들을 먼저 제거합니다.
    deduplicated_lines = _deduplicate_oriented_lines(
        oriented_lines,
        neck_axis=neck_axis,
        config=active_config,
    )
    nut_lines = [line for line in deduplicated_lines if line.kind == "nut"]
    fret_lines = [line for line in deduplicated_lines if line.kind == "fret"]
    if not fret_lines and not nut_lines:
        return ()

    # 5. 남은 nut 후보들 중 대표 nut 하나를 고릅니다.
    nut_line = _choose_nut_line(
        nut_lines,
        fret_lines=fret_lines,
        neck_axis=neck_axis,
    )

    # 6. nut 기준으로 봤을 때 fret들이 neck_axis 반대편에 더 많이 있으면
    # 현재 neck 방향 부호가 뒤집힌 것으로 보고 축을 한 번 뒤집습니다.
    if nut_line is not None and fret_lines:
        projections = [float(np.dot(line.center_xy - nut_line.center_xy, neck_axis)) for line in fret_lines]
        if projections and sum(value > 0.0 for value in projections) < sum(value < 0.0 for value in projections):
            neck_axis = -neck_axis
    # neck_axis = -neck_axis

    # 7. 최종 neck 방향 기준으로 fret 순서를 정렬합니다.
    ordered_frets = _sort_frets(
        fret_lines,
        nut_line=nut_line,
        neck_axis=neck_axis,
    )
    # 8. 대표 nut가 있으면 맨 앞에 붙여 반환하고, 없으면 fret들만 반환합니다.
    if nut_line is not None:
        return (nut_line, *ordered_frets)
    return tuple(ordered_frets)
