from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QLabel

from vision.fretboard_mapping import DetectedLine


class VideoSurface(QLabel):
    clicked = Signal(float, float)

    def __init__(self, placeholder_text: str) -> None:
        super().__init__(placeholder_text)
        self.placeholder_text = placeholder_text
        self.current_image: QImage | None = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(960, 540)
        self.setStyleSheet(
            "background-color: #101418; color: #e8eef2; border: 1px solid #24303a; font-size: 20px;"
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(float(event.position().x()), float(event.position().y()))
        super().mousePressEvent(event)

    def set_current_image(self, image: QImage) -> None:
        self.current_image = image

    def reset_placeholder(self) -> None:
        self.current_image = None
        self.clear()
        self.setText(self.placeholder_text)

    def map_label_point_to_image(self, label_x: float, label_y: float) -> tuple[float, float] | None:
        if self.current_image is None:
            return None

        image_width = self.current_image.width()
        image_height = self.current_image.height()
        if image_width <= 0 or image_height <= 0:
            return None

        scale = min(self.width() / image_width, self.height() / image_height)
        display_width = image_width * scale
        display_height = image_height * scale
        offset_x = (self.width() - display_width) / 2.0
        offset_y = (self.height() - display_height) / 2.0

        if label_x < offset_x or label_x > offset_x + display_width:
            return None
        if label_y < offset_y or label_y > offset_y + display_height:
            return None

        image_x = (label_x - offset_x) / scale
        image_y = (label_y - offset_y) / scale
        return (float(image_x), float(image_y))

    def render_frame(
        self,
        selection_stage: str = "idle",
        selected_nut_line: DetectedLine | None = None,
        selected_reference_line: DetectedLine | None = None,
        selection_points: tuple[tuple[float, float], ...] = (),
        manual_points: bool = False,
        reference_fret_index: int | None = None,
    ) -> None:
        if self.current_image is None:
            return

        image = self.current_image
        if selection_stage in {"select_nut", "select_fret"}:
            image = self._decorate_selection_frame(
                image,
                selection_stage,
                selected_nut_line,
                selected_reference_line,
                selection_points,
                manual_points,
                reference_fret_index,
            )

        pixmap = QPixmap.fromImage(image)
        scaled = pixmap.scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.setPixmap(scaled)

    def _decorate_selection_frame(
        self,
        image: QImage,
        selection_stage: str,
        selected_nut_line: DetectedLine | None,
        selected_reference_line: DetectedLine | None,
        selection_points: tuple[tuple[float, float], ...],
        manual_points: bool,
        reference_fret_index: int | None,
    ) -> QImage:
        preview = image.copy()
        painter = QPainter(preview)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if selected_nut_line is not None:
            painter.setPen(QPen(QColor(70, 255, 150), 5))
            painter.drawLine(
                int(round(selected_nut_line.p0[0])),
                int(round(selected_nut_line.p0[1])),
                int(round(selected_nut_line.p1[0])),
                int(round(selected_nut_line.p1[1])),
            )

        if selected_reference_line is not None:
            painter.setPen(QPen(QColor(0, 218, 255), 5))
            painter.drawLine(
                int(round(selected_reference_line.p0[0])),
                int(round(selected_reference_line.p0[1])),
                int(round(selected_reference_line.p1[0])),
                int(round(selected_reference_line.p1[1])),
            )

        painter.setPen(QPen(QColor(255, 210, 60), 3))
        for x, y in selection_points:
            painter.drawEllipse(int(x) - 5, int(y) - 5, 10, 10)
        instruction = "너트 선을 클릭하세요"
        if selection_stage == "select_fret":
            instruction = f"실제 {reference_fret_index}프렛의 금속선을 클릭하세요"
        if manual_points:
            name = "너트" if selection_stage == "select_nut" else f"실제 {reference_fret_index}프렛"
            instruction = f"{name}의 양 끝점을 클릭하세요 ({len(selection_points) % 2 + 1}/2)"
        painter.setPen(QPen(QColor(255, 255, 255), 2))
        painter.drawText(24, 36, instruction)
        painter.end()
        return preview
