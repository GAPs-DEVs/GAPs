from __future__ import annotations

from pathlib import Path
import os
import json

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent, QImage
from PySide6.QtWidgets import QHBoxLayout, QFrame, QScrollArea, QMainWindow, QMessageBox, QVBoxLayout, QWidget, QStackedWidget, QPushButton, QApplication

from app.config import DEFAULT_AUTO_ADVANCE_DELAY_MS, DEFAULT_CONF_THRESHOLD, DEFAULT_IMAGE_SIZE, DEFAULT_SCORE_PATH
from app.features.audio_devices import enumerate_audio_input_devices
from app.features.practice_audio import PracticeAudioController, RetriggerGate, with_step_context
from app.features.video import PracticeFrameStats, PracticeVideoWorker
from app.ui.camera_panel import CameraPanel
from app.ui.practice_panel import PracticePanel
from app.ui.tab_sheet_view import PracticeTabStrip
from app.ui.tracking_panel import TrackingPanel
from app.ui.video_surface import VideoSurface
from practice.score_steps import (
    PracticeStep,
    build_practice_steps_from_json,
    load_parsed_notes_from_json,
)
from tools.fretboard_detection_viewer import enumerate_camera_indices, find_nearest_line
from vision.camera_calibration import CameraCalibration
from vision.fretboard_edge_refinement import EdgeRefinementResult
from vision.fretboard_mapping import (
    DetectedLine,
    StableFretboardGrid,
)
from vision.fretboard_calibration import build_calibrated_grid

from app.pages.select_page import SelectPage
from app.pages.admin_page import AdminPage
from app.pages.login_page import LoginPage


STATUS_TEXT = {
    "idle": "대기",
    "listening": "듣는 중",
    "correct": "정답",
    "incorrect": "오답",
    "no_signal": "신호 없음",
    "error": "오류",
    "complete": "완료",
    "auto": "자동 흐름",
}


class MainWindow(QMainWindow):
    def __init__(
        self,
        model_path: Path,
        score_path: Path = DEFAULT_SCORE_PATH,
        camera_index: int = 0,
        camera_calibration: CameraCalibration | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Guitar Practice")
        self.resize(1440, 900)

        self.model_path = model_path
        self.score_path = score_path
        self.camera_calibration = camera_calibration

        self.practice_steps = build_practice_steps_from_json(score_path)
        if not self.practice_steps:
            raise ValueError("score JSON must contain at least one practice step.")

        self.video_worker: PracticeVideoWorker | None = None
        self._video_stopping = False
        self.audio_controller = PracticeAudioController(self)
        self.audio_controller.result_ready.connect(self._handle_audio_result)
        self.audio_controller.error_message.connect(self._handle_audio_error)
        self.retrigger_gate = RetriggerGate()
        self.current_index = 0
        self.practice_running = False
        self.selection_stage = "idle"
        self._selection_hint_state = None
        self.selected_nut_line: DetectedLine | None = None
        self.selected_middle_line: DetectedLine | None = None
        self.selected_reference_line: DetectedLine | None = None
        self.manual_point_selection = False
        self.selection_points: list[tuple[float, float]] = []
        self.latest_stable_grid: StableFretboardGrid | None = None
        self.latest_edge_refinement: EdgeRefinementResult | None = None
        self.reference_fret_manual_override = False
        self._updating_reference_fret_spin = False
        self._pending_advance_index: int | None = None

        self.interactive_advance_timer = QTimer(self)
        self.interactive_advance_timer.setSingleShot(True)
        self.interactive_advance_timer.timeout.connect(self._handle_interactive_advance_timeout)
        self.auto_flow_timer = QTimer(self)
        self.auto_flow_timer.setSingleShot(True)
        self.auto_flow_timer.timeout.connect(self._handle_auto_flow_tick)

        self.video_label = VideoSurface("카메라가 꺼져있습니다.")
        self.video_label.clicked.connect(self.handle_video_click)
        self.practice_tab_strip = PracticeTabStrip()

        self.camera_panel = CameraPanel()
        self.camera_combo = self.camera_panel.camera_combo
        self.refresh_camera_button = self.camera_panel.refresh_button
        self.refresh_camera_button.clicked.connect(lambda: self.populate_camera_list())
        self.start_video_button = self.camera_panel.start_button
        self.stop_video_button = self.camera_panel.stop_button
        self.start_video_button.clicked.connect(self.start_video)
        self.stop_video_button.clicked.connect(self.stop_video)
        self.reverse_checkbox = self.camera_panel.reverse_checkbox
        self.reverse_checkbox.stateChanged.connect(self.handle_reverse_change)
        self.video_status_label = self.camera_panel.status_label
        self.marker_status_label = self.camera_panel.marker_status_label

        self.tracking_panel = TrackingPanel()
        self.reference_fret_spin = self.tracking_panel.reference_fret_spin
        self.reference_fret_spin.valueChanged.connect(self.handle_reference_fret_change)
        self.select_lines_button = self.tracking_panel.select_lines_button
        self.clear_tracking_button = self.tracking_panel.clear_tracking_button
        self.select_lines_button.clicked.connect(self.begin_line_selection)
        self.clear_tracking_button.clicked.connect(self.clear_manual_tracking)
        self.tracking_panel.manual_points_checkbox.toggled.connect(self.handle_manual_selection_change)

        self.practice_panel = PracticePanel()
        self.microphone_combo = self.practice_panel.microphone_combo
        self.microphone_combo.currentIndexChanged.connect(self.handle_microphone_change)
        self.refresh_microphone_button = self.practice_panel.refresh_microphone_button
        self.refresh_microphone_button.clicked.connect(lambda: self.populate_microphone_list())
        self.mode_combo = self.practice_panel.mode_combo
        self.mode_combo.currentIndexChanged.connect(self.handle_mode_change)
        self.auto_speed_slider = self.practice_panel.auto_speed_slider
        self.auto_speed_slider.valueChanged.connect(self.handle_auto_speed_change)
        self.auto_speed_value_label = self.practice_panel.auto_speed_value_label
        self.start_practice_button = self.practice_panel.start_button
        self.stop_practice_button = self.practice_panel.stop_button
        self.prev_step_button = self.practice_panel.prev_button
        self.next_step_button = self.practice_panel.next_button
        self.reset_practice_button = self.practice_panel.reset_button
        self.start_practice_button.clicked.connect(self.start_practice)
        self.stop_practice_button.clicked.connect(self.stop_practice)
        self.prev_step_button.clicked.connect(self.previous_step)
        self.next_step_button.clicked.connect(self.next_step)
        self.reset_practice_button.clicked.connect(self.reset_practice)
        self.progress_label = self.practice_panel.progress_label
        self.expected_label = self.practice_panel.expected_label
        self.practice_status_label = self.practice_panel.status_label
        self.detected_label = self.practice_panel.detected_label
        self.missing_label = self.practice_panel.missing_label

        # root = QWidget()
        # root_layout = QHBoxLayout(root)
        # root_layout.setContentsMargins(10, 10, 10, 10)
        # root_layout.setSpacing(10)

        # video_column = QWidget()
        # video_column_layout = QVBoxLayout(video_column)
        # video_column_layout.setContentsMargins(0, 0, 0, 0)
        # video_column_layout.setSpacing(8)
        # video_column_layout.addWidget(self.video_label, stretch=1)
        # video_column_layout.addWidget(self.practice_tab_strip)
        
        # ── [연습 화면 루트 컨테이너 (미니멀 라이트 테마)] ──
        root = QWidget()
        root.setStyleSheet("""
            QWidget {
                font-family: -apple-system, BlinkMacSystemFont, 'Pretendard', 'Apple SD Gothic Neo', 'Segoe UI', sans-serif;
                background-color: #f8f9fa;
                color: #212529;
            }

            /* 비디오 및 타브 악보 감싸는 메인 카드 */
            QFrame#video_card {
                background-color: #ffffff;
                border: 1px solid #e9ecef;
                border-radius: 12px;
            }

            /* 우측 사이드 패널 내부의 그룹박스 스타일 일원화 */
            QGroupBox {
                font-size: 13.5px;
                font-weight: 700;
                color: #111827;
                border: 1px solid #e9ecef;
                border-radius: 10px;
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

            /* 콤보박스 및 입력 위젯 */
            QComboBox {
                background-color: #ffffff;
                border: 1px solid #ced4da;
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 13px;
                color: #212529;
                min-height: 22px;
            }
            QComboBox:hover {
                border-color: #adb5bd;
            }
            QComboBox:focus {
                border-color: #2563eb;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }

            /* 슬라이더 */
            QSlider::groove:horizontal {
                height: 6px;
                background: #e9ecef;
                border-radius: 3px;
            }
            QSlider::sub-page:horizontal {
                background: #111827;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #ffffff;
                border: 2px solid #111827;
                width: 16px;
                margin-top: -5px;
                margin-bottom: -5px;
                border-radius: 8px;
            }

            /* 체크박스 */
            QCheckBox {
                font-size: 13px;
                font-weight: 500;
                color: #374151;
                spacing: 6px;
            }

            /* 패널 기본 버튼 (이전, 다음, 처음, 새로고침 등) */
            QGroupBox QPushButton {
                background-color: #ffffff;
                border: 1px solid #ced4da;
                border-radius: 6px;
                font-size: 13px;
                font-weight: 600;
                color: #374151;
                padding: 7px 12px;
                min-height: 20px;
            }
            QGroupBox QPushButton:hover {
                background-color: #f8f9fa;
                border-color: #adb5bd;
                color: #111827;
            }
            QGroupBox QPushButton:pressed {
                background-color: #e9ecef;
            }
            QGroupBox QPushButton:disabled {
                background-color: #f8f9fa;
                border-color: #e9ecef;
                color: #adb5bd;
            }
        """)

        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(20, 20, 20, 20)
        root_layout.setSpacing(16)

        # 좌측 비디오 + 타브 악보 카드 래퍼
        video_card = QFrame()
        video_card.setObjectName("video_card")
        video_column_layout = QVBoxLayout(video_card)
        video_column_layout.setContentsMargins(16, 16, 16, 16)
        video_column_layout.setSpacing(12)
        video_column_layout.addWidget(self.video_label, stretch=1)
        video_column_layout.addWidget(self.practice_tab_strip)

        root_layout.addWidget(video_card, stretch=1)
        root_layout.addWidget(self._build_side_panel())
        
        # root_layout.addWidget(video_column, stretch=1)
        # root_layout.addWidget(self._build_side_panel())

        # ── [4단 라우팅 스택 조립] ──
        self.stacked_widget = QStackedWidget(self)
        self.setCentralWidget(self.stacked_widget)

        # 2. 인스턴스 생성 (이 4줄이 먼저 와야 합니다)
        self.login_page = LoginPage(main_window=self)     # Index 0
        self.select_page = SelectPage(main_window=self)   # Index 1
        self.practice_root = root                         # Index 2
        self.admin_page = AdminPage(main_window=self)     # Index 3

        # 복조된 팀원분 오리지널 패널(root)을 스택의 Index 1로 통째로 안착
        self.stacked_widget.addWidget(self.login_page)
        self.stacked_widget.addWidget(self.select_page)
        self.stacked_widget.addWidget(self.practice_root)
        self.stacked_widget.addWidget(self.admin_page)
        
        # ── [진단용 디버그 출력] ──
        print("\n================== [현재 스택 위젯 배치 순서] ==================")
        for i in range(self.stacked_widget.count()):
            widget = self.stacked_widget.widget(i)
            print(f"Index [{i}]: {widget.__class__.__name__} (메모리 주소: {id(widget)})")
        print("=================================================================\n")

        self.populate_camera_list(selected=camera_index)
        self.populate_microphone_list()
        self.refresh_practice_tab_data()
        self.retrigger_gate.configure(None, self.current_step())
        self.refresh_auto_speed_controls()
        self.refresh_practice_display()
        self.refresh_marker_status()

    # 클래스 내부에 화면 전환 라우터 메서드 추가/보강
    # ── [화면 라우팅 컨트롤러] ──

    def switch_to_select(self):
        """악보 선택 허브(Index 1)로 복귀"""
        if hasattr(self, 'stop_practice'):
            try:
                self.stop_practice()
            except Exception:
                pass
        self.stacked_widget.setCurrentIndex(1)

    def switch_to_practice(self, json_path: str):
        print(f"🔥🔥 [DEBUG] switch_to_practice 실제로 호출됨! 이동할 목표 Index: 2, 현재 파일: {__file__}")
        """선택한 악보 JSON을 로드하고 연습 화면(Index 2)으로 전환"""
        print(f"[switch_to_practice 진입] 경로: {json_path}")
        self.score_path = Path(json_path)
        steps = build_practice_steps_from_json(self.score_path)
        if not steps:
            QMessageBox.warning(self, "데이터 오류", "선택한 악보의 정형 시퀀스가 비어있습니다.")
            return

        self.practice_steps = steps
        self.current_index = 0
        self.refresh_practice_tab_data()
        self.retrigger_gate.configure(None, self.current_step())
        self.refresh_practice_display()

        # 💡 팀원 연습 화면은 Index 2 입니다!
        self.stacked_widget.setCurrentIndex(2)

    def switch_to_admin(self, json_path: str = None):
        print(f"🔥🔥 [DEBUG] switch_to_admin 실제로 호출됨! 이동할 목표 Index: 3, 현재 파일: {__file__}")
        """악보 정밀 에디터(Index 3)로 전환"""
        print(f"[switch_to_admin 진입] 경로: {json_path}")
        if json_path and os.path.exists(json_path):
            self.score_path = Path(json_path)
            self.admin_page.current_file_path = json_path
            if hasattr(self.admin_page, 'file_status'):
                self.admin_page.file_status.setText(f"📂 로드된 파일: {os.path.basename(json_path)}")

            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    self.admin_page.tab_data = json.load(f)
                if isinstance(self.admin_page.tab_data, list):
                    self.admin_page.tab_data.sort(key=lambda x: (x.get('step', 0), x.get('string', 0)))

                if hasattr(self.admin_page, 'tab_sheet_view') and hasattr(self.admin_page.tab_sheet_view, 'set_data'):
                    self.admin_page.tab_sheet_view.set_data(self.admin_page.tab_data)
                if hasattr(self.admin_page, 'refresh_table'):
                    self.admin_page.refresh_table()
            except Exception as e:
                QMessageBox.critical(self, "오류", f"에디터 다이렉트 로드 실패: {e}")

        # 💡 악보 정밀 에디터는 Index 3 입니다!
        self.stacked_widget.setCurrentIndex(3)

    # def _build_side_panel(self) -> QWidget:
    #     panel = QWidget()
    #     panel.setFixedWidth(420)
    #     layout = QVBoxLayout(panel)
    #     layout.setContentsMargins(0, 0, 0, 0)
    #     layout.setSpacing(10)
    #     layout.addWidget(self.camera_panel)
    #     layout.addWidget(self.tracking_panel)
    #     layout.addWidget(self.practice_panel)
        
    #     # +) UI를 전혀 해치지 않고 맨 밑에 홈 복귀 버튼만 추가 배치
    #     btn_home = QPushButton("메인 화면으로 이동")
    #     btn_home.setCursor(Qt.PointingHandCursor)
    #     btn_home.setFixedHeight(45)
    #     btn_home.setStyleSheet("""
    #         QPushButton { 
    #             background-color: #333333; 
    #             color: white; 
    #             font-weight: bold; 
    #             border-radius: 6px; 
    #             font-size: 14px; 
    #             border: none;
    #         }
    #         QPushButton:hover { background-color: #555555; }
    #     """)
    #     # 복귀 시 오디오 캡처와 카메라 스레드를 완벽히 정지시키고 홈으로 안전 이동
    #     btn_home.clicked.connect(lambda: (self.stop_practice(), self.stop_video(), self.switch_to_select()))
    #     layout.addWidget(btn_home)

    #     layout.addStretch(1)
    #     return panel
    def _build_side_panel(self) -> QWidget:
        # 스크롤 가능하도록 안전하게 감싸는 컨테이너 생성
        scroll_area = QScrollArea()
        scroll_area.setFixedWidth(410)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_area.setStyleSheet("""
            QScrollArea {
                background-color: transparent;
                border: none;
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

        panel = QWidget()
        panel.setStyleSheet("background-color: transparent;")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(12)

        # 핵심 패널 3종 배치
        layout.addWidget(self.camera_panel)
        layout.addWidget(self.tracking_panel)
        layout.addWidget(self.practice_panel)

        # 연습 시작 / 중지 버튼 시각적 위계 부여
        if hasattr(self, 'start_practice_button'):
            self.start_practice_button.setStyleSheet("""
                QPushButton {
                    background-color: #111827;
                    color: #ffffff;
                    border: 1px solid #111827;
                    border-radius: 6px;
                    font-weight: 700;
                    padding: 9px;
                }
                QPushButton:hover { background-color: #27272a; }
                QPushButton:disabled { background-color: #e5e7eb; border-color: #e5e7eb; color: #9ca3af; }
            """)
        if hasattr(self, 'stop_practice_button'):
            self.stop_practice_button.setStyleSheet("""
                QPushButton {
                    background-color: #ffffff;
                    color: #dc2626;
                    border: 1px solid #fca5a5;
                    border-radius: 6px;
                    font-weight: 700;
                    padding: 9px;
                }
                QPushButton:hover { background-color: #fef2f2; border-color: #ef4444; }
                QPushButton:disabled { background-color: #f8f9fa; border-color: #e5e7eb; color: #adb5bd; }
            """)

        layout.addSpacing(6)

        # 홈 복귀 버튼 (정돈된 솔리드 다크 톤)
        btn_home = QPushButton("악보 선택으로 복귀")
        btn_home.setCursor(Qt.PointingHandCursor)
        btn_home.setFixedHeight(44)
        btn_home.setStyleSheet("""
            QPushButton {
                background-color: #111827;
                color: #ffffff;
                font-size: 13.5px;
                font-weight: 700;
                border: 1px solid #111827;
                border-radius: 8px;
                padding: 10px;
            }
            QPushButton:hover {
                background-color: #27272a;
                border-color: #27272a;
            }
            QPushButton:pressed {
                background-color: #09090b;
            }
        """)
        btn_home.clicked.connect(lambda: (self.stop_practice(), self.stop_video(), self.switch_to_select()))
        layout.addWidget(btn_home)

        layout.addStretch(1)
        scroll_area.setWidget(panel)
        return scroll_area

    def current_step(self) -> PracticeStep:
        return self.practice_steps[self.current_index]

    def current_mode(self) -> str:
        return str(self.mode_combo.currentData() or "interactive")

    def current_auto_speed_multiplier(self) -> float:
        return max(0.01, self.auto_speed_slider.value() / 100.0)

    def current_microphone_device(self) -> int | str | None:
        return self.microphone_combo.currentData()

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
            self.camera_combo.setCurrentIndex(available_cameras.index(current_selection))

    def populate_microphone_list(self, selected: int | str | None = None) -> None:
        current_selection = selected
        if current_selection is None and self.microphone_combo.currentData() is not None:
            current_selection = self.microphone_combo.currentData()

        self.microphone_combo.blockSignals(True)
        try:
            self.microphone_combo.clear()
            self.microphone_combo.addItem("기본 마이크", None)
            input_devices = enumerate_audio_input_devices()
            for label, device_index in input_devices:
                self.microphone_combo.addItem(label, device_index)

            if current_selection is not None:
                for index in range(self.microphone_combo.count()):
                    if self.microphone_combo.itemData(index) == current_selection:
                        self.microphone_combo.setCurrentIndex(index)
                        break
        finally:
            self.microphone_combo.blockSignals(False)

    def handle_reverse_change(self, _: int) -> None:
        if self.video_worker is not None:
            self.video_worker.set_reverse_strings(self.reverse_checkbox.isChecked())

    def handle_reference_fret_change(self, value: int) -> None:
        if self._updating_reference_fret_spin:
            return
        self.reference_fret_manual_override = True
        # These fields configure the NEXT calibration. Relabelling an existing
        # model would silently throw away its measured middle correspondence.

    def handle_mode_change(self, _index: int = 0) -> None:
        self.refresh_auto_speed_controls()
        if not self.practice_running:
            self.refresh_practice_display()
            return
        self.stop_practice()
        self.start_practice()

    def handle_auto_speed_change(self, _value: int = 0) -> None:
        self.refresh_auto_speed_controls()
        if self.practice_running and self.current_mode() == "auto":
            self._schedule_auto_advance()

    def handle_microphone_change(self, _index: int = 0) -> None:
        if not self.practice_running or self.current_mode() != "interactive":
            return
        self._cancel_pending_interactive_advance()
        self._start_audio_worker_for_current_step()

    def set_reference_fret_spin_value(self, value: int) -> None:
        self._updating_reference_fret_spin = True
        try:
            self.reference_fret_spin.setValue(value)
        finally:
            self._updating_reference_fret_spin = False

    def start_video(self) -> None:
        if self.video_worker is not None and self.video_worker.isRunning():
            return
        if not self.model_path.exists():
            QMessageBox.critical(self, "실행 파일 오류", "기타 지판 인식 파일을 찾을 수 없습니다. 설치 상태를 확인해주세요.")
            return

        self.video_worker = PracticeVideoWorker(
            model_path=self.model_path,
            camera_index=int(self.camera_combo.currentData() or 0),
            conf_threshold=DEFAULT_CONF_THRESHOLD,
            image_size=DEFAULT_IMAGE_SIZE,
            current_step=self.current_step(),
            reverse_strings=self.reverse_checkbox.isChecked(),
            show_edge_guides=False,
            edge_supported_notes_only=False,
            camera_calibration=self.camera_calibration,
            parent=self,
        )
        self.video_worker.frame_ready.connect(self.update_frame)
        self.video_worker.error_message.connect(self.handle_video_error)
        self.video_worker.finished.connect(self.on_video_worker_finished)
        self._video_stopping = False
        self.camera_panel.set_running(True)
        self.selection_stage = "idle"
        self.tracking_panel.hint_label.setText(TrackingPanel.READY_HINT)
        self.video_label.setCursor(Qt.CursorShape.ArrowCursor)
        self.video_status_label.setText("카메라 실행 중")
        self.video_worker.start()

    def stop_video(self) -> None:
        if self.video_worker is None:
            return
        self._video_stopping = True
        self.video_label.reset_placeholder()
        self.refresh_marker_status()
        self.video_status_label.setText("카메라를 끄는 중")
        self.video_worker.stop()

    def update_frame(self, image: QImage, stats: PracticeFrameStats) -> None:
        if self.video_worker is None or self._video_stopping:
            return
        self.video_label.set_current_image(image)
        self.latest_stable_grid = stats.stable_grid
        self.latest_edge_refinement = stats.edge_refinement

        if stats.tracking_mode == "selection_ready" and self.selection_stage == "await_frame":
            self.selection_stage = "select_nut"
            self.video_status_label.setText("화면에서 너트 선을 클릭하세요.")
        elif self.selection_stage == "idle":
            if stats.tracking_mode == "calibration_required":
                self.video_status_label.setText("먼저 기준 프렛 번호를 지정하고 지판 맞추기를 눌러주세요.")
            elif stats.tracking_mode in {"selected_lines_tracking", "selected_lines_reacquired"}:
                self.video_status_label.setText("지판 맞춤 완료")
                self.tracking_panel.hint_label.setText("지판 맞춤 완료 · 연습을 시작하세요.")
            elif stats.tracking_mode in {"selected_lines_motion", "selected_lines_continuity"}:
                self.video_status_label.setText("지판 움직임 추적 중")
            elif stats.tracking_mode == "selected_lines_hold":
                self.video_status_label.setText("지판을 다시 찾는 중")
            elif stats.tracking_mode == "selected_lines_reference_weak":
                self.video_status_label.setText("기준 영상이 불충분합니다. 손을 잠깐 떼고 지판을 선명하게 다시 맞춰주세요.")
            elif stats.tracking_mode == "selected_lines_lost":
                self.video_status_label.setText("기준 지판을 다시 찾는 중 · 계속 실패하면 다시 맞춰주세요.")
            else:
                self.video_status_label.setText("카메라 실행 중")
            if stats.tracking_mode.startswith("selected_lines_") and not stats.hand_mask_available:
                self.video_status_label.setText(self.video_status_label.text() + " · 손 가림 감지 비활성")

        self.render_current_frame()
        self.marker_status_label.setText(f"가이드 점 {stats.marker_count}개")

    def handle_video_error(self, message: str) -> None:
        QMessageBox.warning(self, "카메라 오류", message)
        self.video_status_label.setText(message)
        self.stop_video()

    def on_video_worker_finished(self) -> None:
        self.camera_panel.set_running(False)
        self.video_worker = None
        self._video_stopping = False
        self.selection_stage = "idle"
        self.selected_nut_line = None
        self.selected_middle_line = None
        self.selected_reference_line = None
        self.selection_points = []
        self.tracking_panel.hint_label.setText(TrackingPanel.READY_HINT)
        self.latest_stable_grid = None
        self.latest_edge_refinement = None
        self.video_label.setCursor(Qt.CursorShape.ArrowCursor)
        self.video_label.reset_placeholder()
        self.video_status_label.setText("카메라 대기")
        self.refresh_marker_status()
        self.render_current_frame()

    def refresh_marker_status(self) -> None:
        self.marker_status_label.setText("가이드 점 0개")

    def refresh_auto_speed_controls(self) -> None:
        is_auto_mode = self.current_mode() == "auto"
        self.practice_panel.set_auto_speed_controls_visible(is_auto_mode)
        self.auto_speed_value_label.setText(f"{self.current_auto_speed_multiplier():.2f}x")

    def refresh_practice_tab_data(self) -> None:
        try:
            self.practice_tab_strip.set_data(load_parsed_notes_from_json(self.score_path))
        except Exception:
            self.practice_tab_strip.set_data([])

    def begin_line_selection(self) -> None:
        if self.selection_stage != "idle":
            return
        if not self.tracking_panel.commit_and_validate():
            return
        if self.video_worker is None or not self.video_worker.isRunning():
            QMessageBox.information(self, "카메라 필요", "카메라를 먼저 시작해주세요.")
            return
        if not self.video_worker.begin_line_selection():
            QMessageBox.warning(self, "준비 중", "카메라 화면이 아직 준비되지 않았습니다.")
            return

        self.selection_stage = "await_frame"
        self.manual_point_selection = self.tracking_panel.manual_points_checkbox.isChecked()
        self.selection_points = []
        self.selected_nut_line = None
        self.selected_middle_line = None
        self.selected_reference_line = None
        self.video_label.setCursor(Qt.CursorShape.CrossCursor)
        self.video_status_label.setText("지판 맞춤을 준비하는 중입니다.")
        self.render_current_frame()

    def clear_manual_tracking(self) -> None:
        self.selection_stage = "idle"
        self.selected_nut_line = None
        self.selected_middle_line = None
        self.selected_reference_line = None
        self.selection_points = []
        self.tracking_panel.clear_errors()
        self.tracking_panel.hint_label.setText(TrackingPanel.READY_HINT)
        self.video_label.setCursor(Qt.CursorShape.ArrowCursor)
        if self.video_worker is not None:
            self.video_worker.clear_manual_tracking()
        self.video_status_label.setText("카메라 실행 중" if self.video_worker is not None else "카메라 대기")
        self.render_current_frame()

    def handle_manual_selection_change(self, checked: bool) -> None:
        self.manual_point_selection = checked
        if self.selection_stage in {"select_nut", "select_middle", "select_fret"}:
            # Reuse the frozen frame, but never mix detected lines with a
            # partially collected six-point calibration.
            self.selection_stage = "select_nut"
            self.selected_nut_line = None
            self.selected_middle_line = None
            self.selected_reference_line = None
            self.selection_points = []
        self.render_current_frame()

    def map_label_click_to_image(self, label_x: float, label_y: float) -> tuple[float, float] | None:
        return self.video_label.map_label_point_to_image(label_x, label_y)

    def handle_video_click(self, label_x: float, label_y: float) -> None:
        if self.selection_stage not in {"select_nut", "select_middle", "select_fret"} or self.video_worker is None:
            return

        image_point = self.map_label_click_to_image(label_x, label_y)
        if image_point is None:
            return

        if self.manual_point_selection:
            self.selection_points.append(image_point)
            if len(self.selection_points) % 2:
                self.render_current_frame()
                return
            first, second = self.selection_points[-2:]
            if (first[0] - second[0]) ** 2 + (first[1] - second[1]) ** 2 < 64:
                self.selection_points = self.selection_points[:-2]
                self.video_status_label.setText("선의 양 끝점을 충분히 떨어뜨려 지정해주세요.")
                self.render_current_frame()
                self.tracking_panel.hint_label.setText("양 끝점을 충분히 떨어뜨려 다시 지정해주세요.")
                return
            kind = "nut" if self.selection_stage == "select_nut" else "fret"
            selected_line = DetectedLine(kind, first, second, 1.0)
        else:
            kind = "nut" if self.selection_stage == "select_nut" else "fret"
            candidates = tuple(line for line in self.video_worker.frozen_selection_lines if line.kind == kind)
            selected_line = find_nearest_line(image_point, candidates)
            if selected_line is None:
                message = "선을 가까이 클릭하거나 ‘수동으로 양 끝점 지정’을 선택하세요."
                self.video_status_label.setText(message)
                self.tracking_panel.hint_label.setText(message)
                return

        if self.selection_stage == "select_nut":
            self.selected_nut_line = selected_line
            self.selection_stage = "select_middle"
            self.video_status_label.setText(f"실제 {self.tracking_panel.middle_fret_spin.value()}프렛의 금속선을 클릭하세요.")
        elif self.selection_stage == "select_middle":
            self.selected_middle_line = selected_line
            self.selected_reference_line = selected_line
            self.selection_stage = "select_fret"
            self.video_status_label.setText(f"실제 {self.reference_fret_spin.value()}프렛의 금속선을 클릭하세요.")
        else:
            self.selected_reference_line = selected_line
            if self.selected_nut_line is not None:
                try:
                    grid = build_calibrated_grid(
                        self.selected_nut_line, self.selected_middle_line,
                        self.tracking_panel.middle_fret_spin.value(), selected_line,
                        self.reference_fret_spin.value(),
                    )
                except ValueError as error:
                    if self.manual_point_selection:
                        self.selection_points = self.selection_points[:2]
                    self.selection_stage = "select_middle"
                    self.selected_middle_line = None
                    self.selected_reference_line = None
                    self.video_status_label.setText(str(error))
                    self.render_current_frame()
                    self.tracking_panel.hint_label.setText(f"중간 프렛을 다시 선택하세요. {error}")
                    return
                self.video_worker.apply_calibrated_selection(grid, self.reference_fret_spin.value())
            self.selection_stage = "idle"
            self.video_label.setCursor(Qt.CursorShape.ArrowCursor)
            self.video_status_label.setText("지판 맞춤을 적용하는 중입니다.")
            self.tracking_panel.hint_label.setText("지판 맞춤을 적용하는 중입니다.")

        self.render_current_frame()

    def render_current_frame(self) -> None:
        self.reference_fret_spin.setEnabled(self.selection_stage == "idle")
        self.tracking_panel.middle_fret_spin.setEnabled(self.selection_stage == "idle")
        self.select_lines_button.setEnabled(self.selection_stage == "idle")
        hint_state = (self.selection_stage, self.manual_point_selection, len(self.selection_points),
                      self.tracking_panel.middle_fret_spin.value(), self.reference_fret_spin.value())
        if hint_state != self._selection_hint_state:
            self._selection_hint_state = hint_state
            self.refresh_selection_hint()
        self.video_label.render_frame(
            selection_stage=self.selection_stage,
            selected_nut_line=self.selected_nut_line,
            selected_reference_line=self.selected_reference_line,
            selection_points=tuple(self.selection_points) if self.manual_point_selection else (),
            manual_points=self.manual_point_selection,
            reference_fret_index=int(self.tracking_panel.middle_fret_spin.value()
                                     if self.selection_stage == "select_middle" else self.reference_fret_spin.value()),
        )

    def refresh_selection_hint(self) -> None:
        if self.selection_stage == "await_frame":
            self.tracking_panel.hint_label.setText("지판 맞춤을 준비하고 있습니다.")
        elif self.selection_stage in {"select_nut", "select_middle", "select_fret"}:
            step, target = {
                "select_nut": (1, "너트"),
                "select_middle": (2, f"중간 {self.tracking_panel.middle_fret_spin.value()}프렛"),
                "select_fret": (3, f"끝 {self.reference_fret_spin.value()}프렛"),
            }[self.selection_stage]
            action = "선을 선택하세요."
            if self.manual_point_selection:
                endpoint = "첫 번째" if len(self.selection_points) % 2 == 0 else "반대쪽"
                action = f"{endpoint} 끝점을 선택하세요."
            self.tracking_panel.hint_label.setText(f"{step}/3 · {target} {action}")
            if not self.manual_point_selection and self.video_worker is not None:
                kind = "nut" if self.selection_stage == "select_nut" else "fret"
                if not any(line.kind == kind for line in self.video_worker.frozen_selection_lines):
                    self.tracking_panel.hint_label.setText(f"{step}/3 · {target} 선이 인식되지 않았습니다. ‘수동으로 양 끝점 지정’을 선택하세요.")
        if self.selection_stage != "idle":
            self.video_status_label.setText(self.tracking_panel.hint_label.text())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.render_current_frame()

    def refresh_practice_display(self) -> None:
        step = self.current_step()
        mode_text = "단일음" if step.mode == "single" else "다중음"
        self.progress_label.setText(f"{self.current_index + 1} / {len(self.practice_steps)} (step {step.step}, {mode_text})")
        self.expected_label.setText(", ".join(f"{note.label} {note.frequency:.2f}Hz" for note in step.notes))
        self.practice_tab_strip.set_current_step(step.step)
        if self.practice_running and self.current_mode() == "auto":
            self.practice_status_label.setText(STATUS_TEXT["auto"])
        elif self.practice_running:
            self.practice_status_label.setText(STATUS_TEXT["listening"])
        else:
            self.practice_status_label.setText(STATUS_TEXT["idle"])
        self.detected_label.setText("-")
        self.missing_label.setText("-")
        if self.video_worker is not None:
            self.video_worker.set_current_step(step)

    def start_practice(self) -> None:
        if self.practice_running:
            return
        self.practice_running = True
        self.practice_panel.set_running(True)
        self._cancel_pending_interactive_advance()
        self.retrigger_gate.configure(None, self.current_step())
        self.refresh_practice_display()

        if self.current_mode() == "interactive":
            self._start_audio_worker_for_current_step()
        else:
            self._stop_audio_worker()
            self.practice_status_label.setText(STATUS_TEXT["auto"])
            self._schedule_auto_advance()

    def stop_practice(self) -> None:
        self.practice_running = False
        self.auto_flow_timer.stop()
        self._cancel_pending_interactive_advance()
        self._stop_audio_worker()
        self.practice_panel.set_running(False)
        self.practice_status_label.setText(STATUS_TEXT["idle"])

    def reset_practice(self) -> None:
        self._move_to_step(0)

    def previous_step(self) -> None:
        if self.current_index <= 0:
            return
        self._move_to_step(self.current_index - 1)

    def next_step(self) -> None:
        if self.current_index >= len(self.practice_steps) - 1:
            self._complete_practice()
            return
        self._move_to_step(self.current_index + 1)

    def _move_to_step(self, index: int) -> None:
        previous_step = self.current_step()
        was_running = self.practice_running
        mode = self.current_mode()
        self._cancel_pending_interactive_advance()
        if mode == "interactive":
            self._stop_audio_worker()
        elif mode == "auto":
            self.auto_flow_timer.stop()

        self.current_index = max(0, min(index, len(self.practice_steps) - 1))
        self.retrigger_gate.configure(previous_step, self.current_step())
        self.refresh_practice_display()

        if was_running and mode == "interactive":
            self.practice_running = True
            self.practice_panel.set_running(True)
            self._start_audio_worker_for_current_step()
        elif was_running and mode == "auto":
            self.practice_status_label.setText(STATUS_TEXT["auto"])
            self._schedule_auto_advance()

    def _complete_practice(self) -> None:
        self.practice_running = False
        self.auto_flow_timer.stop()
        self._cancel_pending_interactive_advance()
        self._stop_audio_worker()
        self.practice_panel.set_running(False)
        self.practice_status_label.setText(STATUS_TEXT["complete"])

    def _auto_interval_ms(self) -> int:
        return max(1, int(round(self.current_step().duration_ms / self.current_auto_speed_multiplier())))

    def _schedule_auto_advance(self) -> None:
        self.auto_flow_timer.start(self._auto_interval_ms())

    def _handle_auto_flow_tick(self) -> None:
        if not self.practice_running or self.current_mode() != "auto":
            return
        if self.current_index >= len(self.practice_steps) - 1:
            self._complete_practice()
            return
        self._move_to_step(self.current_index + 1)

    def _start_audio_worker_for_current_step(self) -> None:
        step = self.current_step()
        microphone_device = self.current_microphone_device()
        self.practice_status_label.setText(STATUS_TEXT["listening"])
        self.audio_controller.start_for_step(step, microphone_device)

    def _stop_audio_worker(self) -> None:
        self.audio_controller.stop()

    def _handle_audio_result(self, result: dict) -> None:
        if self._pending_advance_index is not None:
            return
        step = self.current_step()
        enriched = with_step_context(result, step, self.current_index, len(self.practice_steps))
        enriched = self.retrigger_gate.apply(enriched)
        self._render_audio_result(enriched)

        if enriched.get("is_correct") and not enriched.get("held"):
            self._schedule_interactive_advance()

    def _handle_audio_error(self, message: str) -> None:
        self.practice_status_label.setText(STATUS_TEXT["error"])
        self.detected_label.setText(message)

    def _render_audio_result(self, result: dict) -> None:
        status = str(result.get("status", "idle"))
        self.practice_status_label.setText(STATUS_TEXT.get(status, status))

        if result.get("practice_mode") == "single":
            detected_frequency = result.get("detected_frequency")
            cents = result.get("cents_difference")
            if detected_frequency is None:
                self.detected_label.setText("-")
            elif cents is None:
                self.detected_label.setText(f"{float(detected_frequency):.2f} Hz")
            else:
                self.detected_label.setText(f"{float(detected_frequency):.2f} Hz / {float(cents):+.1f} cents")
            self.missing_label.setText("-" if result.get("is_correct") else str(result.get("expected_label", "-")))
            return

        matched_notes = result.get("matched_notes") or []
        missing_notes = result.get("missing_notes") or []
        self.detected_label.setText(", ".join(matched_notes) if matched_notes else "-")
        self.missing_label.setText(", ".join(missing_notes) if missing_notes else "-")

    def _schedule_interactive_advance(self) -> None:
        self._pending_advance_index = self.current_index
        if DEFAULT_AUTO_ADVANCE_DELAY_MS == 0:
            self._handle_interactive_advance_timeout()
            return
        self.interactive_advance_timer.start(DEFAULT_AUTO_ADVANCE_DELAY_MS)

    def _handle_interactive_advance_timeout(self) -> None:
        pending_index = self._pending_advance_index
        self._pending_advance_index = None
        if pending_index is None:
            return
        if not self.practice_running or self.current_mode() != "interactive" or pending_index != self.current_index:
            return
        if self.current_index >= len(self.practice_steps) - 1:
            self._complete_practice()
            return
        self._move_to_step(self.current_index + 1)

    def _cancel_pending_interactive_advance(self) -> None:
        if self.interactive_advance_timer.isActive():
            self.interactive_advance_timer.stop()
        self._pending_advance_index = None

    def closeEvent(self, event: QCloseEvent) -> None:
        self.stop_practice()
        self.stop_video()
        super().closeEvent(event)
