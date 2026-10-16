from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import QObject, Signal

from app.features.practice_audio import PracticeAudioController, RetriggerGate, with_step_context
from practice.score_steps import build_practice_steps


class PracticeAudioFeatureTests(unittest.TestCase):
    def test_raw_input_level_is_forwarded_and_disconnected_on_stop(self) -> None:
        """분석 결과 없이도 입력을 중계하고 중지 후 이전 장치의 값은 버린다."""
        class Worker(QObject):
            result_ready = Signal(dict)
            error_message = Signal(str)
            input_level_ready = Signal(float)

            def __init__(self, **kwargs):
                super().__init__(kwargs['parent'])

            def start(self):
                pass

            def stop(self):
                pass

        step = build_practice_steps([{'step': 1, 'string': 1, 'fret': 0}])[0]
        controller = PracticeAudioController()
        levels = []
        controller.input_level_ready.connect(levels.append)
        with patch('app.features.practice_audio.AudioWorker', Worker):
            controller.start_for_step(step)
        worker = controller.worker
        worker.input_level_ready.emit(0.02)
        self.assertEqual(levels, [0.02])
        controller.stop()
        worker.input_level_ready.emit(0.04)
        self.assertEqual(levels, [0.02])

    def test_retrigger_gate_blocks_same_note_until_release(self) -> None:
        steps = build_practice_steps(
            [
                {"step": 1, "string": 1, "fret": 0},
                {"step": 2, "string": 2, "fret": 5},
            ]
        )
        gate = RetriggerGate()
        gate.configure(steps[0], steps[1])

        held_result = gate.apply({"status": "correct", "is_correct": True})
        self.assertFalse(held_result["is_correct"])
        self.assertEqual(held_result["status"], "listening")

        release_result = gate.apply({"status": "no_signal", "is_correct": False})
        self.assertTrue(release_result["retrigger_ready"])

        replay_result = gate.apply({"status": "correct", "is_correct": True})
        self.assertTrue(replay_result["is_correct"])

    def test_with_step_context_adds_practice_metadata(self) -> None:
        step = build_practice_steps([{"step": 3, "string": 1, "fret": 5}])[0]

        result = with_step_context({"status": "correct"}, step, current_index=2, total_steps=8)

        self.assertEqual(result["practice_step"], 3)
        self.assertEqual(result["practice_index"], 2)
        self.assertEqual(result["practice_total"], 8)
        self.assertEqual(result["practice_mode"], "single")
        self.assertEqual(result["expected_label"], "1번줄 5프렛")
        self.assertAlmostEqual(result["expected_frequency"], 440.0, places=2)


if __name__ == "__main__":
    unittest.main()
