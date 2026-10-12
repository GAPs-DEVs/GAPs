"""Camera backpressure must drop old input without changing calibration pixels."""
from itertools import count
from queue import Queue
from threading import Event, Thread, get_ident

import numpy as np
import pytest

from app.features.video_capture import LatestFrameCapture


class FakeCamera:
    def __init__(self, *, opened=True):
        self.opened = opened
        self.frames = Queue()
        self.read_requests = Queue()
        self.released = Event()
        self.owner_threads = []

    def set(self, *_):
        self.owner_threads.append(get_ident())

    def isOpened(self):
        return self.opened

    def read(self):
        self.owner_threads.append(get_ident())
        self.read_requests.put(True)
        packet = self.frames.get(timeout=3)
        if isinstance(packet, Exception):
            raise packet
        return packet

    def release(self):
        self.owner_threads.append(get_ident())
        self.released.set()

    def publish(self, frame):
        self.frames.put((True, frame))
        # A new read begins only after the previous pixels were published.
        self.read_requests.get(timeout=3)


@pytest.fixture
def camera(monkeypatch):
    fake = FakeCamera()
    monkeypatch.setattr('app.features.video_capture.cv2.VideoCapture', lambda _: fake)
    ticks = count(10)
    monkeypatch.setattr('app.features.video_capture.monotonic', lambda: float(next(ticks)))
    capture = LatestFrameCapture(0)
    capture.start()
    fake.read_requests.get(timeout=3)
    try:
        yield capture, fake
    finally:
        capture.request_stop()
        fake.frames.put((False, None))
        capture.close()
    assert fake.released.is_set()
    assert len(set(fake.owner_threads)) == 1
    assert fake.owner_threads[0] != get_ident()


def test_slow_consumer_gets_newest_frame_and_original_capture_timestamp(camera):
    capture, fake = camera
    for value in range(1, 4):
        fake.publish(np.full((4, 5, 3), value, dtype=np.uint8))
    packet = capture.read()
    assert np.all(packet.image == 3)
    assert packet.timestamp == 12.
    assert packet.sequence == 3
    assert packet.capture_fps == 1.


def test_backend_buffer_reuse_cannot_mutate_consumed_or_frozen_pixels(camera):
    capture, fake = camera
    shared = np.full((4, 5, 3), 1, dtype=np.uint8)
    fake.publish(shared)
    first = capture.read()
    shared[:] = 2
    fake.publish(shared)
    second = capture.read()
    shared[:] = 3
    assert np.all(first.image == 1)
    assert np.all(second.image == 2)
    assert second.timestamp > first.timestamp


def test_stop_wakes_consumer_even_while_camera_read_is_blocked(camera):
    capture, fake = camera
    result = Queue()
    consumer = Thread(target=lambda: result.put(capture.read()), daemon=True)
    consumer.start()
    capture.request_stop()
    assert result.get(timeout=3) is None
    consumer.join(timeout=3)
    assert not consumer.is_alive()
    # Only the capture thread may release after the pending read returns.
    assert not fake.released.is_set()


@pytest.mark.parametrize('failure', [(False, None), RuntimeError('camera disconnected')])
def test_read_failure_reaches_consumer_and_releases_device(camera, failure):
    capture, fake = camera
    fake.frames.put(failure)
    with pytest.raises(RuntimeError, match='Failed to read|camera disconnected'):
        capture.read()
    assert fake.released.is_set()


def test_open_failure_reaches_consumer_and_releases_device(monkeypatch):
    fake = FakeCamera(opened=False)
    monkeypatch.setattr('app.features.video_capture.cv2.VideoCapture', lambda _: fake)
    capture = LatestFrameCapture(7)
    capture.start()
    try:
        with pytest.raises(RuntimeError, match='webcam index 7'):
            capture.read()
    finally:
        capture.close()
    assert fake.released.is_set()
