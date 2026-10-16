from __future__ import annotations

import unittest
from importlib.util import find_spec

import numpy as np

from audio.pitch_detection import estimate_pitch, estimate_pitch_autocorrelation, estimate_pitch_librosa_yin


class PitchDetectionTests(unittest.TestCase):
    def test_weak_guitar_note_is_detected_with_practice_sensitivity(self) -> None:
        # 낮은 마이크 입력에서도 음정·명료도 검증은 유지하고 무음 기준만 조정한다.
        sample_rate = 44100
        time = np.arange(8192, dtype=np.float32) / sample_rate
        for frequency in (82.4069, 196.0):
            with self.subTest(frequency=frequency):
                samples = 0.0035 * np.sin(2 * np.pi * frequency * time)
                self.assertIsNone(estimate_pitch(samples, sample_rate=sample_rate,
                                               silence_threshold=0.006).frequency)
                result = estimate_pitch(samples, sample_rate=sample_rate, silence_threshold=0.002)
                self.assertAlmostEqual(result.frequency or 0, frequency, delta=2.0)

    def test_estimates_synthetic_g_string_pitch(self) -> None:
        sample_rate = 44100
        duration = 0.12
        frequency = 196.0
        time = np.arange(int(sample_rate * duration), dtype=np.float32) / sample_rate
        samples = 0.4 * np.sin(2.0 * np.pi * frequency * time)

        result = estimate_pitch_autocorrelation(samples, sample_rate=sample_rate)

        self.assertIsNotNone(result.frequency)
        self.assertAlmostEqual(result.frequency or 0.0, frequency, delta=2.0)

    def test_dispatches_autocorrelation_backend(self) -> None:
        sample_rate = 44100
        frequency = 220.0
        time = np.arange(4096, dtype=np.float32) / sample_rate
        samples = 0.4 * np.sin(2.0 * np.pi * frequency * time)

        result = estimate_pitch(samples, sample_rate=sample_rate, backend="autocorrelation")

        self.assertEqual(result.backend, "autocorrelation")
        self.assertIsNotNone(result.frequency)

    @unittest.skipIf(find_spec("librosa") is None, "librosa is not installed")
    def test_librosa_yin_estimates_synthetic_g_string_pitch(self) -> None:
        sample_rate = 44100
        duration = 0.12
        frequency = 196.0
        time = np.arange(int(sample_rate * duration), dtype=np.float32) / sample_rate
        samples = 0.4 * np.sin(2.0 * np.pi * frequency * time)

        result = estimate_pitch_librosa_yin(samples, sample_rate=sample_rate)

        self.assertEqual(result.backend, "librosa_yin")
        self.assertIsNotNone(result.frequency)
        self.assertAlmostEqual(result.frequency or 0.0, frequency, delta=3.0)

    def test_returns_no_pitch_for_silence(self) -> None:
        samples = np.zeros(4096, dtype=np.float32)

        result = estimate_pitch_autocorrelation(samples, sample_rate=44100)

        self.assertIsNone(result.frequency)
        self.assertEqual(result.rms, 0.0)


if __name__ == "__main__":
    unittest.main()
