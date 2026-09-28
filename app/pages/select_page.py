import json
import os
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QListWidget,
    QFileDialog,
    QMessageBox,
    QGroupBox,
    QProgressDialog,
    QApplication,
    QFrame,
)
from PySide6.QtCore import Qt, QThread, Signal
import guitarpro  # PyGuitarPro 라이브러리

MEASURES_PER_SCORE_LINE = 4
STEPS_PER_FALLBACK_SCORE_LINE = 16


def _measure_has_line_break(measure) -> bool:
    line_break = getattr(measure, "lineBreak", None)
    line_break_name = getattr(line_break, "name", "")
    return line_break_name in {"break_", "break"}


def _select_guitar_track(song):
    for track in song.tracks:
        strings = getattr(track, "strings", [])
        if len(strings) == 6:
            return track
    return song.tracks[0]


class ParsingThread(QThread):
    parsing_finished = Signal(bool, str)

    def __init__(self, file_path, db_mock_dir, mode):
        super().__init__()
        self.file_path = file_path
        self.db_mock_dir = db_mock_dir
        self.mode = mode

    def run(self):
        base_name = os.path.basename(self.file_path)
        song_title = os.path.splitext(base_name)[0].lower()
        output_json_path = os.path.join(self.db_mock_dir, f"{song_title}_parsed.json")

        if self.mode == "GP":
            try:
                # ── 기타프로 정밀 시퀀스 분석 엔진 가동 ──
                song = guitarpro.parse(self.file_path)
                guitar_track = _select_guitar_track(song)
                measures = list(guitar_track.measures)
                has_explicit_score_lines = any(_measure_has_line_break(measure) for measure in measures)

                # [BPM 추출] 곡의 마스터 템포 정보 확보 (없으면 기본값 120)
                bpm = song.tempo if song.tempo > 0 else 120

                parsed_data = []
                step_counter = 1
                score_line = 1

                for measure_index, measure in enumerate(measures, start=1):
                    measure_number = int(getattr(measure, "number", measure_index) or measure_index)
                    if has_explicit_score_lines:
                        current_score_line = score_line
                    else:
                        current_score_line = ((measure_index - 1) // MEASURES_PER_SCORE_LINE) + 1

                    for beat_index, beat in enumerate(measure.voices[0].beats, start=1):
                        if not beat.notes:
                            continue

                        # [밀리초 박자 연산] 개별 음표 절대 유지 시간(ms) 계산
                        duration_ms = int((60000 / bpm) * (4.0 / beat.duration.value))

                        # [버전 에러 정밀 방어] PyGuitarPro 버전에 따른 isTriplet 속성 부재 오류 우회
                        is_triplet_rhythm = False
                        try:
                            if hasattr(beat.duration, "isTriplet"):
                                is_triplet_rhythm = beat.duration.isTriplet
                            elif hasattr(beat.duration, "tuplet") and hasattr(beat.duration.tuplet, "isTriplet"):
                                is_triplet_rhythm = beat.duration.tuplet.isTriplet
                        except Exception:
                            pass

                        # 3연음인 경우 2/3 배율 시간 압축 보정 반영
                        if is_triplet_rhythm:
                            duration_ms = int(duration_ms * (2.0 / 3.0))

                        has_valid_note = False
                        for note in beat.notes:
                            string_num = int(note.string)
                            fret_num = int(note.value)
                            if fret_num < 0 or fret_num > 30:
                                continue

                            midi_pitch = note.realValue

                            parsed_data.append({
                                "step": step_counter,
                                "score_line": current_score_line,
                                "measure": measure_number,
                                "beat": beat_index,
                                "string": string_num,
                                "fret": fret_num,
                                "duration_ms": duration_ms,
                                "midi_pitch": midi_pitch,
                            })
                            has_valid_note = True
                        if has_valid_note:
                            step_counter += 1

                    if has_explicit_score_lines and _measure_has_line_break(measure):
                        score_line += 1

                with open(output_json_path, "w", encoding="utf-8") as f:
                    json.dump(parsed_data, f, indent=2, ensure_ascii=False)
                self.parsing_finished.emit(True, f"[{base_name}] 파싱 완료")
            except Exception as e:
                self.parsing_finished.emit(False, f"기타프로 파싱 오류: {e}")

        elif self.mode == "IMAGE":
            try:
                from utils.image_parser import parse_image_to_json
                success = parse_image_to_json(self.file_path, output_json_path)

                if success:
                    with open(output_json_path, "r", encoding="utf-8") as f:
                        image_tab_data = json.load(f)

                    for item in image_tab_data:
                        step = int(item.get("step", 1))
                        if "duration_ms" not in item:
                            item["duration_ms"] = 500
                        if "midi_pitch" not in item:
                            item["midi_pitch"] = 60
                        if "score_line" not in item:
                            item["score_line"] = ((step - 1) // STEPS_PER_FALLBACK_SCORE_LINE) + 1

                    with open(output_json_path, "w", encoding="utf-8") as f:
                        json.dump(image_tab_data, f, indent=2, ensure_ascii=False)

                    self.parsing_finished.emit(True, f"[{base_name}] 이미지 분석 완료")
                else:
                    self.parsing_finished.emit(False, "악보 이미지 분석 실패")
            except Exception as e:
                self.parsing_finished.emit(False, f"분석 오류: {e}")


class SelectPage(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        from app.config import DEFAULT_SCORE_PATH
        self.db_mock_dir = os.path.dirname(os.path.abspath(DEFAULT_SCORE_PATH))
        self.init_ui()
        self.refresh_song_list()

    def init_ui(self):
        # ── [LoginPage와 100% 동일한 미니멀 라이트 테마 QSS] ──
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

            /* 좌/우측 분할 카드 컨테이너 */
            QFrame#card_panel {
                background-color: #ffffff;
                border: 1px solid #e9ecef;
                border-radius: 12px;
            }

            /* 악보 리스트 위젯 */
            QListWidget {
                font-size: 13.5px;
                border: 1px solid #e9ecef;
                border-radius: 8px;
                background-color: #ffffff;
                color: #212529;
                outline: none;
                padding: 4px;
            }
            QListWidget::item {
                padding: 13px 16px;
                border-bottom: 1px solid #f1f3f5;
                border-radius: 6px;
                margin-bottom: 2px;
            }
            QListWidget::item:hover {
                background-color: #f8f9fa;
            }
            QListWidget::item:selected {
                background-color: #f1f5f9;
                color: #111827;
                font-weight: 600;
                border-left: 3px solid #2563eb;
            }

            /* 그룹 박스 (서피스 카드 스타일로 정돈) */
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

            /* 기본 보조 버튼 (업로드, 에디터) */
            QPushButton {
                background-color: #ffffff;
                border: 1px solid #ced4da;
                border-radius: 6px;
                font-size: 13px;
                font-weight: 500;
                color: #374151;
                padding: 10px 14px;
                min-height: 18px;
            }
            QPushButton:hover {
                background-color: #f8f9fa;
                border-color: #adb5bd;
            }
            QPushButton:pressed {
                background-color: #e9ecef;
            }
            QPushButton:disabled {
                background-color: #f8f9fa;
                border-color: #e9ecef;
                color: #adb5bd;
            }

            /* 주 실행 버튼: 선택한 악보로 연습 시작 */
            QPushButton#btn_start_practice {
                background-color: #111827;
                color: #ffffff;
                border: 1px solid #111827;
                border-radius: 8px;
                font-size: 14.5px;
                font-weight: 600;
                padding: 13px;
                min-height: 22px;
            }
            QPushButton#btn_start_practice:hover {
                background-color: #27272a;
                border-color: #27272a;
            }
            QPushButton#btn_start_practice:pressed {
                background-color: #09090b;
                border-color: #09090b;
            }
        """)

        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(28, 28, 28, 28)
        main_layout.setSpacing(20)

        # ── [좌측 메인: 악보 선택 영역] ──
        left_card = QFrame()
        left_card.setObjectName("card_panel")
        left_layout = QVBoxLayout(left_card)
        left_layout.setContentsMargins(24, 24, 24, 24)
        left_layout.setSpacing(14)

        header_layout = QVBoxLayout()
        header_layout.setSpacing(4)
        
        lbl_sub = QLabel("LIBRARY")
        lbl_sub.setStyleSheet("font-size: 11px; font-weight: 700; color: #6b7280; letter-spacing: 1.5px;")
        header_layout.addWidget(lbl_sub)

        lbl_title = QLabel("악보 보관함")
        lbl_title.setStyleSheet("font-size: 18px; font-weight: 700; color: #111827; letter-spacing: -0.3px;")
        header_layout.addWidget(lbl_title)

        lbl_desc = QLabel("연습을 진행할 곡을 목록에서 선택하세요.")
        lbl_desc.setStyleSheet("font-size: 12.5px; color: #6b7280; margin-bottom: 4px;")
        header_layout.addWidget(lbl_desc)

        left_layout.addLayout(header_layout)

        self.song_list_widget = QListWidget()
        left_layout.addWidget(self.song_list_widget, stretch=1)

        # 메인 연습 시작 버튼
        self.btn_start = QPushButton("선택한 악보로 연습 시작")
        self.btn_start.setObjectName("btn_start_practice")
        self.btn_start.setCursor(Qt.PointingHandCursor)
        self.btn_start.clicked.connect(self.launch_practice)
        left_layout.addWidget(self.btn_start)

        # ── [우측 사이드: 관리 및 업로드 영역] ──
        right_card = QFrame()
        right_card.setObjectName("card_panel")
        right_card.setFixedWidth(340)
        right_layout = QVBoxLayout(right_card)
        right_layout.setContentsMargins(20, 24, 20, 24)
        right_layout.setSpacing(16)

        side_header = QVBoxLayout()
        side_header.setSpacing(4)
        lbl_side_sub = QLabel("MANAGEMENT")
        lbl_side_sub.setStyleSheet("font-size: 11px; font-weight: 700; color: #6b7280; letter-spacing: 1.5px;")
        side_header.addWidget(lbl_side_sub)

        lbl_side_title = QLabel("데이터 및 편집")
        lbl_side_title.setStyleSheet("font-size: 16px; font-weight: 700; color: #111827; letter-spacing: -0.3px;")
        side_header.addWidget(lbl_side_title)
        right_layout.addLayout(side_header)

        # 악보 등록 그룹
        upload_group = QGroupBox("악보 등록")
        upload_layout = QVBoxLayout(upload_group)
        upload_layout.setContentsMargins(14, 16, 14, 14)
        upload_layout.setSpacing(10)

        lbl_upload_desc = QLabel("GuitarPro 파일 또는 악보 이미지를 불러와 디지털 시퀀스로 변환합니다.")
        lbl_upload_desc.setWordWrap(True)
        lbl_upload_desc.setStyleSheet("color: #6b7280; font-size: 12px; margin-bottom: 4px;")
        upload_layout.addWidget(lbl_upload_desc)

        self.btn_gp = QPushButton("GuitarPro (gp3/gp5) 파일 가져오기")
        self.btn_gp.setCursor(Qt.PointingHandCursor)
        self.btn_img = QPushButton("악보 이미지 (jpg/png) 가져오기")
        self.btn_img.setCursor(Qt.PointingHandCursor)
        self.btn_img.setEnabled(False)  # 현재 비활성 상태 유지

        self.btn_gp.clicked.connect(lambda: self.start_async_parsing("GP"))
        self.btn_img.clicked.connect(lambda: self.start_async_parsing("IMAGE"))

        upload_layout.addWidget(self.btn_gp)
        upload_layout.addWidget(self.btn_img)
        right_layout.addWidget(upload_group)

        # 악보 데이터 교정 그룹
        admin_group = QGroupBox("정밀 편집")
        admin_layout = QVBoxLayout(admin_group)
        admin_layout.setContentsMargins(14, 16, 14, 14)
        admin_layout.setSpacing(10)

        lbl_admin_desc = QLabel("파싱된 악보의 운지, 프렛, 박자 데이터를 직접 확인하고 수정합니다.")
        lbl_admin_desc.setWordWrap(True)
        lbl_admin_desc.setStyleSheet("color: #6b7280; font-size: 12px; margin-bottom: 4px;")
        admin_layout.addWidget(lbl_admin_desc)

        self.btn_admin = QPushButton("악보 데이터 교정 에디터")
        self.btn_admin.setCursor(Qt.PointingHandCursor)
        self.btn_admin.clicked.connect(self.go_to_admin)
        admin_layout.addWidget(self.btn_admin)

        right_layout.addWidget(admin_group)
        right_layout.addStretch(1)

        # 전체 배치 (3:1 비율)
        main_layout.addWidget(left_card, stretch=3)
        main_layout.addWidget(right_card, stretch=1)

    def refresh_song_list(self):
        self.song_list_widget.clear()
        if os.path.exists(self.db_mock_dir):
            for file in os.listdir(self.db_mock_dir):
                if file.endswith("_parsed.json"):
                    self.song_list_widget.addItem(file.replace("_parsed.json", "").upper())
        if self.song_list_widget.count() > 0:
            self.song_list_widget.setCurrentRow(0)

    def launch_practice(self):
        """곡 선택 확인 후 연습 화면(Index 2) 가동"""
        current_item = self.song_list_widget.currentItem()
        if not current_item:
            QMessageBox.warning(self, "악보 미선택", "연습을 시작할 악보를 목록에서 먼저 선택해 주세요.")
            return

        target_path = os.path.join(self.db_mock_dir, f"{current_item.text().lower()}_parsed.json")
        if not os.path.exists(target_path):
            QMessageBox.critical(self, "파일 오류", f"선택한 악보 파일이 존재하지 않습니다:\n{target_path}")
            return

        print(f"[연습 시작 요청] 전달 악보: {target_path}")
        if hasattr(self.main_window, "switch_to_practice"):
            self.main_window.switch_to_practice(target_path)
        else:
            self.main_window.stacked_widget.setCurrentIndex(2)

    def start_async_parsing(self, mode):
        if mode == "GP":
            f, _ = QFileDialog.getOpenFileName(self, "GuitarPro 악보 열기", "", "GuitarPro (*.gp*)")
        else:
            f, _ = QFileDialog.getOpenFileName(self, "악보 이미지 열기", "", "Images (*.jpg *.png)")
        if not f:
            return

        self.loading_dialog = QProgressDialog("악보 데이터를 분석하고 있습니다...", None, 0, 0, self)
        self.loading_dialog.setWindowTitle("데이터 처리 중")
        self.loading_dialog.setWindowModality(Qt.WindowModal)
        self.loading_dialog.setStyleSheet("background-color: #ffffff; color: #212529;")
        self.loading_dialog.show()
        QApplication.processEvents()

        self.parse_thread = ParsingThread(f, self.db_mock_dir, mode)
        self.parse_thread.parsing_finished.connect(self.on_parsing_completed)
        self.parse_thread.start()

    def on_parsing_completed(self, success, message):
        if self.loading_dialog:
            self.loading_dialog.close()
        if success:
            QMessageBox.information(self, "처리 완료", message)
            self.refresh_song_list()
        else:
            QMessageBox.critical(self, "오류", message)

    def go_to_admin(self):
        """곡 선택 여부와 무관하게 에디터 페이지(Index 3)로 확실히 이동"""
        item = self.song_list_widget.currentItem()
        path = None
        if item:
            candidate_path = os.path.join(self.db_mock_dir, f"{item.text().lower()}_parsed.json")
            if os.path.exists(candidate_path):
                path = candidate_path

        if hasattr(self.main_window, "switch_to_admin"):
            self.main_window.switch_to_admin(path)
        else:
            self.main_window.stacked_widget.setCurrentIndex(3)