import sys
from pathlib import Path

import cv2
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import tools.valid.valid_module as valid_module
from tools.valid.valid_module import AnswerPoints, DEFAULT_DATASET, ValidResultManager, ValidationApp


_BASE_DRAW_POINTS_OVERLAY = ValidationApp.draw_points_overlay


def _draw_points_overlay_with_matches(
    image_bgr,
    gt_points,
    pred_points,
    header_text,
):
    # app 전용 오버레이입니다.
    # 같은 (string, fret) 키를 가진 GT/PRED 점만 연결합니다.
    canvas = image_bgr.copy()

    gt_map = {
        (point.string, point.fret): point
        for point in gt_points
        if point.visible and point.x is not None and point.y is not None
    }
    pred_map = {
        (point.string, point.fret): point
        for point in pred_points
        if point.visible and point.x is not None and point.y is not None
    }

    for key in sorted(set(gt_map.keys()) & set(pred_map.keys())):
        gt_point = gt_map[key]
        pred_point = pred_map[key]
        gt_xy = (int(round(gt_point.x)), int(round(gt_point.y)))
        pred_xy = (int(round(pred_point.x)), int(round(pred_point.y)))
        cv2.line(canvas, gt_xy, pred_xy, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.line(canvas, gt_xy, pred_xy, (40, 40, 40), 1, cv2.LINE_AA)

    return _BASE_DRAW_POINTS_OVERLAY(canvas, gt_points, pred_points, header_text)


class _SwitchableFrameWindow(valid_module._FrameViewerWindow):
    # 프레임 창 상단에 result 선택 UI를 추가한 app 전용 창입니다.
    # 공용 valid_module은 건드리지 않고, app에서만 result 전환 기능을 얹습니다.
    def __init__(self, frames, app_controller, on_result_change, on_pick_result) -> None:
        self.app_controller = app_controller
        # 부모 창의 캐시 구조는 쓰지 않고, 필요한 프레임을 즉시 다시 읽습니다.
        # 주의:
        # - 부모 __init__ 안에서 _render_frame(0)을 바로 호출합니다.
        # - 그래서 app_controller를 super() 전에 먼저 넣어야 첫 렌더가 안전합니다.
        super().__init__(frames, {})
        self._on_result_change = on_result_change
        self._on_pick_result = on_pick_result
        self._result_paths: list[Path] = []
        self._building_combo = False

        # 기존 vertical layout 맨 위에 result 선택 줄을 삽입합니다.
        root_layout = self.centralWidget().layout()
        row = QHBoxLayout()

        row.addWidget(QLabel("Result", self))

        self.result_combo = QComboBox(self)
        self.result_combo.currentIndexChanged.connect(self._handle_result_changed)
        row.addWidget(self.result_combo, stretch=1)

        self.choose_button = QPushButton("Choose Folder", self)
        self.choose_button.clicked.connect(self._handle_pick_result)
        row.addWidget(self.choose_button)

        self.refresh_button = QPushButton("Refresh Runs", self)
        self.refresh_button.clicked.connect(self._handle_refresh)
        row.addWidget(self.refresh_button)

        root_layout.insertLayout(0, row)

    def set_result_choices(self, result_dirs: list[Path], current_result_dir: Path) -> None:
        # result 루트 아래 run_* 목록을 다시 채웁니다.
        # 현재 열려 있는 result가 바뀌어도 combo 상태를 같이 맞춥니다.
        self._building_combo = True
        self._result_paths = list(result_dirs)
        self.result_combo.clear()

        current_index = 0
        for index, result_dir in enumerate(self._result_paths):
            self.result_combo.addItem(result_dir.name, str(result_dir))
            if result_dir.resolve() == current_result_dir.resolve():
                current_index = index

        if self._result_paths:
            self.result_combo.setCurrentIndex(current_index)
        self._building_combo = False

    def replace_data(self, frames) -> None:
        # 같은 창을 유지한 채 frame/result 데이터만 갈아낍니다.
        self.frames = frames
        self.slider.blockSignals(True)
        self.slider.setRange(0, max(len(frames) - 1, 0))
        self.slider.setValue(0)
        self.slider.blockSignals(False)
        if self.frames:
            self._render_frame(0)
        else:
            self.status_label.setText("No frames in selected result.")

    def _handle_result_changed(self, index: int) -> None:
        if self._building_combo:
            return
        if index < 0 or index >= len(self._result_paths):
            return
        self._on_result_change(self._result_paths[index])

    def _handle_pick_result(self) -> None:
        picked = self._on_pick_result()
        if picked is not None:
            self._on_result_change(picked)

    def _handle_refresh(self) -> None:
        self._on_result_change(None)

    def _render_frame(self, index: int) -> None:
        frame = self.frames[index]
        image_bgr, gt_points, pred_points = self.app_controller.load_frame_data(frame.frame_index)
        segment_name = ValidationApp.segment_name(frame.frame_index)
        header = (
            f"frame={frame.frame_index} "
            f"[{segment_name}] "
            f"matched={frame.result.matched_points} "
            f"mean_error_px={frame.result.mean_error_px:.4f}"
        )
        overlay = ValidationApp.draw_points_overlay(
            image_bgr,
            gt_points,
            pred_points,
            header,
        )
        self.frame_view.set_image(ValidationApp.bgr_to_qimage(overlay))
        self.status_label.setText(
            f"frame={frame.frame_index} [{segment_name}] / "
            f"gt={frame.result.gt_points} pred={frame.result.pred_points} "
            f"matched={frame.result.matched_points} "
            f"missing={len(frame.result.missing_keys)} "
            f"max_error_px={frame.result.max_error_px:.4f}"
        )


class _SwitchableReportWindow(valid_module._ReportWindow):
    # 리포트 창은 별도 창을 유지하되, result 전환 시 이미지만 다시 끼웁니다.
    def set_report_image(self, report_image) -> None:
        self.report_view.set_image(ValidationApp.bgr_to_qimage(report_image))


class SwitchableValidationApp(ValidationApp):
    # app 전용 컨트롤러입니다.
    # 책임:
    # - result 폴더 목록 제공
    # - 선택된 result를 다시 로드
    # - frame/report 두 창을 같이 갱신
    def __init__(self, dataset_dir: str | Path, manager: ValidResultManager) -> None:
        # 공용 ValidationApp은 loader를 들고 있도록 설계되어 있지만,
        # app 전용 뷰어는 loader 객체를 만들지 않고 데이터셋 폴더를 직접 읽습니다.
        super().__init__(loader=None, manager=manager)  # type: ignore[arg-type]
        self.dataset_dir = Path(dataset_dir)
        self.images_dir = self.dataset_dir / "images"
        self.labels_dir = self.dataset_dir / "labels_json"
        self.valid_image_paths = self._build_valid_image_paths()
        self.frame_window: _SwitchableFrameWindow | None = None
        self.report_window: _SwitchableReportWindow | None = None

    def _build_valid_image_paths(self) -> list[Path]:
        # loader 없이 app 단에서 검증 가능한 프레임 목록만 한 번 계산합니다.
        # 기준:
        # - images 안에 실제 이미지 파일이 있고
        # - 같은 stem의 labels_json/*.json 이 존재하는 프레임만 사용
        valid_paths: list[Path] = []
        for image_path in sorted(path for path in self.images_dir.iterdir() if path.is_file()):
            answer_path = self.labels_dir / f"{image_path.stem}.json"
            if answer_path.exists():
                valid_paths.append(image_path)
        return valid_paths

    def load_frame_data(self, frame_index: int):
        # 캐시 없이, 현재 선택된 result 기준으로 매번 다시 읽습니다.
        # frame_index는 "검증 가능한 프레임 목록"의 순번으로 해석합니다.
        image_path = self.valid_image_paths[frame_index]
        answer_path = self.labels_dir / f"{image_path.stem}.json"
        input_image = cv2.imread(str(image_path))
        if input_image is None:
            raise FileNotFoundError(f"Could not read image: {image_path}")
        answer_points = AnswerPoints.from_json(
            answer_path,
            frame_index=frame_index,
            total_frames=len(self.valid_image_paths),
        )
        pred_points = self._load_pred_points(frame_index)
        return input_image, answer_points.points, pred_points

    def _list_result_dirs(self) -> list[Path]:
        if not self.manager.result_root.exists():
            return []
        return sorted(
            [path for path in self.manager.result_root.iterdir() if path.is_dir()],
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )

    def _pick_result_dir(self) -> Path | None:
        picked = QFileDialog.getExistingDirectory(
            self.frame_window,
            "Choose Validation Result Directory",
            str(self.manager.result_root),
        )
        if not picked:
            return None
        return Path(picked).resolve()

    def _reload_result(self, result_dir: Path | None) -> None:
        # None이면 최신 run_* 목록만 새로 스캔하고 현재 선택을 유지합니다.
        if result_dir is not None:
            self.manager = ValidResultManager.from_existing(result_dir)
            self.result_dir = self.manager.result_dir

        summary = self.manager.load_summary()
        report_image = self.build_report_image(self.manager.rows, summary)

        result_dirs = self._list_result_dirs()
        if self.frame_window is not None:
            self.frame_window.set_result_choices(result_dirs, self.manager.result_dir)
            self.frame_window.replace_data(self.manager.frames)
        if self.report_window is not None:
            self.report_window.set_report_image(report_image)

    def show(self) -> dict[str, float | int]:
        summary = self.manager.load_summary()
        report_image = self.build_report_image(self.manager.rows, summary)

        app = QApplication.instance()
        owns_app = app is None
        if app is None:
            app = QApplication(sys.argv)

        print("Creating frame slider window...", flush=True)
        self.frame_window = _SwitchableFrameWindow(
            self.manager.frames,
            self,
            on_result_change=self._reload_result,
            on_pick_result=self._pick_result_dir,
        )
        self.frame_window.set_result_choices(self._list_result_dirs(), self.manager.result_dir)
        self.frame_window.move(80, 80)
        self.frame_window.show()
        self.frame_window.raise_()
        self.frame_window.activateWindow()
        print("Frame slider window created.", flush=True)

        print("Creating report window...", flush=True)
        self.report_window = _SwitchableReportWindow(report_image)
        self.report_window.move(220, 120)
        self.report_window.show()
        self.report_window.raise_()
        self.report_window.activateWindow()
        print("Report window created.", flush=True)

        if owns_app:
            app.exec()
        print("Validation windows closed.", flush=True)
        return summary


def main() -> None:
    # Step 1. 결과 폴더 선택
    # - 인자를 주면 그 폴더를 먼저 엽니다.
    # - 인자가 없으면 가장 최근 실행 결과를 엽니다.
    if len(sys.argv) >= 2:
        result_dir = Path(sys.argv[1]).expanduser().resolve()
    else:
        result_dir = ValidResultManager.latest_result_dir()

    # Step 2. 결과를 엽니다.
    # - app은 loader 객체를 쓰지 않고
    # - dataset 폴더를 직접 다시 읽어 프레임/정답을 조립합니다.
    manager = ValidResultManager.from_existing(result_dir)

    # Step 3. app 전용 비교선 오버레이를 붙입니다.
    ValidationApp.draw_points_overlay = staticmethod(_draw_points_overlay_with_matches)

    # Step 4. 결과 확인 앱을 엽니다.
    app = SwitchableValidationApp(DEFAULT_DATASET, manager)
    app.show()


if __name__ == "__main__":
    main()
