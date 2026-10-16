"""화면·카메라·마이크 없이 지판 선택의 실제 결과와 오류 복구를 검사한다."""
import pytest

from app.features.fretboard_selection import (
    CalibrationSelection, advance_selection, requires_manual_selection, valid_reference_frets,
)
from vision.fretboard_mapping import DetectedLine
from vision.line_selection import find_nearest_line


def lines():
    return (DetectedLine('nut', (80, 50), (180, 50), 1.),
            DetectedLine('fret', (80, 170), (180, 170), 1.),
            DetectedLine('fret', (80, 310), (180, 310), 1.))


@pytest.mark.parametrize('manual', [False, True])
def test_line_selection_builds_same_physical_grid_in_both_modes(manual):
    original = CalibrationSelection(stage='select_nut', manual=manual)
    state = original
    points = ((80, 50), (180, 50), (80, 170), (180, 170), (80, 310), (180, 310)) if manual else (
        (130, 50), (130, 170), (130, 310))
    for point in points:
        result = advance_selection(state, point, lines(), 3, 7)
        assert not result.error
        state = result.selection
    assert original.stage == 'select_nut'  # 입력 상태는 바꾸지 않고 새 상태를 반환한다.
    assert state.stage == 'idle'
    assert result.grid.ordered_fret_lines[-1].p0 == pytest.approx((80, 310))
    assert len(result.grid.ordered_fret_lines) == 7


def test_rejected_endpoint_does_not_leave_partial_line():
    state = CalibrationSelection(stage='select_nut', manual=True)
    first = advance_selection(state, (80, 50), (), 3, 7)
    second = advance_selection(first.selection, (81, 50), (), 3, 7)
    assert second.error
    assert second.selection.points == ()
    assert second.selection.stage == 'select_nut'


def test_invalid_middle_line_preserves_only_nut_for_retry():
    state = CalibrationSelection(stage='select_nut', manual=True)
    for point in ((80, 50), (180, 50), (80, 340), (180, 340), (80, 310), (180, 310)):
        result = advance_selection(state, point, (), 3, 7)
        state = result.selection
    assert result.grid is None and result.error
    assert state.stage == 'select_middle'
    assert state.nut is not None and state.middle is None
    assert state.points == ((80, 50), (180, 50))


def test_automatic_selection_ignores_wrong_class_and_distant_lines():
    state = CalibrationSelection(stage='select_nut')
    result = advance_selection(state, (130, 170), lines(), 3, 7)
    assert result.error and result.selection == state
    assert find_nearest_line((500, 500), lines()) is None


def test_manual_fallback_requires_nut_and_two_frets():
    assert not requires_manual_selection(lines())
    assert requires_manual_selection(lines()[1:])
    assert requires_manual_selection(lines()[:2])
    assert requires_manual_selection(())


@pytest.mark.parametrize('middle,last,valid', [(3, 7, True), (7, 24, True), (0, 7, False),
                                             (7, 7, False), (8, 7, False), (7, 25, False)])
def test_reference_numbers_respect_calibration_limits(middle, last, valid):
    assert valid_reference_frets(middle, last) is valid
