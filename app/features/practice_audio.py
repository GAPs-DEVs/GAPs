from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from audio.audio_worker import AudioWorker
from audio.chord_audio_worker import ChordAudioWorker
from practice.score_steps import PracticeStep, practice_steps_have_same_expected_frequencies


PracticeAudioWorker = AudioWorker | ChordAudioWorker


class PracticeAudioController(QObject):
    result_ready = Signal(dict)
    error_message = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.worker: PracticeAudioWorker | None = None

    def start_for_step(self, step: PracticeStep, device: int | str | None = None) -> None:
        self.stop()
        if step.mode == "single":
            expected = step.expected_frequencies[0]
            self.worker = AudioWorker(
                expected_frequency=expected.frequency,
                device=device,
                parent=self,
            )
        else:
            self.worker = ChordAudioWorker(
                expected_frequencies=list(step.expected_frequencies),
                device=device,
                parent=self,
            )

        self.worker.result_ready.connect(self._forward_result)
        self.worker.error_message.connect(self._forward_error)
        self.worker.start()

    def stop(self) -> None:
        if self.worker is None:
            return
        worker = self.worker
        self.worker = None
        try:
            worker.result_ready.disconnect(self._forward_result)
            worker.error_message.disconnect(self._forward_error)
        except RuntimeError:
            pass
        worker.stop()
        worker.deleteLater()

    def _forward_result(self, result: dict) -> None:
        self.result_ready.emit(result)

    def _forward_error(self, message: str) -> None:
        self.error_message.emit(message)


class RetriggerGate:
    def __init__(self) -> None:
        self.release_required = False
        self.release_observed = True

    def configure(self, previous_step: PracticeStep | None, current_step: PracticeStep) -> None:
        self.release_required = (
            previous_step is not None
            and practice_steps_have_same_expected_frequencies(previous_step, current_step)
        )
        self.release_observed = not self.release_required

    def apply(self, result: dict) -> dict:
        if not self.release_required:
            gated = dict(result)
            gated["retrigger_required"] = False
            gated["retrigger_ready"] = True
            return gated

        status = str(result.get("status", "idle"))
        if status == "no_signal":
            self.release_observed = True

        gated = dict(result)
        gated["retrigger_required"] = True
        gated["retrigger_ready"] = self.release_observed

        if self.release_observed:
            return gated

        if gated.get("is_correct"):
            gated["is_correct"] = False
            gated["status"] = "listening"
            gated["message"] = "같은 음 반복 step입니다. 소리가 끊긴 뒤 다시 연주하면 넘어갑니다."
        return gated


def with_step_context(
    result: dict,
    step: PracticeStep,
    current_index: int,
    total_steps: int,
) -> dict:
    enriched = dict(result)
    enriched["practice_step"] = step.step
    enriched["practice_index"] = current_index
    enriched["practice_total"] = total_steps
    enriched["practice_mode"] = step.mode
    enriched["expected_notes"] = [
        {
            "label": note.label,
            "string": note.string_number,
            "fret": note.fret_number,
            "frequency": note.frequency,
        }
        for note in step.notes
    ]
    if step.mode == "single":
        note = step.notes[0]
        enriched["expected_label"] = note.label
        enriched["expected_frequency"] = note.frequency
    return enriched
