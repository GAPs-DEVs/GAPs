# Weights Guide

이 폴더의 모델들은 현재 크게 두 계열로 나뉜다.

## 1. 2-Keypoint Line Models

대상 모델:

- `best06.pt`
- `best07.pt`
- `best08_v1.pt`
- `best08_v2.pt`
- `best09.pt`

공통 메타데이터:

- `task: pose`
- `classes: fret, nut`
- `nc: 2`
- `kpt_shape: [2, 3]`

의미:

- 각 `fret/nut` 객체에 대해 `양 끝점 2개`를 예측한다.
- 즉, 직접 guide point를 내는 모델이 아니라 `선분(line)` 중심 모델이다.

권장 테스트 방향:

- 2개 keypoint가 `PoseLine`으로 정상 변환되는지 확인
- `nut/fret` 선의 개수와 정렬 순서 확인
- 각 선을 6등분하고 인접 선의 중점을 계산
- 최종 `GuidePoint` 좌표 오차 평가

현재 코드에서 잘 맞는 경로:

- `tools/valid/models/line.py`
- `tools/valid/models/output_adapter/`
- `tools/valid/models/run.py`

## 2. 6-Keypoint Guide Models

대상 모델:

- `rot_v1.pt`
- `rot_v2.pt`

공통 메타데이터:

- `task: pose`
- `classes: fret, nut`
- `nc: 2`
- `kpt_shape: [6, 3]`

의미:

- 각 `fret/nut` 객체에 대해 `6개 string 교차점`을 예측한다.
- 검증 어댑터는 이 점들로 선을 정렬한 뒤 각 선을 다시 6등분하여
  best 계열과 동일한 방식으로 인접 선 사이의 가이드 포인트를 만든다.

권장 테스트 방향:

- `string/fret/x/y/visible` 구조로 변환
- GT guide point와 직접 비교
- 구간별 오차(`horizon`, `diagonal`, `hidden`, `top`) 평가

현재 코드에서 잘 맞는 경로:

- `tools/valid/models/rot.py`
- `tools/valid/models/run.py`
- `tools/valid/app.py`

## 3. 테스트 설계 원칙

모든 weight를 같은 방식으로 평가하면 안 된다.

이유:

- `best06~09`는 `2-keypoint line model`
- `rot_v1~v2`는 `6-keypoint guide-point model`

즉 테스트는 최소한 아래처럼 나눠야 한다.

### 공통 Smoke Test

모든 `.pt` 파일에 대해:

- 모델 로드 성공
- 1~3장 추론 성공
- `task`, `names`, `kpt_shape` 확인

### 계열별 Validation

`2-keypoint` 계열:

- line 검출 성능
- grid 생성 성능
- 최종 guide point 변환 성능

`6-keypoint` 계열:

- 직접 guide point 좌표 오차
- `gt/pred/matched`
- `mean_error_px`
- `max_error_px`

## 4. 현재 확인된 메타데이터 요약

| model | type | kpt_shape | note |
| --- | --- | --- | --- |
| `best06.pt` | line | `[2, 3]` | pose line model |
| `best07.pt` | line | `[2, 3]` | pose line model |
| `best08_v1.pt` | line | `[2, 3]` | pose line model |
| `best08_v2.pt` | line | `[2, 3]` | pose line model |
| `best09.pt` | line | `[2, 3]` | pose line model |
| `rot_v1.pt` | guide | `[6, 3]` | six-keypoint model |
| `rot_v2.pt` | guide | `[6, 3]` | six-keypoint model |
