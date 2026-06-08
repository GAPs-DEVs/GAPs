import json
import os
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
                             QLabel, QListWidget, QFileDialog, QMessageBox, QGroupBox, QProgressDialog, QApplication)
from PySide6.QtCore import Qt, QThread, Signal
import guitarpro  # PyGuitarPro 라이브러리

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
                guitar_track = song.tracks[0]
                
                # [BPM 추출] 곡의 마스터 템포 정보 확보 (없으면 기본값 120)
                bpm = song.tempo if song.tempo > 0 else 120
                
                parsed_data = []
                step_counter = 1
                
                for measure in guitar_track.measures:
                    for beat in measure.voices[0].beats:
                        if not beat.notes: continue
                        
                        # [밀리초 박자 연산] 개별 음표가 유지되어야 할 절대 시간(ms) 계산
                        duration_ms = int((60000 / bpm) * (4.0 / beat.duration.value))
                        
                        # [버전 에러 정밀 방어] PyGuitarPro 버전에 따른 isTriplet 속성 부재 오류 완벽 우회
                        is_triplet_rhythm = False
                        try:
                            if hasattr(beat.duration, 'isTriplet'):
                                is_triplet_rhythm = beat.duration.isTriplet
                            elif hasattr(beat.duration, 'tuplet') and hasattr(beat.duration.tuplet, 'isTriplet'):
                                is_triplet_rhythm = beat.duration.tuplet.isTriplet
                        except:
                            pass # 내부 구조 예외 발생 시 안전 패스
                        
                        # 3연음인 경우 2/3 배율 시간 압축 보정 반영
                        if is_triplet_rhythm:
                            duration_ms = int(duration_ms * (2.0 / 3.0))
                        
                        has_valid_note = False
                        for note in beat.notes:
                            string_num = int(note.string)
                            fret_num = int(note.value)
                            if fret_num < 0 or fret_num > 30: continue
                            
                            # [MIDI 음고 추출] 오디오 출력을 위한 절대 가상 피치 값 확보
                            midi_pitch = note.realValue
                            
                            parsed_data.append({
                                "step": step_counter,
                                "string": string_num,
                                "fret": fret_num,
                                "duration_ms": duration_ms, # ⏱️ 노래 박자 자동진행용 시간 메타데이터
                                "midi_pitch": midi_pitch     # 🔊 오디오 가이드 싱크용 미디 음고
                            })
                            has_valid_note = True
                        if has_valid_note: 
                            step_counter += 1
                            
                with open(output_json_path, 'w', encoding='utf-8') as f:
                    json.dump(parsed_data, f, indent=2, ensure_ascii=False)
                self.parsing_finished.emit(True, f"🎸 [{base_name}] 기타프로 박자 싱크 파싱 성공!")
            except Exception as e:
                self.parsing_finished.emit(False, f"기타프로 파싱 오류: {e}")
                
        elif self.mode == "IMAGE":
            try:
                # ── AI 하이브리드 파이프라인 가동 ──
                from utils.image_parser import parse_image_to_json
                success = parse_image_to_json(self.file_path, output_json_path)
                
                if success:
                    # [이미지 폴백 방어] 대기 타이머 에러 방지를 위한 디폴트 박자 속성 강제 주입
                    with open(output_json_path, 'r', encoding='utf-8') as f:
                        image_tab_data = json.load(f)
                    
                    for item in image_tab_data:
                        if "duration_ms" not in item:
                            item["duration_ms"] = 500  # 기본 대기 시간 기본값 지정 (4분음표 방어)
                        if "midi_pitch" not in item:
                            item["midi_pitch"] = 60    # 기본 가상 C 음고 지정
                            
                    with open(output_json_path, 'w', encoding='utf-8') as f:
                        json.dump(image_tab_data, f, indent=2, ensure_ascii=False)
                        
                    self.parsing_finished.emit(True, f"📸 [{base_name}] 이미지 분석 및 자동진행 규격 패키징 완료!")
                else: 
                    self.parsing_finished.emit(False, "악보 이미지 분석 실패")
            except Exception as e:
                self.parsing_finished.emit(False, f"AI 오류: {e}")


# 악보 선택 및 업로드 페이지
class SelectPage(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        # [경로 연동] app.config의 공식 악보 저장소 폴더 자동 참조
        from app.config import DEFAULT_SCORE_PATH
        self.db_mock_dir = os.path.dirname(os.path.abspath(DEFAULT_SCORE_PATH))
        self.init_ui()
        self.refresh_song_list()

    # 우측 부분 디자인
    def init_ui(self):
        self.setStyleSheet("""
            QWidget { 
                font-family: 'Apple SD Gothic Neo', 'Malgun Gothic', 'Arial'; 
                background-color: #ffffff; 
                color: #333333; 
            }
            QLabel { 
                color: #2c3e50; 
                background-color: transparent;
            }
            /* 악보 업로드 및 수정 구역: 테두리를 없애고(#border: none) 부드러운 패널 연회색 바탕 세팅 */
            QGroupBox { 
                font-size: 15px; 
                font-weight: bold; 
                color: #000000;
                border: none; 
                border-radius: 12px;
                margin-top: 15px; 
                padding-top: 25px; 
                background-color: #f5f5f7; 
            }
            QListWidget { 
                font-size: 14px; 
                border: 1px solid #d1d1d6; 
                border-radius: 8px; 
                padding: 5px; 
                background-color: #ffffff; 
                color: #222222;
            }
            QListWidget::item {
                padding: 12px;
                border-bottom: 1px solid #f2f2f2;
            }
            QListWidget::item:selected {
                background-color: #e9ecef;
                color: #000000;
                font-weight: bold;
                border-left: 4px solid #333333;
            }
            /* 우측 카드 패널 내부 일반 버튼들: 이미지 속 회색 버튼 단추 배경(#e5e5ea) 및 모서리 곡률 일치 */
            QPushButton { 
                background-color: #e5e5ea;
                border: none;
                color: #000000;
                font-size: 14px; 
                font-weight: 500; 
                padding: 11px; 
                border-radius: 6px;
                min-height: 20px;
            }
            QPushButton:hover { background-color: #d1d1d6; }
            QPushButton:pressed { background-color: #aeaeb2; }
        """)

        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(20)

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        
        left_layout.addWidget(QLabel("<h2>악보 선택 및 업로드</h2>"))
        left_layout.addWidget(QLabel(""))
        
        self.song_list_widget = QListWidget()
        left_layout.addWidget(self.song_list_widget)
        
        # 메인 연습 시작 버튼: 연습 페이지 하단 '메인 화면으로 이동' 복귀 버튼
        self.btn_start = QPushButton("선택한 악보로 연습 시작")
        self.btn_start.setCursor(Qt.PointingHandCursor)
        self.btn_start.setStyleSheet("""
            QPushButton { 
                background-color: #333333; 
                color: #ffffff; 
                font-weight: bold; 
                font-size: 16px; 
                border: none;
                border-radius: 8px;
                min-height: 50px;
            }
            QPushButton:hover { background-color: #555555; }
        """)
        self.btn_start.clicked.connect(self.launch_practice)
        left_layout.addWidget(self.btn_start)

        right_widget = QWidget()
        right_widget.setFixedWidth(330)
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)

        # 새로운 악보 등록 패널 
        upload_group = QGroupBox("악보 업로드")
        upload_layout = QVBoxLayout(upload_group)
        upload_layout.setContentsMargins(15, 20, 15, 15)
        upload_layout.setSpacing(12)

        lbl_desc = QLabel("TAB 악보 파일 또는 이미지를\n업로드하여 디지털 데이터로 변환합니다.")
        lbl_desc.setStyleSheet("color: #6c757d; font-size: 12px;")
        upload_layout.addWidget(lbl_desc)

        self.btn_gp = QPushButton("GuitarPro (gp3/gp5) 파일 업로드")
        self.btn_img = QPushButton("악보 이미지 (jpg/png) 파일 업로드")
        
        self.btn_img.setEnabled(False)
        
        self.btn_gp.clicked.connect(lambda: self.start_async_parsing("GP"))
        self.btn_img.clicked.connect(lambda: self.start_async_parsing("IMAGE"))

        upload_layout.addWidget(self.btn_gp)
        upload_layout.addWidget(self.btn_img)
        
        # 시스템 권한 패널 
        admin_group = QGroupBox("악보 수정")
        admin_layout = QVBoxLayout(admin_group)
        admin_layout.setContentsMargins(15, 20, 15, 15)
        
        self.btn_admin = QPushButton("악보 수정 에디터")
        self.btn_admin.setCursor(Qt.PointingHandCursor)
        self.btn_admin.setStyleSheet("color: #000000; font-weight: bold;")
        self.btn_admin.clicked.connect(self.go_to_admin)
        admin_layout.addWidget(self.btn_admin)

        right_layout.addWidget(upload_group)
        right_layout.addWidget(admin_group)
        right_layout.addStretch(1)

        main_layout.addWidget(left_widget, stretch=3)
        main_layout.addWidget(right_widget, stretch=1)

    def refresh_song_list(self):
        self.song_list_widget.clear()
        for file in os.listdir(self.db_mock_dir):
            if file.endswith("_parsed.json"):
                self.song_list_widget.addItem(file.replace("_parsed.json", "").upper())
        if self.song_list_widget.count() > 0: self.song_list_widget.setCurrentRow(0)

    def launch_practice(self):
        current_item = self.song_list_widget.currentItem()
        if not current_item: return
        target_path = os.path.join(self.db_mock_dir, f"{current_item.text().lower()}_parsed.json")
        self.main_window.switch_to_practice(target_path)

    def start_async_parsing(self, mode):
        if mode == "GP": f, _ = QFileDialog.getOpenFileName(self, "GP 로드", "", "GuitarPro (*.gp*)")
        else: f, _ = QFileDialog.getOpenFileName(self, "이미지 로드", "", "Images (*.jpg *.png)")
        if not f: return
        self.loading_dialog = QProgressDialog("AI 분석 중입니다. 잠시만 기다려주세요...", None, 0, 0, self)
        self.loading_dialog.setWindowTitle("AI 분석 중")
        self.loading_dialog.setWindowModality(Qt.WindowModal)
        self.loading_dialog.setStyleSheet("background-color: white; color: black;")
        self.loading_dialog.show()
        QApplication.processEvents()
        self.parse_thread = ParsingThread(f, self.db_mock_dir, mode)
        self.parse_thread.parsing_finished.connect(self.on_parsing_completed)
        self.parse_thread.start()

    def on_parsing_completed(self, success, message):
        if self.loading_dialog: self.loading_dialog.close()
        if success:
            QMessageBox.information(self, "완료", message)
            self.refresh_song_list()
        else: QMessageBox.critical(self, "오류", message)

    def go_to_admin(self):
        item = self.song_list_widget.currentItem()
        if not item: return
        path = os.path.join(self.db_mock_dir, f"{item.text().lower()}_parsed.json")
        self.main_window.switch_to_admin(path)