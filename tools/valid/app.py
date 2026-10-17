"""저장된 검증 결과를 프레임 창과 리포트 창으로 조회합니다."""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.valid.module import (
    AnswerPoints,
    DEFAULT_DATASET,
    FrameRecord,
    GuidePoint,
    ValidResultManager,
)

GT_POINT_COLOR = (80, 220, 80)
PRED_POINT_COLOR = (0, 210, 255)

# end는 포함 범위입니다. 시나리오는 계산이 아니라 리포트 표현에만 사용합니다.
SEGMENTS: list[tuple[int, int | None, str]] = [
    (0, 160, "horizon"),
    (161, 290, "diagonal"),
    (291, 470, "hidden 1 all fret"),
    (471, 590, "hidden 2 all fret"),
    (591, 720, "hidden 3 all fret"),
    (721, 840, "hidden 1 half fret"),
    (841, 970, "hidden 2 half fret"),
    (971, 1110, "hidden 3 half fret"),
    (1111, None, "top"),
]


class ZoomableGraphicsView(QGraphicsView):
    """휠 확대, 드래그 이동, 더블클릭 맞춤을 지원하는 이미지 화면입니다."""

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
        self.setBackgroundBrush(Qt.GlobalColor.black)

    def set_image(self, image: QImage) -> None:
        pixmap = QPixmap.fromImage(image)
        self._pixmap_item.setPixmap(pixmap)
        self._scene.setSceneRect(self._pixmap_item.boundingRect())
        if not self._has_image:
            self._has_image = True
            self._fit_to_view()

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
            self._zoom_steps += 1
            self.scale(1.15, 1.15)
            return
        self._zoom_steps -= 1
        if self._zoom_steps <= 0:
            self._fit_to_view()
        else:
            self.scale(1 / 1.15, 1 / 1.15)

    def mouseDoubleClickEvent(self, event) -> None:
        self._fit_to_view()
        super().mouseDoubleClickEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # 직접 확대하지 않은 상태에서는 전체화면 전환 후 새 크기에 다시 맞춥니다.
        if self._has_image and self._zoom_steps == 0:
            self._fit_to_view()


class FrameWindow(QMainWindow):
    """선택한 실행 결과를 프레임 단위로 탐색하는 창입니다."""

    def __init__(self, controller: "ValidationApp") -> None:
        super().__init__()
        self.controller = controller
        self.frames: list[FrameRecord] = []
        self._result_paths: list[Path] = []
        self._building_combo = False
        self.setWindowTitle("Validation Frames")
        self.resize(1080, 820)

        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        result_row = QHBoxLayout()
        result_row.addWidget(QLabel("Result", self))
        self.result_combo = QComboBox(self)
        self.result_combo.currentIndexChanged.connect(self._handle_result_changed)
        result_row.addWidget(self.result_combo, stretch=1)
        choose_button = QPushButton("Choose Folder", self)
        choose_button.clicked.connect(self._handle_pick_result)
        result_row.addWidget(choose_button)
        refresh_button = QPushButton("Refresh Runs", self)
        refresh_button.clicked.connect(lambda: self.controller.reload_result(None))
        result_row.addWidget(refresh_button)
        layout.addLayout(result_row)

        self.frame_view = ZoomableGraphicsView(self)
        self.frame_view.setMinimumSize(960, 540)
        layout.addWidget(self.frame_view, stretch=1)
        self.status_label = QLabel("Ready", self)
        layout.addWidget(self.status_label)
        layout.addWidget(QLabel("Wheel: zoom / Drag: move / Double click: fit", self))
        self.slider = QSlider(Qt.Orientation.Horizontal, self)
        self.slider.valueChanged.connect(self.render_frame)
        layout.addWidget(self.slider)
        self.replace_frames(controller.manager.frames)

    def set_result_choices(self, result_dirs: list[Path], current_result_dir: Path) -> None:
        # 콤보를 채우는 동안 발생하는 변경 신호가 결과를 다시 로드하지 않게 막습니다.
        self._building_combo = True
        self._result_paths = list(result_dirs)
        self.result_combo.clear()
        current_index = 0
        for index, result_dir in enumerate(self._result_paths):
            self.result_combo.addItem(result_dir.name, str(result_dir))
            if result_dir.resolve() == current_result_dir.resolve():
                current_index = index
        if self._result_paths:
            self.result_combo.setCurrentIndex(current_index)
        self._building_combo = False

    def replace_frames(self, frames: list[FrameRecord]) -> None:
        self.frames = frames
        self.slider.blockSignals(True)
        self.slider.setRange(0, max(len(frames) - 1, 0))
        self.slider.setValue(0)
        self.slider.blockSignals(False)
        if self.frames:
            self.render_frame(0)
        else:
            self.status_label.setText("No frames in selected result.")

    def _handle_result_changed(self, index: int) -> None:
        if self._building_combo or index < 0 or index >= len(self._result_paths):
            return
        self.controller.reload_result(self._result_paths[index])

    def _handle_pick_result(self) -> None:
        picked = self.controller.pick_result_dir()
        if picked is not None:
            self.controller.reload_result(picked)

    def render_frame(self, index: int) -> None:
        if index < 0 or index >= len(self.frames):
            return
        frame = self.frames[index]
        image, gt_points, pred_points = self.controller.load_frame_data(frame.frame_index)
        scenario = self.controller.segment_name(frame.frame_index)
        header = (
            f"frame={frame.frame_index} [{scenario}] "
            f"matched={frame.result.matched_points} "
            f"mean_error_px={frame.result.mean_error_px:.4f} "
            f"ellipse={frame.result.region_accuracy:.1%}"
        )
        overlay = self.controller.draw_points_overlay(image, gt_points, pred_points, header)
        self.frame_view.set_image(self.controller.bgr_to_qimage(overlay))
        self.status_label.setText(
            f"frame={frame.frame_index} [{scenario}] / "
            f"gt={frame.result.gt_points} pred={frame.result.pred_points} "
            f"matched={frame.result.matched_points} "
            f"missing={len(frame.result.missing_keys)} "
            f"max_error_px={frame.result.max_error_px:.4f} "
            f"ellipse={frame.result.region_accuracy:.1%}"
        )


class ReportWindow(QMainWindow):
    """점별 판정과 시나리오별 정확도를 표시하는 별도 창입니다."""

    def __init__(self, report_image: np.ndarray) -> None:
        super().__init__()
        self.setWindowTitle("Validation Report")
        self.resize(900, 920)
        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        self.report_view = ZoomableGraphicsView(self)
        layout.addWidget(self.report_view, stretch=1)
        layout.addWidget(QLabel("Wheel: zoom / Drag: move / Double click: fit", self))
        self.set_report_image(report_image)

    def set_report_image(self, report_image: np.ndarray) -> None:
        self.report_view.set_image(ValidationApp.bgr_to_qimage(report_image))


class ValidationApp:
    """데이터셋과 저장 결과를 읽어 두 조회 창을 조립합니다."""

    def __init__(self, dataset_dir: str | Path, manager: ValidResultManager) -> None:
        self.dataset_dir = Path(dataset_dir)
        self.images_dir = self.dataset_dir / "images"
        self.labels_dir = self.dataset_dir / "labels_json"
        self.manager = manager
        self.valid_image_paths = self._build_valid_image_paths()
        self.frame_window: FrameWindow | None = None
        self.report_window: ReportWindow | None = None
        self._enrich_region_rows()
        print("ValidationApp initialized.", flush=True)

    def _build_valid_image_paths(self) -> list[Path]:
        # 이미지 데이터는 캐시하지 않고 경로만 정렬합니다. 슬라이더 이동 때 즉시 읽습니다.
        if not self.images_dir.exists() or not self.labels_dir.exists():
            raise FileNotFoundError(f"Invalid validation dataset: {self.dataset_dir}")
        return [
            image_path
            for image_path in sorted(path for path in self.images_dir.iterdir() if path.is_file())
            if (self.labels_dir / f"{image_path.stem}.json").exists()
        ]

    @staticmethod
    def segment_name(frame_index: int) -> str:
        for start, end, name in SEGMENTS:
            if frame_index >= start and (end is None or frame_index <= end):
                return name
        return "unknown"

    def load_frame_data(self, frame_index: int) -> tuple[np.ndarray, list[GuidePoint], list[GuidePoint]]:
        image_path = self.valid_image_paths[frame_index]
        image = cv2.imread(str(image_path))
        if image is None:
            raise FileNotFoundError(f"Could not read image: {image_path}")
        answer = AnswerPoints.from_json(self.labels_dir / f"{image_path.stem}.json")
        return image, answer.points, self.manager.load_pred_points(frame_index)

    def _enrich_region_rows(self) -> None:
        # 과거 결과에 영역 지표가 없어도 저장된 예측과 현재 GT만으로 다시 계산합니다.
        results_by_frame = {}
        for row in self.manager.rows:
            frame_index = int(row["frame_index"])
            if frame_index < 0 or frame_index >= len(self.valid_image_paths):
                continue
            image_path = self.valid_image_paths[frame_index]
            answer = AnswerPoints.from_json(self.labels_dir / f"{image_path.stem}.json")
            result = answer.L2_check(self.manager.load_pred_points(frame_index))
            results_by_frame[frame_index] = result
            row.update(
                region_correct_points=result.region_correct_points,
                region_accuracy=result.region_accuracy,
                region_correct_keys=result.region_correct_keys,
                region_incorrect_keys=result.region_incorrect_keys,
                missing_keys=result.missing_keys,
            )
        self.manager.frames = [
            FrameRecord(record.frame_index, results_by_frame.get(record.frame_index, record.result))
            for record in self.manager.frames
        ]

    @staticmethod
    def draw_points_overlay(
        image: np.ndarray,
        gt_points: list[GuidePoint],
        pred_points: list[GuidePoint],
        header: str,
    ) -> np.ndarray:
        canvas = image.copy()
        gt_map = {
            (point.string, point.fret): point
            for point in gt_points
            if point.visible and point.x is not None and point.y is not None
        }
        pred_map = {
            (point.string, point.fret): point
            for point in pred_points
            if point.visible and point.x is not None and point.y is not None
        }

        # 실제 평가 타원을 먼저 그리고 같은 (string, fret) 점끼리만 선으로 연결합니다.
        answer = AnswerPoints(list(gt_points))
        correct_keys = set(answer.L2_check(list(pred_points)).region_correct_keys)
        for key, (center, long_radius, short_radius, angle) in answer.region_ellipse_map().items():
            color = (70, 210, 90) if key in correct_keys else (70, 70, 230)
            cv2.ellipse(
                canvas,
                (round(center[0]), round(center[1])),
                (round(long_radius), round(short_radius)),
                angle,
                0,
                360,
                color,
                1,
                cv2.LINE_AA,
            )
        for key in sorted(set(gt_map) & set(pred_map)):
            gt, pred = gt_map[key], pred_map[key]
            gt_xy = (round(gt.x), round(gt.y))
            pred_xy = (round(pred.x), round(pred.y))
            cv2.line(canvas, gt_xy, pred_xy, (255, 255, 255), 2, cv2.LINE_AA)
            cv2.line(canvas, gt_xy, pred_xy, (40, 40, 40), 1, cv2.LINE_AA)

        def draw(points: list[GuidePoint], color: tuple[int, int, int], prefix: str) -> None:
            for point in points:
                if not point.visible or point.x is None or point.y is None:
                    continue
                x, y = round(point.x), round(point.y)
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
        cv2.putText(canvas, header, (18, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(canvas, header, (18, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (30, 30, 30), 1, cv2.LINE_AA)
        return canvas

    @staticmethod
    def bgr_to_qimage(image: np.ndarray) -> QImage:
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        height, width, channels = rgb.shape
        return QImage(rgb.data, width, height, channels * width, QImage.Format.Format_RGB888).copy()

    @staticmethod
    def build_report_image(rows: list[dict[str, object]]) -> np.ndarray:
        width, height = 1400, 1050
        canvas = np.full((height, width, 3), 247, dtype=np.uint8)

        def text(value: str, x: int, y: int, scale: float = 0.65) -> None:
            cv2.putText(canvas, value, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, (30, 30, 30), 2, cv2.LINE_AA)

        frame_count = len(rows)
        tracked = sum(int(row.get("pred_points", 0)) > 0 for row in rows)
        gt_count = sum(int(row.get("gt_points", 0)) for row in rows)
        correct_count = sum(int(row.get("region_correct_points", 0)) for row in rows)
        accuracy = correct_count / gt_count if gt_count else 0.0
        availability = tracked / frame_count if frame_count else 0.0
        text("Validation Report - Point Region Results", 40, 52, 0.95)
        text(f"Point accuracy   {correct_count:,} / {gt_count:,}   ({accuracy:.2%})", 40, 102, 0.72)
        text(f"Tracking frames  {tracked:,} / {frame_count:,}   ({availability:.2%})", 40, 140, 0.72)
        text(f"Lost frames      {frame_count - tracked:,}", 40, 178, 0.72)
        for x, color, label in (
            (40, (70, 190, 80), "inside region"),
            (245, (60, 70, 225), "outside region"),
            (470, (30, 30, 30), "missing prediction"),
        ):
            cv2.rectangle(canvas, (x, 206), (x + 22, 228), color, -1)
            text(label, x + 32, 225, 0.5)

        left, right, top, row_height = 120, width - 35, 260, 7
        keys = [(string, fret) for fret in range(1, 13) for string in range(1, 7)]
        statuses = [
            (
                {tuple(item) for item in row.get("region_correct_keys", [])},
                {tuple(item) for item in row.get("missing_keys", [])},
            )
            for row in rows
        ]
        bottom = top + len(keys) * row_height
        text("72 GT points x validation frames", left, top - 14, 0.6)
        for key_index, key in enumerate(keys):
            y0, y1 = top + key_index * row_height, top + (key_index + 1) * row_height - 1
            if key[0] == 1:
                text(f"F{key[1]:02d}", 68, y1, 0.31)
            for row_index, (correct, missing) in enumerate(statuses):
                x0 = round(left + row_index * (right - left) / max(frame_count, 1))
                x1 = round(left + (row_index + 1) * (right - left) / max(frame_count, 1))
                color = (70, 190, 80) if key in correct else (30, 30, 30) if key in missing else (60, 70, 225)
                cv2.rectangle(canvas, (x0, y0), (max(x0, x1), y1), color, -1)
        for fret in range(13):
            y = top + fret * 6 * row_height
            cv2.line(canvas, (left, y), (right, y), (225, 225, 225), 1)
        cv2.rectangle(canvas, (left, top), (right, bottom), (90, 90, 90), 1)

        chart_top, chart_bottom = bottom + 75, height - 55
        text("Point accuracy by scenario", 40, chart_top - 25, 0.68)
        slot_width = (width - 80) / len(SEGMENTS)
        for index, (start, end, name) in enumerate(SEGMENTS):
            segment_rows = [
                row for row in rows
                if int(row["frame_index"]) >= start and (end is None or int(row["frame_index"]) <= end)
            ]
            segment_gt = sum(int(row.get("gt_points", 0)) for row in segment_rows)
            segment_correct = sum(int(row.get("region_correct_points", 0)) for row in segment_rows)
            segment_accuracy = segment_correct / segment_gt if segment_gt else 0.0
            x0 = round(40 + index * slot_width)
            x1 = round(40 + (index + 1) * slot_width - 12)
            bar_top = round(chart_bottom - segment_accuracy * (chart_bottom - chart_top))
            cv2.rectangle(canvas, (x0, chart_top), (x1, chart_bottom), (220, 220, 220), 1)
            cv2.rectangle(canvas, (x0 + 1, bar_top), (x1 - 1, chart_bottom - 1), (70, 160, 90), -1)
            text(f"{segment_accuracy:.1%}", x0 + 4, bar_top - 7, 0.38)
            text(name[:18], x0, chart_bottom + 20, 0.31)
        return canvas

    def _list_result_dirs(self) -> list[Path]:
        if not self.manager.result_root.exists():
            return []
        return sorted(
            [path for path in self.manager.result_root.iterdir() if path.is_dir()],
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )

    def pick_result_dir(self) -> Path | None:
        picked = QFileDialog.getExistingDirectory(
            self.frame_window,
            "Choose Validation Result Directory",
            str(self.manager.result_root),
        )
        return Path(picked).resolve() if picked else None

    def reload_result(self, result_dir: Path | None) -> None:
        if result_dir is not None:
            self.manager = ValidResultManager.from_existing(result_dir)
            self._enrich_region_rows()
        report_image = self.build_report_image(self.manager.rows)
        if self.frame_window is not None:
            self.frame_window.set_result_choices(self._list_result_dirs(), self.manager.result_dir)
            self.frame_window.replace_frames(self.manager.frames)
        if self.report_window is not None:
            self.report_window.set_report_image(report_image)

    def show(self) -> dict[str, float | int]:
        summary = self.manager.build_summary()
        qt_app = QApplication.instance()
        owns_app = qt_app is None
        if qt_app is None:
            qt_app = QApplication(sys.argv)

        print("Creating frame slider window...", flush=True)
        self.frame_window = FrameWindow(self)
        self.frame_window.set_result_choices(self._list_result_dirs(), self.manager.result_dir)
        self.frame_window.move(80, 80)
        self.frame_window.show()
        print("Frame slider window created.", flush=True)

        print("Creating report window...", flush=True)
        self.report_window = ReportWindow(self.build_report_image(self.manager.rows))
        self.report_window.move(220, 120)
        self.report_window.show()
        print("Report window created.", flush=True)
        if owns_app:
            qt_app.exec()
        print("Validation windows closed.", flush=True)
        return summary


def main() -> None:
    result_dir = (
        Path(sys.argv[1]).expanduser().resolve()
        if len(sys.argv) >= 2
        else ValidResultManager.latest_result_dir()
    )
    manager = ValidResultManager.from_existing(result_dir)
    ValidationApp(DEFAULT_DATASET, manager).show()


if __name__ == "__main__":
    main()
