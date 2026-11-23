from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np

from app.features import practice_audio
from app.features.practice_audio import PracticeAudioController, RetriggerGate, with_step_context
from audio.audio_worker import SharedAudioInput
from practice.score_steps import build_practice_steps


class FakeSignal:
    def connect(self, _slot) -> None:
        pass

    def disconnect(self, _slot) -> None:
        pass


class FakeWorker:
    def __init__(self, *_args, device=None, audio_input=None, **_kwargs) -> None:
        self.device = device
        self.audio_input = audio_input
        self.result_ready = FakeSignal()
        self.error_message = FakeSignal()
        self.finished = FakeSignal()
        self.running = False

    def start(self) -> None:
        self.running = True

    def isRunning(self) -> bool:
        return self.running

    def request_stop(self) -> None:
        self.running = False

    def set_target(self, *_args, **_kwargs) -> None:
        pass

    def wait(self, _timeout_ms: int) -> bool:
        return True

    def deleteLater(self) -> None:
        pass


class FakeSingleWorker(FakeWorker):
    pass


class FakeChordWorker(FakeWorker):
    pass


class FakeAudioInput:
    def __init__(self, device=None) -> None:
        self.device = device
        self.closed = False

    def start(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    def is_alive(self) -> bool:
        return not self.closed

    def join(self, _timeout: float) -> None:
        pass


class SharedAudioInputTests(unittest.TestCase):
    def test_delivers_blocks_to_every_subscriber_until_it_leaves(self) -> None:
        audio_input = SharedAudioInput()
        block = np.arange(4, dtype=np.float32).reshape(-1, 1)

        with audio_input.subscribe(maxsize=2) as first:
            with audio_input.subscribe(maxsize=2) as second:
                audio_input._callback(block, 4, None, None)
            audio_input._callback(block * 2, 4, None, None)

        audio_input._callback(block * 3, 4, None, None)

        self.assertEqual([first.get_nowait().tolist() for _ in range(first.qsize())], [[0, 1, 2, 3], [0, 2, 4, 6]])
        self.assertEqual(second.get_nowait().tolist(), [0, 1, 2, 3])
        self.assertTrue(second.empty())

    def test_full_subscriber_keeps_newest_blocks(self) -> None:
        audio_input = SharedAudioInput()

        with audio_input.subscribe(maxsize=2) as blocks:
            for value in range(4):
                audio_input._callback(np.full((2, 1), value, dtype=np.float32), 2, None, None)

        self.assertEqual([blocks.get_nowait()[0] for _ in range(2)], [2.0, 3.0])


class PracticeAudioControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inputs: list[FakeAudioInput] = []

        def make_input(device=None):
            audio_input = FakeAudioInput(device)
            self.inputs.append(audio_input)
            return audio_input

        for target, replacement in (
            ("SharedAudioInput", make_input),
            ("AudioWorker", FakeSingleWorker),
            ("ChordAudioWorker", FakeChordWorker),
        ):
            patcher = patch.object(practice_audio, target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.single = build_practice_steps([{"step": 1, "string": 1, "fret": 7}])[0]
        self.chord = build_practice_steps(
            [{"step": 1, "string": 1, "fret": 7}, {"step": 1, "string": 6, "fret": 0}]
        )[0]

    def test_switching_between_single_and_chord_steps_keeps_one_input_stream(self) -> None:
        controller = PracticeAudioController()

        workers = []
        for step in (self.single, self.chord, self.single, self.chord):
            controller.start_for_step(step, device=1)
            workers.append(controller.worker)

        self.assertEqual(len(self.inputs), 1)
        self.assertTrue(all(worker.audio_input is self.inputs[0] for worker in workers))
        self.assertFalse(self.inputs[0].closed)

        controller.stop()

        self.assertTrue(self.inputs[0].closed)

    def test_changing_device_reopens_input_stream(self) -> None:
        controller = PracticeAudioController()

        controller.start_for_step(self.single, device=1)
        controller.start_for_step(self.single, device=2)

        self.assertEqual([audio_input.device for audio_input in self.inputs], [1, 2])
        self.assertTrue(self.inputs[0].closed)
        self.assertFalse(self.inputs[1].closed)


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
