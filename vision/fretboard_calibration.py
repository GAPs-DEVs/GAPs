"""Three numbered cross-sections calibrate spacing without a rectangular neck.

Rectification is only an image coordinate system, not a rectangular physical
neck assumption. Three correspondences determine a 1-D projective distance
correction inside it. Neck taper is therefore not mistaken for perspective.
The middle wire may be clipped; its supporting line reaches both board sides.
"""
import cv2
import numpy as np

from vision.fretboard_mapping import (
    DetectedLine, StableFretboardGrid, build_grid_from_lines,
    orient_line_for_screen, orient_line_like_reference, scale_length_ratio,
)


def build_calibrated_grid(nut: DetectedLine, middle: DetectedLine, middle_index: int,
                          reference: DetectedLine, reference_index: int) -> StableFretboardGrid:
    if not 0 < middle_index < reference_index <= 24:
        raise ValueError("중간 프렛 번호는 1 이상이며 끝 기준 프렛 번호보다 작아야 합니다.")
    nut = orient_line_for_screen(nut)
    direction = np.asarray(nut.p1) - nut.p0
    reference = orient_line_like_reference(reference, direction)
    quad = np.asarray((nut.p0, nut.p1, reference.p1, reference.p0), dtype=np.float32)
    if (not np.isfinite(quad).all() or not cv2.isContourConvex(quad)
            or abs(cv2.contourArea(quad)) < 100):
        raise ValueError("너트와 끝 기준 프렛의 양 끝점을 다시 선택해주세요.")
    wire = np.asarray(middle.p1, dtype=float) - middle.p0
    if not np.isfinite(wire).all() or np.linalg.norm(wire) < 8:
        raise ValueError("중간 프렛의 금속선을 충분히 길게 선택해주세요.")
    to_unit = cv2.getPerspectiveTransform(quad, np.asarray(((0, 0), (1, 0), (1, 1), (0, 1)), dtype=np.float32))
    points = cv2.perspectiveTransform(np.asarray((middle.p0, middle.p1), dtype=float)[:, None], to_unit).reshape(2, 2)
    dx, dy = points[1] - points[0]
    if abs(dx) < 1e-6:
        raise ValueError("중간 프렛과 지판 양옆이 교차하도록 다시 선택해주세요.")
    intersections = points[0, 1] + (np.asarray((0., 1.)) - points[0, 0]) * dy / dx
    side_length = min(np.linalg.norm(quad[3] - quad[0]), np.linalg.norm(quad[2] - quad[1]))
    # Parallel physical fret wires must remain parallel after the outer pair
    # is rectified. Do not turn a misclicked diagonal into a twisted fret grid.
    if abs(intersections[1] - intersections[0]) > max(.015, 5 / side_length):
        raise ValueError("중간 프렛의 기울기가 다른 기준선과 맞지 않습니다. 금속선을 다시 선택해주세요.")
    u = float(np.mean(intersections))
    s = scale_length_ratio(middle_index) / scale_length_ratio(reference_index)
    if not .12 < s < .88 or not .08 < u < .92:
        raise ValueError("너트와 끝 기준 프렛 사이의 중앙에 더 가까운 중간 프렛을 선택해주세요.")
    # u(s) = (1+c)*s / (1+c*s), constrained to u(0)=0 and u(1)=1.
    c = (s - u) / (s * (u - 1))
    if not .15 < 1 + c < 6:
        raise ValueError("프렛 번호와 선택한 금속선의 위치를 확인해주세요.")
    lines = []
    into_image = np.linalg.inv(to_unit)
    for index in range(1, reference_index + 1):
        distance = scale_length_ratio(index) / scale_length_ratio(reference_index)
        t = (1 + c) * distance / (1 + c * distance)
        points = cv2.perspectiveTransform(np.asarray(((0., t), (1., t)))[:, None], into_image).reshape(2, 2)
        lines.append(DetectedLine('fret', tuple(points[0]), tuple(points[1]),
                                  min(nut.confidence, middle.confidence, reference.confidence)))
    return build_grid_from_lines(nut, lines)
