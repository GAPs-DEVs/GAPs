"""검증 영상에서 앱 파이프라인의 초기 지판 맞춤 정보를 저장합니다.

실제 사용자 앱의 수동 맞춤과 동일하게 너트, 중간 프렛, 끝 기준 프렛의
양 끝점을 차례로 선택합니다. 저장된 세 선은 이후 앱 파이프라인 검증을
시작할 때 ``build_calibrated_grid``의 입력으로 사용합니다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFormLayout,
    QGraphicsLineItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from vision.fretboard_calibration import build_calibrated_grid
from vision.fretboard_mapping import DetectedLine


DATASET_DIR = PROJECT_ROOT / "tools" / "valid" / "ds"
OUTPUT_PATH = Path(__file__).resolve().with_name("calibration.json")
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp"}


class _CalibrationView(QGraphicsView):
    """이미지 좌표로 클릭을 전달하고 확대·이동을 제공하는 화면입니다."""

    def __init__(self, on_click) -> None:
        super().__init__()
        self._on_click = on_click
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(self.renderHints())
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)

    def mousePressEvent(self, event) -> None:
        # 왼쪽 클릭은 좌표 선택에만 사용하고, 화면 이동은 가운데 버튼으로 합니다.
        if event.button() == Qt.MouseButton.LeftButton:
            point = self.mapToScene(event.position().toPoint())
            if self.sceneRect().contains(point):
                self._on_click(point)
            return
        super().mousePressEvent(event)

    def wheelEvent(self, event) -> None:
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        self.scale(factor, factor)


class CalibrationWindow(QMainWindow):
    """프레임 하나에서 세 기준선을 선택하고 JSON으로 저장하는 도구입니다."""

    LINE_NAMES = ("너트", "중간 프렛", "끝 기준 프렛")
    LINE_COLORS = (QColor(70, 255, 150), QColor(255, 210, 60), QColor(0, 218, 255))

    def __init__(self) -> None:
        super().__init__()
        self.image_paths = self._image_paths()
        self.points: list[QPointF] = []
        self._line_items: list[QGraphicsLineItem] = []

        self.setWindowTitle("Validation Calibration Frame")
        self.resize(1280, 820)

        self.view = _CalibrationView(self._add_point)
        self.frame_label = QLabel()
        self.instruction_label = QLabel()

        self.frame_slider = QSlider(Qt.Orientation.Horizontal)
        self.frame_slider.setRange(0, len(self.image_paths) - 1)
        self.frame_slider.valueChanged.connect(self._load_frame)

        self.middle_spin = QSpinBox()
        self.middle_spin.setRange(1, 23)
        self.middle_spin.setValue(5)
        self.reference_spin = QSpinBox()
        self.reference_spin.setRange(2, 24)
        self.reference_spin.setValue(12)

        undo_button = QPushButton("마지막 점 취소")
        undo_button.clicked.connect(self._undo_point)
        reset_button = QPushButton("선택 초기화")
        reset_button.clicked.connect(self._reset_points)
        save_button = QPushButton("보정 정보 저장")
        save_button.clicked.connect(self._save)

        form = QFormLayout()
        form.addRow("중간 프렛 실제 번호", self.middle_spin)
        form.addRow("끝 기준 프렛 실제 번호", self.reference_spin)

        buttons = QHBoxLayout()
        buttons.addWidget(undo_button)
        buttons.addWidget(reset_button)
        buttons.addStretch(1)
        buttons.addWidget(save_button)

        layout = QVBoxLayout()
        layout.addWidget(self.view, stretch=1)
        layout.addWidget(self.frame_label)
        layout.addWidget(self.frame_slider)
        layout.addWidget(self.instruction_label)
        layout.addLayout(form)
        layout.addLayout(buttons)

        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)
        self._load_frame(0)
        print(f"CalibrationWindow initialized: {len(self.image_paths)} frames", flush=True)

    @staticmethod
    def _image_paths() -> list[Path]:
        images_dir = DATASET_DIR / "images"
        labels_dir = DATASET_DIR / "labels_json"
        if not images_dir.exists():
            raise FileNotFoundError(f"Images directory not found: {images_dir}")

        # 정답 JSON이 있는 이미지만 사용하여 ValidLoader의 frame_index와 맞춥니다.
        paths = [
            path
            for path in sorted(images_dir.iterdir())
            if path.suffix.lower() in IMAGE_SUFFIXES
            and (labels_dir / f"{path.stem}.json").exists()
        ]
        if not paths:
            raise FileNotFoundError(f"No validation images found: {images_dir}")
        return paths

    def _load_frame(self, frame_index: int) -> None:
        image_path = self.image_paths[frame_index]
        pixmap = QPixmap(str(image_path))
        if pixmap.isNull():
            raise FileNotFoundError(f"Could not read image: {image_path}")

        self.view.scene().clear()
        self.view.scene().addPixmap(pixmap)
        self.view.setSceneRect(pixmap.rect())
        self.view.resetTransform()
        self.view.fitInView(self.view.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self.points.clear()
        self._line_items.clear()
        self.frame_label.setText(
            f"frame_index={frame_index} / {len(self.image_paths) - 1} | {image_path.name}"
        )
        self._update_instruction()

    def _add_point(self, point: QPointF) -> None:
        if len(self.points) >= 6:
            return
        self.points.append(point)
        if len(self.points) % 2 == 0:
            line_index = len(self.points) // 2 - 1
            p0, p1 = self.points[-2:]
            pen = QPen(self.LINE_COLORS[line_index], 4)
            pen.setCosmetic(True)
            self._line_items.append(self.view.scene().addLine(p0.x(), p0.y(), p1.x(), p1.y(), pen))
        self._update_instruction()

    def _undo_point(self) -> None:
        if not self.points:
            return
        if len(self.points) % 2 == 0 and self._line_items:
            self.view.scene().removeItem(self._line_items.pop())
        self.points.pop()
        self._update_instruction()

    def _reset_points(self) -> None:
        for item in self._line_items:
            self.view.scene().removeItem(item)
        self.points.clear()
        self._line_items.clear()
        self._update_instruction()

    def _update_instruction(self) -> None:
        if len(self.points) == 6:
            self.instruction_label.setText("선택 완료: 프렛 번호를 확인하고 저장하세요.")
            return
        line_index = len(self.points) // 2
        endpoint = "첫 번째" if len(self.points) % 2 == 0 else "두 번째"
        self.instruction_label.setText(
            f"{self.LINE_NAMES[line_index]} 선의 {endpoint} 끝점을 클릭하세요. "
            "휠: 확대/축소, 가운데 버튼 드래그: 이동"
        )

    def _selected_lines(self) -> tuple[DetectedLine, DetectedLine, DetectedLine]:
        if len(self.points) != 6:
            raise ValueError("너트·중간 프렛·끝 기준 프렛의 양 끝점을 모두 선택해주세요.")

        def line(offset: int, kind: str) -> DetectedLine:
            p0, p1 = self.points[offset : offset + 2]
            return DetectedLine(kind, (p0.x(), p0.y()), (p1.x(), p1.y()), 1.0)

        return line(0, "nut"), line(2, "fret"), line(4, "fret")

    @staticmethod
    def _line_payload(line: DetectedLine) -> list[list[float]]:
        return [
            [round(float(line.p0[0]), 3), round(float(line.p0[1]), 3)],
            [round(float(line.p1[0]), 3), round(float(line.p1[1]), 3)],
        ]

    def _save(self) -> None:
        try:
            nut, middle, reference = self._selected_lines()
            # 실제 앱이 사용하는 함수로 먼저 검사하여 잘못된 선이나 번호는 저장하지 않습니다.
            build_calibrated_grid(
                nut,
                middle,
                self.middle_spin.value(),
                reference,
                self.reference_spin.value(),
            )
        except ValueError as error:
            QMessageBox.warning(self, "보정 정보 확인", str(error))
            return

        calibration = {
            "frame_index": self.frame_slider.value(),
            "nut_line": self._line_payload(nut),
            "middle_fret_line": self._line_payload(middle),
            "middle_fret_index": self.middle_spin.value(),
            "reference_fret_line": self._line_payload(reference),
            "reference_fret_index": self.reference_spin.value(),
        }
        payload = self._read_existing_payload()
        calibrations = payload["calibrations"]

        # 같은 프레임을 다시 저장하면 항목을 중복 생성하지 않고 최신 선택으로 교체합니다.
        calibrations[:] = [
            item for item in calibrations if item.get("frame_index") != calibration["frame_index"]
        ]
        calibrations.append(calibration)
        calibrations.sort(key=lambda item: int(item["frame_index"]))
        OUTPUT_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Calibration saved: {OUTPUT_PATH} (frame={calibration['frame_index']})", flush=True)
        QMessageBox.information(self, "저장 완료", f"보정 정보를 저장했습니다.\n{OUTPUT_PATH}")

    @staticmethod
    def _read_existing_payload() -> dict[str, list[dict[str, object]]]:
        if not OUTPUT_PATH.exists():
            return {"calibrations": []}
        payload = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
        calibrations = payload.get("calibrations")
        if not isinstance(calibrations, list):
            raise ValueError(f"Invalid calibration file: {OUTPUT_PATH}")
        return {"calibrations": calibrations}


def main() -> None:
    app = QApplication(sys.argv)
    window = CalibrationWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
