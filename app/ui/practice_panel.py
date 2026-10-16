from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QSlider, QProgressBar, QToolButton, QWidget


class PracticePanel(QGroupBox):
    def __init__(self) -> None:
        super().__init__("연습")

        self.microphone_combo = QComboBox()
        # Windows의 긴 오디오 장치명도 우측 패널 너비를 강제로 늘리지 않게 한다.
        self.microphone_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.microphone_combo.setMinimumContentsLength(8)
        self.refresh_microphone_button = QPushButton("새로고침")

        # 기기마다 입력 볼륨이 달라 무음 판정 기준을 연주 환경에 맞춰 선택한다.
        self.input_sensitivity_combo = QComboBox()
        self.input_sensitivity_combo.addItem("보통", 0.002)
        self.input_sensitivity_combo.addItem("작은 연주 소리", 0.0005)
        self.input_sensitivity_combo.addItem("주변 소음이 큰 환경", 0.006)
        self.input_level = QProgressBar()
        self.input_level.setRange(0, 100)
        self.input_level.setValue(0)
        self.input_level.setFormat("연습 시작 후 확인")
        self.input_level.setToolTip("입력 신호 크기입니다. 막대가 움직여도 악보의 음과 일치해야 정답이 됩니다.")

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("인터랙티브 연습", "interactive")
        self.mode_combo.addItem("자동 흐름", "auto")

        self.auto_speed_label = QLabel("배속")
        self.auto_speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.auto_speed_slider.setRange(5, 200)
        self.auto_speed_slider.setSingleStep(5)
        self.auto_speed_slider.setPageStep(10)
        self.auto_speed_slider.setTickInterval(50)
        self.auto_speed_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.auto_speed_slider.setValue(100)
        self.auto_speed_value_label = QLabel("1.00x")
        self.auto_speed_value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.start_button = QPushButton("연습 시작")
        self.stop_button = QPushButton("연습 중지")
        self.stop_button.setEnabled(False)
        self.prev_button = QPushButton("이전")
        self.next_button = QPushButton("다음")
        self.reset_button = QPushButton("처음")

        self.progress_label = QLabel()
        self.expected_label = QLabel()
        self.expected_label.setWordWrap(True)
        self.status_label = QLabel()
        self.detected_label = QLabel("-")
        self.missing_label = QLabel("-")
        self._configure_dynamic_labels()
        self._build_layout()

    def _configure_dynamic_labels(self) -> None:
        # 진행 정보와 화음 결과의 길이는 곡마다 다르므로 줄바꿈을 허용한다.
        for label in (self.progress_label, self.status_label, self.detected_label, self.missing_label):
            label.setWordWrap(True)
        self.progress_label.setStyleSheet("font-size: 16px; font-weight: 700;")
        self.expected_label.setStyleSheet("font-size: 13px;")
        self.status_label.setStyleSheet("font-size: 14px; font-weight: 700;")

    def _build_layout(self) -> None:
        layout = QGridLayout(self)
        layout.setVerticalSpacing(10)
        layout.addWidget(QLabel("마이크"), 0, 0)
        layout.addWidget(self.microphone_combo, 0, 1, 1, 3)
        layout.addWidget(self.refresh_microphone_button, 0, 4)
        layout.addWidget(QLabel("모드"), 1, 0)
        layout.addWidget(self.mode_combo, 1, 1, 1, 4)
        layout.addWidget(self.auto_speed_label, 2, 0)
        layout.addWidget(self.auto_speed_slider, 2, 1, 1, 3)
        layout.addWidget(self.auto_speed_value_label, 2, 4)

        button_row = QHBoxLayout()
        button_row.addWidget(self.start_button)
        button_row.addWidget(self.stop_button)
        layout.addLayout(button_row, 3, 0, 1, 5)

        nav_row = QHBoxLayout()
        nav_row.addWidget(self.prev_button)
        nav_row.addWidget(self.next_button)
        nav_row.addWidget(self.reset_button)
        layout.addLayout(nav_row, 4, 0, 1, 5)

        layout.addWidget(QLabel("진행"), 5, 0)
        layout.addWidget(self.progress_label, 5, 1, 1, 4)
        layout.addWidget(QLabel("누를 위치"), 6, 0)
        layout.addWidget(self.expected_label, 6, 1, 1, 4)
        layout.addWidget(QLabel("상태"), 7, 0)
        layout.addWidget(self.status_label, 7, 1, 1, 4)
        layout.addWidget(QLabel("감지된 소리"), 8, 0)
        layout.addWidget(self.detected_label, 8, 1, 1, 4)
        layout.addWidget(QLabel("아직 부족한 음"), 9, 0)
        layout.addWidget(self.missing_label, 9, 1, 1, 4)
        # 연습 기본 화면은 악보·판정에 집중하고, 입력 진단은 필요할 때만 펼친다.
        self.audio_settings_toggle = QToolButton()
        self.audio_settings_toggle.setText("마이크 설정")
        self.audio_settings_toggle.setCheckable(True)
        self.audio_settings_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.audio_settings_toggle.setArrowType(Qt.ArrowType.RightArrow)
        layout.addWidget(self.audio_settings_toggle, 10, 0, 1, 5)
        self.audio_settings_widget = QWidget()
        audio_layout = QGridLayout(self.audio_settings_widget)
        audio_layout.setContentsMargins(0, 4, 0, 0)
        audio_layout.addWidget(QLabel("입력 감도"), 0, 0)
        audio_layout.addWidget(self.input_sensitivity_combo, 0, 1)
        audio_layout.addWidget(QLabel("입력 세기"), 1, 0)
        audio_layout.addWidget(self.input_level, 1, 1)
        layout.addWidget(self.audio_settings_widget, 11, 0, 1, 5)
        self.audio_settings_toggle.toggled.connect(self.set_audio_settings_visible)
        self.set_audio_settings_visible(False)
        self.set_auto_speed_controls_visible(False)

    def set_audio_settings_visible(self, visible: bool) -> None:
        self.audio_settings_widget.setVisible(visible)
        self.audio_settings_toggle.setChecked(visible)
        self.audio_settings_toggle.setArrowType(Qt.ArrowType.DownArrow if visible else Qt.ArrowType.RightArrow)

    def set_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        if not running:
            self.input_level.setValue(0)
            self.input_level.setFormat("연습 시작 후 확인")

    def set_input_level(self, rms: float) -> None:
        # 입력 크기는 로그 축으로 표시한다. 작은 기타 신호도 변화를 볼 수 있게
        # -80dBFS부터 0dBFS까지 0~100으로 바꾸며, 판정 임계값은 별도로 유지한다.
        import math
        level_db = 20 * math.log10(max(float(rms), 1e-6))
        self.input_level.setValue(max(0, min(100, round((level_db + 80) * 100 / 80))))
        self.input_level.setFormat("입력 없음" if rms < 1e-5 else "입력 감지")

    def set_auto_speed_controls_visible(self, visible: bool) -> None:
        self.auto_speed_label.setVisible(visible)
        self.auto_speed_slider.setVisible(visible)
        self.auto_speed_value_label.setVisible(visible)
        self.auto_speed_slider.setEnabled(visible)
