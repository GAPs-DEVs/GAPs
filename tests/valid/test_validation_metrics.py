"""검증 타원 판정이 화면 방향과 누락 규칙을 정확히 따르는지 확인합니다."""

from math import sqrt

import pytest

from tools.valid.module import AnswerPoints, GuidePoint


def point(fret: int, x: float, y: float) -> GuidePoint:
    return GuidePoint(string=1, fret=fret, x=x, y=y, visible=True)


def test_ellipse_long_axis_follows_neighboring_fret_direction() -> None:
    # 세 GT 점을 45도 방향으로 놓으면 가운데 점의 타원 긴 축도 45도가 됩니다.
    answer = AnswerPoints(
        points=[
            point(1, 50.0, 50.0),
            point(2, 100.0, 100.0),
            point(3, 150.0, 150.0),
        ]
    )
    diagonal_offset = 15.0 / sqrt(2.0)
    prediction = [
        point(1, 50.0, 50.0),
        point(2, 100.0 + diagonal_offset, 100.0 + diagonal_offset),
        point(3, 150.0, 150.0),
    ]

    result = answer.L2_check(prediction)

    # 긴 반지름은 20px이므로 긴 축 방향 15px 이동은 타원 안입니다.
    assert (1, 2) in result.region_correct_keys
    assert result.region_accuracy == pytest.approx(1.0)


def test_ellipse_rejects_short_axis_error_and_missing_prediction() -> None:
    answer = AnswerPoints(
        points=[
            point(1, 50.0, 50.0),
            point(2, 100.0, 100.0),
            point(3, 150.0, 150.0),
        ]
    )
    perpendicular_offset = 6.0 / sqrt(2.0)
    prediction = [
        point(1, 50.0, 50.0),
        # 짧은 반지름은 5px이므로 긴 축에 수직인 방향의 6px 이동은 실패입니다.
        point(2, 100.0 - perpendicular_offset, 100.0 + perpendicular_offset),
        # fret=3 예측은 의도적으로 생략하여 누락도 실패로 계산되는지 확인합니다.
    ]

    result = answer.L2_check(prediction)

    assert result.region_correct_keys == [(1, 1)]
    assert result.region_incorrect_keys == [(1, 2), (1, 3)]
    assert result.missing_keys == [(1, 3)]
    assert result.region_accuracy == pytest.approx(1 / 3)

