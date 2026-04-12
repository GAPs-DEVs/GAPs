from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


CAMERA_MATRIX_KEYS = ("camera_matrix", "cameraMatrix", "K")
DIST_COEFF_KEYS = ("dist_coeffs", "distCoeffs", "distortion_coefficients", "distortionCoefficients")


@dataclass(frozen=True)
class CameraCalibration:
    camera_matrix: np.ndarray
    dist_coeffs: np.ndarray
    image_size: tuple[int, int] | None = None

    def scaled_camera_matrix(self, frame_size: tuple[int, int]) -> np.ndarray:
        frame_width, frame_height = frame_size
        scaled_matrix = np.asarray(self.camera_matrix, dtype=np.float32).copy()
        if self.image_size is None:
            return scaled_matrix

        calibration_width, calibration_height = self.image_size
        if calibration_width <= 0 or calibration_height <= 0:
            return scaled_matrix

        scale_x = frame_width / float(calibration_width)
        scale_y = frame_height / float(calibration_height)
        scaled_matrix[0, 0] *= scale_x
        scaled_matrix[0, 2] *= scale_x
        scaled_matrix[1, 1] *= scale_y
        scaled_matrix[1, 2] *= scale_y
        return scaled_matrix

    def undistort_frame(self, frame_bgr: np.ndarray, alpha: float = 0.0) -> np.ndarray:
        if frame_bgr.size == 0:
            return frame_bgr

        frame_height, frame_width = frame_bgr.shape[:2]
        camera_matrix = self.scaled_camera_matrix((frame_width, frame_height))
        new_camera_matrix, _ = cv2.getOptimalNewCameraMatrix(
            camera_matrix,
            self.dist_coeffs,
            (frame_width, frame_height),
            alpha,
        )
        return cv2.undistort(frame_bgr, camera_matrix, self.dist_coeffs, None, new_camera_matrix)


def _read_first_available_array(npz_file, keys: tuple[str, ...], label: str) -> np.ndarray:
    for key in keys:
        if key in npz_file:
            return np.asarray(npz_file[key], dtype=np.float32)
    raise KeyError(f"Missing {label} in calibration file. Expected one of: {', '.join(keys)}")


def _read_image_size(npz_file) -> tuple[int, int] | None:
    if "image_size" in npz_file:
        image_size = np.asarray(npz_file["image_size"]).astype(np.int32).reshape(-1)
        if image_size.size >= 2:
            return (int(image_size[0]), int(image_size[1]))

    width_keys = ("image_width", "frame_width")
    height_keys = ("image_height", "frame_height")
    width = next((int(np.asarray(npz_file[key]).reshape(-1)[0]) for key in width_keys if key in npz_file), None)
    height = next((int(np.asarray(npz_file[key]).reshape(-1)[0]) for key in height_keys if key in npz_file), None)
    if width is not None and height is not None:
        return (width, height)
    return None


def load_camera_calibration(calibration_path: Path | str) -> CameraCalibration:
    path = Path(calibration_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Camera calibration file not found: {path}")

    with np.load(path) as calibration_data:
        camera_matrix = _read_first_available_array(calibration_data, CAMERA_MATRIX_KEYS, "camera matrix")
        dist_coeffs = _read_first_available_array(calibration_data, DIST_COEFF_KEYS, "distortion coefficients")
        image_size = _read_image_size(calibration_data)

    camera_matrix = camera_matrix.reshape(3, 3)
    dist_coeffs = dist_coeffs.reshape(-1)
    return CameraCalibration(
        camera_matrix=camera_matrix,
        dist_coeffs=dist_coeffs,
        image_size=image_size,
    )
