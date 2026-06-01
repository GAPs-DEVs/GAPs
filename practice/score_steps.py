from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from audio.music_math import guitar_note_frequency
from audio.polyphonic_detection import ExpectedFrequency


@dataclass(frozen=True)
class ParsedPracticeNote:
    step: int
    string_number: int
    fret_number: int
    frequency: float
    label: str


@dataclass(frozen=True)
class PracticeStep:
    step: int
    notes: tuple[ParsedPracticeNote, ...]
    expected_frequencies: tuple[ExpectedFrequency, ...]

    @property
    def mode(self) -> str:
        return "single" if len(self.expected_frequencies) == 1 else "chord"

    @property
    def display_label(self) -> str:
        return ", ".join(note.label for note in self.notes)


def build_practice_steps(parsed_notes: Iterable[Mapping[str, object]]) -> list[PracticeStep]:
    grouped: dict[int, list[ParsedPracticeNote]] = defaultdict(list)

    for raw_note in parsed_notes:
        parsed_note = _parse_practice_note(raw_note)
        grouped[parsed_note.step].append(parsed_note)

    practice_steps: list[PracticeStep] = []
    for step in sorted(grouped):
        notes = tuple(grouped[step])
        expected_frequencies = tuple(
            ExpectedFrequency(name=note.label, frequency=note.frequency)
            for note in notes
        )
        practice_steps.append(
            PracticeStep(
                step=step,
                notes=notes,
                expected_frequencies=expected_frequencies,
            )
        )

    return practice_steps


def load_parsed_notes_from_json(json_path: str | Path) -> list[dict[str, Any]]:
    path = Path(json_path)
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    notes = _extract_note_list(data)
    parsed_notes: list[dict[str, Any]] = []
    for index, note in enumerate(notes):
        if not isinstance(note, Mapping):
            raise ValueError(f"JSON note at index {index} must be an object.")
        parsed_notes.append(dict(note))
    return parsed_notes


def build_practice_steps_from_json(json_path: str | Path) -> list[PracticeStep]:
    return build_practice_steps(load_parsed_notes_from_json(json_path))


def practice_step_frequency_signature(step: PracticeStep) -> tuple[float, ...]:
    return tuple(
        sorted(round(expected.frequency, 2) for expected in step.expected_frequencies)
    )


def practice_steps_have_same_expected_frequencies(
    left: PracticeStep,
    right: PracticeStep,
) -> bool:
    return practice_step_frequency_signature(left) == practice_step_frequency_signature(right)


def _extract_note_list(data: object) -> list[object]:
    if isinstance(data, list):
        return data

    if isinstance(data, Mapping):
        for key in ("notes", "parsed_notes"):
            value = data.get(key)
            if isinstance(value, list):
                return value
        raise ValueError("JSON object must contain a 'notes' or 'parsed_notes' list.")

    raise ValueError("JSON root must be a note list or an object containing a note list.")


def _parse_practice_note(raw_note: Mapping[str, object]) -> ParsedPracticeNote:
    try:
        step = int(raw_note["step"])
        string_number = int(raw_note["string"])
        fret_number = int(raw_note["fret"])
    except KeyError as exc:
        raise ValueError(f"missing parsed note field: {exc.args[0]}") from exc
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid parsed note values: {raw_note}") from exc

    if step < 1:
        raise ValueError("step must be greater than or equal to 1.")
    if fret_number < 0:
        raise ValueError("fret must be greater than or equal to 0.")

    frequency = guitar_note_frequency(string_number, fret_number)
    label = f"{string_number}번줄 {fret_number}프렛"
    return ParsedPracticeNote(
        step=step,
        string_number=string_number,
        fret_number=fret_number,
        frequency=frequency,
        label=label,
    )
