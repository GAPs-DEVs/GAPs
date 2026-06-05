from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QLabel,
    QMainWindow,
    QSlider,
    QVBoxLayout,
    QWidget,
)

VALID_ROOT = Path(__file__).resolve().parent
DEFAULT_DATASET = VALID_ROOT / "ds"
DEFAULT_RESULT_DIR = VALID_ROOT / "result"

__all__ = [
    "GuidePoint",
    "AnswerPoints",
    "ValidLoader",
    "ValidResultManager",
    "ValidationApp",
]


DEFAULT_PRINT_EVERY = 100
GT_POINT_COLOR = (80, 220, 80)
PRED_POINT_COLOR = (0, 210, 255)

# 검증 영상의 시나리오 구간 정의입니다.
# end는 포함 범위입니다.
SEGMENTS: list[tuple[int, int | None, str, str]] = [
    (0, 160, "horizon", "지판이 수평에 가까운 상태"),
    (161, 290, "diagonal", "지판이 대각선 방향으로 놓인 상태"),
    (291, 470, "hidden 1 all fret", "n개의 프렛이 완전히 가려진 상태"),
    (471, 590, "hidden 2 all fret", "n개의 프렛이 완전히 가려진 상태"),
    (591, 720, "hidden 3 all fret", "n개의 프렛이 완전히 가려진 상태"),
    (721, 840, "hidden 1 half fret", "n개의 프렛이 절반 정도 가려진 상태"),
    (841, 970, "hidden 2 half fret", "n개의 프렛이 절반 정도 가려진 상태"),
    (971, 1110, "hidden 3 half fret", "n개의 프렛이 절반 정도 가려진 상태"),
    (1111, None, "top", "상단 일부가 가려진 상태"),
]


@dataclass(frozen=True)
class GuidePoint:
    # 검증의 최소 단위입니다.
    # string/fret 의미를 가진 한 점을 표현합니다.
    string: int
    fret: int
    x: float | None
    y: float | None
    visible: bool

    @classmethod
    def list_from_payload(cls, payload: dict[str, object]) -> list["GuidePoint"]:
        # JSON payload를 GuidePoint 목록으로 바꿉니다.
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
        # GuidePoint 목록을 JSON 저장용 구조로 바꿉니다.
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
class _CheckResult:
    # 비교 결과 구조입니다.
    gt_points: int
    pred_points: int
    matched_points: int
    mean_error_px: float
    max_error_px: float
    missing_keys: list[tuple[int, int]]


@dataclass(frozen=True)
class AnswerPoints:
    # 정답 점 묶음입니다.
    # 검증 책임을 이 객체가 가집니다.
    points: list[GuidePoint]
    frame_index: int = 0
    total_frames: int = 0

    def __iter__(self):
        return iter(self.points)

    def __len__(self) -> int:
        return len(self.points)

    @classmethod
    def from_json(
        cls,
        answer_json_path: str | Path,
        frame_index: int = 0,
        total_frames: int = 0,
    ) -> "AnswerPoints":
        payload = json.loads(Path(answer_json_path).read_text(encoding="utf-8"))
        return cls(
            points=GuidePoint.list_from_payload(payload),
            frame_index=frame_index,
            total_frames=total_frames,
        )

    def _point_map(self, points: list[GuidePoint]) -> dict[tuple[int, int], GuidePoint]:
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

    def _check_points(self, pred_points: list[GuidePoint]) -> _CheckResult:
        # 비교 규칙은 단순합니다.
        # - GT와 Pred를 (string, fret) 키로 맵핑합니다.
        # - 같은 키가 양쪽에 다 있을 때만 거리 계산을 합니다.
        # - 즉 (1,1)은 (1,1)과만, (1,2)는 (1,2)와만 비교합니다.
        gt_map = self._point_map(self.points)
        pred_map = self._point_map(pred_points)
        shared_keys = sorted(set(gt_map.keys()) & set(pred_map.keys()))
        missing_keys = sorted(set(gt_map.keys()) - set(pred_map.keys()))

        errors: list[float] = []
        for key in shared_keys:
            error = self._point_error_px(gt_map[key], pred_map[key])
            if error is not None:
                errors.append(error)

        return _CheckResult(
            gt_points=len(gt_map),
            pred_points=len(pred_map),
            matched_points=len(shared_keys),
            mean_error_px=float(np.mean(errors)) if errors else 0.0,
            max_error_px=float(np.max(errors)) if errors else 0.0,
            missing_keys=missing_keys,
        )

    def answer2json(self) -> dict[str, object]:
        return GuidePoint.list_to_payload(self.points)

    def L2_check(
        self,
        pred_points: list[GuidePoint],
    ) -> _CheckResult:
        result = self._check_points(pred_points)
        should_print = self.frame_index == 0
        if self.total_frames > 0 and self.frame_index == self.total_frames - 1:
            should_print = True
        if DEFAULT_PRINT_EVERY > 0 and (self.frame_index + 1) % DEFAULT_PRINT_EVERY == 0:
            should_print = True
        if should_print:
            frame_label = f"frame={self.frame_index}"
            if self.total_frames > 0:
                frame_label = f"frame={self.frame_index}/{self.total_frames}"
            print(
                f"{frame_label} "
                f"gt={result.gt_points} "
                f"pred={result.pred_points} "
                f"matched={result.matched_points} "
                f"mean_error_px={result.mean_error_px:.4f} "
                f"max_error_px={result.max_error_px:.4f} "
                f"missing={len(result.missing_keys)}",
                flush=True,
            )
        return result

    def save_to_json(self, path: str | Path) -> None:
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(self.answer2json(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


@dataclass(frozen=True)
class _FrameRecord:
    frame_index: int
    result: _CheckResult


@dataclass(frozen=True)
class _FrameCacheData:
    # 앱이 빠르게 다시 그리기 위해 들고 있는 캐시입니다.
    # 검증 결과 자체가 아니라, 뷰어 표시용 재조립 결과입니다.
    image_bgr: np.ndarray
    gt_points: list[GuidePoint]
    pred_points: list[GuidePoint]


class ValidLoader:
    # 데이터 준비 책임만 맡습니다.
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
        # 프레임 경로 목록은 한 번만 계산해서 재사용합니다.
        # 여기서는 "이미지 파일 존재 + 대응 answer json 존재"까지만 확인합니다.
        # 실제 이미지 디코드는 __iter__/__getitem__에서 한 번만 수행합니다.
        if self._valid_paths_cache is not None:
            return self._valid_paths_cache

        valid_paths: list[Path] = []
        for image_path in self._iter_image_paths():
            answer_path = self.labels_dir / f"{image_path.stem}.json"
            if answer_path.exists():
                valid_paths.append(image_path)
        self._valid_paths_cache = valid_paths
        return valid_paths

    def __iter__(self) -> Iterator[tuple[np.ndarray, AnswerPoints]]:
        valid_paths = self._valid_image_paths()
        total_frames = len(valid_paths)
        for frame_index, image_path in enumerate(valid_paths):
            yield self._load_sample(image_path, frame_index, total_frames)

    def __len__(self) -> int:
        return len(self._valid_image_paths())

    def __getitem__(self, frame_index: int) -> tuple[np.ndarray, AnswerPoints]:
        valid_paths = self._valid_image_paths()
        total_frames = len(valid_paths)
        image_path = valid_paths[frame_index]
        return self._load_sample(image_path, frame_index, total_frames)

    def _load_sample(
        self,
        image_path: Path,
        frame_index: int,
        total_frames: int,
    ) -> tuple[np.ndarray, AnswerPoints]:
        answer_path = self.labels_dir / f"{image_path.stem}.json"
        image_bgr = cv2.imread(str(image_path))
        if image_bgr is None:
            raise FileNotFoundError(f"Could not read image: {image_path}")
        return image_bgr, AnswerPoints.from_json(
            answer_path,
            frame_index=frame_index,
            total_frames=total_frames,
        )


class ValidResultManager:
    # 검증 루프에서 나온 프레임별 결과를 관리합니다.
    # 책임:
    # - 프레임별 result json 저장
    # - 프레임별 answer json 저장
    # - 앱이 볼 누적 결과 보관
    # - summary json 저장
    def __init__(self, result_dir: str | Path = DEFAULT_RESULT_DIR) -> None:
        self.result_root = Path(result_dir)
        run_name = datetime.now().strftime("run_%Y%m%d_%H%M%S")
        self.result_dir = self.result_root / run_name
        self.rows: list[dict[str, object]] = []
        self.frames: list[_FrameRecord] = []
        self.errors: list[float] = []
        print(f"ValidResultManager initialized: {self.result_dir}", flush=True)

    @classmethod
    def from_existing(cls, result_dir: str | Path) -> "ValidResultManager":
        # 이미 저장된 검증 결과 폴더를 다시 열 때 사용합니다.
        manager = cls.__new__(cls)
        manager.result_root = Path(result_dir).parent
        manager.result_dir = Path(result_dir)
        manager.rows = []
        manager.frames = []
        manager.errors = []

        if not manager.result_dir.exists():
            raise FileNotFoundError(f"Result directory not found: {manager.result_dir}")

        result_files = sorted(manager.result_dir.glob("*_result.json"))
        for result_path in result_files:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            result = _CheckResult(
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
            )
            frame_index = int(payload.get("frame_index", 0))
            manager.rows.append(
                {
                    "frame_index": frame_index,
                    "mean_error_px": result.mean_error_px,
                    "matched_points": result.matched_points,
                    "gt_points": result.gt_points,
                    "pred_points": result.pred_points,
                }
            )
            manager.frames.append(_FrameRecord(frame_index=frame_index, result=result))
            if result.matched_points > 0:
                manager.errors.append(result.mean_error_px)

        print(f"ValidResultManager loaded: {manager.result_dir}", flush=True)
        return manager

    @staticmethod
    def latest_result_dir(result_root: str | Path = DEFAULT_RESULT_DIR) -> Path:
        # result 아래에서 가장 최근 실행 폴더를 찾습니다.
        root = Path(result_root)
        if not root.exists():
            raise FileNotFoundError(f"Result root not found: {root}")

        run_dirs = [path for path in root.iterdir() if path.is_dir()]
        if not run_dirs:
            raise FileNotFoundError(f"No result runs found in: {root}")
        return max(run_dirs, key=lambda path: path.stat().st_mtime)

    def add_frame_result(
        self,
        frame_index: int,
        out: list[GuidePoint],
        result: _CheckResult,
    ) -> None:
        self.result_dir.mkdir(parents=True, exist_ok=True)

        result_path = self.result_dir / f"{frame_index:04d}_result.json"
        result_payload = {
            "frame_index": frame_index,
            "gt_points": result.gt_points,
            "pred_points": result.pred_points,
            "matched_points": result.matched_points,
            "mean_error_px": result.mean_error_px,
            "max_error_px": result.max_error_px,
            "missing_keys": result.missing_keys,
            "pred_output": GuidePoint.list_to_payload(list(out)),
        }
        result_path.write_text(
            json.dumps(result_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        pred_path = self.result_dir / f"{frame_index:04d}_pred.json"
        pred_path.write_text(
            json.dumps(GuidePoint.list_to_payload(list(out)), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        self.rows.append(
            {
                "frame_index": frame_index,
                "mean_error_px": result.mean_error_px,
                "matched_points": result.matched_points,
                "gt_points": result.gt_points,
                "pred_points": result.pred_points,
            }
        )
        self.frames.append(
            _FrameRecord(
                frame_index=frame_index,
                result=result,
            )
        )
        if result.matched_points > 0:
            self.errors.append(result.mean_error_px)

    def build_summary(self) -> dict[str, float | int]:
        frame_count = len(self.rows)
        matched_frames = sum(1 for row in self.rows if int(row["matched_points"]) > 0)
        count_errors = [abs(int(row["gt_points"]) - int(row["pred_points"])) for row in self.rows]
        return {
            "frame_count": frame_count,
            "mean_error_px": round(float(np.mean(self.errors)) if self.errors else 0.0, 6),
            "p95_error_px": round(float(np.percentile(self.errors, 95)) if self.errors else 0.0, 6),
            "guide_count_mae": round(float(np.mean(count_errors)) if count_errors else 0.0, 6),
            "matched_rate": round(matched_frames / frame_count, 6) if frame_count else 0.0,
        }

    def save_summary(self) -> dict[str, float | int]:
        self.result_dir.mkdir(parents=True, exist_ok=True)
        summary = self.build_summary()
        summary_path = self.result_dir / "summary.json"
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return summary

    def load_summary(self) -> dict[str, float | int]:
        # 이미 저장된 summary가 있으면 그 값을 읽고,
        # 없으면 메모리상 계산값만 돌려줍니다.
        summary_path = self.result_dir / "summary.json"
        if summary_path.exists():
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
            return {
                "frame_count": int(payload.get("frame_count", 0)),
                "mean_error_px": float(payload.get("mean_error_px", 0.0) or 0.0),
                "p95_error_px": float(payload.get("p95_error_px", 0.0) or 0.0),
                "guide_count_mae": float(payload.get("guide_count_mae", 0.0) or 0.0),
                "matched_rate": float(payload.get("matched_rate", 0.0) or 0.0),
            }
        return self.build_summary()


class _FrameViewerWindow(QMainWindow):
    class _ZoomableGraphicsView(QGraphicsView):
        # 이미지/리포트 둘 다 재사용하는 확대/축소/이동 뷰입니다.
        # 동작:
        # - 마우스 휠: 확대/축소
        # - 마우스 드래그: 화면 이동
        # - 더블클릭: 화면에 다시 맞춤
        def __init__(self, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self._scene = QGraphicsScene(self)
            self._pixmap_item = QGraphicsPixmapItem()
            self._scene.addItem(self._pixmap_item)
            self.setScene(self._scene)

            self._has_image = False
            self._zoom_steps = 0

            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
            self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
            self.setRenderHints(self.renderHints())
            self.setBackgroundBrush(Qt.GlobalColor.black)

        def set_image(self, image: QImage) -> None:
            pixmap = QPixmap.fromImage(image)
            self._pixmap_item.setPixmap(pixmap)
            self._scene.setSceneRect(pixmap.rect())
            if not self._has_image:
                self._fit_to_view()
                self._has_image = True

        def _fit_to_view(self) -> None:
            if self._pixmap_item.pixmap().isNull():
                return
            self.resetTransform()
            self.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
            self._zoom_steps = 0

        def wheelEvent(self, event) -> None:
            if self._pixmap_item.pixmap().isNull():
                return
            if event.angleDelta().y() > 0:
                factor = 1.15
                self._zoom_steps += 1
            else:
                factor = 1 / 1.15
                self._zoom_steps -= 1

            if self._zoom_steps <= 0:
                self._fit_to_view()
                return

            self.scale(factor, factor)

        def mouseDoubleClickEvent(self, event) -> None:
            self._fit_to_view()
            super().mouseDoubleClickEvent(event)

        def resizeEvent(self, event) -> None:
            super().resizeEvent(event)
            if self._has_image and self._zoom_steps <= 0:
                self._fit_to_view()

    def __init__(
        self,
        frames: list[_FrameRecord],
        frame_cache: dict[int, _FrameCacheData],
    ) -> None:
        super().__init__()
        self.frames = frames
        self.frame_cache = frame_cache
        self.setWindowTitle("Validation Frames")
        self.resize(1080, 820)

        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        self.frame_view = self._ZoomableGraphicsView(self)
        self.frame_view.setMinimumSize(960, 540)
        layout.addWidget(self.frame_view, stretch=1)

        self.status_label = QLabel("Ready", self)
        layout.addWidget(self.status_label)

        self.help_label = QLabel("Wheel: zoom / Drag: move / Double click: fit", self)
        layout.addWidget(self.help_label)

        self.slider = QSlider(Qt.Orientation.Horizontal, self)
        self.slider.setRange(0, max(len(frames) - 1, 0))
        self.slider.valueChanged.connect(self._render_frame)
        layout.addWidget(self.slider)

        if self.frames:
            self._render_frame(0)

    def _render_frame(self, index: int) -> None:
        frame = self.frames[index]
        cached = self.frame_cache[frame.frame_index]
        segment_name = ValidationApp.segment_name(frame.frame_index)
        header = (
            f"frame={frame.frame_index} "
            f"[{segment_name}] "
            f"matched={frame.result.matched_points} "
            f"mean_error_px={frame.result.mean_error_px:.4f}"
        )
        overlay = ValidationApp.draw_points_overlay(
            cached.image_bgr,
            cached.gt_points,
            cached.pred_points,
            header,
        )
        self.frame_view.set_image(ValidationApp.bgr_to_qimage(overlay))
        self.status_label.setText(
            f"frame={frame.frame_index} [{segment_name}] / "
            f"gt={frame.result.gt_points} pred={frame.result.pred_points} "
            f"matched={frame.result.matched_points} "
            f"missing={len(frame.result.missing_keys)} "
            f"max_error_px={frame.result.max_error_px:.4f}"
        )


class _ReportWindow(QMainWindow):
    def __init__(self, report_image: np.ndarray) -> None:
        super().__init__()
        self.setWindowTitle("Validation Report")
        self.resize(900, 920)

        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        self.report_view = _FrameViewerWindow._ZoomableGraphicsView(self)
        layout.addWidget(self.report_view, stretch=1)

        self.help_label = QLabel("Wheel: zoom / Drag: move / Double click: fit", self)
        layout.addWidget(self.help_label)

        self.report_view.set_image(ValidationApp.bgr_to_qimage(report_image))


class ValidationApp:
    # 저장된 결과를 보여주는 책임만 맡습니다.
    def __init__(self, loader: ValidLoader, manager: ValidResultManager) -> None:
        self.loader = loader
        self.manager = manager
        self.result_dir = manager.result_dir
        self.frame_cache: dict[int, _FrameCacheData] = {}
        print("ValidationApp initialized.", flush=True)

    def _load_pred_points(self, frame_index: int) -> list[GuidePoint]:
        pred_path = self.result_dir / f"{frame_index:04d}_pred.json"
        if not pred_path.exists():
            return []
        payload = json.loads(pred_path.read_text(encoding="utf-8"))
        return GuidePoint.list_from_payload(payload)

    @staticmethod
    def segment_name(frame_index: int) -> str:
        # 현재 프레임이 어떤 검증 구간에 속하는지 이름만 돌려줍니다.
        for start, end, name, _description in SEGMENTS:
            if frame_index < start:
                continue
            if end is None or frame_index <= end:
                return name
        return "unknown"

    @staticmethod
    def _draw_segment_bands(
        canvas: np.ndarray,
        rows: list[dict[str, object]],
        graph_left: int,
        graph_right: int,
        top: int,
        plot_height: int,
    ) -> None:
        # 그래프 배경에 구간별 색띠와 이름을 같이 그립니다.
        if not rows:
            return

        frame_indices = [int(row["frame_index"]) for row in rows]
        frame_min = min(frame_indices)
        frame_max = max(frame_indices)
        frame_span = max(frame_max - frame_min, 1)
        graph_width = graph_right - graph_left
        band_colors = [
            (70, 58, 42),
            (56, 74, 56),
        ]

        for band_index, (start, end, name, _description) in enumerate(SEGMENTS):
            band_end = frame_max if end is None else min(end, frame_max)
            band_start = max(start, frame_min)
            if band_end < frame_min or band_start > frame_max:
                continue

            ratio_x0 = (band_start - frame_min) / frame_span
            ratio_x1 = (band_end - frame_min) / frame_span
            x0 = int(round(graph_left + ratio_x0 * graph_width))
            x1 = int(round(graph_left + ratio_x1 * graph_width))
            if x1 <= x0:
                x1 = x0 + 1

            overlay = canvas.copy()
            cv2.rectangle(
                overlay,
                (x0, top),
                (x1, top + plot_height),
                band_colors[band_index % len(band_colors)],
                -1,
            )
            cv2.addWeighted(overlay, 0.32, canvas, 0.68, 0.0, dst=canvas)

            label_x = max(graph_left + 4, min(x0 + 6, graph_right - 140))
            label_y = top + plot_height - 8
            cv2.putText(
                canvas,
                name,
                (label_x, label_y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.38,
                (220, 220, 220),
                1,
                cv2.LINE_AA,
            )

    def _build_frame_cache(self) -> None:
        # 결과 확인 앱은 검증 이후에만 뜨므로,
        # 이 시점에 한 번 전체 프레임을 캐시해두면 슬라이더가 훨씬 빨라집니다.
        if self.frame_cache:
            return
        print("Caching validation frames...", flush=True)
        total = len(self.manager.frames)
        for index, record in enumerate(self.manager.frames):
            input_image, answer_points = self.loader[record.frame_index]
            pred_points = self._load_pred_points(record.frame_index)
            self.frame_cache[record.frame_index] = _FrameCacheData(
                image_bgr=input_image,
                gt_points=answer_points.points,
                pred_points=pred_points,
            )
            should_print = index == 0 or index == total - 1
            if (index + 1) % 100 == 0:
                should_print = True
            if should_print:
                print(
                    f"Caching validation frames... {index + 1}/{total}",
                    flush=True,
                )
        print("Validation frame cache ready.", flush=True)

    @staticmethod
    def draw_points_overlay(
        image_bgr: np.ndarray,
        gt_points: list[GuidePoint],
        pred_points: list[GuidePoint],
        header_text: str,
    ) -> np.ndarray:
        canvas = image_bgr.copy()

        def draw(points: list[GuidePoint], color: tuple[int, int, int], prefix: str) -> None:
            for point in points:
                if not point.visible or point.x is None or point.y is None:
                    continue
                x = int(round(point.x))
                y = int(round(point.y))
                cv2.circle(canvas, (x, y), 5, (0, 0, 0), -1, cv2.LINE_AA)
                cv2.circle(canvas, (x, y), 4, color, -1, cv2.LINE_AA)
                cv2.putText(
                    canvas,
                    f"{prefix}{point.string}-{point.fret}",
                    (x + 6, y - 6),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.35,
                    color,
                    1,
                    cv2.LINE_AA,
                )

        draw(gt_points, GT_POINT_COLOR, "G")
        draw(pred_points, PRED_POINT_COLOR, "P")
        cv2.putText(canvas, header_text, (18, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(canvas, header_text, (18, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (30, 30, 30), 1, cv2.LINE_AA)
        return canvas

    @staticmethod
    def bgr_to_qimage(frame_bgr: np.ndarray) -> QImage:
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        height, width, channels = frame_rgb.shape
        bytes_per_line = channels * width
        return QImage(
            frame_rgb.data,
            width,
            height,
            bytes_per_line,
            QImage.Format.Format_RGB888,
        ).copy()

    @staticmethod
    def build_report_image(rows: list[dict[str, object]], summary: dict[str, object]) -> np.ndarray:
        width = 1400
        height = 900
        canvas = np.full((height, width, 3), 245, dtype=np.uint8)

        def draw_text(text: str, x: int, y: int, scale: float = 0.65, color: tuple[int, int, int] = (30, 30, 30)) -> None:
            cv2.putText(canvas, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 2, cv2.LINE_AA)

        draw_text("Validation Report", 40, 50, 1.0)
        draw_text(f"frames={summary['frame_count']}", 40, 95)
        draw_text(f"mean_error_px={summary['mean_error_px']}", 40, 130)
        draw_text(f"p95_error_px={summary['p95_error_px']}", 40, 165)
        draw_text(f"guide_count_mae={summary['guide_count_mae']}", 40, 200)
        draw_text(f"matched_rate={summary['matched_rate']}", 40, 235)

        graph_left = 60
        graph_right = width - 60
        graph_width = graph_right - graph_left

        def draw_series(title: str, values: list[float], top: int, plot_height: int, color: tuple[int, int, int], y_max: float) -> None:
            draw_text(title, graph_left, top - 20, 0.7)
            ValidationApp._draw_segment_bands(canvas, rows, graph_left, graph_right, top, plot_height)
            cv2.rectangle(canvas, (graph_left, top), (graph_right, top + plot_height), (180, 180, 180), 1, cv2.LINE_AA)
            if not values:
                return
            clip_max = max(y_max, 1e-6)
            poly: list[tuple[int, int]] = []
            count = len(values)
            for index, value in enumerate(values):
                ratio_x = index / max(count - 1, 1)
                ratio_y = min(max(value / clip_max, 0.0), 1.0)
                x = int(round(graph_left + ratio_x * graph_width))
                y = int(round(top + plot_height - ratio_y * plot_height))
                poly.append((x, y))
            if len(poly) >= 2:
                cv2.polylines(canvas, [np.asarray(poly, dtype=np.int32)], False, color, 2, cv2.LINE_AA)
            draw_text(f"max={clip_max:.2f}", graph_right - 170, top - 20, 0.55, color)

        mean_errors = [float(row["mean_error_px"]) for row in rows]
        count_errors = [abs(float(row["gt_points"]) - float(row["pred_points"])) for row in rows]
        draw_series("Mean Error (px)", mean_errors, 300, 220, (0, 120, 220), 100.0)
        draw_series("Guide Count Error", count_errors, 590, 180, (40, 160, 80), max(max(count_errors, default=0.0), 1.0))
        return canvas

    def show(self) -> dict[str, float | int]:
        summary = self.manager.load_summary()
        report_image = self.build_report_image(self.manager.rows, summary)
        self._build_frame_cache()

        app = QApplication.instance()
        owns_app = app is None
        if app is None:
            app = QApplication(sys.argv)

        print("Creating frame slider window...", flush=True)
        frame_window = _FrameViewerWindow(
            self.manager.frames,
            self.frame_cache,
        )
        frame_window.move(80, 80)
        frame_window.show()
        frame_window.raise_()
        frame_window.activateWindow()
        print("Frame slider window created.", flush=True)

        print("Creating report window...", flush=True)
        report_window = _ReportWindow(report_image)
        report_window.move(220, 120)
        report_window.show()
        report_window.raise_()
        report_window.activateWindow()
        print("Report window created.", flush=True)

        if owns_app:
            app.exec()
        print("Validation windows closed.", flush=True)
        return summary
