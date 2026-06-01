from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from vision.camera_calibration import CameraCalibration, load_camera_calibration


class CameraCalibrationTests(unittest.TestCase):
    def test_scaled_camera_matrix_scales_intrinsics_to_frame_size(self) -> None:
        calibration = CameraCalibration(
            camera_matrix=np.asarray(
                [
                    [800.0, 0.0, 320.0],
                    [0.0, 820.0, 240.0],
                    [0.0, 0.0, 1.0],
                ],
                dtype=np.float32,
            ),
            dist_coeffs=np.zeros(5, dtype=np.float32),
            image_size=(640, 480),
        )

        scaled = calibration.scaled_camera_matrix((1280, 960))

        self.assertAlmostEqual(float(scaled[0, 0]), 1600.0, places=4)
        self.assertAlmostEqual(float(scaled[1, 1]), 1640.0, places=4)
        self.assertAlmostEqual(float(scaled[0, 2]), 640.0, places=4)
        self.assertAlmostEqual(float(scaled[1, 2]), 480.0, places=4)

    def test_load_camera_calibration_reads_npz_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            calibration_path = Path(temp_dir) / "camera_calibration.npz"
            np.savez(
                calibration_path,
                camera_matrix=np.asarray(
                    [
                        [700.0, 0.0, 300.0],
                        [0.0, 710.0, 220.0],
                        [0.0, 0.0, 1.0],
                    ],
                    dtype=np.float32,
                ),
                dist_coeffs=np.asarray([0.1, -0.2, 0.0, 0.0, 0.03], dtype=np.float32),
                image_size=np.asarray([640, 480], dtype=np.int32),
            )

            calibration = load_camera_calibration(calibration_path)

        self.assertEqual(calibration.image_size, (640, 480))
        self.assertEqual(calibration.camera_matrix.shape, (3, 3))
        self.assertEqual(calibration.dist_coeffs.shape, (5,))

    def test_undistort_frame_preserves_shape(self) -> None:
        calibration = CameraCalibration(
            camera_matrix=np.asarray(
                [
                    [800.0, 0.0, 320.0],
                    [0.0, 800.0, 240.0],
                    [0.0, 0.0, 1.0],
                ],
                dtype=np.float32,
            ),
            dist_coeffs=np.zeros(5, dtype=np.float32),
            image_size=(640, 480),
        )
        frame = np.zeros((480, 640, 3), dtype=np.uint8)

        undistorted = calibration.undistort_frame(frame)

        self.assertEqual(undistorted.shape, frame.shape)


if __name__ == "__main__":
    unittest.main()
