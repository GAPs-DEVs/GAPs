from __future__ import annotations

import math
import warnings
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from audio.pitch_detection import signal_levels


@dataclass(frozen=True)
class ExpectedFrequency:
    name: str
    frequency: float


@dataclass(frozen=True)
class PolyphonicNoteMatch:
    name: str
    expected_frequency: float
    detected_frequency: float | None
    cents_difference: float | None
    energy_db: float
    is_present: bool


@dataclass(frozen=True)
class PolyphonicDetectionResult:
    matches: tuple[PolyphonicNoteMatch, ...]
    rms: float
    peak: float
    noise_floor_db: float
    is_correct: bool
    status: str
    backend: str = "librosa_cqt"


def detect_expected_frequencies_cqt(
    samples: np.ndarray,
    sample_rate: int,
    expected_frequencies: Sequence[ExpectedFrequency],
    tolerance_cents: float = 50.0,
    silence_threshold: float = 0.006,
    relative_threshold_db: float = -38.0,
    floor_margin_db: float = 6.0,
    bins_per_octave: int = 36,
) -> PolyphonicDetectionResult:
    """Detect whether expected frequencies are present using librosa CQT energy.

    This is intended for known-note tests. It is not a general chord recognizer.
    """
    if sample_rate <= 0:
        raise ValueError("sample_rate must be greater than 0.")
    if tolerance_cents < 0:
        raise ValueError("tolerance_cents must be greater than or equal to 0.")
    if not expected_frequencies:
        raise ValueError("expected_frequencies must not be empty.")

    audio = np.asarray(samples, dtype=np.float32)
    if audio.ndim > 1:
        audio = np.mean(audio, axis=1)
    audio = np.ravel(audio)

    rms, peak = signal_levels(audio)
    empty_matches = tuple(
        PolyphonicNoteMatch(
            name=expected.name,
            expected_frequency=expected.frequency,
            detected_frequency=None,
            cents_difference=None,
            energy_db=-120.0,
            is_present=False,
        )
        for expected in expected_frequencies
    )
    if audio.size < 128 or (rms < silence_threshold and peak < silence_threshold * 2.0):
        return PolyphonicDetectionResult(
            matches=empty_matches,
            rms=rms,
            peak=peak,
            noise_floor_db=-120.0,
            is_correct=False,
            status="no_signal",
        )

    try:
        import librosa
    except Exception as exc:  # pragma: no cover - depends on optional environment
        raise ImportError("librosa is required for the 'librosa_cqt' polyphonic backend.") from exc

    min_expected = min(expected.frequency for expected in expected_frequencies)
    max_expected = max(expected.frequency for expected in expected_frequencies)
    fmin = max(32.7, min_expected / 2.0)
    fmax = min(sample_rate / 2.0, max_expected * 2.0)
    n_bins = max(12, int(math.ceil(bins_per_octave * math.log2(fmax / fmin))) + 1)

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="n_fft=.*too large.*", category=UserWarning)
        cqt = librosa.cqt(
            audio.astype(np.float64, copy=False),
            sr=sample_rate,
            fmin=fmin,
            n_bins=n_bins,
            bins_per_octave=bins_per_octave,
            hop_length=512,
        )
    energy = np.mean(np.square(np.abs(cqt)), axis=1)
    max_energy = float(np.max(energy))
    if max_energy <= 1e-12:
        return PolyphonicDetectionResult(
            matches=empty_matches,
            rms=rms,
            peak=peak,
            noise_floor_db=-120.0,
            is_correct=False,
            status="no_signal",
        )

    energy_db = 10.0 * np.log10(np.maximum(energy, 1e-12) / max_energy)
    noise_floor_db = float(np.percentile(energy_db, 50))
    cqt_frequencies = librosa.cqt_frequencies(
        n_bins=n_bins,
        fmin=fmin,
        bins_per_octave=bins_per_octave,
    )

    matches = tuple(
        _match_expected_frequency(
            expected,
            cqt_frequencies=cqt_frequencies,
            energy_db=energy_db,
            noise_floor_db=noise_floor_db,
            tolerance_cents=tolerance_cents,
            relative_threshold_db=relative_threshold_db,
            floor_margin_db=floor_margin_db,
        )
        for expected in expected_frequencies
    )
    is_correct = all(match.is_present for match in matches)
    return PolyphonicDetectionResult(
        matches=matches,
        rms=rms,
        peak=peak,
        noise_floor_db=noise_floor_db,
        is_correct=is_correct,
        status="correct" if is_correct else "incorrect",
    )


def _match_expected_frequency(
    expected: ExpectedFrequency,
    cqt_frequencies: np.ndarray,
    energy_db: np.ndarray,
    noise_floor_db: float,
    tolerance_cents: float,
    relative_threshold_db: float,
    floor_margin_db: float,
) -> PolyphonicNoteMatch:
    cents_offsets = 1200.0 * np.log2(cqt_frequencies / expected.frequency)
    candidate_indices = np.flatnonzero(np.abs(cents_offsets) <= tolerance_cents)
    if candidate_indices.size == 0:
        nearest_index = int(np.argmin(np.abs(cents_offsets)))
        candidate_indices = np.asarray([nearest_index])

    best_local_index = int(candidate_indices[np.argmax(energy_db[candidate_indices])])
    best_energy_db = float(energy_db[best_local_index])
    detected_frequency = float(cqt_frequencies[best_local_index])
    cents_difference = float(cents_offsets[best_local_index])
    is_present = (
        best_energy_db >= relative_threshold_db
        and best_energy_db >= noise_floor_db + floor_margin_db
    )
    return PolyphonicNoteMatch(
        name=expected.name,
        expected_frequency=expected.frequency,
        detected_frequency=detected_frequency,
        cents_difference=cents_difference,
        energy_db=best_energy_db,
        is_present=is_present,
    )
