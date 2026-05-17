from __future__ import annotations

import unittest

import numpy as np

from audio.music_math import SOLFEGE_TEST_NOTE_FREQUENCIES
from audio.polyphonic_detection import ExpectedFrequency, detect_expected_frequencies_cqt


def synthesize_notes(
    frequencies: list[float],
    sample_rate: int = 44100,
    duration: float = 1.2,
) -> np.ndarray:
    time = np.arange(int(sample_rate * duration), dtype=np.float32) / sample_rate
    samples = np.zeros_like(time)
    for frequency in frequencies:
        samples += 0.25 * np.sin(2.0 * np.pi * frequency * time)
        samples += 0.08 * np.sin(2.0 * np.pi * frequency * 2.0 * time)
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


if __name__ == "__main__":
    unittest.main()
