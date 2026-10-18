# 개발 가이드

이 문서는 메인 사용자 앱을 여러 명이 동시에 개발할 때 지켜야 할 구조와 규칙을 정리합니다.

지판 추적, 음정 인식, 정답 판정의 전체 데이터 흐름은 [파이프라인 설계](PIPELINE_ARCHITECTURE.md)를 참고합니다.

## 기본 방향

- `app/main.py`는 실행 진입점입니다. 기능 로직이나 UI 구현을 넣지 않습니다.
- `app/features/`는 기능 로직을 둡니다. 카메라, 오디오, 악보 로딩, 상태 계산 같은 실제 동작은 이쪽에 둡니다.
- `app/ui/`는 PySide6 화면 구성만 둡니다. 패널, 위젯, 메인 창 조립은 이쪽에 둡니다.
- 기능을 추가할 때는 먼저 `features`에 로직을 만들고, 화면이 필요할 때만 `ui`에 패널/위젯을 추가합니다.
- `app/ui/main_window.py`는 각 기능과 UI 패널을 연결하는 곳입니다. 새 기능 전체를 여기에 직접 구현하지 않습니다.

## 현재 폴더 역할

```text
app/
  main.py                 # 앱 실행, CLI 인자 처리
  config.py               # 기본 경로와 앱 설정값
  practice_markers.py     # PracticeStep -> 지판 위 AR 점 변환
  features/
    video.py              # 카메라, 지판 검출, AR 오버레이 워커
    video_capture.py      # 카메라 입력 스레드, 최신 프레임 한 장 유지
    practice_audio.py     # 인터랙티브 연습용 오디오 워커 연결/판정 보조
    audio_devices.py      # 마이크 입력 장치 목록
  ui/
    main_window.py        # 메인 창 조립과 기능 연결
    camera_panel.py       # 카메라 컨트롤 패널
    practice_panel.py     # 연습/마이크/모드/자동 흐름 배속 컨트롤 패널
    tracking_panel.py     # 지판 맞춤 컨트롤 패널
    video_surface.py      # 카메라 영상 표시와 선택 오버레이
```

## 기능 추가 위치

- 카메라/지판 검출/AR 점 표시 변경: `app/features/video.py`, `app/practice_markers.py`
- 지판 비동기 재탐색: `vision/fretboard_recovery.py`. 기준 영상의 읽기 전용 스냅샷을 사용하며, 재탐색 결과를 현재 영상에서 검증하는 부분은 `vision/fretboard_motion.py`에 둡니다.
- 음 인식, 정답 판정, 인터랙티브 진행 변경: `app/features/practice_audio.py`, `audio/`
- 마이크 선택 관련 변경: `app/features/audio_devices.py`, `app/ui/practice_panel.py`
- 악보 파일 로드 기능 추가: 새 파일 `app/features/score_loader.py` 권장, 화면은 `app/ui/score_panel.py` 권장
- 새 버튼이나 새 화면 영역 추가: `app/ui/`에 패널/위젯을 만들고 `app/ui/main_window.py`에서 연결
- 실행 옵션 추가: 꼭 필요할 때만 `app/main.py`와 `app/config.py` 수정

## 개발 규칙

1. `app/main.py`에는 기능 코드를 넣지 않습니다.
2. `app/ui/main_window.py`에는 긴 알고리즘을 넣지 않습니다. 이벤트 연결, 상태 전달, 패널 조립 정도만 둡니다.
3. PySide6 위젯은 `app/ui/` 아래에 둡니다.
4. 하드웨어나 모델을 직접 다루는 코드는 `app/features/` 또는 기존 도메인 폴더(`audio/`, `vision/`, `practice/`)에 둡니다.
5. 워커 스레드에서 UI를 직접 수정하지 않습니다. Qt `Signal`로 결과를 보내고 UI 계층에서 반영합니다.
6. 카메라/오디오 워커를 교체하거나 중지할 때는 기존 워커를 먼저 `stop()` 처리합니다.
7. 기존 테스트가 있는 로직을 바꾸면 관련 테스트도 함께 수정하거나 추가합니다.
8. 사용자 앱에는 디버그 도구용 UI를 그대로 가져오지 않습니다. 모델 선택, confidence 조절 같은 개발자용 옵션은 기본 사용자 화면에 노출하지 않습니다.

## 유지해야 하는 현재 동작

- 기본 악보는 아직 `examples/score_practice_sample.json`을 사용합니다.
- 일반 연습 화면에서는 지판 grid/검출 선을 표시하지 않습니다.
- `지판 맞추기` 선택 과정에서만 사용자가 클릭할 수 있도록 검출 선을 보여줍니다.
- MediaPipe 손끝 위치 판정 기능은 현재 메인 앱에서 비활성화 상태입니다.
- 사용자는 모델 파일을 직접 선택하지 않습니다. 모델 경로는 실행부에서 내부적으로 resolve합니다.
- 마이크 선택 기능은 연습 패널에 유지합니다.
- 인터랙티브 연습 모드는 정답 음이 들어왔을 때만 다음 step으로 넘어갑니다.
- 자동 흐름 모드는 악보 JSON의 `duration_ms`를 배속 슬라이더 값으로 보정해 사용자 연주 여부와 관계없이 다음 step으로 넘어갑니다.

## 작업 절차

새 기능을 만들 때 권장 흐름은 다음과 같습니다.

1. 기능 책임이 들어갈 `app/features/*.py` 파일을 만들거나 기존 파일을 수정합니다.
2. 기능 입력/출력을 먼저 정합니다. 가능하면 PySide 위젯에 의존하지 않는 함수나 클래스로 만듭니다.
3. 화면이 필요하면 `app/ui/*_panel.py` 또는 `app/ui/*_surface.py`를 추가합니다.
4. `app/ui/main_window.py`에서 새 feature와 새 UI를 최소한으로 연결합니다.
5. `tests/`에 단위 테스트를 추가합니다. 카메라/마이크가 필요한 부분은 가능한 한 순수 함수나 작은 클래스로 분리해 테스트합니다.
6. 아래 검증 명령을 실행합니다.

## 실행과 검증

의존성은 프로젝트 기준으로 `uv`를 우선 사용합니다.

```bash
uv sync --frozen
```

메인 앱 실행:

```bash
uv run python -m app.main
```

특정 카메라로 실행:

```bash
uv run python -m app.main --camera 0
```

테스트:

```bash
uv run pytest
```

빠른 확인이 필요할 때 현재 앱 관련 테스트만 실행할 수 있습니다.

```bash
uv run pytest tests/test_practice_markers.py tests/test_score_steps.py tests/test_fretboard_detection_viewer.py tests/test_practice_audio_feature.py
```

## 코드 리뷰 체크리스트

- 새 기능 코드가 `main.py`에 들어가지 않았는가?
- UI 코드와 기능 로직이 분리되어 있는가?
- 사용자 앱에 디버그용 컨트롤이 노출되지 않았는가?
- 카메라/마이크가 없어도 import와 단위 테스트가 깨지지 않는가?
- QThread 워커 중지/교체 흐름이 안전한가?
- 기본 score 샘플과 기존 연습 모드 동작이 유지되는가?
- 새 로직에 테스트가 있는가?
