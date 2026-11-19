from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from audio.audio_worker import AudioWorker
from audio.chord_audio_worker import ChordAudioWorker
from practice.score_steps import (
    PracticeStep,
    practice_step_carried_over_names,
    practice_steps_have_same_expected_frequencies,
)


PracticeAudioWorker = AudioWorker | ChordAudioWorker


class PracticeAudioController(QObject):
    result_ready = Signal(dict)
    error_message = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.worker: PracticeAudioWorker | None = None
        self._target_id = 0
        self._retired_workers: list[PracticeAudioWorker] = []

    def start_for_step(
        self,
        step: PracticeStep,
        device: int | str | None = None,
        previous_step: PracticeStep | None = None,
    ) -> None:
        self._target_id += 1
        restrike_names = practice_step_carried_over_names(previous_step, step)
        if self._can_retarget(step, device):
            if step.mode == "single":
                self.worker.set_target(step.expected_frequencies[0].frequency, self._target_id)
            else:
                self.worker.set_target(
                    list(step.expected_frequencies),
                    self._target_id,
                    restrike_names=restrike_names,
                )
            return

        self.stop()
        if step.mode == "single":
            expected = step.expected_frequencies[0]
            self.worker = AudioWorker(
                expected_frequency=expected.frequency,
                device=device,
                target_id=self._target_id,
                parent=self,
            )
        else:
            self.worker = ChordAudioWorker(
                expected_frequencies=list(step.expected_frequencies),
                device=device,
                restrike_names=restrike_names,
                target_id=self._target_id,
                parent=self,
            )

        self.worker.result_ready.connect(self._forward_result)
        self.worker.error_message.connect(self._forward_error)
        self.worker.start()

    def _can_retarget(self, step: PracticeStep, device: int | str | None) -> bool:
        worker = self.worker
        if worker is None or not worker.isRunning() or worker.device != device:
            return False
        return isinstance(worker, AudioWorker) == (step.mode == "single")

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
        self._retired_workers.append(worker)
        worker.finished.connect(self._release_finished_worker)
        worker.request_stop()
        if not worker.isRunning():
            self._release_worker(worker)

    def shutdown(self, timeout_ms: int = 3000) -> None:
        self.stop()
        for worker in list(self._retired_workers):
            worker.wait(timeout_ms)
            if not worker.isRunning():
                self._release_worker(worker)

    def _release_finished_worker(self) -> None:
        self._release_worker(self.sender())

    def _release_worker(self, worker: QObject | None) -> None:
        if worker not in self._retired_workers:
            return
        self._retired_workers.remove(worker)
        worker.deleteLater()

    def _forward_result(self, result: dict) -> None:
        if result.get("target_id") != self._target_id:
            return
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
