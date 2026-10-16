"""프렛 선 선택에 사용하는 화면 독립적인 기하 계산."""
from __future__ import annotations

import numpy as np

from vision.fretboard_mapping import DetectedLine


def point_to_segment_distance(point_xy, start_xy, end_xy) -> float:
    """점의 수직 투영을 선분 안으로 제한하여 실제 클릭 거리를 계산한다."""
    point = np.asarray(point_xy, dtype=np.float32)
    start = np.asarray(start_xy, dtype=np.float32)
    end = np.asarray(end_xy, dtype=np.float32)
    segment = end - start
    length_sq = float(np.dot(segment, segment))
    if length_sq <= 1e-6:
        return float(np.linalg.norm(point - start))
    ratio = float(np.dot(point - start, segment) / length_sq)
    projection = start + segment * min(1.0, max(0.0, ratio))
    return float(np.linalg.norm(point - projection))


def find_nearest_line(click_xy, candidate_lines: tuple[DetectedLine, ...],
                      max_distance: float = 30.0) -> DetectedLine | None:
    """허용 거리 안에서 가장 가까운 선만 반환한다. 번호는 추정하지 않는다."""
    nearest_line = None
    nearest_distance = max_distance
    for line in candidate_lines:
        distance = point_to_segment_distance(click_xy, line.p0, line.p1)
        if distance <= nearest_distance:
            nearest_distance = distance
            nearest_line = line
    return nearest_line
