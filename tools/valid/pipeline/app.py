"""메인 앱의 보정·추적 경로를 검증 도구 인터페이스로 감쌉니다."""

from __future__ import annotations

import json
from pathlib import Path
from time import monotonic

import numpy as np

from app.features.video import visible_calibrated_grid
from tools.valid.module import GuidePoint
from vision.fretboard_calibration import build_calibrated_grid
from vision.fretboard_mapping import DetectedLine, SelectedLineTracker, StableFretboardGrid
from vision.fretboard_motion import HybridSelectedLineTracker
from vision.fretboard_recovery import BackgroundFretboardRecovery
from vision.hand_tracking import HandOcclusionMasker


DEFAULT_CALIBRATION_PATH = Path(__file__).resolve().with_name("calibration.json")
VALIDATION_FPS = 15.0


class AppValidationPipeline:
    """저장된 사용자 보정값으로 실제 앱의 시계열 추적기를 실행합니다.

    검증 데이터는 반드시 프레임 순서대로 들어와야 합니다. 보정 프레임 전에는
    ``None``을 반환하고, 보정 프레임부터 ``list[GuidePoint]``을 반환합니다.
    """

    def __init__(self, calibration_path: str | Path = DEFAULT_CALIBRATION_PATH) -> None:
        self.calibration_path = Path(calibration_path)
        self.calibrations = self._load_calibrations()
        self.calibrations_by_frame = {
            int(item["frame_index"]): item for item in self.calibrations
        }
        self.first_frame_index = min(self.calibrations_by_frame)

        # 메인 앱 PracticeVideoWorker와 같은 추적 구성입니다.
        self.selected_line_tracker = SelectedLineTracker(max_history=5)
        self.hybrid_tracker = HybridSelectedLineTracker(
            self.selected_line_tracker,
            recovery=BackgroundFretboardRecovery(),
        )
        self.occlusion_masker = HandOcclusionMasker()
        self._last_frame_index: int | None = None
        self._timestamp_origin = monotonic()
        print(
            f"AppValidationPipeline initialized: {self.calibration_path} "
            f"(first frame={self.first_frame_index})",
            flush=True,
        )

    def _load_calibrations(self) -> list[dict[str, object]]:
        if not self.calibration_path.exists():
            raise FileNotFoundError(f"Calibration file not found: {self.calibration_path}")
        payload = json.loads(self.calibration_path.read_text(encoding="utf-8"))
        calibrations = payload.get("calibrations")
        if not isinstance(calibrations, list) or not calibrations:
            raise ValueError("calibration.json must contain at least one calibration")
        return sorted(calibrations, key=lambda item: int(item["frame_index"]))

    @staticmethod
    def _line(item: dict[str, object], key: str, kind: str) -> DetectedLine:
        raw_line = item.get(key)
        if not isinstance(raw_line, list) or len(raw_line) != 2:
            raise ValueError(f"Invalid calibration line: {key}")
        p0, p1 = raw_line
        if not isinstance(p0, list) or not isinstance(p1, list) or len(p0) != 2 or len(p1) != 2:
            raise ValueError(f"Invalid calibration endpoints: {key}")
        return DetectedLine(
            kind=kind,
            p0=(float(p0[0]), float(p0[1])),
            p1=(float(p1[0]), float(p1[1])),
            confidence=1.0,
        )

    def _apply_calibration(self, frame_index: int, frame_bgr: np.ndarray) -> StableFretboardGrid:
        item = self.calibrations_by_frame[frame_index]
        nut = self._line(item, "nut_line", "nut")
        middle = self._line(item, "middle_fret_line", "fret")
        reference = self._line(item, "reference_fret_line", "fret")
        middle_index = int(item["middle_fret_index"])
        reference_index = int(item["reference_fret_index"])

        # 사용자 앱의 3선 보정 함수로 원근과 지판 폭 변화를 포함한 기준 격자를 만듭니다.
        grid = build_calibrated_grid(
            nut,
            middle,
            middle_index,
            reference,
            reference_index,
        )
        exclusion_mask = self.occlusion_masker.process(frame_bgr)
        self.selected_line_tracker.clear()
        self.selected_line_tracker.reference_fret_index = reference_index
        self.selected_line_tracker.accept_registered_grid(grid)
        self.hybrid_tracker.seed(frame_bgr, exclusion_mask)
        print(f"Calibration applied: frame={frame_index}", flush=True)
        return grid

    @staticmethod
    def _grid_to_points(grid: StableFretboardGrid | None) -> list[GuidePoint]:
        if grid is None or len(grid.string_crossings) < 2:
            return []

        points: list[GuidePoint] = []
        for fret_index in range(1, len(grid.string_crossings)):
            previous_row = grid.string_crossings[fret_index - 1]
            current_row = grid.string_crossings[fret_index]
            for screen_index, (previous, current) in enumerate(
                zip(previous_row, current_row, strict=True)
            ):
                # 정답 라벨은 화면 아래쪽 줄이 string=1입니다. 격자 crossing은
                # 위에서 아래 순서이므로 번호만 뒤집고 좌표는 그대로 사용합니다.
                string_number = len(previous_row) - screen_index
                points.append(
                    GuidePoint(
                        string=string_number,
                        fret=fret_index,
                        x=round((float(previous[0]) + float(current[0])) * 0.5, 4),
                        y=round((float(previous[1]) + float(current[1])) * 0.5, 4),
                        visible=True,
                    )
                )
        return points

    def run(self, frame_index: int, input_image: np.ndarray) -> list[GuidePoint] | None:
        if self._last_frame_index is not None and frame_index != self._last_frame_index + 1:
            raise ValueError("App validation frames must be processed sequentially")
        self._last_frame_index = frame_index

        if frame_index < self.first_frame_index:
            return None
        if frame_index in self.calibrations_by_frame:
            return self._grid_to_points(self._apply_calibration(frame_index, input_image))

        exclusion_mask = self.occlusion_masker.process(input_image)

        # 저장 영상은 15fps이므로 실제 카메라와 같은 시간 간격을 추적기에 전달합니다.
        timestamp = self._timestamp_origin + (frame_index - self.first_frame_index) / VALIDATION_FPS
        tracking_frame = self.hybrid_tracker.update(
            input_image,
            detect=lambda _roi: (),
            exclusion_mask=exclusion_mask,
            timestamp=timestamp,
        )
        # 실제 앱과 동일하게 hold 상태에서는 마지막 좌표를 화면에 표시하지 않습니다.
        return self._grid_to_points(visible_calibrated_grid(tracking_frame))

    def close(self) -> None:
        self.hybrid_tracker.close()
        self.occlusion_masker.close()

