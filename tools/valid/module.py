"""검증 계산, 데이터 로딩, 결과 저장에 필요한 공용 코어입니다."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np


VALID_ROOT = Path(__file__).resolve().parent
DEFAULT_DATASET = VALID_ROOT / "ds"
DEFAULT_RESULT_DIR = VALID_ROOT / "result"

REGION_LONG_DIAMETER_PX = 40.0
REGION_SHORT_DIAMETER_PX = 10.0

__all__ = [
    "DEFAULT_DATASET",
    "DEFAULT_RESULT_DIR",
    "GuidePoint",
    "AnswerPoints",
    "CheckResult",
    "FrameRecord",
    "ValidLoader",
    "ValidResultManager",
    "log_validation_progress",
]


@dataclass(frozen=True)
class GuidePoint:
    """한 줄과 한 프렛의 검증 좌표입니다."""

    string: int
    fret: int
    x: float | None
    y: float | None
    visible: bool

    @classmethod
    def list_from_payload(cls, payload: dict[str, object]) -> list["GuidePoint"]:
        """``{"points": [...]}`` JSON 구조를 점 목록으로 변환합니다."""
        raw_points = payload.get("points", [])
        if not isinstance(raw_points, list):
            return []

        points: list[GuidePoint] = []
        for item in raw_points:
            if not isinstance(item, dict):
                continue
            points.append(
                cls(
                    string=int(item.get("string", 0) or 0),
                    fret=int(item.get("fret", 0) or 0),
                    x=None if item.get("x") is None else float(item["x"]),
                    y=None if item.get("y") is None else float(item["y"]),
                    visible=bool(item.get("visible", False)),
                )
            )
        return points

    @classmethod
    def list_to_payload(cls, points: list["GuidePoint"]) -> dict[str, object]:
        """점 목록을 검증 JSON의 최소 출력 구조로 변환합니다."""
        return {
            "points": [
                {
                    "string": point.string,
                    "fret": point.fret,
                    "x": point.x,
                    "y": point.y,
                    "visible": point.visible,
                }
                for point in points
            ]
        }


@dataclass(frozen=True)
class CheckResult:
    """한 프레임에서 계산된 검증 지표입니다."""

    gt_points: int
    pred_points: int
    matched_points: int
    mean_error_px: float
    max_error_px: float
    missing_keys: list[tuple[int, int]]
    region_correct_points: int
    region_accuracy: float
    region_correct_keys: list[tuple[int, int]]
    region_incorrect_keys: list[tuple[int, int]]


def log_validation_progress(
    frame_index: int,
    total_frames: int,
    result: CheckResult,
    every: int = 100,
) -> None:
    """첫 프레임, 매 ``every`` 프레임, 마지막 프레임의 결과만 출력합니다."""
    is_first = frame_index == 0
    is_interval = every > 0 and (frame_index + 1) % every == 0
    is_last = total_frames > 0 and frame_index == total_frames - 1
    if not (is_first or is_interval or is_last):
        return

    print(
        f"frame={frame_index}/{total_frames} "
        f"gt={result.gt_points} "
        f"pred={result.pred_points} "
        f"matched={result.matched_points} "
        f"mean_error_px={result.mean_error_px:.4f} "
        f"max_error_px={result.max_error_px:.4f} "
        f"missing={len(result.missing_keys)} "
        f"region_accuracy={result.region_accuracy:.2%}",
        flush=True,
    )


@dataclass(frozen=True)
class AnswerPoints:
    """정답 점과 예측 점의 비교를 담당합니다."""

    points: list[GuidePoint]

    def __iter__(self):
        return iter(self.points)

    def __len__(self) -> int:
        return len(self.points)

    @classmethod
    def from_json(cls, answer_json_path: str | Path) -> "AnswerPoints":
        payload = json.loads(Path(answer_json_path).read_text(encoding="utf-8"))
        return cls(points=GuidePoint.list_from_payload(payload))

    def _point_map(self, points: list[GuidePoint]) -> dict[tuple[int, int], GuidePoint]:
        # 비교 키는 좌표 순서가 아니라 의미가 같은 (string, fret)입니다.
        return {
            (point.string, point.fret): point
            for point in points
            if point.string > 0 and point.fret > 0
        }

    def _point_error_px(self, gt_point: GuidePoint, pred_point: GuidePoint) -> float | None:
        if not gt_point.visible or not pred_point.visible:
            return None
        if gt_point.x is None or gt_point.y is None or pred_point.x is None or pred_point.y is None:
            return None
        return float(np.hypot(pred_point.x - gt_point.x, pred_point.y - gt_point.y))

    def _gt_region_ellipse(
        self,
        key: tuple[int, int],
        gt_map: dict[tuple[int, int], GuidePoint],
    ) -> tuple[tuple[float, float], float, float, float]:
        """인접 프렛의 진행 방향을 따르는 40x10px 타원을 만듭니다."""
        gt_point = gt_map[key]
        if gt_point.x is None or gt_point.y is None:
            raise ValueError(f"GT point has no coordinates: {key}")

        string_number, fret_number = key
        center = np.asarray((gt_point.x, gt_point.y), dtype=np.float32)
        previous = gt_map.get((string_number, fret_number - 1))
        following = gt_map.get((string_number, fret_number + 1))
        previous_xy = (
            np.asarray((previous.x, previous.y), dtype=np.float32)
            if previous is not None and previous.x is not None and previous.y is not None
            else None
        )
        following_xy = (
            np.asarray((following.x, following.y), dtype=np.float32)
            if following is not None and following.x is not None and following.y is not None
            else None
        )

        if previous_xy is not None and following_xy is not None:
            direction = following_xy - previous_xy
        elif following_xy is not None:
            direction = following_xy - center
        elif previous_xy is not None:
            direction = center - previous_xy
        else:
            direction = np.asarray((1.0, 0.0), dtype=np.float32)

        angle_degrees = float(np.degrees(np.arctan2(direction[1], direction[0])))
        return (
            (float(center[0]), float(center[1])),
            REGION_LONG_DIAMETER_PX * 0.5,
            REGION_SHORT_DIAMETER_PX * 0.5,
            angle_degrees,
        )

    def _inside_gt_ellipse(
        self,
        key: tuple[int, int],
        gt_map: dict[tuple[int, int], GuidePoint],
        pred_point: GuidePoint,
    ) -> bool:
        """같은 키의 예측점이 해당 GT 타원 내부인지 판정합니다."""
        gt_point = gt_map[key]
        if (
            not gt_point.visible
            or not pred_point.visible
            or gt_point.x is None
            or gt_point.y is None
            or pred_point.x is None
            or pred_point.y is None
        ):
            return False

        center, long_radius, short_radius, angle_degrees = self._gt_region_ellipse(key, gt_map)
        angle = np.deg2rad(angle_degrees)
        delta_x = float(pred_point.x) - center[0]
        delta_y = float(pred_point.y) - center[1]

        # 예측점을 타원 축 기준 좌표로 회전한 뒤 표준 타원 방정식으로 판정합니다.
        local_long = delta_x * np.cos(angle) + delta_y * np.sin(angle)
        local_short = -delta_x * np.sin(angle) + delta_y * np.cos(angle)
        return (local_long / long_radius) ** 2 + (local_short / short_radius) ** 2 <= 1.0

    def region_ellipse_map(
        self,
    ) -> dict[tuple[int, int], tuple[tuple[float, float], float, float, float]]:
        """뷰어가 평가와 동일한 타원을 그릴 수 있도록 영역을 반환합니다."""
        gt_map = self._point_map(self.points)
        return {
            key: self._gt_region_ellipse(key, gt_map)
            for key, point in gt_map.items()
            if point.visible and point.x is not None and point.y is not None
        }

    def _check_points(self, pred_points: list[GuidePoint]) -> CheckResult:
        gt_map = self._point_map(self.points)
        pred_map = self._point_map(pred_points)
        shared_keys = sorted(set(gt_map) & set(pred_map))
        missing_keys = sorted(set(gt_map) - set(pred_map))

        errors: list[float] = []
        region_correct_keys: list[tuple[int, int]] = []
        for key in shared_keys:
            error = self._point_error_px(gt_map[key], pred_map[key])
            if error is not None:
                errors.append(error)
            if self._inside_gt_ellipse(key, gt_map, pred_map[key]):
                region_correct_keys.append(key)

        # 영역 밖 점과 누락 점은 모두 영역 판정 실패입니다.
        region_incorrect_keys = sorted(set(gt_map) - set(region_correct_keys))
        return CheckResult(
            gt_points=len(gt_map),
            pred_points=len(pred_map),
            matched_points=len(shared_keys),
            mean_error_px=float(np.mean(errors)) if errors else 0.0,
            max_error_px=float(np.max(errors)) if errors else 0.0,
            missing_keys=missing_keys,
            region_correct_points=len(region_correct_keys),
            region_accuracy=len(region_correct_keys) / len(gt_map) if gt_map else 0.0,
            region_correct_keys=region_correct_keys,
            region_incorrect_keys=region_incorrect_keys,
        )

    def answer2json(self) -> dict[str, object]:
        return GuidePoint.list_to_payload(self.points)

    def L2_check(self, pred_points: list[GuidePoint]) -> CheckResult:
        """예측 점을 계산만 하고 출력이나 저장은 하지 않습니다."""
        return self._check_points(pred_points)

    def save_to_json(self, path: str | Path) -> None:
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(self.answer2json(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


@dataclass(frozen=True)
class FrameRecord:
    """결과 앱이 필요한 프레임 번호와 계산 결과만 보관합니다."""

    frame_index: int
    result: CheckResult


class ValidLoader:
    """정렬된 이미지와 대응 정답을 한 프레임씩 제공합니다."""

    def __init__(self, dataset_dir: str | Path = DEFAULT_DATASET) -> None:
        self.dataset_dir = Path(dataset_dir)
        self.images_dir = self.dataset_dir / "images"
        self.labels_dir = self.dataset_dir / "labels_json"
        self._valid_paths_cache: list[Path] | None = None
        if not self.images_dir.exists():
            raise FileNotFoundError(f"Images directory not found: {self.images_dir}")
        if not self.labels_dir.exists():
            raise FileNotFoundError(f"JSON labels directory not found: {self.labels_dir}")
        print(f"ValidLoader initialized: {self.dataset_dir}", flush=True)

    def _iter_image_paths(self) -> Iterator[Path]:
        for image_path in sorted(path for path in self.images_dir.iterdir() if path.is_file()):
            yield image_path

    def _valid_image_paths(self) -> list[Path]:
        # 이미지 자체는 캐시하지 않고, 정렬된 경로 목록만 한 번 계산합니다.
        if self._valid_paths_cache is None:
            self._valid_paths_cache = [
                image_path
                for image_path in self._iter_image_paths()
                if (self.labels_dir / f"{image_path.stem}.json").exists()
            ]
        return self._valid_paths_cache

    def __iter__(self) -> Iterator[tuple[np.ndarray, AnswerPoints]]:
        for image_path in self._valid_image_paths():
            yield self._load_sample(image_path)

    def __len__(self) -> int:
        return len(self._valid_image_paths())

    def __getitem__(self, frame_index: int) -> tuple[np.ndarray, AnswerPoints]:
        return self._load_sample(self._valid_image_paths()[frame_index])

    def _load_sample(self, image_path: Path) -> tuple[np.ndarray, AnswerPoints]:
        answer_path = self.labels_dir / f"{image_path.stem}.json"
        image_bgr = cv2.imread(str(image_path))
        if image_bgr is None:
            raise FileNotFoundError(f"Could not read image: {image_path}")
        return image_bgr, AnswerPoints.from_json(answer_path)


class ValidResultManager:
    """프레임별 검증 결과의 저장, 재로딩, 요약을 담당합니다."""

    def __init__(self, result_dir: str | Path = DEFAULT_RESULT_DIR) -> None:
        self.result_root = Path(result_dir)
        run_name = datetime.now().strftime("run_%Y%m%d_%H%M%S")
        self.result_dir = self.result_root / run_name
        self.rows: list[dict[str, object]] = []
        self.frames: list[FrameRecord] = []
        self.errors: list[float] = []
        print(f"ValidResultManager initialized: {self.result_dir}", flush=True)

    @staticmethod
    def _check_result_from_payload(payload: dict[str, object]) -> CheckResult:
        """신규 결과와 일부 지표가 없는 과거 결과를 같은 형식으로 읽습니다."""
        return CheckResult(
            gt_points=int(payload.get("gt_points", 0)),
            pred_points=int(payload.get("pred_points", 0)),
            matched_points=int(payload.get("matched_points", 0)),
            mean_error_px=float(payload.get("mean_error_px", 0.0) or 0.0),
            max_error_px=float(payload.get("max_error_px", 0.0) or 0.0),
            missing_keys=[
                (int(key[0]), int(key[1]))
                for key in payload.get("missing_keys", [])
                if isinstance(key, (list, tuple)) and len(key) == 2
            ],
            region_correct_points=int(payload.get("region_correct_points", 0)),
            region_accuracy=float(payload.get("region_accuracy", 0.0) or 0.0),
            region_correct_keys=[
                (int(key[0]), int(key[1]))
                for key in payload.get("region_correct_keys", [])
                if isinstance(key, (list, tuple)) and len(key) == 2
            ],
            region_incorrect_keys=[
                (int(key[0]), int(key[1]))
                for key in payload.get("region_incorrect_keys", [])
                if isinstance(key, (list, tuple)) and len(key) == 2
            ],
        )

    @classmethod
    def from_existing(cls, result_dir: str | Path) -> "ValidResultManager":
        manager = cls.__new__(cls)
        manager.result_root = Path(result_dir).parent
        manager.result_dir = Path(result_dir)
        manager.rows = []
        manager.frames = []
        manager.errors = []

        if not manager.result_dir.exists():
            raise FileNotFoundError(f"Result directory not found: {manager.result_dir}")

        for result_path in sorted(manager.result_dir.glob("*_result.json")):
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            result = cls._check_result_from_payload(payload)
            frame_index = int(payload.get("frame_index", 0))
            manager.rows.append(manager._row(frame_index, result))
            manager.frames.append(FrameRecord(frame_index=frame_index, result=result))
            if result.matched_points > 0:
                manager.errors.append(result.mean_error_px)

        print(f"ValidResultManager loaded: {manager.result_dir}", flush=True)
        return manager

    @staticmethod
    def latest_result_dir(result_root: str | Path = DEFAULT_RESULT_DIR) -> Path:
        root = Path(result_root)
        if not root.exists():
            raise FileNotFoundError(f"Result root not found: {root}")
        run_dirs = [path for path in root.iterdir() if path.is_dir()]
        if not run_dirs:
            raise FileNotFoundError(f"No result runs found in: {root}")
        return max(run_dirs, key=lambda path: path.stat().st_mtime)

    @staticmethod
    def _row(frame_index: int, result: CheckResult) -> dict[str, object]:
        return {
            "frame_index": frame_index,
            "mean_error_px": result.mean_error_px,
            "matched_points": result.matched_points,
            "gt_points": result.gt_points,
            "pred_points": result.pred_points,
            "region_correct_points": result.region_correct_points,
            "region_accuracy": result.region_accuracy,
            "region_correct_keys": result.region_correct_keys,
            "region_incorrect_keys": result.region_incorrect_keys,
            "missing_keys": result.missing_keys,
        }

    def add_frame_result(
        self,
        frame_index: int,
        out: list[GuidePoint],
        result: CheckResult,
    ) -> None:
        """지표와 예측을 프레임당 JSON 하나에 함께 저장합니다."""
        self.result_dir.mkdir(parents=True, exist_ok=True)
        result_payload = {
            "frame_index": frame_index,
            "gt_points": result.gt_points,
            "pred_points": result.pred_points,
            "matched_points": result.matched_points,
            "mean_error_px": result.mean_error_px,
            "max_error_px": result.max_error_px,
            "missing_keys": result.missing_keys,
            "region_correct_points": result.region_correct_points,
            "region_accuracy": result.region_accuracy,
            "region_correct_keys": result.region_correct_keys,
            "region_incorrect_keys": result.region_incorrect_keys,
            "pred_output": GuidePoint.list_to_payload(list(out)),
        }
        result_path = self.result_dir / f"{frame_index:04d}_result.json"
        result_path.write_text(
            json.dumps(result_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        self.rows.append(self._row(frame_index, result))
        self.frames.append(FrameRecord(frame_index=frame_index, result=result))
        if result.matched_points > 0:
            self.errors.append(result.mean_error_px)

    def load_pred_points(self, frame_index: int) -> list[GuidePoint]:
        """한 프레임의 예측을 읽으며 과거 ``*_pred.json``도 지원합니다."""
        result_path = self.result_dir / f"{frame_index:04d}_result.json"
        if result_path.exists():
            result_payload = json.loads(result_path.read_text(encoding="utf-8"))
            pred_output = result_payload.get("pred_output")
            if isinstance(pred_output, dict):
                return GuidePoint.list_from_payload(pred_output)

        legacy_path = self.result_dir / f"{frame_index:04d}_pred.json"
        if not legacy_path.exists():
            return []
        payload = json.loads(legacy_path.read_text(encoding="utf-8"))
        return GuidePoint.list_from_payload(payload)

    def build_summary(self) -> dict[str, float | int]:
        frame_count = len(self.rows)
        matched_frames = sum(1 for row in self.rows if int(row["matched_points"]) > 0)
        count_errors = [abs(int(row["gt_points"]) - int(row["pred_points"])) for row in self.rows]
        total_gt_points = sum(int(row["gt_points"]) for row in self.rows)
        total_region_correct = sum(int(row.get("region_correct_points", 0)) for row in self.rows)
        return {
            "frame_count": frame_count,
            "mean_error_px": round(float(np.mean(self.errors)) if self.errors else 0.0, 6),
            "p95_error_px": round(float(np.percentile(self.errors, 95)) if self.errors else 0.0, 6),
            "guide_count_mae": round(float(np.mean(count_errors)) if count_errors else 0.0, 6),
            "matched_rate": round(matched_frames / frame_count, 6) if frame_count else 0.0,
            "region_correct_points": total_region_correct,
            "region_accuracy": round(total_region_correct / total_gt_points, 6) if total_gt_points else 0.0,
        }

    def save_summary(self) -> dict[str, float | int]:
        self.result_dir.mkdir(parents=True, exist_ok=True)
        summary = self.build_summary()
        (self.result_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return summary

    def load_summary(self) -> dict[str, float | int]:
        summary_path = self.result_dir / "summary.json"
        if not summary_path.exists():
            return self.build_summary()

        payload = json.loads(summary_path.read_text(encoding="utf-8"))
        return {
            "frame_count": int(payload.get("frame_count", 0)),
            "mean_error_px": float(payload.get("mean_error_px", 0.0) or 0.0),
            "p95_error_px": float(payload.get("p95_error_px", 0.0) or 0.0),
            "guide_count_mae": float(payload.get("guide_count_mae", 0.0) or 0.0),
            "matched_rate": float(payload.get("matched_rate", 0.0) or 0.0),
            "region_correct_points": int(payload.get("region_correct_points", 0)),
            "region_accuracy": float(payload.get("region_accuracy", 0.0) or 0.0),
        }
