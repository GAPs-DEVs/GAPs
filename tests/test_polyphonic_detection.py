from __future__ import annotations

import unittest

import numpy as np

from audio.music_math import SOLFEGE_TEST_NOTE_FREQUENCIES
from audio.polyphonic_detection import ExpectedFrequency, detect_expected_frequencies_cqt


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


if __name__ == "__main__":
    unittest.main()
