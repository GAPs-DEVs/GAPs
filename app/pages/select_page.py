import json
import os
from pathlib import Path
import shutil
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QFileDialog,
    QMessageBox,
    QProgressDialog,
    QApplication,
    QFrame,
    QScrollArea,
    QGridLayout,
    QLineEdit,
)
from PySide6.QtCore import Qt, QThread, Signal
import guitarpro

from app.api_client import api_client
from app.features.score_library import RemoveScoreThread

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
                song = guitarpro.parse(self.file_path)
                guitar_track = _select_guitar_track(song)
                measures = list(guitar_track.measures)
                has_explicit_score_lines = any(_measure_has_line_break(measure) for measure in measures)

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

                        duration_ms = int((60000 / bpm) * (4.0 / beat.duration.value))

                        is_triplet_rhythm = False
                        try:
                            if hasattr(beat.duration, "isTriplet"):
                                is_triplet_rhythm = beat.duration.isTriplet
                            elif hasattr(beat.duration, "tuplet") and hasattr(beat.duration.tuplet, "isTriplet"):
                                is_triplet_rhythm = beat.duration.tuplet.isTriplet
                        except Exception:
                            pass

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


class ScoreCard(QFrame):
    """중앙 갤러리에 들어갈 개별 곡 카드"""
    def __init__(self, song_title, file_path, on_practice_cb, on_edit_cb,
                 on_remove_cb=None, score_id=None, is_default=None):
        super().__init__()
        self.song_title = song_title
        self.file_path = file_path
        self.on_practice_cb = on_practice_cb
        self.on_edit_cb = on_edit_cb
        self.on_remove_cb = on_remove_cb
        self.score_id = score_id
        self.is_default = is_default
        self.init_ui()

    def init_ui(self):
        self.setObjectName("score_card")
        self.setFixedHeight(175)
        self.setStyleSheet("""
            QFrame#score_card {
                background-color: #ffffff;
                border: 1px solid #e9ecef;
                border-radius: 12px;
            }
            QFrame#score_card:hover {
                border: 1.5px solid #111827;
                background-color: #ffffff;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 16)
        layout.setSpacing(8)

        # 상단 태그 및 메타데이터
        top_box = QHBoxLayout()
        badge = QLabel("STANDARD TAB")
        badge.setStyleSheet("""
            background-color: #f1f5f9;
            color: #475569;
            font-size: 10px;
            font-weight: 800;
            padding: 3px 7px;
            border-radius: 4px;
        """)
        top_box.addWidget(badge)
        top_box.addStretch(1)

        note_count = 0
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                note_count = len(data.get("notes", [])) if isinstance(data, dict) else len(data)
        except Exception:
            pass

        lbl_meta = QLabel(f"{note_count} Notes")
        lbl_meta.setStyleSheet("font-size: 11px; color: #94a3b8; font-weight: 600;")
        top_box.addWidget(lbl_meta)
        self.remove_button = QPushButton("삭제")
        self.remove_button.setCursor(Qt.PointingHandCursor)
        self.remove_button.setStyleSheet("""
            QPushButton { background-color: #dc2626; color: #ffffff;
                border: none; border-radius: 5px; padding: 5px 8px; font-weight: 700; }
            QPushButton:hover { background-color: #b91c1c; }
            QPushButton:disabled { background-color: #e5e7eb; color: #6b7280; }
        """)
        self.remove_button.setEnabled(bool(self.score_id) and self.is_default is not True
                                      and self.on_remove_cb is not None)
        self.remove_button.setToolTip(
            "개인 악보함에서 제거" if self.remove_button.isEnabled()
            else "기본 악보 또는 서버 등록 정보를 확인할 수 없는 로컬 악보입니다."
        )
        self.remove_button.clicked.connect(lambda: self.on_remove_cb(self))
        top_box.addWidget(self.remove_button)
        layout.addLayout(top_box)

        # 곡 제목
        title_label = QLabel(self.song_title.upper())
        title_label.setWordWrap(True)
        title_label.setStyleSheet("font-size: 16px; font-weight: 800; color: #111827; letter-spacing: -0.3px;")
        layout.addWidget(title_label)

        layout.addStretch(1)

        # 하단 액션 버튼 그룹
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        btn_practice = QPushButton("연습 시작")
        btn_practice.setCursor(Qt.PointingHandCursor)
        btn_practice.setStyleSheet("""
            QPushButton {
                background-color: #111827;
                color: #ffffff;
                border: 1px solid #111827;
                border-radius: 6px;
                font-size: 12.5px;
                font-weight: 700;
                padding: 8px 12px;
            }
            QPushButton:hover { background-color: #27272a; }
        """)
        btn_practice.clicked.connect(lambda: self.on_practice_cb(self.file_path))
        btn_layout.addWidget(btn_practice, stretch=2)

        btn_edit = QPushButton("에디터")
        btn_edit.setCursor(Qt.PointingHandCursor)
        btn_edit.setStyleSheet("""
            QPushButton {
                background-color: #ffffff;
                color: #4b5563;
                border: 1px solid #ced4da;
                border-radius: 6px;
                font-size: 12px;
                font-weight: 600;
                padding: 8px 10px;
            }
            QPushButton:hover { background-color: #f8f9fa; color: #111827; }
        """)
        btn_edit.clicked.connect(lambda: self.on_edit_cb(self.file_path))
        btn_layout.addWidget(btn_edit, stretch=1)

        layout.addLayout(btn_layout)


class SelectPage(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.remove_thread = None

        # 프로젝트 루트 기준 data/cached_scores 전용 캐시 폴더 설정
        current_dir = os.path.dirname(os.path.abspath(__file__))  # app/pages/
        app_dir = os.path.dirname(current_dir)  # app/
        project_root = os.path.dirname(app_dir)  # 프로젝트 루트

        self.db_mock_dir = os.path.join(
            project_root, "data", "cached_scores"
        )
        os.makedirs(self.db_mock_dir, exist_ok=True)

        self.init_ui()
        self.refresh_song_list()

    def init_ui(self):
        # ── [전체 미니멀 라이트 테마 QSS] ──
        self.setStyleSheet("""
            QWidget {
                font-family: 'Apple SD Gothic Neo', 'Pretendard', 'Malgun Gothic', 'Segoe UI', sans-serif;
                background-color: #f8f9fa;
                color: #212529;
            }

            QFrame#card_panel {
                background-color: #ffffff;
                border: 1px solid #e9ecef;
                border-radius: 12px;
            }

            QLineEdit {
                background-color: #ffffff;
                border: 1px solid #ced4da;
                border-radius: 20px;
                padding: 8px 16px;
                font-size: 13px;
                color: #111827;
                min-height: 20px;
            }
            QLineEdit:focus {
                border: 1.5px solid #111827;
            }

            QScrollArea {
                border: none;
                background-color: transparent;
            }
            QScrollBar:vertical {
                background-color: transparent;
                width: 6px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background-color: #cbd5e1;
                border-radius: 3px;
                min-height: 24px;
            }
            QScrollBar::handle:vertical:hover {
                background-color: #94a3b8;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)

        # 화면 전체 정렬용 외부 레이아웃 (중앙 정렬 담당)
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setAlignment(Qt.AlignCenter)

        # 💡 전체 화면 확대 시에도 과도하게 퍼지지 않도록 max-width 지정
        container = QWidget()
        container.setMaximumWidth(1160)
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(32, 28, 32, 28)
        container_layout.setSpacing(20)

        # ── [1. 상단 바: 좌측 타이틀 / 중앙 검색창 / 우측 카운트] ──
        header_bar = QHBoxLayout()
        header_bar.setSpacing(16)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        lbl_sub = QLabel("LIBRARY")
        lbl_sub.setStyleSheet("font-size: 11px; font-weight: 800; color: #6b7280; letter-spacing: 1.5px;")
        title_box.addWidget(lbl_sub)

        lbl_title = QLabel("악보 보관함")
        lbl_title.setStyleSheet("font-size: 20px; font-weight: 800; color: #111827; letter-spacing: -0.4px;")
        title_box.addWidget(lbl_title)
        header_bar.addLayout(title_box)

        # 중앙 검색창
        header_bar.addStretch(1)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("검색할 곡명을 입력하세요...")
        self.search_input.setFixedWidth(320)
        self.search_input.textChanged.connect(self.filter_song_cards)
        header_bar.addWidget(self.search_input)
        header_bar.addStretch(1)

        # 우측 곡 수 카운터 뱃지
        self.lbl_count = QLabel("총 0개의 악보")
        self.lbl_count.setStyleSheet("""
            background-color: #ffffff;
            border: 1px solid #e9ecef;
            border-radius: 6px;
            padding: 6px 12px;
            font-size: 12px;
            font-weight: 600;
            color: #4b5563;
        """)
        header_bar.addWidget(self.lbl_count)

        container_layout.addLayout(header_bar)

        # ── [2. 메인 콘텐츠: 좌측 카드 그리드 + 우측 사이드 패널] ──
        body_layout = QHBoxLayout()
        body_layout.setSpacing(20)

        # 좌측: 스크롤 가능한 카드 그리드 영역
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)

        self.grid_container = QWidget()
        self.grid_container.setStyleSheet("background-color: transparent;")
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setContentsMargins(0, 0, 10, 0)
        self.grid_layout.setSpacing(14)
        self.grid_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        self.scroll_area.setWidget(self.grid_container)
        body_layout.addWidget(self.scroll_area, stretch=3)

        # 우측: 관리 및 업로드 사이드 패널 (너비 290px 고정)
        right_panel = QFrame()
        right_panel.setObjectName("card_panel")
        right_panel.setFixedWidth(290)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(20, 22, 20, 20)
        right_layout.setSpacing(14)

        lbl_side_title = QLabel("악보 관리")
        lbl_side_title.setStyleSheet("font-size: 15px; font-weight: 700; color: #111827;")
        right_layout.addWidget(lbl_side_title)

        lbl_upload_desc = QLabel("새로운 기타프로 파일(.gp3, .gp5)을 파싱하여 보관함에 등록합니다.")
        lbl_upload_desc.setWordWrap(True)
        lbl_upload_desc.setStyleSheet("color: #6b7280; font-size: 12px; line-height: 1.4;")
        right_layout.addWidget(lbl_upload_desc)

        # 새 악보 등록 버튼
        btn_upload = QPushButton("+ 새 악보 등록 (GuitarPro)")
        btn_upload.setCursor(Qt.PointingHandCursor)
        btn_upload.setStyleSheet("""
            QPushButton {
                background-color: #111827;
                color: #ffffff;
                border: 1px solid #111827;
                border-radius: 8px;
                font-size: 13px;
                font-weight: 700;
                padding: 10px 14px;
            }
            QPushButton:hover { background-color: #27272a; }
        """)
        btn_upload.clicked.connect(lambda: self.start_async_parsing("GP"))
        right_layout.addWidget(btn_upload)

        # 구분선
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #f1f3f5;")
        right_layout.addWidget(sep)

        # 팁 카드
        tip_box = QFrame()
        tip_box.setStyleSheet("background-color: #f8f9fa; border-radius: 8px; padding: 10px;")
        tip_layout = QVBoxLayout(tip_box)
        tip_layout.setContentsMargins(10, 10, 10, 10)
        tip_layout.setSpacing(4)
        lbl_tip_title = QLabel("연습 팁")
        lbl_tip_title.setStyleSheet("font-size: 12px; font-weight: 700; color: #374151;")
        lbl_tip_desc = QLabel("원하는 곡의 '연습 시작'을 누르면 웹캠을 통한 지판 운지 판정이 바로 시작됩니다.")
        lbl_tip_desc.setWordWrap(True)
        lbl_tip_desc.setStyleSheet("font-size: 11.5px; color: #6b7280;")
        tip_layout.addWidget(lbl_tip_title)
        tip_layout.addWidget(lbl_tip_desc)
        right_layout.addWidget(tip_box)

        right_layout.addStretch(1)

        # 하단: 로그인 화면 복귀 (로그아웃)
        btn_logout = QPushButton("로그아웃")
        btn_logout.setCursor(Qt.PointingHandCursor)
        btn_logout.setStyleSheet("""
            QPushButton {
                background-color: #ffffff;
                color: #6b7280;
                border: 1px solid #ced4da;
                border-radius: 8px;
                font-size: 12.5px;
                font-weight: 600;
                padding: 10px;
            }
            QPushButton:hover {
                background-color: #f8f9fa;
                color: #111827;
                border-color: #adb5bd;
            }
        """)
        btn_logout.clicked.connect(self.go_to_login)
        right_layout.addWidget(btn_logout)

        body_layout.addWidget(right_panel)
        container_layout.addLayout(body_layout, stretch=1)

        outer_layout.addWidget(container)

    def refresh_song_list(self):
        """로컬 악보 디렉토리를 스캔하여 카드 그리드로 동적 생성"""
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        self.cards = []
        if os.path.exists(self.db_mock_dir):
            directory = Path(self.db_mock_dir)
            files = list(directory.glob("*_parsed.json"))
            files.extend((directory / "server").glob("*_parsed.json"))
            files.sort()

            self.lbl_count.setText(f"총 {len(files)}개의 악보")

            columns = 2  # 우측 패널이 있으므로 좌측은 2열 카드가 시각적으로 가장 안정적임
            for idx, file in enumerate(files):
                song_title = file.name.replace("_parsed.json", "")
                file_path = str(file)
                metadata = {}
                if file.parent == directory / "server":
                    try:
                        metadata_path = file.parent / file.name.replace("_parsed.json", ".meta.json")
                        with metadata_path.open(encoding="utf-8") as cached_file:
                            data = json.load(cached_file)
                        if isinstance(data, dict) and data.get("cache_source") == "server":
                            metadata = data
                            song_title = data.get("title") or song_title
                    except (OSError, ValueError):
                        pass

                card = ScoreCard(
                    song_title=song_title,
                    file_path=file_path,
                    on_practice_cb=self.launch_practice_by_path,
                    on_edit_cb=self.go_to_admin_by_path,
                    on_remove_cb=self.remove_score,
                    score_id=metadata.get("score_id"),
                    is_default=metadata.get("is_default"),
                )
                card.setFixedWidth(275)

                row = idx // columns
                col = idx % columns
                self.grid_layout.addWidget(card, row, col)
                self.cards.append((song_title.lower(), card))

    def remove_score(self, card):
        if self.remove_thread is not None:
            return
        if not api_client.access_token:
            QMessageBox.warning(self, "로그인 필요", "로그인 후 악보를 삭제할 수 있습니다.")
            return
        if not card.score_id or card.is_default is True:
            return
        answer = QMessageBox.question(
            self, "악보 삭제", f"'{card.song_title}' 악보를 개인 악보함에서 삭제하시겠습니까?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.remove_thread = RemoveScoreThread(
            card.score_id, card.file_path, self.db_mock_dir, self,
        )
        self.remove_thread.finished.connect(self.on_score_removed)
        self.setEnabled(False)
        self.remove_thread.start()

    def on_score_removed(self):
        worker = self.remove_thread
        self.remove_thread = None
        self.setEnabled(True)
        self.refresh_song_list()
        self.filter_song_cards(self.search_input.text())
        if worker.message:
            QMessageBox.warning(self, "악보 삭제 결과", worker.message)
        worker.deleteLater()

    def filter_song_cards(self, text):
        query = text.strip().lower()
        visible_idx = 0
        columns = 2

        for song_title, card in self.cards:
            if not query or query in song_title:
                card.setVisible(True)
                self.grid_layout.removeWidget(card)
                row = visible_idx // columns
                col = visible_idx % columns
                self.grid_layout.addWidget(card, row, col)
                visible_idx += 1
            else:
                card.setVisible(False)

    def launch_practice_by_path(self, target_path):
        if not os.path.exists(target_path):
            QMessageBox.critical(self, "파일 오류", f"선택한 악보 파일이 존재하지 않습니다:\n{target_path}")
            return

        print(f"[연습 시작 요청] 전달 악보: {target_path}")
        if hasattr(self.main_window, "switch_to_practice"):
            self.main_window.switch_to_practice(target_path)
        else:
            self.main_window.stacked_widget.setCurrentIndex(2)

    def go_to_admin_by_path(self, target_path):
        if hasattr(self.main_window, "switch_to_admin"):
            self.main_window.switch_to_admin(target_path)
        else:
            self.main_window.stacked_widget.setCurrentIndex(3)

    def go_to_login(self):
        """로그인 화면(Index 0)으로 복귀"""
        if hasattr(self.main_window, "stacked_widget"):
            self.main_window.stacked_widget.setCurrentIndex(0)

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

    # def on_parsing_completed(self, success, message):
    #     if hasattr(self, "loading_dialog") and self.loading_dialog:
    #         self.loading_dialog.close()
    #     if success:
    #         QMessageBox.information(self, "처리 완료", message)
    #         self.refresh_song_list()
    #     else:
    #         QMessageBox.critical(self, "오류", message)
    def sync_with_server(self):
        """로그인 후 서버의 최신 악보 목록과 동기화"""
        success, result = api_client.sync_scores(self.db_mock_dir)
        self.refresh_song_list()
        if success:
            if result > 0:
                print(f"[동기화 성공] 서버로부터 {result}개의 신규 악보를 다운로드했습니다.")
            else:
                print("[동기화 확인] 로컬 악보 보관함이 최신 상태입니다.")
        else:
            print(f"[동기화 실패/건너뜀] {result}")

    # 적용직전(26.10.08 19:11)
    # def on_parsing_completed(self, success, message):
    #     if hasattr(self, "loading_dialog") and self.loading_dialog:
    #         self.loading_dialog.close()

    #     if success:
    #         # 💡 [핵심] 파싱 완료 시 로컬 저장에 이어 서버 POST /api/scores 전송
    #         base_name = getattr(self.parse_thread, "file_path", "")
    #         song_title = os.path.splitext(os.path.basename(base_name))[0].lower()
    #         output_json_path = os.path.join(self.db_mock_dir, f"{song_title}_parsed.json")

    #         if os.path.exists(output_json_path):
    #             try:
    #                 with open(output_json_path, "r", encoding="utf-8") as f:
    #                     parsed_data = json.load(f)

    #                 # 백엔드로 전송
    #                 up_ok, up_msg = api_client.upload_score(f"{song_title}_parsed.json", parsed_data)
    #                 if up_ok:
    #                     message += "\n(서버 클라우드 동기화 완료)"
    #                 else:
    #                     message += f"\n(로컬 저장 완료 / 서버 업로드 실패: {up_msg})"
    #             except Exception as e:
    #                 print(f"[업로드 예외] {e}")

    #         QMessageBox.information(self, "처리 완료", message)
    #         self.refresh_song_list()
    #     else:
    #         QMessageBox.critical(self, "오류", message)
            
    def on_parsing_completed(self, success, message):
        """GuitarPro 파싱 완료 시 백엔드 업로드 및 화면 갱신"""
        if hasattr(self, "loading_dialog") and self.loading_dialog:
            self.loading_dialog.close()

        if success:
            base_file_path = getattr(self.parse_thread, "file_path", "")
            raw_title = os.path.splitext(os.path.basename(base_file_path))[0]
            clean_title = raw_title.replace("_", " ").title()
            output_json_path = os.path.join(self.db_mock_dir, f"{raw_title.lower()}_parsed.json")

            if os.path.exists(output_json_path):
                try:
                    with open(output_json_path, "r", encoding="utf-8") as f:
                        parsed_data = json.load(f)

                    # 💡 백엔드 스펙에 맞춰 전체 음표 리스트를 그대로 전송
                    up_ok, up_res = api_client.upload_score(
                        title=f"{clean_title} - Client Upload",
                        parsed_notes=parsed_data,
                        source_type="guitar_pro"
                    )

                    if up_ok:
                        message += "\n\n[클라우드 업로드 완료]"
                        score = up_res.get("score", up_res)
                        api_client.cache_server_score(
                            self.db_mock_dir, score.get("score_id") or score.get("id"),
                            score.get("title", clean_title), parsed_data, is_default=False,
                        )
                        # This is the parser's generated cache, not the selected source file.
                        os.unlink(output_json_path)
                        message += "\n서버 악보 보관함에 안전하게 등록되었습니다."
                    else:
                        message += f"\n\n[로컬 저장 완료 / 서버 업로드 실패]\n{up_res}"

                except Exception as e:
                    print(f"[업로드 예외] {e}")
                    message += f"\n(업로드 예외: {e})"

            QMessageBox.information(self, "악보 등록 결과", message)
            self.refresh_song_list()
        else:
            QMessageBox.critical(self, "파싱 실패", message)
            
    def clear_local_scores(self):
        """로컬 악보 저장소(self.db_mock_dir)의 캐시 파일들을 모두 삭제"""
        try:
            if hasattr(self, "db_mock_dir") and os.path.exists(self.db_mock_dir):
                for filename in os.listdir(self.db_mock_dir):
                    file_path = os.path.join(self.db_mock_dir, filename)
                    if os.path.isfile(file_path) or os.path.islink(file_path):
                        os.unlink(file_path)
                    elif os.path.isdir(file_path):
                        shutil.rmtree(file_path)
                print(f"[캐시 정리] 로컬 악보 파일({self.db_mock_dir})이 모두 정리되었습니다.")

            # 현재 화면의 카드 그리드 비우기
            if hasattr(self, "refresh_song_list"):
                self.refresh_song_list()
        except Exception as e:
            print(f"[캐시 정리 실패] {e}")

    def go_to_login(self):
        """로그인 화면 복귀 처리 (로컬 악보 삭제 + 세션 초기화 + 화면 이동)"""
        # 1. 로컬에 저장되어 있던 이전 유저의 악보 파일들 삭제
        self.clear_local_scores()

        # 2. 백엔드 인증 토큰 제거
        api_client.logout()

        # 3. 로그인 화면으로 이동
        if hasattr(self, "main_window") and hasattr(
            self.main_window, "stacked_widget"
        ):
            self.main_window.stacked_widget.setCurrentIndex(0)
            print("[네비게이션] 로그인 화면으로 복귀했습니다.")
