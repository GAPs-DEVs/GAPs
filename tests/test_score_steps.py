from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from practice.score_steps import (
    build_practice_steps,
    build_practice_steps_from_json,
    load_parsed_notes_from_json,
    practice_step_frequency_signature,
    practice_steps_have_same_expected_frequencies,
)


class ScoreStepTests(unittest.TestCase):
    def test_build_practice_steps_groups_notes_by_step(self) -> None:
        parsed_notes = [
            {"step": 1, "string": 1, "fret": 7},
            {"step": 1, "string": 6, "fret": 0},
            {"step": 2, "string": 2, "fret": 0},
            {"step": 3, "string": 3, "fret": 0},
            {"step": 4, "string": 1, "fret": 5},
        ]

        steps = build_practice_steps(parsed_notes)

        self.assertEqual([step.step for step in steps], [1, 2, 3, 4])
        self.assertEqual(steps[0].mode, "chord")
        self.assertEqual(steps[1].mode, "single")
        self.assertEqual([note.label for note in steps[0].notes], ["1번줄 7프렛", "6번줄 0프렛"])

    def test_build_practice_steps_converts_string_and_fret_to_frequency(self) -> None:
        steps = build_practice_steps([{"step": 1, "string": 1, "fret": 5}])

        self.assertAlmostEqual(steps[0].expected_frequencies[0].frequency, 440.0, places=2)

    def test_build_practice_steps_rejects_missing_fields(self) -> None:
        with self.assertRaises(ValueError):
            build_practice_steps([{"step": 1, "string": 1}])

    def test_build_practice_steps_rejects_invalid_step(self) -> None:
        with self.assertRaises(ValueError):
            build_practice_steps([{"step": 0, "string": 1, "fret": 0}])

    def test_load_parsed_notes_from_json_accepts_top_level_list(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            json_path = Path(temp_dir) / "score.json"
            json_path.write_text(
                json.dumps(
                    [
                        {"step": 1, "string": 1, "fret": 7},
                        {"step": 1, "string": 6, "fret": 0},
                    ]
                ),
                encoding="utf-8",
            )

            parsed_notes = load_parsed_notes_from_json(json_path)
            steps = build_practice_steps_from_json(json_path)

        self.assertEqual(parsed_notes[0]["fret"], 7)
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].mode, "chord")

    def test_load_parsed_notes_from_json_accepts_wrapped_notes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            json_path = Path(temp_dir) / "score.json"
            json_path.write_text(
                json.dumps({"notes": [{"step": 1, "string": 2, "fret": 0}]}),
                encoding="utf-8",
            )

            parsed_notes = load_parsed_notes_from_json(json_path)

        self.assertEqual(parsed_notes, [{"step": 1, "string": 2, "fret": 0}])

    def test_load_parsed_notes_from_json_rejects_invalid_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            json_path = Path(temp_dir) / "score.json"
            json_path.write_text(json.dumps({"title": "song"}), encoding="utf-8")

            with self.assertRaises(ValueError):
                load_parsed_notes_from_json(json_path)

    def test_practice_steps_have_same_expected_frequencies(self) -> None:
        steps = build_practice_steps(
            [
                {"step": 1, "string": 1, "fret": 0},
                {"step": 2, "string": 2, "fret": 5},
                {"step": 3, "string": 1, "fret": 1},
            ]
        )

        self.assertEqual(practice_step_frequency_signature(steps[0]), (329.63,))
        self.assertTrue(practice_steps_have_same_expected_frequencies(steps[0], steps[1]))
        self.assertFalse(practice_steps_have_same_expected_frequencies(steps[0], steps[2]))


if __name__ == "__main__":
    unittest.main()
