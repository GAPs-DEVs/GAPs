# Validation Guide

이 폴더의 목적은 다음 두 가지입니다.

- 검증 데이터셋 기반으로 파이프라인을 평가
- 저장된 검증 결과를 다시 열어 시각적으로 확인

## Structure

```text
tools/valid/
├─ app.py
├─ run_validation.py
├─ valid_module.py
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

현재 로그로는 아래 항목들을 확인한다.

- `frame`: 현재 프레임 번호
- `gt`: 정답 점 개수
- `pred`: 예측 점 개수
- `matched`: 실제 비교된 점 개수
- `mean_error_px`: 평균 위치 오차
- `max_error_px`: 최대 위치 오차
- `missing`: 정답에는 있지만 예측에는 없는 점 개수

## 실행 방법

파이프라인 import와 기본 사용 형태:

```python
from demo_pipeline import demo_Pipeline

pipeline = demo_Pipeline()
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

검증 실행:

```powershell
uv run python tools/valid/run_validation.py
```

기본 검증 흐름:

```python
out = pipeline.run(input_image)
result = answer_points.L2_check(out)
manager.add_frame_result(frame_index, out, result)
```

가장 최근 결과 보기:

```powershell
uv run python tools/valid/app.py
```

특정 결과 폴더 보기:

```powershell
uv run python tools/valid/app.py tools/valid/result/run_20260604_153210
```

## 파일 역할

- `run_validation.py`
  - 전체 검증 실행
  - 프레임별 `pred/result json` 저장
  - 실행별 `run_YYYYMMDD_HHMMSS` 폴더 생성

- `app.py`
  - 저장된 결과만 읽어서 표시
  - 프레임 슬라이더 창 + 리포트 창 표시

- `valid_module.py`
  - 공용 모듈
  - 로더 / 비교 / 결과관리 / 앱 클래스
