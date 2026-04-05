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

## 기능

- 웹캠 실시간 입력
- YOLO 추론 결과 `fret`, `nut` 선분과 키포인트 오버레이
- 음 선택 `없음, 도, 레, 미, 파, 솔, 라, 시`
- 표준튜닝(EADGBE) 기준으로 선택 음의 모든 가시 후보 위치 점 표시
- `현 방향 반전` 토글로 1번줄/6번줄 방향 보정
- `fret`, `nut` 클래스별 개수와 visible fret 수 표시
- confidence 슬라이더와 image size 조절
- 모델 파일 선택 및 카메라 인덱스 선택

## 참고

- macOS에서는 처음 실행 시 카메라 권한 허용이 필요할 수 있습니다.
- 현재 모델은 `PoseModel`로 인식되며, 각 검출 결과에 2개의 키포인트를 함께 그리도록 구성했습니다.
- 음 위치 점은 검출된 `nut/fret` 선분으로 지판 좌표계를 만들고, 프렛 사이 `85%` 지점에 운지 위치 기준으로 계산합니다.
