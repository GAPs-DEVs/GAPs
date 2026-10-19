"""Optional benchmark controls, hidden while the normal app is measured."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout


class ScreenBenchmarkDialog(QDialog):
    def __init__(self, parent, *, duration=30):
        super().__init__(parent)
        self.setWindowTitle('화면 성능 측정 설정 · F8 설정 / F9 시작·중단')
        self.setModal(False)
        layout = QVBoxLayout(self)
        hint = QLabel('메인 앱에서 악보 선택 → 카메라 시작 → 지판 맞추기 후 측정하세요.\n측정 중에는 이 창이 숨겨지고, 통계는 종료 후 계산합니다.')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        row = QHBoxLayout()
        row.addWidget(QLabel('측정 시간'))
        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(1, 600)
        self.duration_spin.setValue(duration)
        self.duration_spin.setSuffix(' 초')
        row.addWidget(self.duration_spin)
        self.start_button = QPushButton('시간 지정 측정 시작')
        row.addWidget(self.start_button)
        self.save_button = QPushButton('결과 저장')
        self.save_button.setEnabled(False)
        row.addWidget(self.save_button)
        layout.addLayout(row)
        self.status_label = QLabel('설정창을 닫아도 F8로 다시 열 수 있습니다. F9로 바로 측정할 수 있습니다.')
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status_label)
        self.resize(600, 180)

    def show_result(self, report, directory):
        summary = report['summary']
        run = report['timed_run']
        def value(key):
            return '—' if summary[key] is None else f'{summary[key]:.2f}'
        self.status_label.setText(
            f"종료 상태: {run['status']} · {run['elapsed_seconds']:.1f}초\n"
            f"화면 FPS {value('paint_fps')} · 프레임 간격 p95 {value('paint_interval_p95_ms')} ms\n"
            f"입력→화면 p95 {value('capture_to_paint_p95_ms')} ms · >50ms 비율 {value('long_frame_percent')}%\n"
            f'저장됨: {directory}\nsummary.json · frames.csv · report.md'
        )
