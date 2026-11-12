"""The asynchronous hand mask must never make the tracking thread wait."""
from threading import Event

import numpy as np

from vision.hand_tracking import AsyncHandOcclusionMasker


class FakeMasker:
    """Marks pixel (0, 0) with the frame's own value; later calls can be held."""

    def __init__(self):
        self.available = False
        self.release = Event()
        self.release.set()
        self.finished = Event()
        self.seen = []
        self.closed = False

    @staticmethod
    def shrink(frame):
        return frame.copy()

    def mask(self, small, frame_size):
        self.release.wait(5)
        self.available = True
        self.seen.append(int(small[0, 0, 0]))
        mask = np.zeros(frame_size, dtype=np.uint8)
        mask[0, 0] = small[0, 0, 0]
        self.finished.set()
        return mask

    def close(self):
        self.closed = True


def frame(value, shape=(40, 60, 3)):
    image = np.zeros(shape, dtype=np.uint8)
    image[0, 0] = value
    return image


def wait_for_result(fake):
    assert fake.finished.wait(5)
    fake.finished.clear()


def test_first_call_waits_and_later_calls_return_the_previous_frames_mask():
    fake = FakeMasker()
    masker = AsyncHandOcclusionMasker(fake)
    try:
        first = masker.process(frame(1))
        assert first[0, 0] == 1 and masker.available
        fake.finished.clear()

        fake.release.clear()  # Inference for frame 2 is still running.
        assert masker.process(frame(2))[0, 0] == 1
        fake.release.set()
        wait_for_result(fake)
        # The result is published under the lock right after ``mask`` returns.
        for _ in range(1000):
            if masker.process(frame(2))[0, 0] == 2:
                break
        assert masker.process(frame(2))[0, 0] == 2
    finally:
        masker.close()
    assert fake.closed


def test_slow_inference_skips_frames_instead_of_queueing_them():
    fake = FakeMasker()
    masker = AsyncHandOcclusionMasker(fake)
    try:
        masker.process(frame(1))
        fake.finished.clear()
        fake.release.clear()
        for value in (2, 3, 4, 5):
            masker.process(frame(value))
        fake.release.set()
        masker.close()
    finally:
        masker.close()
    # At most one frame was already running and one newer frame may follow it;
    # the frames in between are dropped, and nothing runs out of order.
    assert fake.seen[0] == 1 and len(fake.seen) <= 3
    assert fake.seen == sorted(fake.seen)


def test_mask_for_another_resolution_is_not_returned():
    fake = FakeMasker()
    masker = AsyncHandOcclusionMasker(fake)
    try:
        assert masker.process(frame(1)) is not None
        fake.release.clear()
        assert masker.process(frame(2, shape=(80, 120, 3))) is None
        fake.release.set()
    finally:
        masker.close()


def test_closed_masker_returns_no_mask():
    masker = AsyncHandOcclusionMasker(FakeMasker())
    masker.close()
    assert masker.process(frame(1)) is None
