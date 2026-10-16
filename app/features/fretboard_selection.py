"""Qt 위젯 없이 지판 선택의 진행 상태와 보정 결과를 계산한다."""
from __future__ import annotations

from dataclasses import dataclass, replace

from vision.fretboard_calibration import build_calibrated_grid
from vision.fretboard_mapping import DetectedLine, StableFretboardGrid
from vision.line_selection import find_nearest_line


SELECTION_STAGES = frozenset({"select_nut", "select_middle", "select_fret"})


@dataclass(frozen=True)
class CalibrationSelection:
    """한 번의 입력 전후 상태. 원본을 바꾸지 않아 실패 후 재선택도 검증하기 쉽다."""
    stage: str = "idle"
    manual: bool = False
    nut: DetectedLine | None = None
    middle: DetectedLine | None = None
    reference: DetectedLine | None = None
    points: tuple[tuple[float, float], ...] = ()


@dataclass(frozen=True)
class SelectionResult:
    selection: CalibrationSelection
    grid: StableFretboardGrid | None = None
    error: str = ""


def valid_reference_frets(middle: int, last: int) -> bool:
    """보정기가 지원하는 번호 범위를 UI·선택 로직에서 공통으로 사용한다."""
    return 0 < middle < last <= 24


def requires_manual_selection(lines: tuple[DetectedLine, ...]) -> bool:
    """너트와 최소 두 프렛이 있어야 세 선의 자동 선택을 완료할 수 있다."""
    return not any(line.kind == "nut" for line in lines) or sum(line.kind == "fret" for line in lines) < 2


def advance_selection(selection: CalibrationSelection, point: tuple[float, float],
                      lines: tuple[DetectedLine, ...], middle_index: int,
                      reference_index: int) -> SelectionResult:
    """클릭 하나를 처리하고, 세 선이 완성되면 검증된 지판 모델을 반환한다.

    자동 선택과 수동 끝점 지정 모두 같은 보정기를 통과한다. 중간 선이 잘못되면
    너트만 보존하여 재선택하게 하며, 실패한 좌표로 안내점을 생성하지 않는다.
    """
    if selection.stage not in SELECTION_STAGES:
        return SelectionResult(selection)
    kind = "nut" if selection.stage == "select_nut" else "fret"
    if selection.manual:
        selection = replace(selection, points=(*selection.points, point))
        if len(selection.points) % 2:
            return SelectionResult(selection)
        first, second = selection.points[-2:]
        if (first[0] - second[0]) ** 2 + (first[1] - second[1]) ** 2 < 64:
            return SelectionResult(replace(selection, points=selection.points[:-2]),
                                   error="선의 양 끝점을 충분히 떨어뜨려 지정해주세요.")
        line = DetectedLine(kind, first, second, 1.0)
    else:
        line = find_nearest_line(point, tuple(line for line in lines if line.kind == kind))
        if line is None:
            return SelectionResult(selection, error="선에 더 가깝게 클릭하거나, 양 끝점 직접 지정을 사용해주세요.")

    if selection.stage == "select_nut":
        return SelectionResult(replace(selection, stage="select_middle", nut=line))
    if selection.stage == "select_middle":
        return SelectionResult(replace(selection, stage="select_fret", middle=line, reference=line))
    try:
        if selection.nut is None or selection.middle is None:
            raise ValueError("너트와 중간 프렛부터 다시 선택해주세요.")
        grid = build_calibrated_grid(selection.nut, selection.middle, middle_index, line, reference_index)
    except ValueError as error:
        return SelectionResult(replace(selection, stage="select_middle", middle=None, reference=None,
                                       points=selection.points[:2] if selection.manual else ()), error=str(error))
    return SelectionResult(replace(selection, stage="idle", reference=line), grid=grid)
