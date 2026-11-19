import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from app.ui.practice_panel import PracticePanel
from practice.score_steps import build_practice_steps


@pytest.fixture
def panel():
    application = QApplication.instance() or QApplication([])
    widget = PracticePanel()
    widget.resize(396, 600)
    widget.show()
    yield widget
    widget.close()
    application.processEvents()


def chord():
    return build_practice_steps([
        {"step": 1, "string": 1, "fret": 7},
        {"step": 1, "string": 6, "fret": 0},
    ])[0]


def test_default_feedback_shows_positions_without_frequency_or_instructions(panel):
    panel.show_step(chord(), 0, 130)
    assert panel.progress_label.text() == "1 / 130"
    assert panel.progress_bar.value() == 1
    assert panel.progress_bar.maximum() == 130
    assert [card.string_label.text() for card in panel.note_cards] == ["1번 줄", "6번 줄"]
    assert [card.fret_label.text() for card in panel.note_cards] == ["7프렛", "개방현"]
    assert panel.details_widget.isHidden()
    visible_text = " ".join(label.text() for label in panel.findChildren(QLabel) if label.isVisibleTo(panel))
    assert all(text not in visible_text for text in ("Hz", "step", "함께 연주", "누르지 않고"))


def test_partial_match_marks_only_detected_position_and_resets_for_next_step(panel):
    step = chord()
    panel.show_step(step, 0, 130)
    panel.show_audio_result({
        "practice_mode": "chord", "status": "incorrect", "is_correct": False,
        "matched_notes": [step.notes[0].label], "missing_notes": [step.notes[1].label],
    })
    assert panel.status_label.text() == "1 / 2음 확인"
    assert [card.match_label.text() for card in panel.note_cards] == ["✓", ""]
    panel.show_audio_result({"practice_mode": "chord", "status": "correct", "is_correct": True})
    assert panel.status_label.text() == "정확해요!"
    assert all(card.match_label.text() == "✓" for card in panel.note_cards)
    panel.show_step(step, 1, 130)
    assert panel.progress_label.text() == "2 / 130"
    assert panel.progress_bar.value() == 2
    assert all(card.match_label.text() == "" for card in panel.note_cards)


def test_single_note_frequency_is_available_only_in_expanded_details(panel):
    step = build_practice_steps([{"step": 1, "string": 1, "fret": 5}])[0]
    panel.show_step(step, 0, 1)
    panel.show_audio_result({
        "practice_mode": "single", "status": "correct", "is_correct": True,
        "detected_frequency": 440.0, "cents_difference": 0.0,
    })
    assert panel.note_cards[0].match_label.text() == "✓"
    assert "440.00 Hz" in panel.detected_label.text()
    assert not panel.detected_label.isVisibleTo(panel)
    QTest.mouseClick(panel.details_button, Qt.MouseButton.LeftButton)
    assert panel.detected_label.isVisibleTo(panel)
    QTest.mouseClick(panel.details_button, Qt.MouseButton.LeftButton)
    assert not panel.detected_label.isVisibleTo(panel)


def test_repeat_gate_does_not_show_old_note_as_new_success(panel):
    step = chord()
    panel.show_step(step, 1, 130)
    panel.show_audio_result({
        "practice_mode": "chord", "status": "listening", "is_correct": False,
        "matched_notes": [note.label for note in step.notes],
        "retrigger_required": True, "retrigger_ready": False,
    })
    assert panel.status_label.text() == "소리를 끊고 다시 연주해주세요"
    assert all(card.match_label.text() == "" for card in panel.note_cards)


def test_silence_and_error_clear_card_matches_and_error_is_visible(panel):
    panel.show_step(chord(), 0, 130)
    panel.show_audio_result({"status": "correct", "is_correct": True})
    panel.show_audio_result({"status": "no_signal", "is_correct": False})
    assert panel.status_label.text() == "소리 대기 중"
    assert all(card.match_label.text() == "" for card in panel.note_cards)
    panel.show_audio_result({"status": "error", "message": "마이크 연결을 확인해주세요."})
    assert "마이크 연결을 확인해주세요." in panel.status_label.text()
    assert panel.details_widget.isHidden()


@pytest.mark.skipif(sys.platform == "win32", reason="Windows font metrics wrap chips into a different row count")
def test_six_note_chips_reflow_without_clipping_or_horizontal_overflow(panel):
    step = build_practice_steps([{"step": 1, "string": i, "fret": 0 if i == 6 else 24} for i in range(1, 7)])[0]
    panel.show_step(step, 0, 1)
    for width, expected_rows in ((396, 2), (320, 3), (396, 2)):
        panel.resize(width, 600)
        QApplication.processEvents()
        QApplication.processEvents()
        assert panel.width() == width
        assert len({card.y() for card in panel.note_cards}) == expected_rows
        assert panel.notes_widget.height() <= (82 if expected_rows == 2 else 124)
        for card in panel.note_cards:
            assert card.geometry().right() < panel.notes_widget.width()
            assert card.geometry().left() >= 0
            assert card.string_label.y() == card.fret_label.y()
            for label in (card.string_label, card.fret_label):
                assert label.width() >= label.minimumSizeHint().width()
        panel.show_audio_result({"status": "correct", "is_correct": True})
        QApplication.processEvents()
        assert len({card.y() for card in panel.note_cards}) == expected_rows
