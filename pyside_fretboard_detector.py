from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import cv2
import numpy as np
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QCloseEvent, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
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
from ultralytics import YOLO

from fretboard_mapping import (
    DetectedLine,
    FretboardTracker,
    NoteMarker,
    StableFretboardGrid,
    build_fretboard_grid,
    build_note_markers,
    count_detected_classes,
)


DEFAULT_MODEL_PATH = Path(__file__).with_name("best06.pt")
NOTE_CHOICES = ["없음", "도", "레", "미", "파", "솔", "라", "시"]
POINT_THRESHOLD = 0.15
CLASS_COLORS = {
    "fret": (80, 190, 255),
    "nut": (70, 255, 150),
}
NOTE_MARKER_COLOR = (0, 218, 255)


@dataclass
class FrameAnalysis:
    detected_lines: tuple[DetectedLine, ...]
    stable_grid: StableFretboardGrid | None
    note_markers: tuple[NoteMarker, ...]
    class_counts: dict[str, int]


@dataclass
class InferenceStats:
    fps: float
    inference_ms: float
    total_detections: int
    class_counts: dict[str, int]
    visible_fret_count: int
    marker_count: int
    selected_note: str


def resolve_model_path(model_path: str | None) -> Path:
    if model_path:
        return Path(model_path).expanduser().resolve()
    return DEFAULT_MODEL_PATH.resolve()


def load_yolo_model(model_path: Path) -> YOLO:
    if not model_path.exists():
        raise FileNotFoundError(f"YOLO weight file not found: {model_path}")
    return YOLO(str(model_path))


def format_counts(class_counts: dict[str, int]) -> str:
    if not class_counts:
        return "no detections"
    return ", ".join(f"{name}: {count}" for name, count in sorted(class_counts.items()))


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


def extract_detected_lines(
    result,
    names: dict[int, str],
    point_threshold: float = POINT_THRESHOLD,
) -> tuple[DetectedLine, ...]:
    boxes = result.boxes
    keypoints = result.keypoints
    if boxes is None or keypoints is None or len(boxes) == 0:
        return ()

    classes = boxes.cls.cpu().numpy().astype(int)
    confidences = boxes.conf.cpu().numpy()
    keypoint_xy = keypoints.xy.cpu().numpy()
    keypoint_conf = None
    if getattr(keypoints, "conf", None) is not None:
        keypoint_conf = keypoints.conf.cpu().numpy()

    detected_lines: list[DetectedLine] = []
    for index, (class_index, confidence) in enumerate(zip(classes, confidences)):
        class_name = names.get(class_index, str(class_index))
        if class_name not in CLASS_COLORS:
            continue
        if index >= len(keypoint_xy):
            continue

        visible_points: list[tuple[float, float]] = []
        for point_index, point in enumerate(keypoint_xy[index]):
            point_confidence = 1.0
            if keypoint_conf is not None and point_index < len(keypoint_conf[index]):
                point_confidence = float(keypoint_conf[index][point_index])
            if point_confidence < point_threshold:
                continue
            visible_points.append((float(point[0]), float(point[1])))

        if len(visible_points) < 2:
            continue

        detected_lines.append(
            DetectedLine(
                kind=class_name,
                p0=visible_points[0],
                p1=visible_points[1],
                confidence=float(confidence),
            )
        )

    return tuple(detected_lines)


def draw_detected_lines(
    frame_bgr: np.ndarray,
    detected_lines: tuple[DetectedLine, ...],
) -> np.ndarray:
    annotated = frame_bgr.copy()
    if not detected_lines:
        cv2.putText(
            annotated,
            "Waiting for fretboard detections...",
            (24, 36),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.9,
            (230, 230, 230),
            2,
            cv2.LINE_AA,
        )
        return annotated

    for line in detected_lines:
        color = CLASS_COLORS.get(line.kind, (255, 210, 90))
        p0 = tuple(int(value) for value in line.p0)
        p1 = tuple(int(value) for value in line.p1)
        cv2.line(annotated, p0, p1, color, 3, cv2.LINE_AA)
        cv2.circle(annotated, p0, 6, color, -1, cv2.LINE_AA)
        cv2.circle(annotated, p1, 6, color, -1, cv2.LINE_AA)

    return annotated


def draw_note_markers(
    frame_bgr: np.ndarray,
    note_markers: tuple[NoteMarker, ...],
) -> np.ndarray:
    annotated = frame_bgr.copy()
    for marker in note_markers:
        center = tuple(int(round(value)) for value in marker.point_xy)
        if marker.is_open:
            cv2.circle(annotated, center, 11, NOTE_MARKER_COLOR, 3, cv2.LINE_AA)
        else:
            cv2.circle(annotated, center, 9, NOTE_MARKER_COLOR, -1, cv2.LINE_AA)
    return annotated


def analyze_result(
    result,
    names: dict[int, str],
    tracker: FretboardTracker,
    selected_note: str | None,
    reverse_strings: bool,
) -> FrameAnalysis:
    detected_lines = extract_detected_lines(result, names)
    class_counts = count_detected_classes(detected_lines)
    stable_grid = tracker.update(build_fretboard_grid(detected_lines))
    note_markers = build_note_markers(stable_grid, selected_note, reverse_strings=reverse_strings)
    return FrameAnalysis(
        detected_lines=detected_lines,
        stable_grid=stable_grid,
        note_markers=note_markers,
        class_counts=class_counts,
    )


def run_inference(
    frame_bgr: np.ndarray,
    model: YOLO,
    conf_threshold: float,
    image_size: int,
    tracker: FretboardTracker,
    selected_note: str | None,
    reverse_strings: bool,
) -> tuple[np.ndarray, FrameAnalysis]:
    result = model.predict(
        source=frame_bgr,
        conf=conf_threshold,
        imgsz=image_size,
        verbose=False,
    )[0]
    analysis = analyze_result(
        result=result,
        names=model.names,
        tracker=tracker,
        selected_note=selected_note,
        reverse_strings=reverse_strings,
    )

    annotated = draw_detected_lines(frame_bgr, analysis.detected_lines)
    annotated = draw_note_markers(annotated, analysis.note_markers)
    return annotated, analysis


def enumerate_camera_indices(max_index: int = 5) -> list[int]:
    available: list[int] = []
    for camera_index in range(max_index):
        capture = cv2.VideoCapture(camera_index)
        try:
            if capture.isOpened():
                ok, _ = capture.read()
                if ok:
                    available.append(camera_index)
        finally:
            capture.release()
    return available


class VideoWorker(QThread):
    frame_ready = Signal(QImage, object)
    error_message = Signal(str)

    def __init__(
        self,
        model_path: Path,
        camera_index: int,
        conf_threshold: float,
        image_size: int,
        selected_note: str | None,
        reverse_strings: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.model_path = model_path
        self.camera_index = camera_index
        self.conf_threshold = conf_threshold
        self.image_size = image_size
        self.selected_note = selected_note
        self.reverse_strings = reverse_strings
        self.tracker = FretboardTracker(max_history=5)
        self._running = True

    def stop(self) -> None:
        self._running = False
        self.wait(2000)

    def set_conf_threshold(self, conf_threshold: float) -> None:
        self.conf_threshold = conf_threshold

    def set_image_size(self, image_size: int) -> None:
        self.image_size = image_size

    def set_selected_note(self, selected_note: str | None) -> None:
        self.selected_note = selected_note

    def set_reverse_strings(self, reverse_strings: bool) -> None:
        self.reverse_strings = reverse_strings

    def run(self) -> None:
        try:
            model = load_yolo_model(self.model_path)
        except Exception as exc:  # pragma: no cover - GUI error path
            self.error_message.emit(str(exc))
            return

        capture = cv2.VideoCapture(self.camera_index)
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

        if not capture.isOpened():
            self.error_message.emit(
                f"Could not open webcam index {self.camera_index}. Check camera permission or another camera index."
            )
            capture.release()
            return

        previous_frame_time = perf_counter()

        while self._running:
            ok, frame_bgr = capture.read()
            if not ok:
                self.error_message.emit("Failed to read frame from webcam.")
                break

            confidence = self.conf_threshold
            image_size = self.image_size
            selected_note = self.selected_note
            reverse_strings = self.reverse_strings

            inference_start = perf_counter()
            annotated, analysis = run_inference(
                frame_bgr=frame_bgr,
                model=model,
                conf_threshold=confidence,
                image_size=image_size,
                tracker=self.tracker,
                selected_note=selected_note,
                reverse_strings=reverse_strings,
            )
            inference_ms = (perf_counter() - inference_start) * 1000.0

            now = perf_counter()
            fps = 1.0 / max(now - previous_frame_time, 1e-6)
            previous_frame_time = now

            visible_fret_count = 0
            if analysis.stable_grid is not None:
                visible_fret_count = len(analysis.stable_grid.ordered_fret_lines)

            stats = InferenceStats(
                fps=fps,
                inference_ms=inference_ms,
                total_detections=sum(analysis.class_counts.values()),
                class_counts=analysis.class_counts,
                visible_fret_count=visible_fret_count,
                marker_count=len(analysis.note_markers),
                selected_note=selected_note or "없음",
            )
            self.frame_ready.emit(bgr_to_qimage(annotated), stats)

        capture.release()


class MainWindow(QMainWindow):
    def __init__(self, model_path: Path, camera_index: int = 0) -> None:
        super().__init__()
        self.setWindowTitle("Guitar Fretboard YOLO Viewer")
        self.resize(1360, 860)

        self.model_path = model_path
        self.worker: VideoWorker | None = None

        self.video_label = QLabel("Press Start to open the webcam.")
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setMinimumSize(960, 540)
        self.video_label.setStyleSheet(
            "background-color: #101418; color: #e8eef2; border: 1px solid #24303a; font-size: 20px;"
        )

        self.model_value = QLabel(str(self.model_path))
        self.model_value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.camera_combo = QComboBox()
        self.refresh_camera_button = QPushButton("Refresh Cameras")
        self.refresh_camera_button.clicked.connect(self.populate_camera_list)

        self.note_combo = QComboBox()
        self.note_combo.addItems(NOTE_CHOICES)
        self.note_combo.currentTextChanged.connect(self.handle_note_change)

        self.reverse_checkbox = QCheckBox("현 방향 반전")
        self.reverse_checkbox.stateChanged.connect(self.handle_reverse_change)

        self.conf_slider = QSlider(Qt.Orientation.Horizontal)
        self.conf_slider.setRange(10, 95)
        self.conf_slider.setValue(25)
        self.conf_slider.valueChanged.connect(self.handle_conf_change)
        self.conf_value = QLabel("0.25")

        self.image_size_spin = QSpinBox()
        self.image_size_spin.setRange(320, 1600)
        self.image_size_spin.setSingleStep(32)
        self.image_size_spin.setValue(960)
        self.image_size_spin.valueChanged.connect(self.handle_image_size_change)

        self.start_button = QPushButton("Start")
        self.stop_button = QPushButton("Stop")
        self.browse_button = QPushButton("Choose Model")

        self.start_button.clicked.connect(self.start_detection)
        self.stop_button.clicked.connect(self.stop_detection)
        self.browse_button.clicked.connect(self.choose_model_file)
        self.stop_button.setEnabled(False)

        self.status_label = QLabel("선택 음: 없음 / 후보 0개 / visible frets: 0")
        self.fps_label = QLabel("FPS: -")
        self.inference_label = QLabel("Inference: -")
        self.counts_label = QLabel("Detections: -")

        controls = self._build_controls()
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addWidget(self.video_label, stretch=1)
        layout.addWidget(controls)
        self.setCentralWidget(root)

        self.populate_camera_list(selected=camera_index)

    def _build_controls(self) -> QWidget:
        controls_box = QGroupBox("Controls")
        layout = QGridLayout(controls_box)

        layout.addWidget(QLabel("Model"), 0, 0)
        layout.addWidget(self.model_value, 0, 1, 1, 4)
        layout.addWidget(self.browse_button, 0, 5)

        layout.addWidget(QLabel("Camera"), 1, 0)
        layout.addWidget(self.camera_combo, 1, 1)
        layout.addWidget(self.refresh_camera_button, 1, 2)

        layout.addWidget(QLabel("Confidence"), 1, 3)
        layout.addWidget(self.conf_slider, 1, 4)
        layout.addWidget(self.conf_value, 1, 5)

        layout.addWidget(QLabel("Selected Note"), 2, 0)
        layout.addWidget(self.note_combo, 2, 1)
        layout.addWidget(self.reverse_checkbox, 2, 2)
        layout.addWidget(QLabel("Image Size"), 2, 3)
        layout.addWidget(self.image_size_spin, 2, 4)

        button_row = QHBoxLayout()
        button_row.addWidget(self.start_button)
        button_row.addWidget(self.stop_button)
        layout.addLayout(button_row, 2, 5)

        layout.addWidget(QLabel("Status"), 3, 0)
        layout.addWidget(self.status_label, 3, 1, 1, 5)
        layout.addWidget(self.fps_label, 4, 3)
        layout.addWidget(self.inference_label, 4, 4)
        layout.addWidget(self.counts_label, 4, 5)

        return controls_box

    def selected_note(self) -> str | None:
        note = self.note_combo.currentText()
        return None if note == "없음" else note

    def update_note_status(self, marker_count: int = 0, visible_frets: int = 0) -> None:
        note_label = self.selected_note() or "없음"
        self.status_label.setText(
            f"선택 음: {note_label} / 후보 {marker_count}개 / visible frets: {visible_frets}"
        )

    def populate_camera_list(self, selected: int | None = None) -> None:
        current_selection = selected
        if current_selection is None and self.camera_combo.currentData() is not None:
            current_selection = int(self.camera_combo.currentData())

        self.camera_combo.clear()
        available_cameras = enumerate_camera_indices()
        if not available_cameras:
            available_cameras = [0]

        for camera_index in available_cameras:
            self.camera_combo.addItem(f"Camera {camera_index}", camera_index)

        if current_selection in available_cameras:
            index = available_cameras.index(current_selection)
            self.camera_combo.setCurrentIndex(index)

    def handle_conf_change(self, value: int) -> None:
        confidence = value / 100.0
        self.conf_value.setText(f"{confidence:.2f}")
        if self.worker is not None:
            self.worker.set_conf_threshold(confidence)

    def handle_image_size_change(self, value: int) -> None:
        if self.worker is not None:
            self.worker.set_image_size(value)

    def handle_note_change(self, _: str) -> None:
        if self.worker is not None:
            self.worker.set_selected_note(self.selected_note())
        else:
            self.update_note_status(marker_count=0, visible_frets=0)

    def handle_reverse_change(self, _: int) -> None:
        if self.worker is not None:
            self.worker.set_reverse_strings(self.reverse_checkbox.isChecked())

    def choose_model_file(self) -> None:
        file_name, _ = QFileDialog.getOpenFileName(
            self,
            "Select YOLO Weights",
            str(self.model_path.parent),
            "YOLO weights (*.pt *.onnx *.engine *.pth);;All files (*)",
        )
        if not file_name:
            return

        self.model_path = Path(file_name).expanduser().resolve()
        self.model_value.setText(str(self.model_path))
        self.update_note_status(marker_count=0, visible_frets=0)

        if self.worker is not None:
            self.stop_detection()

    def start_detection(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            return

        model_path = self.model_path
        if not model_path.exists():
            QMessageBox.critical(self, "Missing Weights", f"Model file does not exist:\n{model_path}")
            return

        camera_index = int(self.camera_combo.currentData() or 0)
        confidence = self.conf_slider.value() / 100.0
        image_size = int(self.image_size_spin.value())

        self.worker = VideoWorker(
            model_path=model_path,
            camera_index=camera_index,
            conf_threshold=confidence,
            image_size=image_size,
            selected_note=self.selected_note(),
            reverse_strings=self.reverse_checkbox.isChecked(),
            parent=self,
        )
        self.worker.frame_ready.connect(self.update_frame)
        self.worker.error_message.connect(self.handle_worker_error)
        self.worker.finished.connect(self.on_worker_finished)

        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.update_note_status(marker_count=0, visible_frets=0)
        self.worker.start()

    def stop_detection(self) -> None:
        if self.worker is None:
            return
        self.status_label.setText("Stopping...")
        self.worker.stop()

    def update_frame(self, image: QImage, stats: InferenceStats) -> None:
        pixmap = QPixmap.fromImage(image)
        scaled = pixmap.scaled(
            self.video_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.video_label.setPixmap(scaled)
        self.status_label.setText(
            f"선택 음: {stats.selected_note} / 후보 {stats.marker_count}개 / visible frets: {stats.visible_fret_count}"
        )
        self.fps_label.setText(f"FPS: {stats.fps:.1f}")
        self.inference_label.setText(f"Inference: {stats.inference_ms:.1f} ms")
        self.counts_label.setText(
            f"Detections: {stats.total_detections} ({format_counts(stats.class_counts)})"
        )

    def handle_worker_error(self, message: str) -> None:
        QMessageBox.warning(self, "Webcam / Model Error", message)
        self.status_label.setText(message)
        self.stop_detection()

    def on_worker_finished(self) -> None:
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.worker = None
        if self.video_label.pixmap() is None:
            self.video_label.setText("Press Start to open the webcam.")
        self.update_note_status(marker_count=0, visible_frets=0)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.stop_detection()
        super().closeEvent(event)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PySide webcam viewer for guitar fretboard YOLO detection")
    parser.add_argument("--model", type=str, default=None, help="Path to the YOLO weight file")
    parser.add_argument("--camera", type=int, default=0, help="Webcam index to open first")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    model_path = resolve_model_path(args.model)

    app = QApplication(sys.argv)
    window = MainWindow(model_path=model_path, camera_index=args.camera)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
