"""Save the frames a practice session tracked, so tracking can be replayed.

The recorder never runs on the measured path: the video thread only hands over
an array reference, and encoding happens on a writer thread. When that thread
falls behind, frames are dropped and counted rather than slowing tracking.
"""
from __future__ import annotations

import json
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Thread

import cv2
import numpy as np

from vision.fretboard_mapping import DetectedLine, StableFretboardGrid


VIDEO_NAME = 'frames.avi'
INDEX_NAME = 'recording.json'


def grid_to_json(grid: StableFretboardGrid) -> dict:
    def line(value: DetectedLine) -> dict:
        return {'kind': value.kind, 'p0': list(value.p0), 'p1': list(value.p1),
                'confidence': value.confidence, 'endpoints_observed': value.endpoints_observed}
    return {
        'nut_line': line(grid.nut_line),
        'ordered_fret_lines': [line(value) for value in grid.ordered_fret_lines],
        'string_crossings': [[list(point) for point in row] for row in grid.string_crossings],
    }


def grid_from_json(data: dict) -> StableFretboardGrid:
    def line(value: dict) -> DetectedLine:
        return DetectedLine(value['kind'], tuple(value['p0']), tuple(value['p1']),
                            value['confidence'], value['endpoints_observed'])
    return StableFretboardGrid(
        nut_line=line(data['nut_line']),
        ordered_fret_lines=tuple(line(value) for value in data['ordered_fret_lines']),
        string_crossings=tuple(tuple(tuple(point) for point in row) for row in data['string_crossings']),
    )


class SessionRecorder:
    """Motion-JPEG video plus the calibration events needed to replay it.

    The calibration image is the tracker's permanent reference, so it is also
    stored losslessly: compression artefacts there would bias every later
    comparison. Ordinary frames are compressed; they are only tracked against.
    """

    def __init__(self, directory: Path, *, fps: float = 30., queue_frames: int = 30) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.fps = fps
        self.dropped_frames = 0
        self._queue: Queue = Queue(maxsize=queue_frames)
        self._frames: list[dict] = []
        self._events: list[dict] = []
        self._results: dict[int, dict] = {}
        self._writer: cv2.VideoWriter | None = None
        self._closed = False
        self._thread = Thread(target=self._run, name='session-recorder', daemon=True)
        self._thread.start()

    def frame(self, image: np.ndarray, sequence: int, timestamp: float) -> None:
        """Queue one tracked frame. The caller must not modify ``image`` later."""
        if self._closed:
            return
        try:
            self._queue.put_nowait((image, int(sequence), float(timestamp)))
        except Full:
            self.dropped_frames += 1

    def result(self, sequence: int, mode: str, detail: str, marker_count: int) -> None:
        self._results[int(sequence)] = {'mode': mode, 'detail': detail, 'markers': int(marker_count)}

    def seeded(self, sequence: int, image: np.ndarray, exclusion_mask: np.ndarray | None,
               grid: StableFretboardGrid | None, reference_fret_index: int | None) -> None:
        """Calibration applied after frame ``sequence`` was processed."""
        if self._closed or grid is None:
            return
        number = sum(event['type'] == 'seed' for event in self._events)
        image_name, mask_name = f'seed-{number}.png', None
        cv2.imwrite(str(self.directory / image_name), image)
        if exclusion_mask is not None:
            mask_name = f'seed-{number}-mask.png'
            cv2.imwrite(str(self.directory / mask_name), exclusion_mask)
        self._events.append({'type': 'seed', 'after_sequence': int(sequence), 'image': image_name, 'mask': mask_name,
                             'grid': grid_to_json(grid), 'reference_fret_index': reference_fret_index})

    def cleared(self, sequence: int) -> None:
        if not self._closed:
            self._events.append({'type': 'clear', 'after_sequence': int(sequence)})

    def close(self) -> Path:
        if not self._closed:
            self._closed = True
            self._queue.put(None)
            self._thread.join()
            if self._writer is not None:
                self._writer.release()
            for frame in self._frames:
                frame.update(self._results.get(frame['sequence'], {}))
            with (self.directory / INDEX_NAME).open('w', encoding='utf-8') as handle:
                json.dump({'version': 1, 'video': VIDEO_NAME, 'fps': self.fps, 'dropped_frames': self.dropped_frames,
                           'frames': self._frames, 'events': self._events}, handle, ensure_ascii=False)
        return self.directory

    def _run(self) -> None:
        while True:
            try:
                item = self._queue.get(timeout=.5)
            except Empty:
                continue
            if item is None:
                return
            image, sequence, timestamp = item
            if self._writer is None:
                height, width = image.shape[:2]
                self._writer = cv2.VideoWriter(str(self.directory / VIDEO_NAME),
                                               cv2.VideoWriter_fourcc(*'MJPG'), self.fps, (width, height))
                self._writer.set(cv2.VIDEOWRITER_PROP_QUALITY, 95)
            self._writer.write(image)
            self._frames.append({'sequence': sequence, 'timestamp': timestamp})
