# Validation Guide

이 폴더의 목적은 다음 두 가지입니다.

- 검증 데이터셋 기반으로 파이프라인을 평가
- 저장된 검증 결과를 다시 열어 시각적으로 확인

## Structure

```text
tools/valid/
├─ app.py
├─ module.py
├─ labeling/
│  └─ app.py              # JSON 정답 라벨 읽기 전용 검토
├─ pipeline/
│  ├─ app.py              # 실제 앱 추적 파이프라인 검증 어댑터
│  ├─ calibration_app.py  # 보정 프레임과 세 기준선 선택
│  ├─ calibration.json    # calibration_app.py 실행 결과
│  └─ run.py              # 실제 앱 추적 코어 검증 실행기
├─ models/
│  ├─ output_adapter/       # 모델 출력을 공통 선 형식으로 변환·정렬
│  ├─ base.py              # 모델 공통 검증 반복문
│  ├─ line.py              # best 계열 모델 공통 파이프라인
│  ├─ rot.py               # rot 계열 모델 공통 파이프라인
│  ├─ run.py               # 모델 이름을 받는 단일 실행기
│  └─ run_all_models.sh
├─ ds/
│  ├─ images/
│  └─ labels_json/
└─ result/
```

## 1-1. 검증 데이터셋 구축

가이드 포인트 검출 성능을 수치화하기 위해 검증 데이터셋을 구축하였다.

- 시계열 영상에서의 안정성 평가
- 프렛 가림 상황에서의 성능 평가

모델 추론 속도가 약 `15fps` 수준이므로, 검증 프레임도 `15fps` 기준으로 생성하였다.
즉, 시나리오 촬영 원본 영상에서 평가 가능한 밀도로 프레임을 샘플링하였다.

또한 해당 영상에 대해 정답 라벨링을 수행하였다.

## 검증 시나리오

| part of frame | senario | explain |
| --- | --- | --- |
| 0~160 | horizon | 지판이 수평에 가까운 상태 |
| 161~290 | diagonal | 지판이 대각선 방향으로 놓인 상태 |
| 291~470 | hidden 1 all fret | n개의 프렛이 완전히 가려진 상태 |
| 471~590 | hidden 2 all fret | 위와 같은 유형의 가림 구간 |
| 591~720 | hidden 3 all fret | 위와 같은 유형의 가림 구간 |
| 721~840 | hidden 1 half fret | n개의 프렛이 절반 정도 가려진 상태 |
| 841~970 | hidden 2 half fret | 위와 같은 유형의 가림 구간 |
| 971~1110 | hidden 3 half fret | 위와 같은 유형의 가림 구간 |
| 1111~end | top | 상단 일부가 가려진 상태 |

## 데이터셋 형식

```text
tools/valid/ds/
├─ images/
└─ labels_json/
```

정답 JSON은 `points`만 사용한다.
기존 JSON에 남아 있는 `image_name`, `frame_index`, 이미지 크기 메타데이터는
검증 계산에 사용하지 않는다. 검증의 `frame_index`와 현재 라벨 메타데이터는
정렬된 이미지 순서를 기준으로 모두 `0`부터 시작한다.

정답 라벨을 모델 실행 없이 눈으로 검토하려면 다음 명령을 사용한다.

```bash
uv run python tools/valid/labeling/app.py
```

```json
{
  "points": [
    {
      "string": 1,
      "fret": 1,
      "x": 545.57,
      "y": 846.07,
      "visible": true
    }
  ]
}
```

즉, 한 점은 다음 의미를 가진다.

- `string`: 몇 번째 줄인지
- `fret`: 몇 번째 프렛 구간인지
- `x`, `y`: 픽셀 좌표
- `visible`: 해당 점이 유효한지

## 평가 지표

각 프레임에서 예측된 가이드 포인트와 정답 라벨을 비교하여 다음 지표를 계산한다.

| 평가 지표 | 설명 |
| --- | --- |
| `guide_count_error` | 정답 개수와 예측 개수의 차이 |
| `mean_keypoint_error_px` | 대응된 가이드 포인트들 사이의 평균 좌표 오차 |
| `max_keypoint_error_px` | 한 프레임에서 발생한 가장 큰 좌표 오차. 일부 이상치를 확인하기 쉽다 |
| `region_accuracy` | 같은 `(string, fret)` 예측점이 GT 중심의 방향 정렬 타원 안에 들어온 비율. 누락은 실패로 계산 |

판정 타원은 긴 지름 `40px`, 짧은 지름 `10px`이다. 긴 축은 같은 줄의
앞·뒤 프렛 GT 점 관계로 계산한 지판 진행 방향을 따른다.

현재 로그로는 아래 항목들을 확인한다.

- `frame`: 현재 프레임 번호
- `gt`: 정답 점 개수
- `pred`: 예측 점 개수
- `matched`: 실제 비교된 점 개수
- `mean_error_px`: 평균 위치 오차
- `max_error_px`: 최대 위치 오차
- `missing`: 정답에는 있지만 예측에는 없는 점 개수

## 실행 방법

사용자 파이프라인의 기본 인터페이스:

```python
out = pipeline.run(input_image)
```

여기서 `out`은 `list[GuidePoint]` 형식이다.

```python
[
    GuidePoint(
        string=1,
        fret=1,
        x=545.57,
        y=846.07,
        visible=True,
    )
]
```

즉 파이프라인은

- 입력: `input_image`
- 출력: `string/fret/x/y/visible`을 가진 점 목록

형태를 가진다.

개별 모델 검증:

```bash
uv run python tools/valid/models/run.py best06
uv run python tools/valid/models/run.py rot_v2
```

모든 모델 검증(Git Bash):

```bash
bash tools/valid/models/run_all_models.sh
```

### 사용자 파이프라인 개발 규칙

별도 실행 파일을 복사하지 말고, 사용자 코드에서 아래 공개 인터페이스를
import한다. 파이프라인은 이미지 한 장을 받고 `list[GuidePoint]`를 반환해야 한다.

```python
from your_package import YourPipeline
from tools.valid.module import ValidLoader, ValidResultManager, log_validation_progress

pipeline = YourPipeline()
loader = ValidLoader()
manager = ValidResultManager()
total_frames = len(loader)

for frame_index, (input_image, answer_points) in enumerate(loader):
    # 사용자 파이프라인은 현재 프레임의 예측 점만 반환합니다.
    predicted_points = pipeline.run(input_image)

    # 검증 모듈이 같은 (string, fret) 키끼리 좌표와 영역을 비교합니다.
    result = answer_points.L2_check(predicted_points)
    log_validation_progress(frame_index, total_frames, result)
    manager.add_frame_result(frame_index, predicted_points, result)

# 전체 검증이 끝난 후 실행 요약을 결과 폴더에 저장합니다.
manager.save_summary()
print(manager.result_dir)
```

앱 파이프라인의 초기 지판 맞춤 프레임 생성:

```bash
uv run python tools/valid/pipeline/calibration_app.py
```

프레임 슬라이더로 손이 지판을 가리지 않고 세 기준선이 선명한 프레임을 고른다.
이후 실제 앱과 같은 순서로 다음 항목을 입력한다.

1. 너트 선의 양 끝점
2. 중간 기준 프렛 선의 양 끝점과 실제 프렛 번호
3. 끝 기준 프렛 선의 양 끝점과 실제 프렛 번호

저장 전에 실제 앱의 `build_calibrated_grid()`로 선택값을 검사하며, 결과는
`tools/valid/pipeline/calibration.json`에 기록된다. `frame_index`는 `ValidLoader`와 같은
0부터 시작하는 번호이다. 같은 프레임을 다시 저장하면 기존 항목을 교체한다.

저장한 보정 프레임으로 실제 앱의 보정·시계열 추적 경로 검증:

```bash
uv run python tools/valid/pipeline/run.py
```

보정 프레임 이전 구간은 평가하지 않는다. 보정 프레임부터 앱과 같은 추적기를
순서대로 실행하며, 추적에 실패한 프레임은 빈 예측으로 저장되어 `missing`에 반영된다.
실제 앱과 마찬가지로 `selected_lines_hold` 상태도 화면에 점을 표시하지 않으므로
빈 예측으로 평가한다.

결과 앱의 프레임 화면에서는 판정 영역도 함께 표시한다.

- 초록색 타원: 같은 `(string, fret)` 예측점이 영역 안에 있음
- 빨간색 타원: 예측점이 영역 밖이거나 누락됨

리포트 창은 픽셀 오차 등급 대신 실제 점 판정만 표시한다.

- `72 GT points x validation frames`: 세로축은 `(fret, string)` 72개, 가로축은 프레임 순서
- 초록색: 해당 점이 자신의 방향 정렬 타원 안에 있음
- 빨간색: 해당 점이 존재하지만 자신의 영역 밖에 있음
- 검정색: 해당 점의 예측이 없음
- 하단 막대: 검증 시나리오별 점 정답률

기본 검증 흐름:

```python
out = pipeline.run(input_image)
result = answer_points.L2_check(out)
manager.add_frame_result(frame_index, out, result)
```

가장 최근 결과 보기:

```bash
uv run python tools/valid/app.py
```

특정 결과 폴더 보기:

```bash
uv run python tools/valid/app.py tools/valid/result/run_20260604_153210
```

## 검증 범위

- `models/run.py`: 모델 출력에 최소 추출·정렬만 적용한 baseline 검증
- 사용자 파이프라인: 위 개발 규칙의 공개 API를 직접 import하여 검증
- `pipeline/run.py`: 메인 앱의 3선 보정과 시계열 추적 코어 검증

두 결과는 후처리 범위가 다르므로 같은 의미로 해석하지 않는다.

### 메인 앱과 동일한 부분

- `build_calibrated_grid()`를 사용한 너트·중간 프렛·끝 프렛 3선 보정
- `SelectedLineTracker(max_history=5)`
- `HybridSelectedLineTracker`와 `BackgroundFretboardRecovery`
- `HandOcclusionMasker`
- 앱 공용 `visible_calibrated_grid()`를 사용한 `hold` 상태 출력 억제

### 검증 어댑터에서만 수행하는 부분

- 앱은 현재 악보 음표만 AR 마커로 만들지만, 검증은 GT 비교를 위해 격자의 모든 점을 출력
- 검증 데이터는 이미 GT·보정 좌표와 같은 화면 방향이므로 웹캠 입력의 `cv2.flip()`을 다시 적용하지 않음
- 카메라 장치의 실시간 프레임 드롭 대신 저장된 15fps 프레임을 모두 순서대로 입력
- UI, 악보 진행, 손끝 판정, 오디오 판정은 이 검증의 대상이 아님

## 파일 역할

- `pipeline/calibration_app.py`
  - 보정 프레임 및 세 기준선 선택
  - `calibration.json` 생성

- `pipeline/app.py`
  - 메인 앱의 보정·추적 클래스를 검증 출력 형식으로 연결

- `pipeline/run.py`
  - 앱 추적 코어 전체 검증 실행
  - 프레임별 지표와 예측을 `result json` 하나에 저장
  - 실행별 `run_YYYYMMDD_HHMMSS` 폴더 생성

- `app.py`
  - 저장된 결과만 읽어서 표시
  - 프레임 슬라이더 창 + 리포트 창 표시

- `module.py`
  - UI와 분리된 공용 검증 코어
  - 점 자료형 / 비교 / 로더 / 결과 관리
