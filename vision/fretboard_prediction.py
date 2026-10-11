"""Bounded motion hints for optical flow; predictions never authorize drawing."""
from collections import deque

import cv2
import numpy as np


class CornerMotionPredictor:
    def __init__(self) -> None:
        self.samples = deque(maxlen=2)

    def clear(self) -> None:
        self.samples.clear()

    def observe(self, quad: np.ndarray, timestamp: float) -> None:
        if not np.isfinite(timestamp) or not np.isfinite(quad).all():
            self.clear()
            return
        if self.samples and not .005 <= timestamp - self.samples[-1][0] <= .25:
            self.clear()
        self.samples.append((timestamp, np.asarray(quad, dtype=np.float32).copy()))

    def predict_relative(self, timestamp: float) -> np.ndarray | None:
        """Return a last-frame -> next-frame search hint using elapsed time."""
        if len(self.samples) != 2 or not np.isfinite(timestamp):
            return None
        (first_time, first), (last_time, last) = self.samples
        horizon = timestamp - last_time
        if not 0 < horizon <= .2:
            return None
        delta = (last - first) * min(horizon / (last_time - first_time), 2.)
        displacement = float(np.max(np.linalg.norm(delta, axis=1)))
        if displacement < .25:
            return None
        # Cap extrapolation by board size, including under irregular frame times.
        long_edge = float(np.max(np.linalg.norm(np.roll(last, -1, axis=0) - last, axis=1)))
        delta *= min(1., max(20., long_edge * .25) / displacement)
        predicted = last + delta
        area = cv2.contourArea(last, oriented=True)
        predicted_area = cv2.contourArea(predicted, oriented=True)
        if (abs(area) < 100 or not cv2.isContourConvex(predicted)
                or not .65 < predicted_area / area < 1.55):
            return None
        pose = cv2.getPerspectiveTransform(last, predicted)
        denominators = np.c_[last, np.ones(4)] @ pose[2]
        if (not np.isfinite(pose).all() or np.any(abs(denominators) < 1e-6)
                or np.any(denominators * denominators[0] <= 0)):
            return None
        return pose
