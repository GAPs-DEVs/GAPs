import json
import os
import tempfile
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QMessageBox,
    QFileDialog,
    QSpinBox,
    QGroupBox,
    QScrollArea,
    QFrame,
)
from PySide6.QtCore import Qt, QRect, Signal, QPointF
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QBrush, QPixmap, QPolygonF

from app.ui.tab_sheet_view import TAB_SCROLLBAR_QSS, TabSheetView, scrollbar_qss


def make_arrow_png(direction: str, color="#374151", size=12) -> str:
    """스핀박스용 위/아래 화살표 PNG를 임시 폴더에 만들고 경로(슬래시 형식)를 반환"""
    path = os.path.join(tempfile.gettempdir(), f"spin_arrow_{direction}_{color.strip('#')}_{size}.png")
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    m = 2  # 여백
    if direction == "up":
        pts = [QPointF(size / 2, m), QPointF(size - m, size - m - 1), QPointF(m, size - m - 1)]
    else:
        pts = [QPointF(m, m + 1), QPointF(size - m, m + 1), QPointF(size / 2, size - m)]
    p.drawPolygon(QPolygonF(pts))
    p.end()
    pix.save(path, "PNG")
    return path.replace("\\", "/")  # QSS url()은 반드시 슬래시


class FretboardMonitor(QWidget):
    note_position_changed = Signal(int, int)  # (변경된 string, 변경된 fret)

    def __init__(self):
        super().__init__()
        self.setFixedHeight(140)
        self.active_notes = []

        self.margin_left = 42
        self.margin_right = 24
        self.fret_count = 15

        self.setMouseTracking(True)
        self.is_dragging = False

    def update_notes(self, notes_list):
        self.active_notes = notes_list
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        width = self.width()
        height = self.height()

        # 지판 바디 (정제된 슬레이트 다크 톤)
        painter.fillRect(0, 0, width, height, QColor("#1a1d24"))

        board_width = width - self.margin_left - self.margin_right
        board_height = height - 40

        self.string_spacing = board_height / 5
        self.fret_spacing = board_width / self.fret_count

        # 포지션 마커 도트 (3, 5, 7, 9, 12, 15)
        marker_frets = [3, 5, 7, 9, 12, 15]
        painter.setBrush(QColor("#2d3340"))
        painter.setPen(Qt.NoPen)
        center_y = 20 + (board_height / 2)
        for mf in marker_frets:
            if mf <= self.fret_count:
                dot_x = self.margin_left + (mf * self.fret_spacing) - (self.fret_spacing / 2)
                if mf == 12:
                    painter.drawEllipse(int(dot_x - 3), int(center_y - 14), 6, 6)
                    painter.drawEllipse(int(dot_x - 3), int(center_y + 8), 6, 6)
                else:
                    painter.drawEllipse(int(dot_x - 3.5), int(center_y - 3.5), 7, 7)

        # 6개 현 렌더링 (1번줄~6번줄)
        for i in range(6):
            y = 20 + (i * self.string_spacing)
            thickness = 1 + (5 - i) * 0.4
            painter.setPen(QPen(QColor("#4b5563"), thickness, Qt.SolidLine))
            painter.drawLine(self.margin_left, int(y), int(width - self.margin_right), int(y))

            # 줄 번호 인디케이터
            painter.setPen(QColor("#9ca3af"))
            painter.setFont(QFont("-apple-system", 8, QFont.Bold))
            painter.drawText(14, int(y + 3), f"{i+1}")

        # 프렛 와이어 및 번호 렌더링
        for i in range(self.fret_count + 1):
            x = self.margin_left + (i * self.fret_spacing)
            is_nut = (i == 0)
            wire_pen = QPen(QColor("#94a3b8") if is_nut else QColor("#374151"), 3 if is_nut else 1, Qt.SolidLine)
            painter.setPen(wire_pen)
            painter.drawLine(int(x), 20, int(x), int(20 + board_height))

            if i > 0:
                painter.setPen(QColor("#6b7280"))
                painter.setFont(QFont("-apple-system", 7))
                painter.drawText(int(x - (self.fret_spacing / 2) - 4), height - 7, str(i))

        # 활성화된 음표 렌더링
        for note in self.active_notes:
            s_num = note.get("string")
            f_num = note.get("fret")
            if s_num is None or f_num is None:
                continue

            target_y = 20 + ((s_num - 1) * self.string_spacing)
            if f_num == 0:
                target_x = self.margin_left - 14
                badge_color = QColor("#2563eb")  # 개방현
            else:
                target_x = self.margin_left + (f_num * self.fret_spacing) - (self.fret_spacing / 2)
                badge_color = QColor("#dc2626")  # 프렛 운지

            radius = 10
            painter.setBrush(badge_color)
            painter.setPen(QPen(QColor("#ffffff"), 1.5))
            painter.drawEllipse(int(target_x - radius), int(target_y - radius), radius * 2, radius * 2)

            painter.setPen(QColor("#ffffff"))
            painter.setFont(QFont("-apple-system", 8, QFont.Bold))
            painter.drawText(
                QRect(int(target_x - radius), int(target_y - radius), radius * 2, radius * 2),
                Qt.AlignCenter,
                str(f_num),
            )

    def calculate_string_fret(self, x, y):
        width = self.width()
        relative_y = y - 20
        string_idx = int(round(relative_y / self.string_spacing)) + 1
        string_idx = max(1, min(6, string_idx))

        if x < self.margin_left:
            fret_idx = 0
        else:
            relative_x = x - self.margin_left
            fret_idx = int(relative_x / self.fret_spacing) + 1
            fret_idx = max(0, min(self.fret_count, fret_idx))

        return string_idx, fret_idx

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.is_dragging = True
            string_idx, fret_idx = self.calculate_string_fret(event.position().x(), event.position().y())
            self.note_position_changed.emit(string_idx, fret_idx)

    def mouseMoveEvent(self, event):
        if self.is_dragging and (event.buttons() & Qt.LeftButton):
            string_idx, fret_idx = self.calculate_string_fret(event.position().x(), event.position().y())
            self.note_position_changed.emit(string_idx, fret_idx)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.is_dragging = False


class AdminPage(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.tab_data = []
        self.current_file_path = None
        self.init_ui()

    def init_ui(self):
        # 스핀박스 화살표 이미지 생성 (QSS의 UP_ARROW / DOWN_ARROW 자리에 경로가 들어감)
        up_arrow = make_arrow_png("up")
        down_arrow = make_arrow_png("down")

        # ── [LoginPage / SelectPage와 통일된 미니멀 라이트 테마 QSS] ──
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("""
            QWidget {
                font-family: -apple-system, BlinkMacSystemFont, 'Pretendard', 'Apple SD Gothic Neo', 'Segoe UI', sans-serif;
                background-color: #f8f9fa;
                color: #212529;
            }

            QLabel {
                background-color: transparent;
                color: #212529;
            }

            QFrame#card_panel {
                background-color: #ffffff;
                border: 1px solid #e9ecef;
                border-radius: 12px;
            }

            /* 테이블 위젯 스타일 */
            QTableWidget {
                background-color: #ffffff;
                border: 1px solid #e9ecef;
                border-radius: 8px;
                gridline-color: #f1f3f5;
                font-size: 13px;
                outline: none;
            }
            QTableWidget::item {
                padding: 6px;
                border-bottom: 1px solid #f1f3f5;
            }
            QTableWidget::item:selected {
                background-color: #f1f5f9;
                color: #111827;
                font-weight: 600;
            }
            QHeaderView::section {
                background-color: #f8f9fa;
                color: #4b5563;
                font-size: 12px;
                font-weight: 600;
                padding: 8px;
                border: none;
                border-bottom: 1.5px solid #e9ecef;
            }

            /* 스핀박스 */
            QSpinBox {
                background-color: #ffffff;
                border: 1px solid #ced4da;
                border-radius: 6px;
                padding: 6px 30px 6px 10px;
                font-size: 13px;
                color: #212529;
                min-width: 50px;
            }
            QSpinBox:focus {
                border: 1px solid #2563eb;
            }
            QSpinBox::up-button {
                subcontrol-origin: border;
                subcontrol-position: top right;
                width: 24px;
                border-left: 1px solid #e9ecef;
                border-top-right-radius: 6px;
                background-color: #f8f9fa;
            }
            QSpinBox::down-button {
                subcontrol-origin: border;
                subcontrol-position: bottom right;
                width: 24px;
                border-left: 1px solid #e9ecef;
                border-top: 1px solid #e9ecef;
                border-bottom-right-radius: 6px;
                background-color: #f8f9fa;
            }
            QSpinBox::up-button:hover, QSpinBox::down-button:hover {
                background-color: #e9ecef;
            }
            QSpinBox::up-button:pressed, QSpinBox::down-button:pressed {
                background-color: #dee2e6;
            }
            QSpinBox::up-arrow {
                image: url(UP_ARROW);
                width: 12px;
                height: 12px;
            }
            QSpinBox::down-arrow {
                image: url(DOWN_ARROW);
                width: 12px;
                height: 12px;
            }

            /* 그룹 박스 */
            QGroupBox {
                font-size: 13px;
                font-weight: 700;
                color: #374151;
                border: 1px solid #e9ecef;
                border-radius: 8px;
                margin-top: 10px;
                padding-top: 18px;
                background-color: #ffffff;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 0 8px;
                left: 12px;
                background-color: #ffffff;
            }

            /* 보조 버튼 기본 */
            QPushButton {
                background-color: #ffffff;
                border: 1px solid #ced4da;
                border-radius: 6px;
                font-size: 13px;
                font-weight: 500;
                color: #374151;
                padding: 9px 12px;
                min-height: 18px;
            }
            QPushButton:hover {
                background-color: #f8f9fa;
                border-color: #adb5bd;
            }
            QPushButton:pressed {
                background-color: #e9ecef;
            }

            /* 액션 버튼 (저장) */
            QPushButton#btn_save {
                background-color: #111827;
                color: #ffffff;
                border: 1px solid #111827;
                font-weight: 600;
            }
            QPushButton#btn_save:hover {
                background-color: #27272a;
                border-color: #27272a;
            }

            /* 위험 버튼 (삭제) */
            QPushButton#btn_delete {
                background-color: #ffffff;
                color: #dc2626;
                border: 1px solid #fecaca;
            }
            QPushButton#btn_delete:hover {
                background-color: #fef2f2;
                border-color: #fca5a5;
            }
        """.replace("UP_ARROW", up_arrow).replace("DOWN_ARROW", down_arrow))

        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(24, 24, 24, 24)
        main_layout.setSpacing(20)

        # ── [좌측 메인: 모니터 및 데이터 테이블 영역] ──
        left_card = QFrame()
        left_card.setObjectName("card_panel")
        left_layout = QVBoxLayout(left_card)
        left_layout.setContentsMargins(20, 20, 20, 20)
        left_layout.setSpacing(12)

        # 상단 헤더 및 파일 상태
        header_box = QHBoxLayout()
        header_text_box = QVBoxLayout()
        header_text_box.setSpacing(2)

        lbl_sub = QLabel("EDITOR")
        lbl_sub.setStyleSheet("font-size: 11px; font-weight: 700; color: #6b7280; letter-spacing: 1.5px;")
        header_text_box.addWidget(lbl_sub)

        self.title_label = QLabel("악보 정밀 에디터")
        self.title_label.setStyleSheet("font-size: 18px; font-weight: 700; color: #111827; letter-spacing: -0.3px;")
        header_text_box.addWidget(self.title_label)

        header_box.addLayout(header_text_box)
        header_box.addStretch(1)

        self.file_status = QLabel("로드된 파일 없음")
        self.file_status.setStyleSheet("""
            background-color: #f1f5f9;
            color: #475569;
            font-size: 12px;
            font-weight: 500;
            padding: 5px 12px;
            border-radius: 6px;
            border: 1px solid #e2e8f0;
        """)
        header_box.addWidget(self.file_status, alignment=Qt.AlignVCenter)
        left_layout.addLayout(header_box)

        # 상단 시각화 듀얼 모니터
        monitor_layout = QHBoxLayout()
        monitor_layout.setSpacing(12)

        self.fretboard_monitor = FretboardMonitor()
        self.fretboard_monitor.setStyleSheet("border-radius: 8px;")
        self.fretboard_monitor.note_position_changed.connect(self.on_fretboard_dragged)

        self.scroll_area = QScrollArea()
        self.scroll_area.setFixedHeight(140)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("""
            QScrollArea {
                border: 1px solid #e9ecef;
                border-radius: 8px;
                background-color: #fbfbfc;
            }
        """ + TAB_SCROLLBAR_QSS)

        self.tab_sheet_view = TabSheetView()
        self.scroll_area.setWidget(self.tab_sheet_view)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        monitor_layout.addWidget(self.fretboard_monitor, stretch=4)
        monitor_layout.addWidget(self.scroll_area, stretch=5)
        left_layout.addLayout(monitor_layout)

        # 하단 정밀 테이블
        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["연주 순서 (Step)", "기타 줄 (String)", "프렛 번호 (Fret)"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setStyleSheet(scrollbar_qss("#ffffff"))
        self.table.itemSelectionChanged.connect(self.on_selection_changed)
        self.table.cellChanged.connect(self.on_cell_changed)
        left_layout.addWidget(self.table, stretch=1)

        # ── [우측 사이드: 편집 제어 패널] ──
        right_card = QFrame()
        right_card.setObjectName("card_panel")
        right_card.setFixedWidth(310)
        right_layout = QVBoxLayout(right_card)
        right_layout.setContentsMargins(18, 20, 18, 20)
        right_layout.setSpacing(14)

        side_header = QVBoxLayout()
        side_header.setSpacing(2)
        lbl_ctrl_sub = QLabel("CONTROLS")
        lbl_ctrl_sub.setStyleSheet("font-size: 11px; font-weight: 700; color: #6b7280; letter-spacing: 1.5px;")
        side_header.addWidget(lbl_ctrl_sub)

        lbl_ctrl_title = QLabel("편집 도구")
        lbl_ctrl_title.setStyleSheet("font-size: 16px; font-weight: 700; color: #111827; letter-spacing: -0.3px;")
        side_header.addWidget(lbl_ctrl_title)
        right_layout.addLayout(side_header)

        # 파일 입출력 그룹
        io_group = QGroupBox("파일 관리")
        io_layout = QVBoxLayout(io_group)
        io_layout.setContentsMargins(12, 16, 12, 12)
        io_layout.setSpacing(8)

        btn_load = QPushButton("다른 JSON 파일 열기")
        btn_save = QPushButton("수정 사항 저장")
        btn_save.setObjectName("btn_save")

        io_layout.addWidget(btn_load)
        io_layout.addWidget(btn_save)
        right_layout.addWidget(io_group)

        # 행 삽입/삭제 제어 그룹
        edit_group = QGroupBox("음표 데이터 조작")
        edit_layout = QVBoxLayout(edit_group)
        edit_layout.setContentsMargins(12, 16, 12, 12)
        edit_layout.setSpacing(10)

        form_layout = QHBoxLayout()
        form_layout.setSpacing(6)
        self.spin_string = QSpinBox()
        self.spin_string.setRange(1, 6)
        self.spin_fret = QSpinBox()
        self.spin_fret.setRange(0, 24)

        lbl_s = QLabel("줄:")
        lbl_s.setStyleSheet("font-weight: 600; font-size: 13px; color: #374151;")
        lbl_f = QLabel("프렛:")
        lbl_f.setStyleSheet("font-weight: 600; font-size: 13px; color: #374151;")

        form_layout.addWidget(lbl_s)
        form_layout.addWidget(self.spin_string, stretch=1)
        form_layout.addWidget(lbl_f)
        form_layout.addWidget(self.spin_fret, stretch=1)
        edit_layout.addLayout(form_layout)

        btn_insert = QPushButton("선택 행 아래 신규 음표 생성")
        btn_delete = QPushButton("선택 음표 데이터 삭제")
        btn_delete.setObjectName("btn_delete")

        edit_layout.addWidget(btn_insert)
        edit_layout.addWidget(btn_delete)
        right_layout.addWidget(edit_group)

        right_layout.addStretch(1)

        for btn in [btn_load, btn_save, btn_insert, btn_delete]:
            btn.setCursor(Qt.PointingHandCursor)

        btn_load.clicked.connect(self.load_json_file)
        btn_save.clicked.connect(self.save_json_file)
        btn_insert.clicked.connect(self.insert_row_data)
        btn_delete.clicked.connect(self.delete_selected_row)

        main_layout.addWidget(left_card, stretch=3)
        main_layout.addWidget(right_card, stretch=1)

    def on_fretboard_dragged(self, string_num, fret_num):
        if not self.tab_data:
            return
        current_row = self.table.currentRow()
        if current_row < 0:
            return

        open_string_map = getattr(self, "open_string_midi", {1: 64, 2: 59, 3: 55, 4: 50, 5: 45, 6: 40})
        calculated_midi = open_string_map.get(string_num, 40) + fret_num

        self.table.blockSignals(True)
        self.tab_data[current_row]["string"] = string_num
        self.tab_data[current_row]["fret"] = fret_num
        self.tab_data[current_row]["midi_pitch"] = calculated_midi

        self.table.item(current_row, 1).setText(str(string_num))
        self.table.item(current_row, 2).setText(str(fret_num))
        self.table.blockSignals(False)

        target_step = self.tab_data[current_row].get("step", -1)
        harmonics = [item for item in self.tab_data if item.get("step") == target_step]
        self.fretboard_monitor.update_notes(harmonics)
        self.tab_sheet_view.set_current_step(target_step)

    def on_selection_changed(self):
        selected_ranges = self.table.selectedRanges()
        if not selected_ranges or not self.tab_data:
            return

        current_row = selected_ranges[0].topRow()
        if current_row >= len(self.tab_data):
            return

        target_step = self.tab_data[current_row].get("step", -1)
        harmonics = [item for item in self.tab_data if item.get("step") == target_step]

        self.fretboard_monitor.update_notes(harmonics)
        self.tab_sheet_view.set_current_step(target_step)

        x_target = 30 + (target_step * 35)
        self.scroll_area.horizontalScrollBar().setValue(x_target - 150)

    def insert_row_data(self):
        if not self.current_file_path:
            return
        current_row = self.table.currentRow()
        if current_row < 0:
            current_row = len(self.tab_data) - 1

        if self.tab_data and current_row >= 0:
            new_item = self.tab_data[current_row].copy()
            new_item["string"] = self.spin_string.value()
            new_item["fret"] = self.spin_fret.value()

            insert_idx = current_row + 1
            self.tab_data.insert(insert_idx, new_item)

            self.table.blockSignals(True)
            self.refresh_table()
            self.table.blockSignals(False)

            self.table.selectRow(insert_idx)

    def load_json_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "파싱 결과 JSON 불러오기", "", "JSON Files (*.json)")
        if not file_path:
            return
        self.current_file_path = file_path
        self.file_status.setText(f"파일: {os.path.basename(file_path)}")
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                self.tab_data = json.load(f)
            self.tab_data.sort(key=lambda x: (x.get("step", 0), x.get("string", 0)))

            self.tab_sheet_view.set_data(self.tab_data)
            self.scroll_area.setWidget(self.tab_sheet_view)
            self.refresh_table()
        except Exception as e:
            QMessageBox.critical(self, "오류", f"로드 실패: {e}")

    def refresh_table(self):
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.tab_data))
        for row, item in enumerate(self.tab_data):
            item_step = QTableWidgetItem(str(item.get("step", "")))
            item_string = QTableWidgetItem(str(item.get("string", "")))
            item_fret = QTableWidgetItem(str(item.get("fret", "")))

            item_step.setTextAlignment(Qt.AlignCenter)
            item_string.setTextAlignment(Qt.AlignCenter)
            item_fret.setTextAlignment(Qt.AlignCenter)

            self.table.setItem(row, 0, item_step)
            self.table.setItem(row, 1, item_string)
            self.table.setItem(row, 2, item_fret)
        self.table.blockSignals(False)
        if self.tab_data:
            self.table.selectRow(0)

    def on_cell_changed(self, row, column):
        if row >= len(self.tab_data):
            return
        item = self.table.item(row, column)
        if not item:
            return
        try:
            val = int(item.text())
            if column == 0:
                self.tab_data[row]["step"] = val
            elif column == 1 and 1 <= val <= 6:
                self.tab_data[row]["string"] = val
            elif column == 2 and 0 <= val <= 24:
                self.tab_data[row]["fret"] = val

            if column in [1, 2]:
                open_string_map = getattr(self, "open_string_midi", {1: 64, 2: 59, 3: 55, 4: 50, 5: 45, 6: 40})
                s_num = self.tab_data[row].get("string", 6)
                f_num = self.tab_data[row].get("fret", 0)
                self.tab_data[row]["midi_pitch"] = open_string_map.get(s_num, 40) + f_num

            self.table.blockSignals(True)
            self.tab_data.sort(key=lambda x: (x.get("step", 0), x.get("string", 0)))
            self.tab_sheet_view.set_data(self.tab_data)
            self.refresh_table()
            self.table.blockSignals(False)
        except ValueError:
            self.table.blockSignals(True)
            self.refresh_table()
            self.table.blockSignals(False)

    def delete_selected_row(self):
        row = self.table.currentRow()
        if row < 0:
            return
        del self.tab_data[row]
        self.tab_sheet_view.set_data(self.tab_data)
        self.refresh_table()

    def save_json_file(self):
        if not self.current_file_path:
            return
        try:
            with open(self.current_file_path, "w", encoding="utf-8") as f:
                json.dump(self.tab_data, f, indent=2, ensure_ascii=False)
            QMessageBox.information(self, "저장 완료", "수정 사항이 저장되었습니다.")
        except Exception as e:
            QMessageBox.critical(self, "오류", f"저장 실패: {e}")