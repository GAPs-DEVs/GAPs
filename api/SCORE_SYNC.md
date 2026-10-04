# 개인 악보함 동기화 API

기존 서비스 Bearer 토큰을 사용합니다. DB 구조·앱 코드는 변경하지 않습니다.
GET 요청은 선택 버전, 즐겨찾기, 열람 시간 등 DB 값을 수정하지 않습니다.

## 1. 악보함 목록

`GET /api/scores/sync?limit=20&cursor=...`

첫 요청은 cursor를 생략합니다. limit은 1~100, 기본 20입니다.
`user_score_library`에 저장되어 있고 현재 접근 가능한 악보만 반환합니다.
직접 올린 악보는 업로드 API에서 악보함에도 등록합니다.
공개 악보 전체나 악보함에 저장하지 않은 공유 악보를 자동으로 포함하지 않습니다.

```json
{
  "items": [{
    "score_id": "악보 UUID",
    "title": "연습 악보",
    "description": null,
    "visibility": "private",
    "parser_status": "ready",
    "updated_at": "2026-10-05T00:00:00Z",
    "is_favorite": false,
    "library_updated_at": "2026-10-05T00:00:00Z",
    "selected_version_id": "선택 버전 UUID",
    "selected_version_number": 1,
    "latest_version_id": "최신 버전 UUID",
    "latest_version_number": 2,
    "has_update": true,
    "permission": "owner"
  }],
  "next_cursor": null
}
```

저장된 버전이 있으면 유지하며, 없으면 최신 버전을 선택해 반환합니다. 버전이 하나도
없으면 버전 ID·번호는 null이고 has_update는 false입니다. 이 경우 본문 요청을 하지 않습니다.
permission은 owner/editor/viewer이며 public 읽기만 허용된 경우 viewer입니다.

다음 페이지가 있으면 next_cursor를 URL 인코딩하여 cursor로 보냅니다. 커서는
사용자별로 서명되어 1시간 유효하며 다른 계정의 커서는 사용할 수 없습니다.
악보 UUID 오름차순으로 페이지를 나눕니다. 최신순 정렬이나 증분 동기화 토큰이 아닙니다.

페이지 사이의 동시 추가·삭제·권한 변경을 고정하는 스냅샷은 제공하지 않습니다.
다음 전체 조회에서 다시 맞추며, 목록에 없다는 이유만으로 클라이언트 로컬 원본을 삭제하면 안 됩니다.

## 2. 버전 상세

`GET /api/scores/{score_id}/versions/{version_id}`

```json
{
  "score_id": "악보 UUID",
  "title": "연습 악보",
  "version_id": "버전 UUID",
  "version_number": 1,
  "note_count": 1,
  "step_count": 1,
  "notes": [{
    "score_step_id": "step UUID",
    "step": 1,
    "score_line": 1,
    "measure": 1,
    "beat": 1,
    "duration_ms": 500,
    "string": 1,
    "fret": 0,
    "midi_pitch": 64
  }]
}
```

음표는 step 순서 → 기타 줄 번호 순서로 반환합니다. 선택적 위치·MIDI 값은 null일 수 있습니다.
연습 기록은 version_id와 score_step_id로 연결할 수 있습니다. 원본 파일이나 S3 key는 반환하지 않습니다.

## 접근 권한

- 활성 회원에게만 허용합니다.
- 소유자, score_members의 공유 회원, public 읽기 권한을 검사합니다.
- unlisted는 링크 정책 구현 전까지 소유자·공유 회원만 허용합니다.
- 악보함에 저장되어 있어도 공유 취소 후 private 악보를 읽을 수 없습니다.
- 삭제된 악보는 제외합니다. 상세 요청의 접근 불가·없는 버전·다른 악보의 버전은 모두 404입니다.
- 본문 조회는 개인 악보함 등록 여부와 무관하게 위 접근 권한으로 판단합니다.

응답: 정상 200, 미인증/만료 401, 비활성 계정 403, 잘못된 페이지 인자 400, DB 장애 503.

## 서버 적용 및 Postman 확인

이 변경을 서버에서 받은 뒤 `api/`에서 `sudo docker compose up -d --build`를 실행합니다.
DB migrate는 실행하지 않습니다.

Postman에서 Google 로그인으로 받은 서비스 토큰을 Bearer Token에 넣고 다음을 확인합니다.

1. `/api/scores/sync?limit=1`: 업로드한 악보와 선택 버전이 반환되는지 확인
2. next_cursor가 있으면 Params의 cursor에 넣어 다음 페이지 확인
3. 반환된 score_id와 selected_version_id로 버전 상세 조회
4. 다른 사용자의 private 악보, 삭제된 악보, 잘못 조합한 버전이 404인지 확인
5. 공유한 악보를 악보함에 등록하면 조회되고, 공유 취소 후 제외되는지 확인
6. 저장된 버전이 1이고 최신이 2이면 선택은 1, has_update는 true인지 확인

자동 테스트: `python -m unittest discover -s api -p 'test_*.py'`.
단위 테스트는 DB 조회를 mock 처리합니다. 실제 PostgreSQL 권한 필터·JOIN 결과는
별도의 테스트 데이터로 위 시나리오를 확인해야 합니다.
