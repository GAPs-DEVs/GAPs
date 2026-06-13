import json
import os
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
                             QLabel, QTableWidget, QTableWidgetItem, QHeaderView, 
                             QMessageBox, QFileDialog, QSpinBox, QGroupBox, QScrollArea)
from PySide6.QtCore import Qt, QRect, Signal
from PySide6.QtGui import QPainter, QColor, QPen, QFont

from app.ui.tab_sheet_view import TabSheetView

# 인터랙티브 지판 모니터 

class FretboardMonitor(QWidget):
    # 마우스 조작으로 줄과 프렛이 변경되었을 때 관리자 페이지로 알려줄 시그널 선언
    note_position_changed = Signal(int, int) # (변경된 string, 변경된 fret)

    def __init__(self):
        super().__init__()
        self.setFixedHeight(140)
        self.active_notes = []
        
        # 좌표 계산을 위한 내부 레이아웃 마진 변수 표준화
        self.margin_left = 40
        self.margin_right = 20
        self.fret_count = 15
        
        # 마우스 드래그 추적 활성화
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
        painter.fillRect(0, 0, width, height, QColor("#2d2d2d")) 

        board_width = width - self.margin_left - self.margin_right
        board_height = height - 40
        
        self.string_spacing = board_height / 5
        self.fret_spacing = board_width / self.fret_count
        
        # 6현선 그리기
        for i in range(6):
            y = 20 + (i * self.string_spacing)
            thickness = 1 + (5 - i) * 0.5
            painter.setPen(QPen(QColor("#555555"), thickness, Qt.SolidLine))
            painter.drawLine(self.margin_left, y, width - self.margin_right, y)
            painter.setPen(QColor("#ffffff"))
            painter.setFont(QFont("Arial", 8, QFont.Bold))
            painter.drawText(12, y + 4, f"{i+1}")

        # 15프렛 뼈대 그리기
        for i in range(self.fret_count + 1):
            x = self.margin_left + (i * self.fret_spacing)
            painter.setPen(QPen(QColor("#444444"), 1 if i>0 else 3, Qt.SolidLine))
            painter.drawLine(x, 20, x, 20 + board_height)
            if i > 0:
                painter.setPen(QColor("#aaaaaa"))
                painter.setFont(QFont("Arial", 7))
                painter.drawText(x - (self.fret_spacing/2) - 3, height - 5, str(i))

        # 활성화된 음표 렌더링
        for note in self.active_notes:
            s_num = note.get('string')
            f_num = note.get('fret')
            if s_num is None or f_num is None: continue
            
            target_y = 20 + ((s_num - 1) * self.string_spacing)
            if f_num == 0:
                target_x = self.margin_left - 12
                color = QColor("#007aff")
            else:
                target_x = self.margin_left + (f_num * self.fret_spacing) - (self.fret_spacing / 2)
                color = QColor("#ff453a")

            painter.setBrush(color)
            painter.setPen(Qt.NoPen)
            radius = 10
            painter.drawEllipse(target_x - radius, target_y - radius, radius * 2, radius * 2)

            painter.setPen(QColor("#ffffff"))
            painter.setFont(QFont("Arial", 9, QFont.Bold))
            painter.drawText(QRect(target_x - radius, target_y - radius, radius * 2, radius * 2), Qt.AlignCenter, str(f_num))

    # 마우스 좌표를 기타 줄/프렛 정수로 맵핑
    def calculate_string_fret(self, x, y):
        width = self.width()
        board_width = width - self.margin_left - self.margin_right
        
        # 1. 몇 번 줄인지 계산 
        relative_y = y - 20
        string_idx = int(round(relative_y / self.string_spacing)) + 1
        string_idx = max(1, min(6, string_idx)) # 1~6번 줄 방어선
        
        # 2. 몇 번 프렛인지 계산
        if x < self.margin_left:
            fret_idx = 0 # 개방현
        else:
            relative_x = x - self.margin_left
            fret_idx = int(relative_x / self.fret_spacing) + 1
            fret_idx = max(0, min(self.fret_count, fret_idx)) # 0~15프렛 방어선
            
        return string_idx, fret_idx

    # 마우스 클릭 이벤트 처리
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.is_dragging = True
            string_idx, fret_idx = self.calculate_string_fret(event.position().x(), event.position().y())
            self.note_position_changed.emit(string_idx, fret_idx)

    # 마우스 드래그 이벤트 처리
    def mouseMoveEvent(self, event):
        if self.is_dragging and (event.buttons() & Qt.LeftButton):
            string_idx, fret_idx = self.calculate_string_fret(event.position().x(), event.position().y())
            self.note_position_changed.emit(string_idx, fret_idx)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.is_dragging = False


# 관리자 에디터 페이지 

class AdminPage(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.tab_data = []
        self.current_file_path = None
        self.init_ui()
        
    def init_ui(self):
        self.setStyleSheet("""
            QWidget { font-family: 'Malgun Gothic', 'Arial'; }
            QGroupBox { font-size: 13px; font-weight: bold; margin-top: 15px; padding-top: 20px; }
            QPushButton { 
                font-size: 14px; 
                font-weight: bold; 
                padding: 12px 10px; 
                border-radius: 6px;
                min-height: 20px;
            }
            QSpinBox { font-size: 14px; padding: 6px; min-width: 60px; }
            QLabel { font-size: 13px; }
        """)

        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(15)

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)
        
        self.title_label = QLabel("<h2>악보 수정 에디터</h2>")
        self.file_status = QLabel("📂 로드된 파일: 없음")
        self.file_status.setStyleSheet("color: #555555; font-style: italic;")
        
        # 상단 시각화 모니터
        monitor_layout = QHBoxLayout()
        monitor_layout.setSpacing(10)
        
        self.fretboard_monitor = FretboardMonitor()
        self.fretboard_monitor.setStyleSheet("border: 1px solid #cccccc; border-radius: 6px;")
        
        # 💡 지판에서 발생한 마우스 드래그/클릭 이벤트를 데이터 수정 함수와 연결!!
        self.fretboard_monitor.note_position_changed.connect(self.on_fretboard_dragged)
        
        self.scroll_area = QScrollArea()
        self.scroll_area.setFixedHeight(140)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("border: 1px solid #cccccc; border-radius: 6px; background-color: #f9f9f9;")
        
        self.tab_sheet_view = TabSheetView()
        self.scroll_area.setWidget(self.tab_sheet_view)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        
        monitor_layout.addWidget(self.fretboard_monitor, stretch=4)
        monitor_layout.addWidget(self.scroll_area, stretch=5)
        
        # 테이블 설정
        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["연주 순서 (Step)", "기타 줄 (String)", "프렛 번호 (Fret)"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setStyleSheet("QTableWidget { border: 1px solid #cccccc; }")
        
        self.table.itemSelectionChanged.connect(self.on_selection_changed)
        self.table.cellChanged.connect(self.on_cell_changed)
        
        left_layout.addWidget(self.title_label)
        left_layout.addWidget(self.file_status)
        left_layout.addLayout(monitor_layout)
        left_layout.addWidget(self.table)
        
        # 우측 제어 패널
        right_widget = QWidget()
        right_widget.setFixedWidth(310) 
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        io_group = QGroupBox("파일 시스템")
        io_layout = QVBoxLayout(io_group)
        io_layout.setContentsMargins(12, 18, 12, 12)
        io_layout.setSpacing(10)
        
        btn_load = QPushButton("JSON 파일 로드")
        btn_save = QPushButton("수정내용 반영 및 저장")
        
        btn_load.setStyleSheet("""
            QPushButton { background-color: #e1e1e1; color: #333333; border: 1px solid #cccccc; }
            QPushButton:hover { background-color: #d4d4d4; }
        """)
        btn_save.setStyleSheet("""
            QPushButton { background-color: #00875a; color: white; border: none; }
            QPushButton:hover { background-color: #00a36c; }
        """)
        io_layout.addWidget(btn_load)
        io_layout.addWidget(btn_save)
        
        edit_group = QGroupBox("정밀 행 삽입 제어")
        edit_layout = QVBoxLayout(edit_group)
        edit_layout.setContentsMargins(12, 18, 12, 12)
        edit_layout.setSpacing(12)
        
        form_layout = QHBoxLayout()
        form_layout.setSpacing(8)
        self.spin_string = QSpinBox()
        self.spin_string.setRange(1, 6)
        self.spin_fret = QSpinBox()
        self.spin_fret.setRange(0, 24)
        
        lbl_s = QLabel("줄:")
        lbl_s.setStyleSheet("font-weight: bold; font-size: 14px;")
        lbl_f = QLabel("프렛:")
        lbl_f.setStyleSheet("font-weight: bold; font-size: 14px;")
        
        form_layout.addWidget(lbl_s)
        form_layout.addWidget(self.spin_string, stretch=1)
        form_layout.addWidget(lbl_f)
        form_layout.addWidget(self.spin_fret, stretch=1)
        
        btn_insert = QPushButton("선택 행 아래 생성")
        btn_insert.setStyleSheet("""
            QPushButton { background-color: #007aff; color: white; border: none; }
            QPushButton:hover { background-color: #268fff; }
        """)
        btn_delete = QPushButton("선택 행 데이터 삭제")
        btn_delete.setStyleSheet("""
            QPushButton { background-color: #de350b; color: white; border: none; }
            QPushButton:hover { background-color: #ff5630; }
        """)
        edit_layout.addLayout(form_layout)
        edit_layout.addWidget(btn_insert)
        edit_layout.addWidget(btn_delete)
        
        nav_widget = QWidget()
        nav_layout = QVBoxLayout(nav_widget)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        
        btn_back = QPushButton("메인 화면으로 복귀")
        btn_back.setFixedHeight(45) 
        btn_back.setStyleSheet("""
            QPushButton { background-color: #f0f0f0; color: #444444; border: 1px solid #cccccc; }
            QPushButton:hover { background-color: #e2e2e2; border-color: #bbbbbb; }
        """)
        nav_layout.addWidget(btn_back)
        
        for btn in [btn_load, btn_save, btn_insert, btn_delete, btn_back]:
            btn.setCursor(Qt.PointingHandCursor)
            
        btn_load.clicked.connect(self.load_json_file)
        btn_save.clicked.connect(self.save_json_file)
        btn_insert.clicked.connect(self.insert_row_data)
        btn_delete.clicked.connect(self.delete_selected_row)
        btn_back.clicked.connect(self.go_back)
        
        right_layout.addWidget(io_group)
        right_layout.addWidget(edit_group)
        right_layout.addStretch(1) 
        right_layout.addWidget(nav_widget) 

        main_layout.addWidget(left_widget, stretch=3)
        main_layout.addWidget(right_widget, stretch=1)

    
    # 지판 드래그 앤 드롭
    # def on_fretboard_dragged(self, string_num, fret_num):
    #     if not self.tab_data: return
    #     current_row = self.table.currentRow()
    #     if current_row < 0: return 
        
    #     # 1. 테이블 차단 해제 후 데이터 교체
    #     self.table.blockSignals(True)
    #     self.tab_data[current_row]['string'] = string_num
    #     self.tab_data[current_row]['fret'] = fret_num
        
    #     # 2. 하단 테이블 셀 텍스트 즉시 동기화
    #     self.table.item(current_row, 1).setText(str(string_num))
    #     self.table.item(current_row, 2).setText(str(fret_num))
    #     self.table.blockSignals(False)
        
    #     # 3. 변경 사항을 기반으로 상단 지판 모니터와 악보 뷰 리프레시
    #     target_step = self.tab_data[current_row].get('step', -1)
    #     harmonics = [item for item in self.tab_data if item.get('step') == target_step]
    #     self.fretboard_monitor.update_notes(harmonics)
    #     self.tab_sheet_view.set_current_step(target_step)
    # 지판 드래그 앤 드롭 시 midi_pitch 자동 갱신 반영
    def on_fretboard_dragged(self, string_num, fret_num):
        if not self.tab_data: return
        current_row = self.table.currentRow()
        if current_row < 0: return 
        
        # 1. 표준 조율 개방현 미디 번호 매핑 매트릭스 (1번줄~6번줄)
        # 💡 파일 로드 시 자동 추출된 곡 고유의 조율 맵 사용 (없으면 방어선으로 표준 조율 40 사용)
        open_string_map = getattr(self, 'open_string_midi', {1: 64, 2: 59, 3: 55, 4: 50, 5: 45, 6: 40})
        calculated_midi = open_string_map.get(string_num, 40) + fret_num        
        
        # 3. 테이블 차단 후 메모리 데이터셋 동시 갱신
        self.table.blockSignals(True)
        self.tab_data[current_row]['string'] = string_num
        self.tab_data[current_row]['fret'] = fret_num
        self.tab_data[current_row]['midi_pitch'] = calculated_midi # 💡 midi_pitch 실시간 자동 수정
        
        # 4. 하단 테이블 셀 텍스트 즉시 동기화
        self.table.item(current_row, 1).setText(str(string_num))
        self.table.item(current_row, 2).setText(str(fret_num))
        self.table.blockSignals(False)
        
        # 5. 변경 사항을 기반으로 상단 지판 모니터와 악보 뷰 리프레시
        target_step = self.tab_data[current_row].get('step', -1)
        harmonics = [item for item in self.tab_data if item.get('step') == target_step]
        self.fretboard_monitor.update_notes(harmonics)
        self.tab_sheet_view.set_current_step(target_step)

    def on_selection_changed(self):
        selected_ranges = self.table.selectedRanges()
        if not selected_ranges or not self.tab_data: return
            
        current_row = selected_ranges[0].topRow()
        if current_row >= len(self.tab_data): return
            
        target_step = self.tab_data[current_row].get('step', -1)
        harmonics = [item for item in self.tab_data if item.get('step') == target_step]
        
        self.fretboard_monitor.update_notes(harmonics)
        self.tab_sheet_view.set_current_step(target_step)
        
        x_target = 30 + (target_step * 35)
        self.scroll_area.horizontalScrollBar().setValue(x_target - 150)

    # def insert_row_data(self):
    #     if not self.current_file_path: return
    #     current_row = self.table.currentRow()
    #     if current_row < 0: current_row = len(self.tab_data) - 1
            
    #     base_step = self.tab_data[current_row]['step'] if self.tab_data else 1
    #     new_item = {"step": base_step, "string": self.spin_string.value(), "fret": self.spin_fret.value()}
        
    #     insert_idx = current_row + 1
    #     self.tab_data.insert(insert_idx, new_item)
    #     self.refresh_table()
    #     self.table.selectRow(insert_idx)
    
    def insert_row_data(self):
        if not self.current_file_path: return
        current_row = self.table.currentRow()
        if current_row < 0: current_row = len(self.tab_data) - 1
            
        if self.tab_data and current_row >= 0:
            # 1. 현재 선택된 행의 모든 데이터(8개 필드)를 완벽히 복사
            new_item = self.tab_data[current_row].copy()
            
            # 2. 사용자가 스핀박스에서 지정한 값으로만 덮어쓰기
            new_item["string"] = self.spin_string.value()
            new_item["fret"] = self.spin_fret.value()
            
            insert_idx = current_row + 1
            self.tab_data.insert(insert_idx, new_item)
            
            # 3. 테이블 신호를 잠시 차단하고 안전하게 새로고침
            self.table.blockSignals(True)
            self.refresh_table()
            self.table.blockSignals(False)
            
            self.table.selectRow(insert_idx)

    def load_json_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "파싱 결과 JSON 불러오기", "", "JSON Files (*.json)")
        if not file_path: return
        self.current_file_path = file_path
        self.file_status.setText(f"📂 로드된 파일: {os.path.basename(file_path)}")
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                self.tab_data = json.load(f)
            self.tab_data.sort(key=lambda x: (x.get('step', 0), x.get('string', 0)))
            
            self.tab_sheet_view.set_data(self.tab_data) 
            self.scroll_area.setWidget(self.tab_sheet_view) 
            self.refresh_table()
        except Exception as e:
            QMessageBox.critical(self, "오류", f"로드 실패: {e}")

    # def refresh_table(self):
    #     self.table.blockSignals(True)
    #     self.table.setRowCount(len(self.tab_data))
    #     for row, item in enumerate(self.tab_data):
    #         item_step = QTableWidgetItem(str(item.get('step', '')))
    #         item_string = QTableWidgetItem(str(item.get('string', '')))
    #         item_fret = QTableWidgetItem(str(item.get('fret', '')))
            
    #         item_step.setTextAlignment(Qt.AlignCenter)
    #         item_string.setTextAlignment(Qt.AlignCenter)
    #         item_fret.setTextAlignment(Qt.AlignCenter)
            
    #         self.table.setItem(row, 0, item_step)
    #         self.table.setItem(row, 1, item_string)
    #         self.table.setItem(row, 2, item_fret)
    #     self.table.blockSignals(False)
    #     if self.tab_data: self.table.selectRow(0)
    def refresh_table(self):
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.tab_data))
        for row, item in enumerate(self.tab_data):
            # 💡 사용자가 편집하고 볼 수 있는 화면 열은 원래대로 3개만 유지합니다.
            item_step = QTableWidgetItem(str(item.get('step', '')))
            item_string = QTableWidgetItem(str(item.get('string', '')))
            item_fret = QTableWidgetItem(str(item.get('fret', '')))
            
            item_step.setTextAlignment(Qt.AlignCenter)
            item_string.setTextAlignment(Qt.AlignCenter)
            item_fret.setTextAlignment(Qt.AlignCenter)
            
            self.table.setItem(row, 0, item_step)
            self.table.setItem(row, 1, item_string)
            self.table.setItem(row, 2, item_fret)
        self.table.blockSignals(False)
        if self.tab_data: self.table.selectRow(0)

    # def on_cell_changed(self, row, column):
    #     if row >= len(self.tab_data): return
    #     item = self.table.item(row, column)
    #     if not item: return
    #     try:
    #         val = int(item.text())
    #         # 오직 화면에 보이는 0, 1, 2번 컬럼을 수동 편집할 때만 메모리를 교체하고 나머지 5개는 건드리지 않음
    #         if column == 0: self.tab_data[row]['step'] = val
    #         elif column == 1 and 1 <= val <= 6: self.tab_data[row]['string'] = val
    #         elif column == 2 and 0 <= val <= 24: self.tab_data[row]['fret'] = val
            
    #         # 무한 루프 방지를 위해 신호 차단 후 정렬 및 리프레시
    #         self.table.blockSignals(True)
    #         self.tab_data.sort(key=lambda x: (x.get('step', 0), x.get('string', 0)))
    #         self.tab_sheet_view.set_data(self.tab_data)
    #         self.refresh_table()
    #         self.table.blockSignals(False)
    #     except ValueError:
    #         self.table.blockSignals(True)
    #         self.refresh_table()
    #         self.table.blockSignals(False)
    # 테이블 셀 수동 수정 시 midi_pitch 자동 연산 및 유실 차단 방어선
    def on_cell_changed(self, row, column):
        if row >= len(self.tab_data): return
        item = self.table.item(row, column)
        if not item: return
        try:
            val = int(item.text())
            if column == 0: 
                self.tab_data[row]['step'] = val
            elif column == 1 and 1 <= val <= 6: 
                self.tab_data[row]['string'] = val
            elif column == 2 and 0 <= val <= 24: 
                self.tab_data[row]['fret'] = val
            
            # 💡 [자동 연산] 줄(1번열) 또는 프렛(2번열) 데이터가 수정되었다면 midi_pitch를 즉시 동적 갱신
            # 💡 [자동 연산] 변경된 곡 고유의 개방현 스펙을 반영하여 실시간 동적 연산
            if column in [1, 2]:
                open_string_map = getattr(self, 'open_string_midi', {1: 64, 2: 59, 3: 55, 4: 50, 5: 45, 6: 40})
                s_num = self.tab_data[row].get('string', 6)
                f_num = self.tab_data[row].get('fret', 0)
                self.tab_data[row]['midi_pitch'] = open_string_map.get(s_num, 40) + f_num            
            
            # 무한 루프 방지를 위해 신호 차단 후 정렬 및 리프레시
            self.table.blockSignals(True)
            self.tab_data.sort(key=lambda x: (x.get('step', 0), x.get('string', 0)))
            self.tab_sheet_view.set_data(self.tab_data)
            self.refresh_table()
            self.table.blockSignals(False)
        except ValueError:
            self.table.blockSignals(True)
            self.refresh_table()
            self.table.blockSignals(False)


    def delete_selected_row(self):
        row = self.table.currentRow()
        if row < 0: return
        del self.tab_data[row]
        self.tab_sheet_view.set_data(self.tab_data)
        self.refresh_table()

    def save_json_file(self):
      if not self.current_file_path: return
      try:
          with open(self.current_file_path, 'w', encoding='utf-8') as f:
              json.dump(self.tab_data, f, indent=2, ensure_ascii=False)
          QMessageBox.information(self, "저장 완료", "수정 사항이 성공적으로 반영되었습니다.")
      except Exception as e:
          QMessageBox.critical(self, "오류", f"저장 실패: {e}")
    
    def go_back(self):
        self.main_window.stacked_widget.setCurrentIndex(0)
