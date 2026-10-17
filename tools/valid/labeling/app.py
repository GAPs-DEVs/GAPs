"""Read-only review of images/ + labels_json/ ground-truth datasets.

Run from the repository root: uv run python tools/valid/labeling/app.py
No model, prediction results, or label-writing operations are used.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QKeySequence, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QGraphicsScene, QGraphicsView, QHBoxLayout,
    QLabel, QMainWindow, QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget,
)

# labeling/은 valid/ 아래의 기능 폴더이므로 한 단계 위의 ds를 사용합니다.
DEFAULT_DATASET = Path(__file__).resolve().parents[1] / "ds"

# ID별 색은 프레임에 어떤 점이 존재하는지와 무관하게 고정합니다.
# 프레임마다 색을 다시 배정하면 누락된 점 때문에 줄 순서가 바뀐 것처럼
# 보일 수 있으므로 줄 1~6과 프렛 1~12가 항상 같은 색을 사용합니다.
POINT_COLORS = (
    "#ff5252", "#ffad33", "#ffee58", "#4dff88", "#40cfff", "#ce7bff",
    "#ff80ab", "#b8d84a", "#20bfa9", "#7090ff", "#ed66ed", "#ffffff",
)


def point_color(point, mode):
    """라벨 좌표·번호는 변경하지 않고 표시 색상만 결정합니다."""
    if mode == "visible":
        return QColor("#00ff88" if point.get("visible") is True else "#ff4040")
    identifier = point.get(mode)
    if type(identifier) is not int or identifier < 1:
        return QColor("#999999")
    if identifier <= len(POINT_COLORS):
        return QColor(POINT_COLORS[identifier - 1])
    return QColor.fromHsv((identifier * 137) % 360, 180, 255)


def read_label(path: Path, image: Path, width: int, height: int):
    """Return drawable points and warnings without modifying source JSON.

    Invalid coordinates are skipped so that one broken label does not prevent
    reviewing subsequent frames. Duplicate IDs remain visible but are flagged.
    """
    warnings, points, seen = [], [], set()
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict) or not isinstance(data.get("points"), list):
            raise ValueError("points must be an array")
        if data.get("image_name") != image.name:
            warnings.append("image_name differs from the displayed image")
        if (data.get("image_width"), data.get("image_height")) != (width, height):
            warnings.append("Recorded image dimensions do not match")
        for index, point in enumerate(data["points"]):
            if not isinstance(point, dict):
                warnings.append(f"Point {index + 1}: invalid object")
                continue
            x, y = point.get("x"), point.get("y")
            if any(type(v) not in (int, float) or not math.isfinite(v) for v in (x, y)):
                warnings.append(f"Point {index + 1}: invalid coordinates")
                continue
            string, fret = point.get("string"), point.get("fret")
            if type(string) is not int or string not in range(1, 7) or type(fret) is not int or fret < 1:
                warnings.append(f"Point {index + 1}: invalid string/fret")
            # Use strings for diagnostic keys so malformed JSON values cannot
            # crash the reviewer's duplicate check (e.g. an array as fret ID).
            key = (str(string), str(fret))
            if key in seen:
                warnings.append(f"Duplicate S{string}/F{fret}")
            seen.add(key)
            if not (0 <= x < width and 0 <= y < height):
                warnings.append(f"S{string}/F{fret}: outside image")
            if type(point.get("visible")) is not bool:
                warnings.append(f"S{string}/F{fret}: visible must be boolean")
            points.append(point)
    except (OSError, ValueError) as exc:
        warnings.append(f"Cannot read label: {exc}")
    return points, warnings


class ImageView(QGraphicsView):
    """Keep coordinates in original pixels; zoom only changes the view."""

    def __init__(self):
        super().__init__()
        # QGraphicsView does not take ownership of a scene passed into its
        # constructor. A temporary scene can therefore be garbage-collected,
        # making scene() return None on the first redraw. Keep both a Python
        # reference and a Qt parent so it lives as long as this view.
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

    def wheelEvent(self, event):
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        if .05 <= self.transform().m11() * factor <= 20:
            self.scale(factor, factor)
        event.accept()

    def fit(self):
        self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)


class LabelReviewer(QMainWindow):
    def __init__(self, dataset: Path):
        super().__init__()
        self.dataset = dataset
        # Enumerate images, not JSONs: images with missing labels must remain
        # visible during review instead of being silently excluded.
        self.images = sorted(p for p in (dataset / "images").glob("*")
                             if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp"})
        if not self.images:
            raise ValueError(f"No images found: {dataset / 'images'}")
        self.setWindowTitle("Ground-truth label review (read-only)")
        self.resize(1280, 850)
        self.view = ImageView()
        self.info, self.warning = QLabel(), QLabel()
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet("color: #e6a23c")
        self.frame = QSpinBox()
        self.frame.setRange(1, len(self.images))
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(1, len(self.images))
        self.overlay = QCheckBox("Show points")
        self.overlay.setChecked(True)
        self.ids = QCheckBox("Show S/F IDs")
        self.color_mode = QComboBox()
        self.color_mode.addItem("Color: String", "string")
        self.color_mode.addItem("Color: Fret", "fret")
        self.color_mode.addItem("Color: Visibility", "visible")
        self.legend = QLabel()
        self.legend.setWordWrap(True)
        previous, following, fit = QPushButton("Previous"), QPushButton("Next"), QPushButton("Fit")
        previous.clicked.connect(lambda: self.frame.setValue(self.frame.value() - 1))
        following.clicked.connect(lambda: self.frame.setValue(self.frame.value() + 1))
        fit.clicked.connect(self.view.fit)
        self.frame.valueChanged.connect(self.load_frame)
        self.slider.valueChanged.connect(self.frame.setValue)
        self.overlay.toggled.connect(self.redraw)
        self.ids.toggled.connect(self.redraw)
        self.color_mode.currentIndexChanged.connect(self.redraw)
        controls = QHBoxLayout()
        for widget in (previous, following, self.frame, self.overlay, self.ids, self.color_mode, fit):
            controls.addWidget(widget)
        layout = QVBoxLayout()
        layout.addLayout(controls)
        layout.addWidget(self.slider)
        layout.addWidget(self.info)
        layout.addWidget(self.legend)
        layout.addWidget(self.view, 1)
        layout.addWidget(self.warning)
        layout.addWidget(QLabel("Filled: visible | Hollow: hidden/unknown | Hover: S/F and coordinates | Wheel: zoom | Drag: pan"))
        root = QWidget()
        root.setLayout(layout)
        self.setCentralWidget(root)
        self.shortcuts = []
        for key, button in (("Left", previous), ("Right", following), ("F", fit)):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(button.click)
            self.shortcuts.append(shortcut)
        self.load_frame(1)

    def load_frame(self, number):
        self.slider.blockSignals(True)
        self.slider.setValue(number)
        self.slider.blockSignals(False)
        path = self.images[number - 1]
        self.pixmap = QPixmap(str(path))
        self.points, warnings = read_label(
            self.dataset / "labels_json" / f"{path.stem}.json", path,
            self.pixmap.width(), self.pixmap.height())
        if self.pixmap.isNull():
            warnings.insert(0, "Cannot decode image")
        visible = sum(p.get("visible") is True for p in self.points)
        self.info.setText(f"{number}/{len(self.images)} | {path.name} | Points: {len(self.points)} | Visible: {visible}")
        self.warning.setText("\n".join(warnings[:8]) if warnings else
                             ("Empty label: confirm whether the fretboard is absent." if not self.points else "No structural issues. Check point placement visually."))
        self.redraw()
        self.view.fit()

    def redraw(self):
        mode = self.color_mode.currentData()
        if mode == "visible":
            self.legend.setText('<span style="color:#00ff88">● Visible</span> &nbsp; '
                                '<span style="color:#ff4040">○ Hidden / unknown</span>')
        else:
            # 줄 색상은 전체 6개, 프렛 색상은 현재 프레임에 있는 번호를 표시합니다.
            # 순서 판단은 영상의 회전·좌우 반전을 고려해 사용자가 수행합니다.
            # 화면 x/y 정렬만으로 라벨 번호를 자동 변경하지 않습니다.
            identifiers = range(1, 7) if mode == "string" else sorted({
                p["fret"] for p in self.points if type(p.get("fret")) is int and p["fret"] > 0})
            prefix = "S" if mode == "string" else "F"
            self.legend.setText(" &nbsp; ".join(
                f'<span style="color:{point_color({mode: n}, mode).name()}">● {prefix}{n}</span>'
                for n in identifiers))
        scene = self.view.scene()
        scene.clear()
        scene.addPixmap(self.pixmap)
        scene.setSceneRect(0, 0, self.pixmap.width(), self.pixmap.height())
        if not self.overlay.isChecked():
            return
        for point in self.points:
            x, y = point["x"], point["y"]
            color = point_color(point, mode)
            # 가시성은 색상 모드와 독립적으로 채움/빈 원으로 구분합니다.
            # 같은 줄/프렛의 색을 유지하면서 가려진 점도 식별할 수 있습니다.
            brush = QBrush(color) if point.get("visible") is True else QBrush(Qt.BrushStyle.NoBrush)
            pen = QPen(color, 1.5)
            pen.setCosmetic(True)
            marker = scene.addEllipse(x - 4, y - 4, 8, 8, pen, brush)
            marker.setToolTip(f"S{point.get('string')} / F{point.get('fret')}\n"
                              f"x={x:.2f}, y={y:.2f}\nvisible={point.get('visible')}")
            if self.ids.isChecked():
                text = scene.addSimpleText(f"S{point.get('string')}/F{point.get('fret')}")
                text.setBrush(color)
                text.setPos(x + 5, y - 12)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    try:
        window = LabelReviewer(args.dataset.resolve())
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    window.show()
    app.processEvents()
    window.view.fit()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
