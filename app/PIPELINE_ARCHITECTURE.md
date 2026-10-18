# 기타 연습 앱 파이프라인 설계

이 문서는 현재 코드의 실제 실행 흐름을 기준으로 지판 추적, 음정 인식, 정답 판정, 연습 진행의 관계를 정리합니다.

> 이 앱의 "음성 인식"은 사람의 말을 문자로 바꾸는 음성 인식(STT)이 아닙니다. 마이크에서 기타의 기본 주파수 또는 여러 목표 주파수를 찾는 **음정 인식**입니다.

## 1. 전체 구조

악보의 같은 `PracticeStep`이 영상과 오디오 파이프라인에 동시에 전달됩니다. 영상은 연주 위치를 보여주고, 오디오는 연주한 음이 맞는지 판정합니다.

```mermaid
flowchart LR
    SCORE["악보 JSON<br/>step, string, fret, duration_ms"]
    PARSE["score_steps.py<br/>step별 그룹화 및 주파수 계산"]
    STEP["PracticeStep<br/>현재 연주할 음들"]
    MAIN["MainWindow<br/>현재 step과 연습 상태 관리"]

    SCORE --> PARSE --> STEP --> MAIN

    subgraph VIDEO["영상 안내 파이프라인"]
        CAMERA["웹캠 최신 프레임"]
        BOARD["보정된 지판 추적"]
        MARKER["string/fret -> 화면 안내점"]
        FRAME["카메라 영상 + AR 안내점"]
        CAMERA --> BOARD --> MARKER --> FRAME
    end

    subgraph AUDIO["음정 판정 파이프라인"]
        MIC["마이크 입력"]
        DETECT["단일음 YIN / 화음 CQT"]
        JUDGE["목표 주파수와 비교"]
        GATE["반복음 재입력 게이트"]
        MIC --> DETECT --> JUDGE --> GATE
    end

    MAIN -->|"현재 string/fret"| MARKER
    MAIN -->|"현재 목표 주파수"| DETECT
    FRAME -->|"frame_ready"| MAIN
    GATE -->|"result_ready"| MAIN
    MAIN -->|"정답이면 다음 step"| STEP

    VIDEO_ONLY["화면 안내 전용<br/>현재 정답 판정에는 사용하지 않음"]
    FRAME -.-> VIDEO_ONLY
```

핵심 경계는 다음과 같습니다.

- 영상의 안내점과 손가락 위치는 현재 정답 여부를 결정하지 않습니다.
- 인터랙티브 모드는 마이크 판정이 `is_correct=True`이고 `held=False`일 때만 다음 step으로 이동합니다.
- 자동 흐름 모드는 마이크를 사용하지 않고 `duration_ms / 배속` 시간이 지나면 이동합니다.
- 손가락 끝과 안내점 매칭 구현은 있지만 메인 앱에서는 `HAND_TRACKING_ENABLED = False`로 비활성화되어 있습니다. 지판 추적에서 손 영역을 제외하는 `HandOcclusionMasker`는 이 설정과 별개로 계속 사용합니다.

## 2. 악보 데이터 파이프라인

```mermaid
flowchart LR
    JSON["JSON note"] --> VALIDATE["step/string/fret/duration_ms 검증"]
    VALIDATE --> FREQ["기타 표준 튜닝으로 주파수 계산<br/>f = open * 2^(fret/12)"]
    FREQ --> GROUP["같은 step 번호끼리 그룹화"]
    GROUP --> MODE{"목표 음 개수"}
    MODE -->|"1개"| SINGLE["mode = single"]
    MODE -->|"2개 이상"| CHORD["mode = chord"]
    SINGLE --> STEP["PracticeStep"]
    CHORD --> STEP
```

`PracticeStep`은 양쪽 파이프라인이 공유하는 기준 데이터입니다.

| 필드 | 의미 | 소비자 |
| --- | --- | --- |
| `step` | 악보의 진행 번호 | `MainWindow`, 악보 UI |
| `notes[].string_number` | 기타 줄 번호 1~6 | 지판 안내점 계산 |
| `notes[].fret_number` | 프렛 번호, 0은 개방현 | 지판 안내점 계산 |
| `expected_frequencies` | 각 음의 이름과 목표 Hz | 오디오 워커 |
| `duration_ms` | 해당 step의 표시 시간 | 자동 흐름 모드 |

## 3. 지판 보정 및 추적

### 3.1 최초 지판 맞춤

```mermaid
flowchart TD
    START["카메라 시작"] --> CAPTURE["LatestFrameCapture<br/>최신 프레임 한 장만 유지"]
    CAPTURE --> PRE["카메라 왜곡 보정(선택)<br/>좌우 반전"]
    PRE --> WAIT{"지판 맞춤 요청?"}
    WAIT -->|"아니오"| REQUIRED["calibration_required<br/>AR 안내점 없음"]
    WAIT -->|"예"| YOLO["YOLO pose 추론"]
    YOLO --> LINES["nut/fret 클래스의<br/>두 keypoint -> DetectedLine"]
    LINES --> FREEZE["현재 프레임과 검출선 고정"]
    FREEZE --> SELECT["사용자 선택<br/>너트 -> 중간 프렛 -> 끝 프렛"]
    MANUAL["대안: 각 선의 양 끝점을 직접 선택"] --> SELECT
    SELECT --> INDEX["중간/끝 프렛의 실제 번호 결합"]
    INDEX --> CALIBRATE["build_calibrated_grid<br/>3개 선으로 1차원 원근 간격 보정"]
    CALIBRATE --> GRID["StableFretboardGrid<br/>너트 + 순서화된 프렛 + 줄 교차점"]
    GRID --> SEED["기준 영상·기준 격자 고정<br/>추적기와 비동기 복구기 seed"]
```

보정 격자는 다음 순서로 생성됩니다.

1. 너트와 끝 프렛으로 지판 사각 영역을 정규화합니다.
2. 중간 프렛의 실제 번호와 화면 위치로 길이 방향의 원근 왜곡을 계산합니다.
3. 기타의 프렛 비율 `1 - 2^(-fret/12)`로 너트부터 끝 기준 프렛까지 모든 프렛선을 복원합니다.
4. 너트와 각 프렛선 위에 6개의 등간격 줄 좌표를 만들어 줄별 교차 좌표로 사용합니다.

### 3.2 매 프레임 추적

```mermaid
flowchart TD
    FRAME["현재 카메라 프레임"] --> MASK["MediaPipe 손 영역 마스크<br/>현재+직전 마스크 결합"]
    MASK --> FLOW["Optical Flow 두 경로<br/>직전 자세 / 최초 기준 자세"]
    PREDICT["최근 검증 자세의 속도 예측<br/>검색 시작점으로만 사용"] --> FLOW
    FLOW --> VERIFY["최초 기준 영상과 검증<br/>분산 특징점·형상·가시 영역 검사"]
    VERIFY --> AGREE{"두 경로가 유효하고<br/>서로 일치하는가?"}
    AGREE -->|"예"| REFINE["필요 시 ECC 미세 정렬"]
    AGREE -->|"아니오"| RECOVERY["백그라운드 SIFT 재탐색"]
    RECOVERY --> CONFIRM["촬영 시각 보정 + 현재 프레임 재검증<br/>큰 이동은 독립 후보 2회 확인"]
    CONFIRM --> REFINE
    VERIFY -->|"짧은 흐림"| CONTINUITY["최대 0.25초 / 6프레임<br/>별도 coarse flow 검증"]
    CONTINUITY --> POSE["현재 homography"]
    REFINE --> POSE
    POSE --> GRID["고정된 기준 격자를 현재 자세로 투영"]
    GRID --> STATE["tracking_mode 결정"]
    STATE --> VISIBLE{"현재 프레임에서<br/>표시 가능한 상태?"}
    VISIBLE -->|"예"| MARKERS["현재 PracticeStep의 안내점 계산"]
    VISIBLE -->|"hold/lost/weak"| HIDE["과거 안내선·안내점 숨김"]
```

보정 후 메인 앱은 YOLO 검출로 지판 형상이나 프렛 번호를 다시 쓰지 않습니다. YOLO는 최초 맞춤용이며, 런타임 지판 좌표는 고정된 기준 격자와 현재 영상의 대응 관계로 계산됩니다.

| `tracking_mode` | 의미 | AR 표시 |
| --- | --- | --- |
| `calibration_required` | 아직 지판을 맞추지 않음 | 숨김 |
| `selected_lines_reference_weak` | 기준 영상 특징이 부족함 | 숨김 |
| `selected_lines_motion` | 현재 영상에서 정상 이동 추적 | 표시 |
| `selected_lines_continuity` | 짧은 흐림 중 별도 대응점으로 현재 위치 확인 | 표시 |
| `selected_lines_reacquired` | 기준 영상으로 재탐색 성공 | 표시 |
| `selected_lines_hold` | 마지막 격자만 내부에 남고 현재 위치는 미검증 | 숨김 |
| `selected_lines_lost` | 격자를 잃음 | 숨김 |

### 3.3 프렛 중심 안내점 계산

```mermaid
flowchart LR
    GRID["너트/프렛선마다<br/>6개 줄 교차점"]
    NOTE["현재 note<br/>(string, fret)"]
    MAP["기타 줄 번호 -> 화면 줄 인덱스"]
    OPEN{"fret == 0?"}
    NUT["현재 너트와 해당 줄의 교차점"]
    CENTER["이전 프렛선과 현재 프렛선의<br/>해당 줄 교차점 사이 50% 지점"]
    SMOOTH["NoteMarkerTracker<br/>움직임 적용 후 좌표 안정화"]
    CHECK["화면 및 프렛 영역 유효성 검사"]
    DRAW["AR 안내점 표시"]

    GRID --> MAP
    NOTE --> MAP --> OPEN
    OPEN -->|"예"| NUT --> SMOOTH
    OPEN -->|"아니오"| CENTER --> SMOOTH
    GRID --> CENTER
    SMOOTH --> CHECK --> DRAW
```

여기서 “중심점”은 모델이 직접 예측하는 별도 keypoint가 아닙니다. 모델/보정으로 얻은 프렛 경계와 줄 위치의 관계에서 계산한 **프렛 칸 중심 좌표**입니다. 현재 손으로 가린 안내점도 지판 자세가 검증되었다면 보이도록 `hide_occluded=False`를 사용합니다.

## 4. 음정 인식 및 정답 판정

```mermaid
flowchart TD
    STEP["현재 PracticeStep"] --> MODE{"single / chord"}
    MIC["sounddevice InputStream<br/>44.1 kHz, mono float32"] --> QUEUE["제한된 큐<br/>밀리면 가장 오래된 블록 폐기"]

    MODE -->|"single"| SINGLE["AudioWorker"]
    QUEUE --> SINGLE
    SINGLE --> LEVEL1{"RMS/peak가<br/>무음 기준 이상?"}
    LEVEL1 -->|"아니오"| NOSIG1["no_signal"]
    LEVEL1 -->|"예"| YIN["librosa YIN<br/>70~1000 Hz 기본음 추정"]
    YIN --> CLEAR["autocorrelation clarity 검사"]
    CLEAR --> CENTS["1200 * log2(검출Hz / 목표Hz)"]
    CENTS --> TOL{"절대 오차 <= 30 cents?"}
    TOL -->|"예"| CORRECT1["correct"]
    TOL -->|"아니오"| WRONG1["incorrect"]

    MODE -->|"chord"| CHORD["ChordAudioWorker"]
    QUEUE --> CHORD
    CHORD --> WINDOW["최근 650 ms 롤링 버퍼"]
    WINDOW --> LEVEL2{"무음 기준 이상?"}
    LEVEL2 -->|"아니오"| NOSIG2["no_signal"]
    LEVEL2 -->|"예"| CQT["librosa CQT<br/>옥타브당 36 bin"]
    CQT --> MATCH["각 목표음 ±35 cents 영역의 에너지 검사"]
    MATCH --> HARMONIC["noise floor·상대 크기 검사<br/>낮은 음의 배음 오검출 제거"]
    HARMONIC --> ALL{"모든 목표음이 존재?"}
    ALL -->|"예"| CORRECT2["correct"]
    ALL -->|"아니오"| WRONG2["incorrect + missing_notes"]

    CORRECT1 --> RESULT["result dict"]
    WRONG1 --> RESULT
    NOSIG1 --> RESULT
    CORRECT2 --> RESULT
    WRONG2 --> RESULT
    NOSIG2 --> RESULT
```

### 단일음 판정

- 기본 입력 블록은 4096 sample, 결과 갱신 간격은 50ms입니다.
- RMS와 peak가 무음 기준보다 작으면 주파수를 판정하지 않습니다.
- YIN이 찾은 주파수의 선명도를 다시 검사하고, 목표음과의 오차가 기본 `±30 cents` 이내이면 정답입니다.
- 마지막 유효 결과는 최대 900ms 동안 `held=True`로 화면에 유지되지만, 유지 결과만으로 다음 step으로 넘어가지는 않습니다.

### 화음 판정

- 기본 입력 블록은 2048 sample이며 최근 650ms를 모아 120ms 간격으로 분석합니다.
- 일반 화음 이름을 추측하는 방식이 아니라, 현재 step에 이미 지정된 목표 주파수들이 존재하는지만 검사합니다.
- 각 목표음은 기본 `±35 cents`, 최대 에너지 대비 `-30dB` 이상, noise floor 대비 `+10dB` 이상이어야 합니다.
- 강한 저음의 배음을 별도 음으로 잘못 인정하지 않도록 배음 후보를 제거합니다.
- 목표음이 모두 검출되어야 정답이며, 빠진 음은 `missing_notes`로 UI에 전달됩니다.
- 마지막 정답은 최대 600ms 동안 화면에 유지되지만 `held=True` 결과는 진행 조건에서 제외됩니다.

### 현재 판정 범위

- 단일음은 주파수만 비교하므로, 같은 음높이를 다른 줄·프렛에서 연주해도 정답으로 처리될 수 있습니다.
- 화음은 목표음이 모두 있는지 검사하며, 목표 외의 음이 함께 들어왔다는 이유만으로 오답 처리하지는 않습니다.
- 인터랙티브 모드는 음의 시작 시각이나 길이를 채점하지 않고, 현재 step의 목표음 검출 여부로 진행합니다.
- 영상에서 계산한 손가락 위치와 오디오 정답을 결합하지 않으므로 “올바른 줄·프렛을 눌렀는가”는 현재 판정 범위 밖입니다.

## 5. 연습 진행 상태

```mermaid
stateDiagram-v2
    [*] --> Idle
    Idle --> Listening: 인터랙티브 시작
    Idle --> AutoWait: 자동 흐름 시작

    Listening --> Listening: no_signal / incorrect / held
    Listening --> CorrectDelay: fresh is_correct
    CorrectDelay --> ReleaseWait: 500ms 후 같은 음의 다음 step
    CorrectDelay --> Listening: 500ms 후 다른 음의 다음 step
    ReleaseWait --> ReleaseWait: 이전 소리가 계속 검출됨
    ReleaseWait --> Listening: no_signal 확인
    CorrectDelay --> Complete: 마지막 step

    AutoWait --> AutoWait: duration_ms / speed 후 다음 step
    AutoWait --> Complete: 마지막 step
    Listening --> Idle: 정지
    ReleaseWait --> Idle: 정지
    AutoWait --> Idle: 정지
    Complete --> [*]
```

같은 목표 주파수 조합이 연속되는 경우 `RetriggerGate`가 중간의 `no_signal`을 기다립니다. 따라서 길게 유지한 한 번의 소리가 반복음 두 개를 모두 통과시키지 않습니다.

| 모드 | 다음 step 조건 | 영상 결과 사용 여부 |
| --- | --- | --- |
| 인터랙티브 | 새 오디오 결과가 정답이고 `held=False`, 이후 500ms | 사용 안 함 |
| 자동 흐름 | `duration_ms / speed_multiplier` 경과 | 사용 안 함 |
| 이전/다음 버튼 | 사용자 입력 | 사용 안 함 |

## 6. 스레드와 신호 경계

```mermaid
flowchart LR
    UI["Qt UI thread<br/>MainWindow + QTimer"]
    CAP["카메라 캡처 thread<br/>VideoCapture 전담"]
    VIDEO["PracticeVideoWorker QThread<br/>추적·AR 렌더링"]
    RECOVERY["복구 thread<br/>SIFT 재탐색"]
    AUDIO["AudioWorker 또는<br/>ChordAudioWorker QThread"]

    CAP -->|"최신 프레임 1장"| VIDEO
    VIDEO <--> RECOVERY
    VIDEO -->|"frame_ready(QImage, PracticeFrameStats)"| UI
    AUDIO -->|"result_ready(dict)"| UI
    AUDIO -->|"error_message(str)"| UI
    UI -->|"현재 PracticeStep"| VIDEO
    UI -->|"worker 시작/중지"| AUDIO
```

- 카메라 프레임은 쌓지 않고 아직 처리하지 않은 최신 한 장으로 교체합니다.
- 카메라 장치는 캡처 thread만 열고 읽고 닫습니다.
- 영상 및 오디오 워커는 UI를 직접 수정하지 않고 Qt signal로 결과를 보냅니다.
- 인터랙티브 모드에서 step이 바뀌면 기존 오디오 워커를 중지하고 해당 step에 맞는 단일음/화음 워커를 새로 시작합니다.

## 7. 주요 데이터 계약

| 형식 | 생성 위치 | 핵심 내용 | 사용 위치 |
| --- | --- | --- | --- |
| `PracticeStep` | `practice/score_steps.py` | 현재 step의 줄, 프렛, 목표 Hz, 시간 | 영상·오디오 공통 입력 |
| `DetectedLine` | YOLO 변환 또는 수동 선택 | 종류, 두 끝점, confidence | 최초 지판 보정 |
| `StableFretboardGrid` | `vision/fretboard_calibration.py` | 너트, 순서화된 프렛선, 6줄 교차점 | 추적 및 안내점 계산 |
| `NoteMarker` | `app/practice_markers.py` | 줄 인덱스, 프렛 인덱스, 화면 좌표 | AR 렌더링 |
| 단일음 결과 `dict` | `audio/audio_worker.py` | 검출 Hz, cents, 상태, 정답, held | UI 및 진행 판정 |
| 화음 결과 `dict` | `audio/chord_audio_worker.py` | 음별 match, 누락음, 상태, 정답, held | UI 및 진행 판정 |
| `PracticeFrameStats` | `app/features/video.py` | FPS, 추적 상태, 격자, marker 수 | 카메라 UI 및 진단 |

## 8. 책임 경계

- `app/main.py`: 실행 인자 해석과 `MainWindow` 생성만 담당합니다.
- `app/ui/main_window.py`: 영상·오디오·악보 상태를 연결하고 다음 step 이동을 결정합니다.
- `app/features/video.py`: 카메라 프레임에서 검증된 지판 자세와 AR 영상을 만듭니다.
- `vision/`: 보정 격자, 이동 추적, 기준 영상 검증, 재탐색을 담당합니다.
- `app/practice_markers.py`: `(string, fret)`을 화면 안내점으로 변환합니다.
- `app/features/practice_audio.py`: step에 맞는 워커 선택과 반복음 게이트를 담당합니다.
- `audio/`: 실제 신호 분석과 음정 정답 여부를 계산합니다.
- `practice/score_steps.py`: 악보 JSON을 영상·오디오가 공유할 `PracticeStep`으로 변환합니다.

따라서 향후 “손가락 위치까지 맞아야 정답” 정책을 추가하려면 영상의 `matched_marker_indices`와 오디오의 `is_correct`를 결합하는 별도 판정 계층이 필요합니다. 현재 구조에는 이 결합 판정이 없습니다.
