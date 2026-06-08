from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping

from PySide6.QtCore import QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget


DEFAULT_STEPS_PER_SCORE_LINE = 16


def _note_int(note: Mapping[str, object], key: str, default: int = 0) -> int:
    try:
        return int(note.get(key, default))
    except (TypeError, ValueError):
        return default


class TabSheetView(QWidget):
    clicked = Signal()
    step_clicked = Signal(int)

    def __init__(self, wrap_score_lines: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tab_data: list[dict[str, object]] = []
        self.selected_step = -1
        self.visible_score_line: int | None = None
        self.wrap_score_lines = wrap_score_lines
        self.fit_to_width = False
        self.step_spacing = 35
        self.row_height = 112
        self.string_top = 24
        self.string_spacing = 12
        self.note_radius = 8
        self.left_margin = 34
        self.right_margin = 40
        self._notes_by_step: dict[int, list[dict[str, object]]] = {}
        self._steps_by_line: dict[int, list[int]] = {}
        self._step_to_line: dict[int, int] = {}
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumSize(QSize(3000, 120))

    def set_data(self, data: list[Mapping[str, object]]) -> None:
        self.tab_data = [dict(item) for item in data]
        self._rebuild_index()
        self._refresh_minimum_size()
        self.update()

    def set_current_step(self, step: int) -> None:
        self.selected_step = int(step)
        self.update()

    def set_visible_score_line(self, score_line: int | None) -> None:
        self.visible_score_line = None if score_line is None else int(score_line)
        self._refresh_minimum_size()
        self.update()

    def set_wrap_score_lines(self, wrap_score_lines: bool) -> None:
        self.wrap_score_lines = wrap_score_lines
        self._refresh_minimum_size()
        self.update()

    def set_fit_to_width(self, fit_to_width: bool) -> None:
        self.fit_to_width = fit_to_width
        self._refresh_minimum_size()
        self.update()

    def score_line_for_step(self, step: int) -> int:
        return self._step_to_line.get(int(step), ((int(step) - 1) // DEFAULT_STEPS_PER_SCORE_LINE) + 1)

    def step_x_position(self, step: int) -> int:
        row_line = self.score_line_for_step(step) if self._uses_line_relative_steps() else None
        return self._x_for_step(step, row_line)

    def score_line_y_position(self, score_line: int) -> int:
        lines = self._visible_lines()
        try:
            row_index = lines.index(score_line)
        except ValueError:
            row_index = 0
        return 6 + (row_index * self.row_height)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            step = self._step_at_position(int(event.position().x()), int(event.position().y()))
            self.clicked.emit()
            if step is not None:
                self.step_clicked.emit(step)
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(0, 0, self.width(), self.height(), QColor("#fbfbfc"))

        if not self.tab_data:
            painter.setPen(QColor("#8e8e93"))
            painter.setFont(QFont("Arial", 12))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "TAB")
            return

        for row_index, score_line in enumerate(self._visible_lines()):
            row_top = 6 + (row_index * self.row_height)
            self._paint_score_line(painter, score_line, row_top)

    def _paint_score_line(self, painter: QPainter, score_line: int, row_top: int) -> None:
        line_steps = self._steps_for_line(score_line)
        if not line_steps:
            return

        painter.setPen(QColor("#8e8e93"))
        painter.setFont(QFont("Arial", 8, QFont.Weight.DemiBold))
        painter.drawText(8, row_top + 16, f"L{score_line}")

        for string_index in range(6):
            y = row_top + self.string_top + (string_index * self.string_spacing)
            painter.setPen(QPen(QColor("#d1d1d6"), 1, Qt.PenStyle.SolidLine))
            painter.drawLine(28, y, self.width() - 12, y)

        if self.selected_step in line_steps:
            selected_x = self._x_for_step(self.selected_step, score_line)
            painter.setPen(QPen(QColor("#ff3b30"), 1.5, Qt.PenStyle.SolidLine))
            painter.setBrush(QColor(255, 59, 48, 28))
            painter.drawRoundedRect(
                QRect(selected_x - 13, row_top + 12, 26, 82),
                4,
                4,
            )

        for step in line_steps:
            x_pos = self._x_for_step(step, score_line)
            notes = sorted(self._notes_by_step.get(step, []), key=lambda item: _note_int(item, "string", 1))
            for note in notes:
                string_number = _note_int(note, "string", 1)
                fret_number = _note_int(note, "fret", 0)
                y_pos = row_top + self.string_top + ((string_number - 1) * self.string_spacing)

                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor("#fbfbfc"))
                painter.drawRect(x_pos - 8, y_pos - 7, 16, 14)

                painter.setPen(QColor("#111111"))
                painter.setFont(QFont("Helvetica", 10, QFont.Weight.Bold))
                painter.drawText(
                    QRect(x_pos - self.note_radius, y_pos - self.note_radius, self.note_radius * 2, self.note_radius * 2),
                    Qt.AlignmentFlag.AlignCenter,
                    str(fret_number),
                )

    def _rebuild_index(self) -> None:
        notes_by_step: dict[int, list[dict[str, object]]] = defaultdict(list)
        steps_by_line: dict[int, set[int]] = defaultdict(set)
        step_to_line: dict[int, int] = {}

        for note in self.tab_data:
            step = max(1, _note_int(note, "step", 1))
            score_line = _note_int(
                note,
                "score_line",
                ((step - 1) // DEFAULT_STEPS_PER_SCORE_LINE) + 1,
            )
            score_line = max(1, score_line)
            notes_by_step[step].append(note)
            steps_by_line[score_line].add(step)
            step_to_line.setdefault(step, score_line)

        self._notes_by_step = dict(notes_by_step)
        self._steps_by_line = {line: sorted(steps) for line, steps in steps_by_line.items()}
        self._step_to_line = step_to_line

    def _visible_lines(self) -> list[int]:
        if self.visible_score_line is not None:
            return [self.visible_score_line]
        lines = sorted(self._steps_by_line)
        if self.wrap_score_lines:
            return lines
        return [lines[0]] if lines else [1]

    def _steps_for_line(self, score_line: int) -> list[int]:
        if self.visible_score_line is None and not self.wrap_score_lines:
            return sorted(self._notes_by_step)
        return self._steps_by_line.get(score_line, [])

    def _uses_line_relative_steps(self) -> bool:
        return self.visible_score_line is not None or self.wrap_score_lines

    def _x_for_step(self, step: int, score_line: int | None) -> int:
        if self._uses_line_relative_steps() and score_line is not None:
            line_steps = self._steps_for_line(score_line)
            first_step = min(line_steps) if line_steps else step
            if self.fit_to_width and line_steps:
                try:
                    step_index = line_steps.index(step)
                except ValueError:
                    step_index = max(0, step - first_step)
                available_width = max(1, self.width() - self.left_margin - self.right_margin)
                spacing = available_width / max(1, len(line_steps) + 1)
                return int(round(self.left_margin + ((step_index + 1) * spacing)))
            return self.left_margin + ((step - first_step + 1) * self.step_spacing)
        return self.left_margin + (step * self.step_spacing)

    def _step_at_position(self, x: int, y: int) -> int | None:
        for score_line in self._visible_lines():
            line_top = self.score_line_y_position(score_line)
            if not (line_top <= y <= line_top + self.row_height):
                continue
            for step in self._steps_for_line(score_line):
                if abs(x - self._x_for_step(step, score_line)) <= self.step_spacing // 2:
                    return step
        return None

    def _refresh_minimum_size(self) -> None:
        lines = self._visible_lines()
        row_count = max(1, len(lines))
        height = max(120, 12 + (row_count * self.row_height))
        max_step_count = 0
        for score_line in lines:
            max_step_count = max(max_step_count, len(self._steps_for_line(score_line)))

        if self.fit_to_width and self._uses_line_relative_steps():
            width = 240
        elif self._uses_line_relative_steps():
            width = max(620, self.left_margin + self.right_margin + ((max_step_count + 1) * self.step_spacing))
        else:
            max_step = max(self._notes_by_step, default=0)
            width = max(3000, self.left_margin + self.right_margin + ((max_step + 1) * self.step_spacing))
        self.setMinimumSize(QSize(width, height))


class PracticeTabStrip(QFrame):
    expanded_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.current_step = 1
        self.current_score_line = 1
        self.is_expanded = False
        self.collapsed_height = 132
        self.expanded_height = 360

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            """
            QFrame {
                background-color: #fbfbfc;
                border: 1px solid #d1d1d6;
                border-radius: 6px;
            }
            QLabel {
                background-color: transparent;
                border: none;
                color: #303033;
            }
            QScrollArea {
                background-color: #fbfbfc;
                border: none;
            }
            """
        )

        self.title_label = QLabel("TAB")
        self.title_label.setStyleSheet("font-size: 12px; font-weight: 700;")
        self.line_label = QLabel("LINE 1")
        self.line_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.line_label.setStyleSheet("font-size: 11px; color: #6e6e73;")

        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(10, 4, 10, 0)
        header_layout.addWidget(self.title_label)
        header_layout.addStretch(1)
        header_layout.addWidget(self.line_label)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.tab_view = TabSheetView()
        self.tab_view.set_fit_to_width(True)
        self.tab_view.clicked.connect(self.toggle_expanded)
        self.scroll_area.setWidget(self.tab_view)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(header)
        layout.addWidget(self.scroll_area, stretch=1)

        self.setFixedHeight(self.collapsed_height)
        self._apply_expanded_state()

    def set_data(self, data: list[Mapping[str, object]]) -> None:
        self.tab_view.set_data(data)
        self.set_current_step(self.current_step)

    def set_current_step(self, step: int) -> None:
        previous_score_line = self.current_score_line
        self.current_step = int(step)
        self.current_score_line = self.tab_view.score_line_for_step(self.current_step)
        self.tab_view.set_current_step(self.current_step)
        self._apply_expanded_state()
        if previous_score_line != self.current_score_line:
            QTimer.singleShot(0, self._sync_scroll_position)

    def toggle_expanded(self) -> None:
        self.is_expanded = not self.is_expanded
        self._apply_expanded_state()
        self.expanded_changed.emit(self.is_expanded)
        QTimer.singleShot(0, self._sync_scroll_position)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_expanded()
            event.accept()
            return
        super().mousePressEvent(event)

    def _apply_expanded_state(self) -> None:
        if self.is_expanded:
            self.setFixedHeight(self.expanded_height)
            self.tab_view.set_wrap_score_lines(True)
            self.tab_view.set_visible_score_line(None)
            self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.line_label.setText("ALL")
            return

        self.setFixedHeight(self.collapsed_height)
        self.tab_view.set_wrap_score_lines(False)
        self.tab_view.set_visible_score_line(self.current_score_line)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.line_label.setText(f"LINE {self.current_score_line}")

    def _sync_scroll_position(self) -> None:
        self.scroll_area.horizontalScrollBar().setValue(0)

        if self.is_expanded:
            y_position = self.tab_view.score_line_y_position(self.current_score_line)
            vertical_bar = self.scroll_area.verticalScrollBar()
            vertical_bar.setValue(max(0, int(y_position - (self.scroll_area.viewport().height() / 2))))
        else:
            self.scroll_area.verticalScrollBar().setValue(0)
