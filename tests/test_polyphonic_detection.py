from __future__ import annotations

import unittest

import numpy as np

from audio.music_math import SOLFEGE_TEST_NOTE_FREQUENCIES
from audio.polyphonic_detection import (
    ChordJudgeGate,
    ExpectedFrequency,
    PolyphonicDetectionResult,
    PolyphonicNoteMatch,
    detect_expected_frequencies_cqt,
)


def synthesize_notes(
    frequencies: list[float],
    sample_rate: int = 44100,
    duration: float = 1.2,
    harmonic_count: int = 2,
) -> np.ndarray:
    time = np.arange(int(sample_rate * duration), dtype=np.float32) / sample_rate
    samples = np.zeros_like(time)
    for frequency in frequencies:
        samples += 0.25 * np.sin(2.0 * np.pi * frequency * time)
        if harmonic_count >= 2:
            samples += 0.08 * np.sin(2.0 * np.pi * frequency * 2.0 * time)
        if harmonic_count >= 3:
            for harmonic in range(3, harmonic_count + 1):
                amplitude = 0.12 / harmonic
                samples += amplitude * np.sin(2.0 * np.pi * frequency * harmonic * time)
    return samples.astype(np.float32)


class PolyphonicDetectionTests(unittest.TestCase):
    def test_detects_expected_c_major_triad(self) -> None:
        expected = [
            ExpectedFrequency("낮은 도", SOLFEGE_TEST_NOTE_FREQUENCIES["낮은 도"]),
            ExpectedFrequency("미", SOLFEGE_TEST_NOTE_FREQUENCIES["미"]),
            ExpectedFrequency("솔", SOLFEGE_TEST_NOTE_FREQUENCIES["솔"]),
        ]
        samples = synthesize_notes([note.frequency for note in expected])

        result = detect_expected_frequencies_cqt(samples, sample_rate=44100, expected_frequencies=expected)

        self.assertEqual(result.status, "correct")
        self.assertTrue(result.is_correct)
        self.assertEqual({match.name for match in result.matches if match.is_present}, {"낮은 도", "미", "솔"})

    def test_marks_missing_note_when_expected_frequency_is_absent(self) -> None:
        expected = [
            ExpectedFrequency("낮은 도", SOLFEGE_TEST_NOTE_FREQUENCIES["낮은 도"]),
            ExpectedFrequency("미", SOLFEGE_TEST_NOTE_FREQUENCIES["미"]),
            ExpectedFrequency("솔", SOLFEGE_TEST_NOTE_FREQUENCIES["솔"]),
        ]
        samples = synthesize_notes(
            [
                SOLFEGE_TEST_NOTE_FREQUENCIES["낮은 도"],
                SOLFEGE_TEST_NOTE_FREQUENCIES["솔"],
            ]
        )

        result = detect_expected_frequencies_cqt(samples, sample_rate=44100, expected_frequencies=expected)

        self.assertEqual(result.status, "incorrect")
        self.assertFalse(result.is_correct)
        self.assertIn("미", {match.name for match in result.matches if not match.is_present})

    def test_returns_no_signal_for_silence(self) -> None:
        expected = [ExpectedFrequency("낮은 도", SOLFEGE_TEST_NOTE_FREQUENCIES["낮은 도"])]
        samples = np.zeros(8192, dtype=np.float32)

        result = detect_expected_frequencies_cqt(samples, sample_rate=44100, expected_frequencies=expected)

        self.assertEqual(result.status, "no_signal")
        self.assertFalse(result.is_correct)

    def test_rejects_upper_note_when_only_lower_string_harmonic_is_present(self) -> None:
        expected = [
            ExpectedFrequency("1번줄 7프렛", 493.89),
            ExpectedFrequency("6번줄 0프렛", 82.41),
        ]
        samples = synthesize_notes([82.41], harmonic_count=6)

        result = detect_expected_frequencies_cqt(samples, sample_rate=44100, expected_frequencies=expected)
        matches = {match.name: match for match in result.matches}

        self.assertEqual(result.status, "incorrect")
        self.assertFalse(result.is_correct)
        self.assertFalse(matches["1번줄 7프렛"].is_present)
        self.assertEqual(matches["1번줄 7프렛"].rejection_reason, "probable_harmonic_of:6번줄 0프렛")

    def test_accepts_upper_note_when_it_has_independent_energy(self) -> None:
        expected = [
            ExpectedFrequency("1번줄 7프렛", 493.89),
            ExpectedFrequency("6번줄 0프렛", 82.41),
        ]
        samples = synthesize_notes([82.41, 493.89], harmonic_count=6)

        result = detect_expected_frequencies_cqt(samples, sample_rate=44100, expected_frequencies=expected)

        self.assertEqual(result.status, "correct")
        self.assertTrue(result.is_correct)

    def test_rejects_octave_note_explained_by_lower_note_harmonic(self) -> None:
        expected = [ExpectedFrequency("E2", 82.41), ExpectedFrequency("E3", 164.81)]
        samples = synthesize_notes([82.41], harmonic_count=6)

        result = detect_expected_frequencies_cqt(samples, sample_rate=44100, expected_frequencies=expected)
        matches = {match.name: match for match in result.matches}

        self.assertFalse(result.is_correct)
        self.assertEqual(matches["E3"].rejection_reason, "probable_harmonic_of:E2")

    def test_accepts_octave_pair_when_both_notes_are_played(self) -> None:
        expected = [ExpectedFrequency("E2", 82.41), ExpectedFrequency("E3", 164.81)]
        samples = synthesize_notes([82.41, 164.81], harmonic_count=6)

        result = detect_expected_frequencies_cqt(samples, sample_rate=44100, expected_frequencies=expected)

        self.assertTrue(result.is_correct)

    def test_rejects_semitone_neighbour_leakage(self) -> None:
        expected = [ExpectedFrequency("G3", 196.0), ExpectedFrequency("G#3", 207.65)]
        samples = synthesize_notes([196.0])

        result = detect_expected_frequencies_cqt(samples, sample_rate=44100, expected_frequencies=expected)
        matches = {match.name: match for match in result.matches}

        self.assertFalse(result.is_correct)
        self.assertFalse(matches["G#3"].is_present)

    def test_accepts_semitone_pair_when_both_notes_are_played(self) -> None:
        expected = [ExpectedFrequency("G3", 196.0), ExpectedFrequency("G#3", 207.65)]
        samples = synthesize_notes([196.0, 207.65])

        result = detect_expected_frequencies_cqt(samples, sample_rate=44100, expected_frequencies=expected)

        self.assertTrue(result.is_correct)

    def test_rejects_note_far_weaker_than_the_rest_of_the_chord(self) -> None:
        expected = [ExpectedFrequency("G3", 196.0), ExpectedFrequency("B3", 246.94)]
        time = np.arange(int(44100 * 1.2), dtype=np.float32) / 44100
        samples = 0.25 * np.sin(2.0 * np.pi * 246.94 * time)
        samples += 0.25 * 10 ** (-26 / 20) * np.sin(2.0 * np.pi * 196.0 * time)

        result = detect_expected_frequencies_cqt(
            samples.astype(np.float32), sample_rate=44100, expected_frequencies=expected
        )
        matches = {match.name: match for match in result.matches}

        self.assertFalse(result.is_correct)
        self.assertEqual(matches["G3"].rejection_reason, "too_weak_for_chord")


def make_detection(recent_energies_db: dict[str, float], status: str = "correct") -> PolyphonicDetectionResult:
    matches = tuple(
        PolyphonicNoteMatch(
            name=name,
            expected_frequency=100.0,
            detected_frequency=100.0,
            cents_difference=0.0,
            energy_db=0.0,
            is_present=status != "no_signal",
            recent_energy_db=energy,
        )
        for name, energy in recent_energies_db.items()
    )
    return PolyphonicDetectionResult(
        matches=matches,
        rms=0.1,
        peak=0.2,
        noise_floor_db=-40.0,
        is_correct=status == "correct",
        status=status,
    )


class ChordJudgeGateTests(unittest.TestCase):
    def test_requires_consecutive_correct_windows(self) -> None:
        gate = ChordJudgeGate(confirm_frames=2)

        first = gate.update(make_detection({"A": -20.0, "B": -20.0}))
        second = gate.update(make_detection({"A": -20.0, "B": -20.0}))

        self.assertFalse(first.is_correct)
        self.assertEqual(first.status, "confirming")
        self.assertTrue(second.is_correct)
        self.assertEqual(second.status, "correct")

    def test_carried_over_note_must_be_struck_again(self) -> None:
        gate = ChordJudgeGate(restrike_names=["A"], confirm_frames=1)

        ringing = gate.update(make_detection({"A": -30.0, "B": -20.0}))
        decaying = gate.update(make_detection({"A": -31.0, "B": -20.0}))
        struck = gate.update(make_detection({"A": -22.0, "B": -20.0}))
        sustained = gate.update(make_detection({"A": -27.0, "B": -21.0}))

        for result in (ringing, decaying):
            matches = {match.name: match for match in result.matches}
            self.assertFalse(result.is_correct)
            self.assertEqual(matches["A"].rejection_reason, "awaiting_restrike")
            self.assertTrue(matches["B"].is_present)
        self.assertTrue(struck.is_correct)
        self.assertTrue(sustained.is_correct)

    def test_silence_releases_carried_over_note(self) -> None:
        gate = ChordJudgeGate(restrike_names=["A"], confirm_frames=1)

        gate.update(make_detection({"A": -30.0, "B": -20.0}))
        gate.update(make_detection({"A": -120.0, "B": -120.0}, status="no_signal"))
        replayed = gate.update(make_detection({"A": -30.0, "B": -20.0}))

        self.assertTrue(replayed.is_correct)

    def test_notes_not_carried_over_pass_without_onset(self) -> None:
        gate = ChordJudgeGate(restrike_names=[], confirm_frames=1)

        result = gate.update(make_detection({"A": -30.0, "B": -20.0}))

        self.assertTrue(result.is_correct)


if __name__ == "__main__":
    unittest.main()
