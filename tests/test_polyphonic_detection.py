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
    recent_tone_energy_db,
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


def synthesize_steep_low_e(boosted_partial: int = 0, boost_db: float = 0.0) -> np.ndarray:
    time = np.arange(int(44100 * 1.2)) / 44100
    samples = np.zeros_like(time)
    for partial in range(1, 13):
        gain = 10 ** (boost_db / 20) if partial == boosted_partial else 1.0
        samples += 0.25 / partial**2.5 * gain * np.sin(2.0 * np.pi * 82.41 * partial * time)
    return samples


def pluck(
    frequency: float,
    time: np.ndarray,
    amplitude: float,
    decay_db_per_s: float,
    decay_step_db_per_s: float,
    rolloff: float = 1.0,
    partial_count: int = 12,
) -> np.ndarray:
    samples = np.zeros_like(time)
    for partial in range(1, partial_count + 1):
        decay = decay_db_per_s + decay_step_db_per_s * (partial - 1)
        envelope = 10 ** (-decay * np.maximum(time, 0.0) / 20)
        samples += amplitude / partial**rolloff * np.sin(2.0 * np.pi * frequency * partial * time) * envelope
    return samples * (time >= 0)


def judge_stream(
    samples: np.ndarray,
    expected: list[ExpectedFrequency],
    sample_rate: int = 44100,
    previous_audio: np.ndarray | None = None,
) -> list[PolyphonicDetectionResult]:
    baseline_db = None
    if previous_audio is not None:
        baseline_db = {
            note.name: recent_tone_energy_db(previous_audio, sample_rate, note.frequency) for note in expected
        }
    gate = ChordJudgeGate(restrike_names=[note.name for note in expected], baseline_db=baseline_db)
    window = int(sample_rate * 0.65)
    interval = int(sample_rate * 0.12)
    results = []
    next_at = 0
    for end in range(2048, samples.size + 1, 2048):
        if end < 4096 or end < next_at:
            continue
        buffer = samples[max(0, end - window) : end].astype(np.float32)
        results.append(gate.update(detect_expected_frequencies_cqt(buffer, sample_rate, expected)))
        next_at = end + interval
    return results


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
        samples = synthesize_notes([82.41], harmonic_count=6)
        samples += 10 ** (-12 / 20) * synthesize_notes([493.89], harmonic_count=6)

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

    def test_accepts_quiet_upper_note_on_fifth_harmonic_of_low_string(self) -> None:
        # 2번줄 9프렛(G#4)은 6번줄 개방(E2)의 5배음 자리에 있다. 기본음은 E2 배음에
        # 묻히지만 G#4의 2배음이 E2의 이웃 배음보다 뚜렷하게 크다.
        expected = [ExpectedFrequency("2번줄 9프렛", 415.30), ExpectedFrequency("6번줄 0프렛", 82.41)]
        time = np.arange(int(44100 * 1.2), dtype=np.float32) / 44100
        samples = np.zeros_like(time)
        for harmonic in range(1, 13):
            samples += 0.25 / harmonic * np.sin(2.0 * np.pi * 82.41 * harmonic * time)
        for harmonic, amplitude in ((1, 0.06), (2, 0.05)):
            samples += amplitude * np.sin(2.0 * np.pi * 415.30 * harmonic * time)

        both = detect_expected_frequencies_cqt(samples, sample_rate=44100, expected_frequencies=expected)
        low_only = detect_expected_frequencies_cqt(
            synthesize_notes([82.41], harmonic_count=12), sample_rate=44100, expected_frequencies=expected
        )

        self.assertTrue(both.is_correct)
        self.assertFalse(low_only.is_correct)

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
        samples += 0.25 * 10 ** (-45 / 20) * np.sin(2.0 * np.pi * 196.0 * time)

        result = detect_expected_frequencies_cqt(
            samples.astype(np.float32), sample_rate=44100, expected_frequencies=expected
        )
        matches = {match.name: match for match in result.matches}

        self.assertFalse(result.is_correct)
        self.assertFalse(matches["G3"].is_present)

    def test_accepts_quiet_note_that_stands_out_from_its_neighbourhood(self) -> None:
        expected = [ExpectedFrequency("G3", 196.0), ExpectedFrequency("B3", 246.94)]
        time = np.arange(int(44100 * 1.2), dtype=np.float32) / 44100
        samples = 0.25 * np.sin(2.0 * np.pi * 246.94 * time)
        samples += 0.25 * 10 ** (-26 / 20) * np.sin(2.0 * np.pi * 196.0 * time)

        result = detect_expected_frequencies_cqt(
            samples.astype(np.float32), sample_rate=44100, expected_frequencies=expected
        )
        matches = {match.name: match for match in result.matches}

        self.assertTrue(result.is_correct)
        self.assertTrue(matches["G3"].is_quiet)

    def test_accepts_quiet_upper_string_well_above_lower_string_harmonic(self) -> None:
        expected = [ExpectedFrequency("E5", 659.26), ExpectedFrequency("E2", 82.41)]
        low = synthesize_steep_low_e()
        time = np.arange(low.size) / 44100
        samples = low + 0.25 * 10 ** (-30 / 20) * np.sin(2.0 * np.pi * 659.26 * time + np.pi / 2)

        result = detect_expected_frequencies_cqt(samples.astype(np.float32), 44100, expected)
        low_only = detect_expected_frequencies_cqt(low.astype(np.float32), 44100, expected)

        self.assertTrue(result.is_correct)
        self.assertTrue(result.matches[0].is_quiet)
        self.assertFalse(low_only.is_correct)

    def test_quiet_note_needs_clear_excess_over_lower_string_harmonic(self) -> None:
        expected = [ExpectedFrequency("B4", 493.88), ExpectedFrequency("E2", 82.41)]
        samples = synthesize_steep_low_e(boosted_partial=6, boost_db=8.0).astype(np.float32)

        result = detect_expected_frequencies_cqt(samples, 44100, expected)
        loose = detect_expected_frequencies_cqt(samples, 44100, expected, quiet_harmonic_excess_db=5.0)

        self.assertFalse(result.is_correct)
        self.assertEqual(result.matches[0].rejection_reason, "probable_harmonic_of:E2")
        self.assertTrue(loose.is_correct)


class ChordStreamTests(unittest.TestCase):
    expected = [ExpectedFrequency("1번줄 7프렛", 493.88), ExpectedFrequency("6번줄 0프렛", 82.41)]

    def test_ringing_open_string_does_not_complete_chord(self) -> None:
        time = np.arange(int(44100 * 1.5)) / 44100
        ringing = pluck(82.41, time + 0.6, 0.25, 6.0, 3.5)
        struck = pluck(493.88, time - 0.4, 0.25, 12.0, 7.0, rolloff=1.4)

        results = judge_stream(ringing + struck, self.expected)
        reasons = {match.rejection_reason for result in results for match in result.matches[1:]}

        self.assertFalse(any(result.is_correct for result in results))
        self.assertIn("awaiting_restrike", reasons)

    def test_chord_passes_when_both_strings_are_struck(self) -> None:
        time = np.arange(int(44100 * 1.5)) / 44100
        ringing = pluck(82.41, time + 0.6, 0.25, 6.0, 3.5)
        struck = pluck(82.41, time - 0.4, 0.25, 6.0, 3.5) + pluck(493.88, time - 0.4, 0.04, 12.0, 7.0, rolloff=1.4)

        results = judge_stream(ringing + struck, self.expected)

        self.assertTrue(any(result.is_correct for result in results))

    def test_open_string_resonating_with_upper_string_does_not_count(self) -> None:
        time = np.arange(int(44100 * 1.5)) / 44100
        struck = pluck(493.88, time - 0.4, 0.25, 12.0, 7.0, rolloff=1.4)
        resonating = pluck(82.41, time - 0.4, 0.25 * 10 ** (-19 / 20), 6.0, 3.5, rolloff=2.0)

        results = judge_stream(struck + resonating, self.expected)
        reasons = {match.rejection_reason for result in results for match in result.matches[1:]}

        self.assertFalse(any(result.is_correct for result in results))
        self.assertIn("probable_sympathetic_of:1번줄 7프렛", reasons)

    def test_note_ringing_before_step_switch_must_be_struck_again(self) -> None:
        time = np.arange(int(44100 * 2.0)) / 44100 - 0.5
        samples = pluck(82.41, time + 0.3, 0.25, 6.0, 3.5) + pluck(493.88, time - 0.3, 0.25, 12.0, 7.0, rolloff=1.4)
        switch = int(44100 * 0.5)

        results = judge_stream(samples[switch:], self.expected, previous_audio=samples[:switch])

        self.assertFalse(any(result.is_correct for result in results))


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

    def test_seeded_baseline_requires_rise_from_level_before_start(self) -> None:
        gate = ChordJudgeGate(restrike_names=["A", "B"], baseline_db={"A": -25.0, "B": -60.0}, confirm_frames=1)

        result = gate.update(make_detection({"A": -26.0, "B": -20.0}))
        matches = {match.name: match for match in result.matches}

        self.assertFalse(result.is_correct)
        self.assertEqual(matches["A"].rejection_reason, "awaiting_restrike")
        self.assertTrue(matches["B"].is_present)

    def test_notes_not_carried_over_pass_without_onset(self) -> None:
        gate = ChordJudgeGate(restrike_names=[], confirm_frames=1)

        result = gate.update(make_detection({"A": -30.0, "B": -20.0}))

        self.assertTrue(result.is_correct)


if __name__ == "__main__":
    unittest.main()
