from __future__ import annotations

import unittest

from audio.music_math import (
    SOLFEGE_TEST_NOTE_FREQUENCIES,
    cents_difference,
    frequency_tolerance_range,
    frequency_to_midi,
    guitar_note_frequency,
    is_within_tolerance,
    midi_to_frequency,
)


class MusicMathTests(unittest.TestCase):
    def test_frequency_to_midi_maps_a4(self) -> None:
        self.assertAlmostEqual(frequency_to_midi(440.0), 69.0, places=6)

    def test_midi_to_frequency_maps_a4(self) -> None:
        self.assertAlmostEqual(midi_to_frequency(69.0), 440.0, places=6)

    def test_cents_difference_is_signed(self) -> None:
        self.assertGreater(cents_difference(445.0, 440.0), 0.0)
        self.assertLess(cents_difference(435.0, 440.0), 0.0)

    def test_tolerance_uses_absolute_cents(self) -> None:
        self.assertTrue(is_within_tolerance(440.0, 440.0, 30.0))
        self.assertFalse(is_within_tolerance(466.16, 440.0, 30.0))

    def test_guitar_note_frequency_uses_standard_tuning(self) -> None:
        self.assertAlmostEqual(guitar_note_frequency(3, 0), 196.0, places=2)
        self.assertAlmostEqual(guitar_note_frequency(6, 12), 164.82, places=2)

    def test_solfege_test_notes_use_guitar_first_position_octave(self) -> None:
        self.assertAlmostEqual(SOLFEGE_TEST_NOTE_FREQUENCIES["낮은 도"], 130.81, places=2)
        self.assertAlmostEqual(SOLFEGE_TEST_NOTE_FREQUENCIES["솔"], 196.00, places=2)
        self.assertAlmostEqual(SOLFEGE_TEST_NOTE_FREQUENCIES["높은 도"], 261.63, places=2)

    def test_frequency_tolerance_range_uses_cents_ratio(self) -> None:
        lower_frequency, upper_frequency = frequency_tolerance_range(220.0, 30.0)

        self.assertAlmostEqual(lower_frequency, 216.22, places=2)
        self.assertAlmostEqual(upper_frequency, 223.85, places=2)


if __name__ == "__main__":
    unittest.main()
