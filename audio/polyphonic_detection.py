from __future__ import annotations

import math
import warnings
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from dataclasses import replace

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
    rejection_reason: str = ""
    recent_energy_db: float = -120.0
    is_quiet: bool = False


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
    tolerance_cents: float = 35.0,
    silence_threshold: float = 0.006,
    relative_threshold_db: float = -30.0,
    floor_margin_db: float = 10.0,
    prominence_threshold_db: float = 10.0,
    prominence_min_energy_db: float = -50.0,
    harmonic_rejection_margin_db: float = 18.0,
    bins_per_octave: int = 36,
    quiet_threshold_db: float = -20.0,
    balance_threshold_db: float = -40.0,
    harmonic_excess_db: float = 5.0,
    quiet_harmonic_excess_db: float = 12.0,
    sympathetic_margin_db: float = 12.0,
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
            rejection_reason="",
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

    spectrum = _Spectrum(
        frequencies=cqt_frequencies,
        energy_db=energy_db,
        cents_per_bin=1200.0 / bins_per_octave,
        tolerance_cents=tolerance_cents,
    )
    matches = tuple(
        _match_expected_frequency(
            expected,
            spectrum=spectrum,
            noise_floor_db=noise_floor_db,
            relative_threshold_db=relative_threshold_db,
            floor_margin_db=floor_margin_db,
            prominence_threshold_db=prominence_threshold_db,
            prominence_min_energy_db=prominence_min_energy_db,
            recent_energy_db=recent_tone_energy_db(audio, sample_rate, expected.frequency),
        )
        for expected in expected_frequencies
    )
    matches = _mark_quiet_matches(matches, quiet_threshold_db=quiet_threshold_db)
    matches = _reject_probable_harmonic_matches(
        matches,
        spectrum=spectrum,
        tolerance_cents=tolerance_cents,
        harmonic_rejection_margin_db=harmonic_rejection_margin_db,
        harmonic_excess_db=harmonic_excess_db,
        quiet_harmonic_excess_db=quiet_harmonic_excess_db,
        fine_spectrum=_FineSpectrum(audio, sample_rate),
    )
    matches = _reject_sympathetic_matches(
        matches, tolerance_cents=tolerance_cents, margin_db=sympathetic_margin_db
    )
    matches = _reject_unbalanced_matches(matches, balance_threshold_db=balance_threshold_db)
    is_correct = all(match.is_present for match in matches)
    return PolyphonicDetectionResult(
        matches=matches,
        rms=rms,
        peak=peak,
        noise_floor_db=noise_floor_db,
        is_correct=is_correct,
        status="correct" if is_correct else "incorrect",
    )


@dataclass(frozen=True)
class _Spectrum:
    frequencies: np.ndarray
    energy_db: np.ndarray
    cents_per_bin: float
    tolerance_cents: float

    def strongest_bin_near(self, frequency: float) -> tuple[int, float]:
        """Return (bin index, cents offset) of the strongest bin within tolerance."""
        cents_offsets = 1200.0 * np.log2(self.frequencies / frequency)
        candidates = np.flatnonzero(np.abs(cents_offsets) <= self.tolerance_cents)
        if candidates.size == 0:
            candidates = np.asarray([int(np.argmin(np.abs(cents_offsets)))])
        index = int(candidates[np.argmax(self.energy_db[candidates])])
        return index, float(cents_offsets[index])

    def peak_near(self, frequency: float) -> tuple[float, float] | None:
        """Return (energy dB, cents offset) of a real spectral peak near frequency.

        The strongest bin inside the tolerance window only counts when it is a
        local maximum whose interpolated centre also lies inside the window.
        Otherwise the energy is the skirt of a neighbouring note, not a peak.
        """
        if frequency < self.frequencies[0] * 0.97 or frequency > self.frequencies[-1] * 1.03:
            return None
        index, cents = self.strongest_bin_near(frequency)
        centre = float(self.energy_db[index])
        left = float(self.energy_db[index - 1]) if index > 0 else -math.inf
        right = (
            float(self.energy_db[index + 1])
            if index + 1 < self.energy_db.size
            else -math.inf
        )
        if left > centre or right > centre:
            return None
        if math.isfinite(left) and math.isfinite(right):
            curvature = left - 2.0 * centre + right
            if curvature < 0.0:
                cents += 0.5 * (left - right) / curvature * self.cents_per_bin
        if abs(cents) > self.tolerance_cents:
            return None
        return centre, cents

    def local_prominence_db(self, index: int, frequency: float) -> float:
        cents_offsets = np.abs(1200.0 * np.log2(self.frequencies / frequency))
        surrounding = np.flatnonzero((cents_offsets > 100.0) & (cents_offsets <= 300.0))
        if surrounding.size == 0:
            return 0.0
        return float(self.energy_db[index] - np.median(self.energy_db[surrounding]))


def _match_expected_frequency(
    expected: ExpectedFrequency,
    spectrum: _Spectrum,
    noise_floor_db: float,
    relative_threshold_db: float,
    floor_margin_db: float,
    prominence_threshold_db: float,
    prominence_min_energy_db: float,
    recent_energy_db: float,
) -> PolyphonicNoteMatch:
    index, bin_cents = spectrum.strongest_bin_near(expected.frequency)
    best_energy_db = float(spectrum.energy_db[index])
    peak = spectrum.peak_near(expected.frequency)
    cents_difference = peak[1] if peak is not None else bin_cents
    above_floor = (
        best_energy_db >= relative_threshold_db
        and best_energy_db >= noise_floor_db + floor_margin_db
    )
    loud_enough = above_floor or (
        best_energy_db >= prominence_min_energy_db
        and spectrum.local_prominence_db(index, expected.frequency) >= prominence_threshold_db
    )
    return PolyphonicNoteMatch(
        name=expected.name,
        expected_frequency=expected.frequency,
        detected_frequency=float(expected.frequency * 2.0 ** (cents_difference / 1200.0)),
        cents_difference=cents_difference,
        energy_db=best_energy_db,
        is_present=loud_enough and peak is not None,
        rejection_reason="no_spectral_peak" if loud_enough and peak is None else "",
        recent_energy_db=recent_energy_db,
        is_quiet=not above_floor,
    )


_RECENT_ENERGY_SAMPLES = 8192


def recent_tone_energy_db(audio: np.ndarray, sample_rate: int, frequency: float) -> float:
    """Absolute level of `frequency` over the last ~190 ms of the buffer.

    Unlike the CQT average this is local in time and does not depend on how
    full the analysis buffer is, so consecutive windows can be compared to
    tell a fresh strike from a note that is merely still ringing.
    """
    segment = audio[-_RECENT_ENERGY_SAMPLES:].astype(np.float64, copy=False)
    window = np.hanning(segment.size)
    phase = 2.0 * np.pi * frequency * np.arange(segment.size) / sample_rate
    amplitude = 2.0 * abs(np.sum(segment * window * np.exp(-1j * phase))) / np.sum(window)
    return 20.0 * math.log10(max(amplitude, 1e-6))


def _mark_quiet_matches(
    matches: tuple[PolyphonicNoteMatch, ...],
    quiet_threshold_db: float,
) -> tuple[PolyphonicNoteMatch, ...]:
    present = [match.energy_db for match in matches if match.is_present]
    if not present:
        return matches
    minimum_db = max(present) + quiet_threshold_db
    return tuple(
        replace(match, is_quiet=True) if match.is_present and match.energy_db < minimum_db else match
        for match in matches
    )


def _reject_sympathetic_matches(
    matches: tuple[PolyphonicNoteMatch, ...],
    tolerance_cents: float,
    margin_db: float,
    min_harmonic: int = 3,
    max_harmonic: int = 8,
) -> tuple[PolyphonicNoteMatch, ...]:
    guarded_matches: list[PolyphonicNoteMatch] = []
    for match in matches:
        rejection_reason = ""
        for upper_match in matches:
            if not match.is_present or not upper_match.is_present:
                break
            if upper_match.expected_frequency <= match.expected_frequency:
                continue
            ratio = upper_match.expected_frequency / match.expected_frequency
            harmonic = int(round(ratio))
            if harmonic < min_harmonic or harmonic > max_harmonic:
                continue
            if abs(1200.0 * math.log2(ratio / harmonic)) > tolerance_cents:
                continue
            if match.energy_db < upper_match.energy_db + margin_db:
                rejection_reason = f"probable_sympathetic_of:{upper_match.name}"
                break
        if rejection_reason:
            guarded_matches.append(replace(match, is_present=False, rejection_reason=rejection_reason))
        else:
            guarded_matches.append(match)
    return tuple(guarded_matches)


def _reject_unbalanced_matches(
    matches: tuple[PolyphonicNoteMatch, ...],
    balance_threshold_db: float,
) -> tuple[PolyphonicNoteMatch, ...]:
    """Reject notes that are far weaker than the strongest expected note.

    A note that was really struck together with the others is roughly as loud
    as they are; resonance, leftover ringing and hum sit well below.
    """
    present = [match.energy_db for match in matches if match.is_present]
    if not present:
        return matches
    minimum_db = max(present) + balance_threshold_db
    return tuple(
        replace(match, is_present=False, rejection_reason="too_weak_for_chord")
        if match.is_present and match.energy_db < minimum_db
        else match
        for match in matches
    )


_HARMONIC_SLOPE_DB_PER_OCTAVE = 6.0
_EVIDENCE_PARTIALS = 3
_HARMONIC_COINCIDENCE_CENTS = 50.0


def _reject_probable_harmonic_matches(
    matches: tuple[PolyphonicNoteMatch, ...],
    spectrum: _Spectrum,
    tolerance_cents: float,
    harmonic_rejection_margin_db: float,
    harmonic_excess_db: float,
    fine_spectrum: _FineSpectrum | None = None,
    max_harmonic: int = 8,
    quiet_harmonic_excess_db: float | None = None,
) -> tuple[PolyphonicNoteMatch, ...]:
    """Reject notes whose energy is explained by a lower expected note's harmonic.

    When a note sits on harmonic h of a lower expected note, its fundamental
    alone proves nothing. The lower note's neighbouring harmonics (h-1, h+1)
    predict how strong harmonic h would be on its own; the upper note counts
    when the measured energy clearly exceeds that prediction (or, when no
    prediction is possible, when it is not far weaker than the lower note).

    A note rejected that way is still accepted when a fine-resolution spectrum
    shows two separate peaks where one of its partials meets the lower note's
    harmonic: equal-tempered notes rarely sit exactly on a harmonic (a major
    third is ~14 cents off the 5th harmonic), so two sounding strings leave a
    doublet while a lone harmonic leaves a single peak.
    """
    if harmonic_rejection_margin_db <= 0:
        return matches

    guarded_matches: list[PolyphonicNoteMatch] = []
    for match in matches:
        if not match.is_present:
            guarded_matches.append(match)
            continue

        rejection_reason = ""
        for lower_match in matches:
            if lower_match is match or not lower_match.is_present:
                continue
            if lower_match.expected_frequency >= match.expected_frequency:
                continue

            ratio = match.expected_frequency / lower_match.expected_frequency
            harmonic = int(round(ratio))
            if harmonic < 2 or harmonic > max_harmonic:
                continue
            if abs(1200.0 * math.log2(ratio / harmonic)) > tolerance_cents:
                continue

            predicted_db = _predict_harmonic_energy_db(
                lower_match, harmonic, matches, spectrum, max_harmonic
            )
            if predicted_db is not None:
                excess_db = harmonic_excess_db
                if match.is_quiet and quiet_harmonic_excess_db is not None:
                    excess_db = quiet_harmonic_excess_db
                explained = match.energy_db < predicted_db + excess_db
            else:
                explained = (
                    lower_match.energy_db - match.energy_db > harmonic_rejection_margin_db
                )
            if explained and fine_spectrum is not None:
                explained = not _has_partial_doublet(
                    match, lower_match, harmonic, matches, fine_spectrum, max_harmonic
                )
            if explained:
                rejection_reason = f"probable_harmonic_of:{lower_match.name}"
                break

        if rejection_reason:
            guarded_matches.append(
                replace(match, is_present=False, rejection_reason=rejection_reason)
            )
        else:
            guarded_matches.append(match)

    return tuple(guarded_matches)


def _has_partial_doublet(
    match: PolyphonicNoteMatch,
    lower_match: PolyphonicNoteMatch,
    harmonic: int,
    matches: tuple[PolyphonicNoteMatch, ...],
    fine_spectrum: _FineSpectrum,
    max_harmonic: int,
) -> bool:
    for partial in range(1, _EVIDENCE_PARTIALS + 1):
        note_frequency = match.expected_frequency * partial
        harmonic_frequency = lower_match.expected_frequency * harmonic * partial
        if any(
            other is not match
            and _coincides_with_other_note(note_frequency, lower_match, (other,), max_harmonic)
            for other in matches
        ):
            continue
        if fine_spectrum.has_doublet(note_frequency, harmonic_frequency):
            return True
    return False


class _FineSpectrum:
    """Zero-padded FFT of the whole buffer, computed only when it is needed."""

    _PADDING = 4
    _SEARCH_CENTS = 35.0
    _MAX_LEVEL_GAP_DB = 15.0
    _MIN_PROMINENCE_DB = 20.0
    _FLOOR_SPAN = 1.2

    def __init__(self, audio: np.ndarray, sample_rate: int) -> None:
        self._audio = audio
        self._sample_rate = sample_rate
        self._magnitude_db: np.ndarray | None = None
        self._bin_hz = 0.0
        # Two tones closer than about two analysis bins merge into one peak.
        self._resolution_hz = 2.0 * sample_rate / max(1, audio.size)

    def has_doublet(self, frequency_a: float, frequency_b: float) -> bool:
        """True when two distinct peaks sit where the two frequencies are expected."""
        spacing_hz = abs(frequency_a - frequency_b)
        if spacing_hz < self._resolution_hz:
            return False
        magnitude_db = self._spectrum_db()
        search = 2.0 ** (self._SEARCH_CENTS / 1200.0)
        low = int(min(frequency_a, frequency_b) / search / self._bin_hz)
        high = int(math.ceil(max(frequency_a, frequency_b) * search / self._bin_hz)) + 1
        low, high = max(1, low), min(magnitude_db.size - 1, high)
        if high - low < 3:
            return False

        band = magnitude_db[low:high]
        is_peak = (band >= magnitude_db[low - 1 : high - 1]) & (band > magnitude_db[low + 1 : high + 1])
        peaks = np.flatnonzero(is_peak)
        if peaks.size < 2:
            return False
        # Both peaks must stand clear of the surrounding noise, otherwise any
        # random bump next to a lone harmonic would look like a second string.
        centre = math.sqrt(frequency_a * frequency_b)
        floor_low = max(1, int(centre / self._FLOOR_SPAN / self._bin_hz))
        floor_high = min(magnitude_db.size, int(centre * self._FLOOR_SPAN / self._bin_hz) + 1)
        minimum_db = float(np.median(magnitude_db[floor_low:floor_high])) + self._MIN_PROMINENCE_DB
        peaks = peaks[band[peaks] >= minimum_db]
        if peaks.size < 2:
            return False
        strongest = int(peaks[np.argmax(band[peaks])])
        for peak in peaks:
            distance_hz = abs(int(peak) - strongest) * self._bin_hz
            if distance_hz < self._resolution_hz:
                continue
            if not 0.5 * spacing_hz <= distance_hz <= 2.0 * spacing_hz:
                continue
            if band[peak] >= band[strongest] - self._MAX_LEVEL_GAP_DB:
                return True
        return False

    def _spectrum_db(self) -> np.ndarray:
        if self._magnitude_db is None:
            samples = self._audio.astype(np.float64, copy=False)
            size = 1 << int(math.ceil(math.log2(max(2, samples.size * self._PADDING))))
            spectrum = np.fft.rfft(samples * np.hanning(samples.size), n=size)
            self._magnitude_db = 20.0 * np.log10(np.maximum(np.abs(spectrum), 1e-12))
            self._bin_hz = self._sample_rate / size
        return self._magnitude_db


def _predict_harmonic_energy_db(
    lower_match: PolyphonicNoteMatch,
    harmonic: int,
    matches: tuple[PolyphonicNoteMatch, ...],
    spectrum: _Spectrum,
    max_harmonic: int,
) -> float | None:
    """Estimate harmonic `harmonic` of lower_match from its clean neighbours."""
    neighbours: list[tuple[int, float]] = []
    for index in (harmonic - 1, harmonic + 1):
        frequency = lower_match.expected_frequency * index
        if _coincides_with_other_note(frequency, lower_match, matches, max_harmonic):
            continue
        peak = spectrum.peak_near(frequency)
        if peak is not None:
            neighbours.append((index, peak[0]))

    if not neighbours:
        return None
    if len(neighbours) == 2:
        (low_index, low_db), (high_index, high_db) = neighbours
        position = math.log2(harmonic / low_index) / math.log2(high_index / low_index)
        return low_db + (high_db - low_db) * position
    index, energy_db = neighbours[0]
    return energy_db - _HARMONIC_SLOPE_DB_PER_OCTAVE * math.log2(harmonic / index)


def _coincides_with_other_note(
    frequency: float,
    lower_match: PolyphonicNoteMatch,
    matches: tuple[PolyphonicNoteMatch, ...],
    max_harmonic: int,
) -> bool:
    for other in matches:
        if other is lower_match:
            continue
        for index in range(1, max_harmonic + 1):
            cents = 1200.0 * math.log2(frequency / (other.expected_frequency * index))
            if abs(cents) <= _HARMONIC_COINCIDENCE_CENTS:
                return True
    return False


class ChordJudgeGate:
    """Frame-to-frame gate applied on top of single-window detections.

    - Notes in `restrike_names` must be struck again: their energy has to rise
      above the level they were ringing at (or the input has to go silent
      first) before they count. `baseline_db` seeds that level from audio
      heard before the gate started; otherwise the first window sets it.
    - A chord only passes after `confirm_frames` consecutive correct windows.
    """

    def __init__(
        self,
        restrike_names: Iterable[str] = (),
        onset_rise_db: float = 3.0,
        confirm_frames: int = 2,
        baseline_db: Mapping[str, float] | None = None,
    ) -> None:
        if confirm_frames < 1:
            raise ValueError("confirm_frames must be greater than or equal to 1.")
        self.onset_rise_db = onset_rise_db
        self.confirm_frames = confirm_frames
        seeded = baseline_db or {}
        self._baseline_db: dict[str, float] = {
            name: seeded.get(name, math.inf) for name in restrike_names
        }
        self._struck: set[str] = set()
        self._correct_streak = 0

    def update(self, detection: PolyphonicDetectionResult) -> PolyphonicDetectionResult:
        if detection.status == "no_signal":
            self._struck.update(self._baseline_db)
            self._correct_streak = 0
            return detection

        matches = tuple(self._gate_match(match) for match in detection.matches)
        is_correct = all(match.is_present for match in matches)
        self._correct_streak = self._correct_streak + 1 if is_correct else 0
        if is_correct and self._correct_streak < self.confirm_frames:
            return replace(detection, matches=matches, is_correct=False, status="confirming")
        return replace(
            detection,
            matches=matches,
            is_correct=is_correct,
            status="correct" if is_correct else "incorrect",
        )

    def _gate_match(self, match: PolyphonicNoteMatch) -> PolyphonicNoteMatch:
        name = match.name
        if name not in self._baseline_db or name in self._struck:
            return match

        baseline_db = self._baseline_db[name]
        if match.recent_energy_db >= baseline_db + self.onset_rise_db:
            self._struck.add(name)
            return match
        self._baseline_db[name] = min(baseline_db, match.recent_energy_db)
        if not match.is_present:
            return match
        return replace(match, is_present=False, rejection_reason="awaiting_restrike")
