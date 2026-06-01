from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class PitchDetectionResult:
    frequency: float | None
    rms: float
    peak: float
    clarity: float
    backend: str = "autocorrelation"


def estimate_pitch(
    samples: np.ndarray,
    sample_rate: int,
    min_freq: float = 70.0,
    max_freq: float = 1000.0,
    silence_threshold: float = 0.006,
    backend: str = "librosa_yin",
) -> PitchDetectionResult:
    if backend == "autocorrelation":
        return estimate_pitch_autocorrelation(
            samples,
            sample_rate=sample_rate,
            min_freq=min_freq,
            max_freq=max_freq,
            silence_threshold=silence_threshold,
        )
    if backend == "librosa_yin":
        return estimate_pitch_librosa_yin(
            samples,
            sample_rate=sample_rate,
            min_freq=min_freq,
            max_freq=max_freq,
            silence_threshold=silence_threshold,
        )
    raise ValueError(f"Unsupported pitch detection backend: {backend}")


def signal_levels(samples: np.ndarray) -> tuple[float, float]:
    audio = _prepare_mono_samples(samples)
    if audio.size == 0:
        return 0.0, 0.0
    rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))
    peak = float(np.max(np.abs(audio)))
    return rms, peak


def estimate_pitch_autocorrelation(
    samples: np.ndarray,
    sample_rate: int,
    min_freq: float = 70.0,
    max_freq: float = 1000.0,
    silence_threshold: float = 0.01,
    clarity_threshold: float = 0.20,
) -> PitchDetectionResult:
    """Estimate fundamental frequency with a lightweight autocorrelation method.

    This is intentionally dependency-light so it can later be swapped for YIN,
    CREPE, librosa, or another detector without changing the widget API.
    """
    if sample_rate <= 0:
        raise ValueError("sample_rate must be greater than 0.")
    if min_freq <= 0 or max_freq <= 0 or min_freq >= max_freq:
        raise ValueError("Expected 0 < min_freq < max_freq.")

    audio = _prepare_mono_samples(samples)
    if audio.size < 3:
        return PitchDetectionResult(frequency=None, rms=0.0, peak=0.0, clarity=0.0, backend="autocorrelation")

    rms, peak = signal_levels(audio)
    if rms < silence_threshold and peak < silence_threshold * 2.0:
        return PitchDetectionResult(frequency=None, rms=rms, peak=peak, clarity=0.0, backend="autocorrelation")

    audio = audio - float(np.mean(audio))
    if not np.any(audio):
        return PitchDetectionResult(frequency=None, rms=rms, peak=peak, clarity=0.0, backend="autocorrelation")

    window = np.hanning(audio.size).astype(np.float32)
    windowed = audio * window
    autocorrelation = _fft_autocorrelation(windowed)
    zero_lag = float(autocorrelation[0])
    if zero_lag <= 1e-12:
        return PitchDetectionResult(frequency=None, rms=rms, peak=peak, clarity=0.0, backend="autocorrelation")

    min_lag = max(1, int(sample_rate / max_freq))
    max_lag = min(audio.size - 2, int(sample_rate / min_freq))
    if max_lag <= min_lag:
        return PitchDetectionResult(frequency=None, rms=rms, peak=peak, clarity=0.0, backend="autocorrelation")

    search = autocorrelation[min_lag : max_lag + 1]
    if search.size == 0:
        return PitchDetectionResult(frequency=None, rms=rms, peak=peak, clarity=0.0, backend="autocorrelation")

    best_offset = _select_peak_offset(search)
    lag_index = min_lag + best_offset
    clarity = float(autocorrelation[lag_index] / zero_lag)
    if clarity < clarity_threshold:
        return PitchDetectionResult(frequency=None, rms=rms, peak=peak, clarity=clarity, backend="autocorrelation")

    interpolated_lag = _parabolic_lag(autocorrelation, lag_index)
    if interpolated_lag <= 0:
        return PitchDetectionResult(frequency=None, rms=rms, peak=peak, clarity=clarity, backend="autocorrelation")

    frequency = float(sample_rate / interpolated_lag)
    if frequency < min_freq or frequency > max_freq:
        return PitchDetectionResult(frequency=None, rms=rms, peak=peak, clarity=clarity, backend="autocorrelation")

    return PitchDetectionResult(frequency=frequency, rms=rms, peak=peak, clarity=clarity, backend="autocorrelation")


def estimate_pitch_librosa_yin(
    samples: np.ndarray,
    sample_rate: int,
    min_freq: float = 70.0,
    max_freq: float = 1000.0,
    silence_threshold: float = 0.006,
    clarity_threshold: float = 0.12,
) -> PitchDetectionResult:
    """Estimate fundamental frequency using librosa's YIN implementation."""
    if sample_rate <= 0:
        raise ValueError("sample_rate must be greater than 0.")
    if min_freq <= 0 or max_freq <= 0 or min_freq >= max_freq:
        raise ValueError("Expected 0 < min_freq < max_freq.")

    audio = _prepare_mono_samples(samples)
    if audio.size < 3:
        return PitchDetectionResult(frequency=None, rms=0.0, peak=0.0, clarity=0.0, backend="librosa_yin")

    rms, peak = signal_levels(audio)
    if rms < silence_threshold and peak < silence_threshold * 2.0:
        return PitchDetectionResult(frequency=None, rms=rms, peak=peak, clarity=0.0, backend="librosa_yin")

    try:
        import librosa
    except Exception as exc:  # pragma: no cover - depends on optional environment
        raise ImportError("librosa is required for the 'librosa_yin' pitch backend.") from exc

    frame_length = _frame_length_for_audio(audio.size)
    hop_length = max(1, frame_length // 4)
    frequencies = librosa.yin(
        audio.astype(np.float64, copy=False),
        fmin=min_freq,
        fmax=max_freq,
        sr=sample_rate,
        frame_length=frame_length,
        hop_length=hop_length,
        trough_threshold=0.10,
        center=False,
    )
    valid_frequencies = frequencies[np.isfinite(frequencies)]
    valid_frequencies = valid_frequencies[(valid_frequencies >= min_freq) & (valid_frequencies <= max_freq)]
    if valid_frequencies.size == 0:
        return PitchDetectionResult(frequency=None, rms=rms, peak=peak, clarity=0.0, backend="librosa_yin")

    frequency = float(np.median(valid_frequencies))
    clarity = _pitch_clarity(audio, sample_rate=sample_rate, frequency=frequency)
    if clarity < clarity_threshold:
        return PitchDetectionResult(frequency=None, rms=rms, peak=peak, clarity=clarity, backend="librosa_yin")

    return PitchDetectionResult(frequency=frequency, rms=rms, peak=peak, clarity=clarity, backend="librosa_yin")


def _frame_length_for_audio(sample_count: int) -> int:
    if sample_count <= 2048:
        return sample_count
    if sample_count <= 4096:
        return sample_count
    return 4096


def _pitch_clarity(samples: np.ndarray, sample_rate: int, frequency: float) -> float:
    if frequency <= 0:
        return 0.0
    audio = _prepare_mono_samples(samples)
    audio = audio - float(np.mean(audio))
    if audio.size < 3 or not np.any(audio):
        return 0.0
    lag_index = int(round(sample_rate / frequency))
    if lag_index <= 0 or lag_index >= audio.size:
        return 0.0
    autocorrelation = _fft_autocorrelation(audio * np.hanning(audio.size).astype(np.float32))
    zero_lag = float(autocorrelation[0])
    if zero_lag <= 1e-12:
        return 0.0
    return max(0.0, min(1.0, float(autocorrelation[lag_index] / zero_lag)))


def _prepare_mono_samples(samples: np.ndarray) -> np.ndarray:
    audio = np.asarray(samples, dtype=np.float32)
    if audio.ndim == 0:
        return np.asarray([], dtype=np.float32)
    if audio.ndim > 1:
        audio = np.mean(audio, axis=1)
    return np.ravel(audio).astype(np.float32, copy=False)


def _fft_autocorrelation(samples: np.ndarray) -> np.ndarray:
    fft_size = 1 << (samples.size * 2 - 1).bit_length()
    spectrum = np.fft.rfft(samples, n=fft_size)
    autocorrelation = np.fft.irfft(spectrum * np.conj(spectrum), n=fft_size)
    return autocorrelation[: samples.size].real


def _select_peak_offset(search: np.ndarray) -> int:
    if search.size < 3:
        return int(np.argmax(search))

    center = search[1:-1]
    local_peak_offsets = np.flatnonzero((center > search[:-2]) & (center >= search[2:])) + 1
    if local_peak_offsets.size == 0:
        return int(np.argmax(search))

    strongest = float(np.max(search[local_peak_offsets]))
    threshold = strongest * 0.85
    for offset in local_peak_offsets:
        if search[offset] >= threshold:
            return int(offset)
    return int(local_peak_offsets[np.argmax(search[local_peak_offsets])])


def _parabolic_lag(autocorrelation: np.ndarray, lag_index: int) -> float:
    if lag_index <= 0 or lag_index >= autocorrelation.size - 1:
        return float(lag_index)

    left = float(autocorrelation[lag_index - 1])
    center = float(autocorrelation[lag_index])
    right = float(autocorrelation[lag_index + 1])
    denominator = left - 2.0 * center + right
    if abs(denominator) <= 1e-12:
        return float(lag_index)

    offset = 0.5 * (left - right) / denominator
    if abs(offset) > 1.0:
        return float(lag_index)
    return float(lag_index) + offset
