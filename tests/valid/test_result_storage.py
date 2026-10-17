"""검증 결과 저장 형식과 공개 API의 회귀를 확인합니다."""

import json

from tools.valid.module import (
    AnswerPoints,
    CheckResult,
    FrameRecord,
    GuidePoint,
    ValidResultManager,
)


def _point(x: float, y: float) -> GuidePoint:
    return GuidePoint(string=1, fret=1, x=x, y=y, visible=True)


def test_l2_check_only_calculates_without_printing(capsys) -> None:
    answer = AnswerPoints([_point(10.0, 20.0)])

    result = answer.L2_check([_point(13.0, 24.0)])

    assert isinstance(result, CheckResult)
    assert result.mean_error_px == 5.0
    assert capsys.readouterr().out == ""


def test_frame_result_uses_one_json_and_viewer_reads_prediction(tmp_path) -> None:
    answer = AnswerPoints([_point(10.0, 20.0)])
    prediction = [_point(11.0, 21.0)]
    result = answer.L2_check(prediction)
    manager = ValidResultManager(tmp_path)

    manager.add_frame_result(0, prediction, result)

    assert (manager.result_dir / "0000_result.json").exists()
    assert not (manager.result_dir / "0000_pred.json").exists()
    assert manager.load_pred_points(0) == prediction


def test_viewer_keeps_legacy_pred_json_compatibility(tmp_path) -> None:
    prediction = [_point(11.0, 21.0)]
    manager = ValidResultManager(tmp_path)
    manager.result_dir.mkdir(parents=True)
    legacy_path = manager.result_dir / "0000_pred.json"
    legacy_path.write_text(
        json.dumps(GuidePoint.list_to_payload(prediction)),
        encoding="utf-8",
    )

    assert manager.load_pred_points(0) == prediction


def test_frame_record_is_public() -> None:
    result = AnswerPoints([_point(10.0, 20.0)]).L2_check([])

    assert FrameRecord(frame_index=0, result=result).frame_index == 0
