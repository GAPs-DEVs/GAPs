# Guitar Fretboard PySide Detector

`best06.pt` 가중치를 사용해서 웹캠 영상 위에 기타 지판 관련 검출 결과를 그려주는 `PySide6` 데스크톱 앱입니다.

## 실행

```bash
python3 pyside_fretboard_detector.py
```

다른 가중치를 쓰고 싶으면:

```bash
python3 pyside_fretboard_detector.py --model /absolute/path/to/model.pt --camera 0
```

카메라 보정 파일이 있으면:

```bash
python3 pyside_fretboard_detector.py --camera-calibration /absolute/path/to/camera_calibration.npz
```

## 기능

- 웹캠 실시간 입력
- `camera_calibration.npz` 또는 `--camera-calibration` 입력 시 프레임 undistort 적용
- 선택 모드에서는 YOLO 추론 결과 `fret`, `nut` 선분을 표시하고, 추적 중에는 가짜 중간 프렛선 혼동을 줄이기 위해 `nut`/선택 기준선과 줄 가이드를 중심으로 표시
- `Select Nut + Last Visible Fret` 버튼으로 YOLO가 검출한 선 중 `nut` 1개와 현재 보이는 마지막 프렛 1개를 클릭 선택
- 선택된 두 선만 매 프레임 YOLO 재검출 결과에서 다시 매칭해서 추적
- 선택 상태에서는 마지막 사각형 주변 ROI를 우선 탐색하고, 필요하면 전체 프레임으로 자동 재획득
- 검출이 잠깐 흔들려도 즉시 해제하지 않고 `hold -> reacquire` 방식으로 안정화
- 수동 선택 추적은 선택 시점의 `마지막 가시 프렛` 번호를 자동 추정한 뒤 그 기준을 잠가서 추적
- 선택한 기준 프렛 번호에서는 기준선과 실제 간격 모델을 유지하되, 음 점 후보는 실제 검출/확정된 인접 프렛 경계 사이에만 표시
- 자동 모드도 visible fret 여러 개를 실제 프렛선 기준으로 유지하면서 note 좌표를 안정화
- 음 선택 `없음, 도, 레, 미, 파, 솔, 라, 시, 높은 도`
- `중앙 테스트 점` 토글을 켜면 선택한 음을 지판 중앙선 위 단일 점으로 표시
- `MediaPipe` 손끝 검출을 별도 모듈([hand_tracking.py](/Users/kjw/Desktop/01.Project/capstone/guitar_fret_test/hand_tracking.py))로 분리
- 손끝이 음 점 반경 안에 들어오면 정확 운지로 판단하고 해당 점 색상을 변경
- 표준튜닝(EADGBE) 기준으로 선택 음의 모든 가시 후보 위치 점 표시
- `현 방향 반전` 토글로 1번줄/6번줄 방향 보정
- `fret`, `nut` 클래스별 개수와 visible fret 수 표시
- confidence 슬라이더와 image size 조절
- 모델 파일 선택 및 카메라 인덱스 선택

## 참고

- macOS에서는 처음 실행 시 카메라 권한 허용이 필요할 수 있습니다.
- 현재 모델은 `PoseModel`로 인식되며, 각 검출 결과에 2개의 키포인트를 함께 그리도록 구성했습니다.
- 손끝 검출은 `mediapipe`가 설치된 경우 자동 활성화됩니다. 설치되지 않으면 앱은 계속 동작하지만 손 검출만 비활성화됩니다.
- 손 landmark 모델은 프로젝트의 [models/hand_landmarker.task](/Users/kjw/Desktop/01.Project/capstone/guitar_fret_test/models/hand_landmarker.task) 를 사용합니다.
- 음 위치 점은 안정화된 `nut/fret` 기준으로 계산하고, 운지 위치는 해당 프렛선 바로 뒤쪽에 표시합니다.
- calibration 파일은 `npz` 형식이며 `camera_matrix`, `dist_coeffs`, `image_size` 키를 권장합니다.
- 수동 선택 순서는 `Select Nut + Last Visible Fret` 클릭 후 `nut 선 클릭 -> 현재 보이는 마지막 프렛 선 클릭` 입니다.
- `Last Visible Fret #` 값은 사용자가 클릭한 마지막 가시 프렛 위치를 바탕으로 자동 계산되며, 점이 밀리면 직접 수정할 수 있습니다.
