# Guitar Practice Support App

기타 연습을 돕는 `PySide6` 데스크톱 앱입니다. 메인 실행 대상은 `app/main.py`이며, 웹캠 기반 지판 위치 표시, 마이크 기반 음정/화음 판정, 악보 선택/업로드, 악보 수정 에디터를 하나의 앱 흐름으로 제공합니다.

기존의 지판 검출 디버그 뷰어는 개발 보조 도구로 남아 있으며, 이 리포지토리의 중심은 메인 사용자 앱인 `app`입니다.

## 주요 기능

- 악보 선택 화면에서 `examples/*_parsed.json` 악보 목록 확인
- GuitarPro `gp3/gp5` 파일 업로드 후 연습용 JSON 변환
- 선택한 악보로 연습 화면 진입
- 웹캠 영상 위에 현재 연습 step의 기타 줄/프렛 위치 표시
- 지판 맞추기 기능으로 `nut`과 현재 보이는 기준 프렛을 선택해 AR 마커 위치 보정
- 마이크 입력으로 단음/화음 판정
- 정답을 연주하면 다음 step으로 넘어가는 인터랙티브 연습 모드
- 악보의 `duration_ms` 기준으로 자동 진행되는 자동 흐름 모드
- 악보 수정 에디터에서 step, string, fret 데이터 확인 및 수정

## 설치

이 프로젝트는 `uv` 기준으로 의존성을 관리합니다.

```bash
uv sync --frozen
```

Python 버전과 잠금된 의존성은 `pyproject.toml`, `uv.lock` 기준으로 관리합니다.

## 실행

메인 앱 실행:

```bash
uv run python -m app.main
```

특정 카메라 인덱스로 실행:

```bash
uv run python -m app.main --camera 0
```

특정 악보 JSON으로 실행:

```bash
uv run python -m app.main --score-json examples/score_practice_sample.json
```

모델 또는 카메라 보정 파일을 직접 지정할 수도 있습니다.

```bash
uv run python -m app.main --model models/best06.pt --camera-calibration /absolute/path/to/camera_calibration.npz
```

## 기본 사용 흐름

1. 앱을 실행하면 악보 선택 및 업로드 화면이 열립니다.
2. 목록에서 악보를 선택하고 `선택한 악보로 연습 시작`을 누릅니다.
3. 연습 화면에서 카메라와 마이크 입력 장치를 선택합니다.
4. `카메라 시작` 후 `지판 맞추기`에서 `nut`과 기준 프렛을 선택해 표시 위치를 맞춥니다.
5. 연습 모드를 고르고 `연습 시작`을 누릅니다.
6. 인터랙티브 연습 모드는 정답 음을 감지하면 다음 step으로 이동합니다.
7. 자동 흐름 모드는 사용자 연주 여부와 관계없이 악보의 시간 정보에 맞춰 진행합니다.

## 프로젝트 구조

```text
app/
  main.py                 # 메인 앱 실행 진입점, CLI 인자 처리
  config.py               # 기본 경로와 앱 설정값
  practice_markers.py     # PracticeStep을 지판 위 AR 마커로 변환
  pages/
    select_page.py        # 악보 선택/업로드 화면
    admin_page.py         # 악보 수정 에디터
  features/
    video.py              # 카메라, 지판 검출, AR 오버레이 워커
    practice_audio.py     # 연습용 오디오 판정 컨트롤러
    audio_devices.py      # 마이크 입력 장치 목록
  ui/
    main_window.py        # 메인 창과 화면 전환 조립
    camera_panel.py       # 카메라 컨트롤
    tracking_panel.py     # 지판 맞추기 컨트롤
    practice_panel.py     # 연습/마이크/모드 컨트롤
    video_surface.py      # 카메라 영상 표시 영역

audio/                    # 음정/화음 감지 로직
vision/                   # 지판 검출, 보정, 맵핑 로직
practice/                 # 악보 step 데이터 모델과 변환 로직
widgets/                  # 독립 실행/테스트용 위젯
tools/                    # 개발용 디버그 도구
examples/                 # 샘플 및 변환된 악보 JSON
models/                   # YOLO/MediaPipe 모델 파일
tests/                    # 단위 테스트
```

## 악보 데이터

메인 앱은 기본적으로 `examples/score_practice_sample.json`을 사용합니다. 악보 JSON은 연습 step 단위의 배열이며, 주요 필드는 다음과 같습니다.

- `step`: 연주 순서
- `string`: 기타 줄 번호
- `fret`: 프렛 번호
- `duration_ms`: 자동 흐름 모드에서 해당 step을 유지할 시간
- `midi_pitch`: 오디오 가이드나 후속 기능에서 사용할 MIDI 음고

GuitarPro 업로드 기능은 변환된 파일을 `examples/*_parsed.json` 형태로 저장하고, 악보 선택 화면에서 다시 불러옵니다.

## 테스트

전체 테스트 실행:

```bash
uv run pytest
```

앱 관련 빠른 확인:

```bash
uv run pytest tests/test_practice_markers.py tests/test_score_steps.py tests/test_practice_audio_feature.py
```

## 개발 참고

- 메인 앱 개발 규칙은 `app/DEVELOPMENT_GUIDE.md`를 따릅니다.
- 사용자 앱의 실행 진입점은 `app/main.py`입니다.
- 개발용 지판 검출 뷰어는 `tools/fretboard_detection_viewer.py`에 남아 있습니다.
- macOS에서는 처음 실행할 때 카메라와 마이크 권한 허용이 필요할 수 있습니다.
- 기본 YOLO 모델은 `models/best06.pt`를 사용합니다.
- 카메라 보정 파일은 `npz` 형식이며 `camera_matrix`, `dist_coeffs`, `image_size` 키를 권장합니다.
