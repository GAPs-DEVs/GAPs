# Audio Judgement API

이 문서는 현재 오디오 판정 프로토타입을 실행하는 방법과, 이후 기존 앱에 Worker/Widget을 연결할 때 필요한 API 사용법을 정리한다.

## 의존성

현재 오디오 판정은 다음 라이브러리를 사용한다.

- `PySide6`: GUI, `QThread`, `Signal`, `QTimer`
- `sounddevice`: 마이크 입력 스트림
- `numpy`: 오디오 샘플 배열 처리
- `librosa`: 단일음 YIN, 다중음 CQT 분석

프로젝트 의존성 설치:

```bash
uv sync
```

직접 설치가 필요하면:

```bash
uv add PySide6 sounddevice numpy librosa
```

## 위젯 실행 명령어

### 단일음 판정 위젯

낮은 도부터 높은 도까지 선택해서 단일음 F0 추정을 테스트한다.

```bash
uv run python widgets/pitch_judge_widget.py
```

기본 동작:

- `sounddevice.InputStream`으로 마이크 입력을 계속 받음
- `block_size=4096`, `sample_rate=44100`
- 한 block은 약 `4096 / 44100 = 0.093초`
- `librosa.yin`으로 기본 주파수(F0)를 추정
- 기본 허용 오차는 `±30 cents`

### 다중음 판정 위젯

여러 음을 체크박스로 선택하고 동시에 쳤을 때 기대 음이 있는지 테스트한다.

```bash
uv run python widgets/chord_judge_widget.py
```

기본 동작:

- 마이크 입력은 `block_size=2048` 단위로 계속 받음
- 최근 `analysis_window_ms=650`, 즉 약 0.65초 구간을 모아서 분석
- `librosa.cqt`로 주파수 대역별 에너지를 계산
- 기대 주파수 주변 에너지가 충분하면 해당 음을 감지한 것으로 판단
- 낮은 줄의 배음이 높은 목표음처럼 잡히는 경우를 줄이기 위해 harmonic rejection을 적용

### 악보 JSON 기반 step 연습 위젯

파싱된 악보 JSON을 읽고 step별로 단일음/다중음 판정을 자동 선택한다.

```bash
uv run python widgets/score_practice_widget.py --score-json examples/score_practice_sample.json
```

정답 표시 후 다음 step으로 넘어가는 시간 조절:

```bash
uv run python widgets/score_practice_widget.py \
  --score-json examples/score_practice_sample.json \
  --auto-advance-delay-ms 1200
```

기본값은 `800ms`이다.

## JSON 입력 형식

최상위 배열 형태:

```json
[
  { "step": 1, "string": 1, "fret": 7 },
  { "step": 1, "string": 6, "fret": 0 },
  { "step": 2, "string": 2, "fret": 0 }
]
```

또는 `notes`로 감싼 형태:

```json
{
  "notes": [
    { "step": 1, "string": 1, "fret": 7 },
    { "step": 1, "string": 6, "fret": 0 },
    { "step": 2, "string": 2, "fret": 0 }
  ]
}
```

`parsed_notes` 키도 허용한다.

해석 규칙:

- `step`: 연주 순서
- `string`: 기타 줄 번호. 6번 줄은 저음 E2, 1번 줄은 고음 E4
- `fret`: 프렛 번호
- 같은 `step`에 note가 1개면 단일음 판정
- 같은 `step`에 note가 2개 이상이면 다중음 판정

줄/프렛은 `audio.music_math.guitar_note_frequency()`로 정답 주파수로 변환한다.

```text
frequency = open_string_frequency * 2^(fret / 12)
```

표준 튜닝:

```text
6번 줄 E2 = 82.41Hz
5번 줄 A2 = 110.00Hz
4번 줄 D3 = 146.83Hz
3번 줄 G3 = 196.00Hz
2번 줄 B3 = 246.94Hz
1번 줄 E4 = 329.63Hz
```

## QWidget 연결 예시

### PitchJudgeWidget

단일음 판정용 독립 위젯이다.

```python
from widgets.pitch_judge_widget import PitchJudgeWidget

widget = PitchJudgeWidget(
    expected_frequency=196.0,
    tolerance_cents=30,
)
layout.addWidget(widget)

def handle_pitch_result(result: dict) -> None:
    print(result["status"], result["detected_frequency"], result["cents_difference"])

widget.result_changed.connect(handle_pitch_result)
```

주요 생성자 인자:

- `expected_frequency`: 정답 주파수
- `tolerance_cents`: 허용 오차. 기본 `30`
- `sample_rate`: 기본 `44100`
- `block_size`: 기본 `4096`
- `min_freq`: 기본 `70`
- `max_freq`: 기본 `1000`
- `result_hold_ms`: 최근 감지 결과 유지 시간. 기본 `900`
- `pitch_backend`: 기본 `"librosa_yin"`

### ChordJudgeWidget

다중음 테스트용 독립 위젯이다.

```python
from widgets.chord_judge_widget import ChordJudgeWidget

widget = ChordJudgeWidget(
    expected_notes=["낮은 도", "미", "솔"],
    tolerance_cents=35,
)
layout.addWidget(widget)

widget.result_changed.connect(lambda result: print(result["status"], result["matched_notes"]))
```

### ScorePracticeWidget

악보 파싱 결과 JSON 또는 note list를 받아 step별 판정을 진행하는 위젯이다.

```python
from widgets.score_practice_widget import ScorePracticeWidget

widget = ScorePracticeWidget.from_json_file(
    "examples/score_practice_sample.json",
    auto_advance_delay_ms=800,
)
layout.addWidget(widget)

def handle_result(result: dict) -> None:
    print(result["practice_step"], result["practice_mode"], result["status"])

widget.result_changed.connect(handle_result)
```

직접 list를 넣을 수도 있다.

```python
parsed_notes = [
    {"step": 1, "string": 1, "fret": 7},
    {"step": 1, "string": 6, "fret": 0},
    {"step": 2, "string": 2, "fret": 0},
]

widget = ScorePracticeWidget(parsed_notes)
```

주요 특징:

- step별 note 개수에 따라 `AudioWorker` 또는 `ChordAudioWorker`를 자동 선택
- 정답이면 기본 `800ms` 동안 정답 표시 후 다음 step으로 이동
- 같은 기대 음이 연속 step에 나오면 `no_signal`이 한 번 감지된 뒤 다시 쳐야 정답 처리
- `step_changed`, `result_changed`, `practice_completed` Signal 제공

## Worker 직접 연결

위젯을 쓰지 않고 기존 앱 로직에서 Worker만 직접 연결할 수도 있다.

### AudioWorker

단일음 판정 Worker이다.

```python
from audio.audio_worker import AudioWorker

worker = AudioWorker(
    expected_frequency=196.0,
    tolerance_cents=30,
)

def handle_result(result: dict) -> None:
    if result["status"] == "correct":
        print("correct")
    elif result["status"] == "incorrect":
        print("incorrect", result["cents_difference"])

def handle_error(message: str) -> None:
    print("audio error:", message)

worker.result_ready.connect(handle_result)
worker.error_message.connect(handle_error)
worker.start()

# 종료 시 반드시 호출
worker.stop()
worker.deleteLater()
```

`AudioWorker.result_ready` dict 필드:

```python
{
    "detected_frequency": float | None,
    "expected_frequency": float,
    "cents_difference": float | None,
    "is_correct": bool,
    "status": "listening" | "correct" | "incorrect" | "no_signal" | "error",
    "rms": float,
    "peak": float,
    "clarity": float,
    "message": str,
    "stream_status": str,
    "held": bool,
    "held_ms": int,
    "pitch_backend": str,
}
```

### ChordAudioWorker

다중음 판정 Worker이다.

```python
from audio.chord_audio_worker import ChordAudioWorker
from audio.polyphonic_detection import ExpectedFrequency

worker = ChordAudioWorker(
    expected_frequencies=[
        ExpectedFrequency("5번줄 3프렛", 130.81),
        ExpectedFrequency("4번줄 2프렛", 164.81),
        ExpectedFrequency("3번줄 0프렛", 196.00),
    ],
    tolerance_cents=35,
)

worker.result_ready.connect(lambda result: print(result["status"], result["missing_notes"]))
worker.error_message.connect(lambda message: print("audio error:", message))
worker.start()

# 종료 시 반드시 호출
worker.stop()
worker.deleteLater()
```

`ChordAudioWorker.result_ready` dict 주요 필드:

```python
{
    "status": "listening" | "correct" | "incorrect" | "no_signal" | "error",
    "is_correct": bool,
    "matches": [
        {
            "name": str,
            "expected_frequency": float,
            "detected_frequency": float | None,
            "cents_difference": float | None,
            "energy_db": float,
            "is_present": bool,
            "rejection_reason": str,
        }
    ],
    "matched_notes": list[str],
    "missing_notes": list[str],
    "rejected_notes": list[str],
    "rms": float,
    "peak": float,
    "noise_floor_db": float,
    "message": str,
    "stream_status": str,
    "held": bool,
    "held_ms": int,
    "backend": "librosa_cqt",
    "expected_frequencies": list[dict],
}
```

## Worker 생명주기 주의사항

Worker는 `QThread`를 상속한다. 앱 종료, 화면 전환, 위젯 종료 시 반드시 `stop()`을 호출해야 한다.

```python
def closeEvent(self, event):
    if self.worker is not None:
        self.worker.stop()
        self.worker.deleteLater()
        self.worker = None
    super().closeEvent(event)
```

중복 스트림 방지:

- 이미 실행 중이면 새 Worker를 또 만들지 않는다.
- 기대 음이 바뀌면 기존 Worker를 `stop()`하고 새 Worker를 만든다.
- UI 갱신은 Worker 내부에서 직접 하지 않고 Signal로 main thread에 전달한다.

## 알고리즘 요약

### 단일음

```text
sounddevice InputStream
→ 4096 sample block
→ librosa.yin
→ detected_frequency
→ expected_frequency와 cents 비교
```

### 다중음

```text
sounddevice InputStream
→ 2048 sample block 누적
→ 최근 650ms audio buffer 유지
→ librosa.cqt
→ 기대 주파수 주변 energy_db 확인
→ harmonic rejection
→ matched/missing note 계산
```

## 현재 한계

- 다중음 판정은 범용 코드 인식기가 아니라, "기대 음이 있는지" 확인하는 프로토타입이다.
- 마이크 입력에는 기타 잔향, 배음, 주변 소음이 섞인다.
- 같은 음 반복은 현재 `no_signal`을 한 번 요구해서 잔향으로 연속 통과하는 문제를 막는다.
- 더 자연스럽게 반복음을 처리하려면 추후 attack/onset 감지가 필요하다.
- pitch만으로는 같은 주파수를 내는 다른 줄/프렛을 구분할 수 없다. 줄 위치까지 판정하려면 카메라/손 위치/악보 정보와 결합해야 한다.
