from __future__ import annotations

import math
from collections.abc import Mapping


STANDARD_TUNING_FREQUENCIES: dict[int, float] = {
    6: 82.41,
    5: 110.00,
    4: 146.83,
    3: 196.00,
    2: 246.94,
    1: 329.63,
}

SOLFEGE_TEST_NOTE_FREQUENCIES: dict[str, float] = {
    "낮은 도": 130.8128,
    "레": 146.8324,
    "미": 164.8138,
    "파": 174.6141,
    "솔": 195.9977,
    "라": 220.0000,
    "시": 246.9417,
    "높은 도": 261.6256,
}


def _require_positive_frequency(frequency: float, name: str = "frequency") -> None:
    if frequency <= 0:
        raise ValueError(f"{name} must be greater than 0 Hz.")


def frequency_to_midi(frequency: float) -> float:
    """Convert a frequency in Hz to a MIDI note number."""
    _require_positive_frequency(frequency)
    return 69.0 + 12.0 * math.log2(frequency / 440.0)


def midi_to_frequency(midi: float) -> float:
    """Convert a MIDI note number to frequency in Hz."""
    return 440.0 * (2.0 ** ((midi - 69.0) / 12.0))


def cents_difference(detected_frequency: float, expected_frequency: float) -> float:
    """Return signed cents difference between detected and expected frequency."""
    _require_positive_frequency(detected_frequency, "detected_frequency")
    _require_positive_frequency(expected_frequency, "expected_frequency")
    return 1200.0 * math.log2(detected_frequency / expected_frequency)


def is_within_tolerance(
    detected_frequency: float,
    expected_frequency: float,
    tolerance_cents: float,
) -> bool:
    """Return True when the detected frequency is within the cents tolerance."""
    if tolerance_cents < 0:
        raise ValueError("tolerance_cents must be greater than or equal to 0.")
    return abs(cents_difference(detected_frequency, expected_frequency)) <= tolerance_cents


def frequency_tolerance_range(
    expected_frequency: float,
    tolerance_cents: float,
) -> tuple[float, float]:
    """Return inclusive lower/upper frequency bounds for a cents tolerance."""
    _require_positive_frequency(expected_frequency, "expected_frequency")
    if tolerance_cents < 0:
        raise ValueError("tolerance_cents must be greater than or equal to 0.")

    ratio = 2.0 ** (tolerance_cents / 1200.0)
    return expected_frequency / ratio, expected_frequency * ratio


def guitar_note_frequency(
    string_number: int,
    fret_number: int,
    tuning: Mapping[int, float] | None = None,
) -> float:
    """Return the frequency for a guitar string/fret pair.

    string_number uses common guitar notation: 6 is low E2 and 1 is high E4.
    """
    if fret_number < 0:
        raise ValueError("fret_number must be greater than or equal to 0.")

    tuning_frequencies = tuning or STANDARD_TUNING_FREQUENCIES
    if string_number not in tuning_frequencies:
        raise ValueError("string_number must be one of 1, 2, 3, 4, 5, or 6.")

    open_frequency = float(tuning_frequencies[string_number])
    _require_positive_frequency(open_frequency, f"tuning[{string_number}]")
    return open_frequency * (2.0 ** (fret_number / 12.0))
