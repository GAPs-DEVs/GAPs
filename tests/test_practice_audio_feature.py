from __future__ import annotations

import unittest

from app.features.practice_audio import RetriggerGate, with_step_context
from practice.score_steps import build_practice_steps


class PracticeAudioFeatureTests(unittest.TestCase):
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
