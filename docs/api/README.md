# API

- [프론트·앱 개발자 연동 가이드](#client-guide)
- [Django 실행 및 DB 연결 확인](#setup)
- [Google 로그인 실행](#authentication)
- [일반 사용자 악보 업로드](#upload)
- [개인 악보함 동기화 API](#sync)
- [Postman으로 로그인·악보 업로드 테스트](#postman)
- [API 테스트](#tests)

<a id="client-guide"></a>

## 프론트·앱 개발자 연동 가이드

클라이언트는 HTTPS API를 호출합니다. DB에 직접 연결하거나 DB 비밀번호를 배포하지 않습니다.
아래 계약은 저장소의 `api/config/urls.py`, `auth.py`, `scores.py` 기준이며 서버에도 해당 코드가 배포되어 있어야 합니다.

### 주소와 공통 규칙

- 현재 서버 주소: `https://54.117.40.85` (탄력적 IP). 클라이언트 설정값 `API_BASE_URL`로 관리하며 IP를 코드에 고정하지 않습니다.
- `/health/`, `/health/db/`만 끝에 `/`가 있습니다. `/api/...` 경로에는 끝에 `/`를 붙이지 않습니다.
- POST 본문은 JSON이며 `Content-Type: application/json`을 지정합니다.
- 인증 요청에는 `Authorization: Bearer <서비스 access_token>`을 보냅니다. Google ID 토큰은 로그인 요청에만 사용합니다.
- 사용자·악보·버전 ID는 UUID 문자열입니다. 클라이언트가 임의로 생성하거나 숫자로 변환하지 않습니다.
- 날짜는 시간대가 포함된 ISO 8601 문자열입니다. UI에서 사용자 시간대로 표시합니다.
- 인증서 검증은 켜두며 토큰을 로그·URL·공유 파일에 넣지 않습니다.

| 기능 | 메서드·경로 | 인증 | 성공 |
| --- | --- | --- | --- |
| API 상태 | `GET /health/` | 불필요 | 200 |
| DB 연결 상태 | `GET /health/db/` | 불필요 | 200 |
| Google 로그인 | `POST /api/auth/google` | Google ID 토큰을 본문으로 전달 | 200 |
| 내 정보 | `GET /api/auth/me` | Bearer | 200 |
| 새 악보 업로드 | `POST /api/scores` | Bearer | 201 |
| 내 악보함 목록 | `GET /api/scores/sync` | Bearer | 200 |
| 악보 버전 상세 | `GET /api/scores/{score_id}/versions/{version_id}` | Bearer | 200 |

### EC2 공인 IP가 변경된 경우

현재 서버는 탄력적 IP `54.117.40.85`를 사용합니다. 할당·연결을 유지하면 중지 후 시작해도 같은 주소를 사용합니다.
아래 절차는 탄력적 IP 자체를 교체할 때 적용합니다. [AWS 인스턴스 상태 안내](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-instance-lifecycle.html)

서버 담당자는 AWS 콘솔에서 현재 공인 IPv4를 확인하고 다음을 반영합니다.

1. `api/.env.local`의 `DJANGO_ALLOWED_HOSTS`에 새 IP를 반영합니다. `127.0.0.1,localhost`도 유지하고 API 폴더에서 `sudo docker compose up -d`로 적용합니다.
2. 현재 HTTPS 인증서는 IP에 발급되므로 **새 IP용 인증서를 발급·적용**해야 합니다. 기존 인증서의 자동 갱신만으로 대상 IP가 바뀌지는 않습니다. [Nginx·인증서 안내](../nginx/README.md)를 참고합니다.
3. 클라이언트의 `API_BASE_URL`, Postman 도우미의 `api/postman/.env.local` 내 `GUITAR_API_BASE_URL`, Postman 환경의 `base_url`을 새 HTTPS 주소로 갱신합니다.
4. 새 주소의 `/health/`, `/health/db/`가 인증서 검증을 켠 상태에서 200인지 확인한 뒤 앱 담당자에게 전달합니다.

DB 컨테이너 연결 주소 `postgres:5432`는 외부 공인 IP로 변경하지 않습니다.
아래 curl 예시의 `CURRENT_EC2_PUBLIC_IP`는 실행 전에 실제 현재 공인 IP로 바꿉니다.

### 1. 로그인 후 서비스 토큰 보관

클라이언트가 Google 인증으로 받은 **ID 토큰**을 서버에 전달합니다.
Google access token이나 authorization code를 대신 보내지 않습니다.
서버는 현재 `GOOGLE_OAUTH_CLIENT_ID`에 설정한 단일 클라이언트 ID를 검증합니다.
다른 웹·모바일 OAuth 클라이언트 ID를 사용하려면 먼저 API 담당자와 허용 대상 변경을 협의해야 합니다.

```http
POST /api/auth/google
Content-Type: application/json

{"id_token":"<Google ID 토큰>"}
```

성공 응답 예시:

```json
{
  "access_token": "<서비스 토큰>",
  "token_type": "Bearer",
  "expires_in": 3600,
  "user": {
    "id": "00000000-0000-0000-0000-000000000001",
    "username": "사용자 표시 이름",
    "role": "user",
    "email": "user@example.com",
    "is_guest": false
  }
}
```

처음 로그인한 Google 계정은 서버에서 회원을 생성합니다. 기존 계정은 같은 회원으로 연결합니다.
서비스 토큰은 JWT가 아닌 Django 서명 토큰입니다. 내용을 해석하지 말고 받은 문자열 그대로 사용합니다.
유효 기간은 1시간이며 refresh API는 없습니다. 만료되면 Google 로그인부터 다시 진행합니다.
로그아웃은 클라이언트의 토큰을 비우는 방식이며 서버 토큰 폐기 API는 없습니다.

`GET /api/auth/me` 응답은 `{"user":{"id":"...","username":"...","role":"user"}}`입니다.
로그인 응답과 달리 `email`, `is_guest` 필드는 포함하지 않습니다. `username`은 로그인 ID가 아닌 표시 이름입니다.

### 2. 로컬에서 파싱한 악보 업로드

앱에서 파싱을 완료한 뒤 제목과 음표 배열을 전달합니다. 원본 Guitar Pro·이미지 파일을 보내는 API가 아닙니다.

```http
POST /api/scores
Authorization: Bearer <서비스 토큰>
Content-Type: application/json

{
  "title": "연습 악보",
  "source_type": "guitar_pro",
  "notes": [
    {"step": 1, "score_line": 1, "measure": 1, "beat": 1, "string": 1, "fret": 0, "duration_ms": 500, "midi_pitch": 64}
  ]
}
```

| 필드 | 규칙 |
| --- | --- |
| `title` | 필수, 앞뒤 공백 제거 후 1~200자 |
| `source_type` | `guitar_pro`, `image`, `manual`, `sample`, `import`. 생략 시 `import` |
| `notes` | 필수 배열, 1~1,000개. 요청 본문 전체 최대 64 KiB |
| `notes[].step` | 필수 양의 정수. 같은 step의 음표는 동시에 연주하는 한 그룹 |
| `notes[].string` | 필수 정수 1~6 |
| `notes[].fret` | 필수 정수 0~30 |
| `notes[].duration_ms` | 양의 정수, 생략 시 500. null은 불가 |
| `notes[].score_line`, `measure`, `beat` | 양의 정수 또는 null, 생략 가능 |
| `notes[].midi_pitch` | 정수 0~127 또는 null, 생략 가능 |

같은 step에서는 위치·길이가 같아야 하며 같은 기타 줄을 중복해서 넣을 수 없습니다.
정수 필드에 문자열·소수·boolean을 넣지 않습니다. 양의 정수 필드의 최댓값은 2,147,483,647입니다.
명시한 필드 외의 `owner_user_id`, `visibility`, `score_step_id` 등을 본문에 추가하면 거부됩니다.
상세 조회 결과를 다시 업로드하려면 DB 생성 필드인 `score_step_id`를 제거해야 합니다.

```json
{
  "score": {
    "id": "00000000-0000-0000-0000-000000000002",
    "version_id": "00000000-0000-0000-0000-000000000003",
    "version_number": 1,
    "title": "연습 악보",
    "visibility": "private",
    "note_count": 1,
    "step_count": 1
  }
}
```

응답은 **201**이며 `score.id`, `score.version_id`를 저장합니다. 소유자는 로그인한 사용자로 결정됩니다.
악보는 비공개로 생성되고 개인 악보함에도 등록됩니다. 저장 작업은 한 트랜잭션으로 처리합니다.
POST를 다시 보내면 새 악보가 생기므로 업로드 버튼 중복 클릭을 막고, 응답 유실 시 자동 재전송하지 않습니다.
현재 중복 방지 키나 업로드 결과를 요청 ID로 확인하는 기능은 없습니다.

### 3. 목록 조회 → 선택한 버전 내려받기

1. `GET /api/scores/sync?limit=20`으로 목록을 표시합니다.
2. `items[].score_id`를 악보 ID로, `selected_version_id`를 내려받을 버전 ID로 사용합니다.
3. 다음 페이지는 `next_cursor`를 URL 쿼리 파라미터로 전달합니다. null이면 마지막 페이지입니다.
4. 악보를 선택하면 `GET /api/scores/{score_id}/versions/{selected_version_id}`를 호출합니다.
5. 상세 응답의 `notes`로 연습 화면을 구성합니다. [목록·상세 응답 전체 예시](#sync)를 참고하세요.

업로드 응답의 ID 키는 `score.id`, 목록·상세 응답의 키는 `score_id`입니다.
`selected_version_id`가 null이면 본문 요청을 하지 않습니다. `has_update=true`는 저장된 선택 버전보다 최신 버전이 있다는 뜻입니다.
목록 조회만으로 저장된 선택 버전이 갱신되지는 않습니다.

이 기능은 **개인 악보함의 전체 목록을 페이지별로 읽는 방식**입니다. 변경분만 받는 증분 동기화가 아닙니다.
공개 악보 전체 검색도 제공하지 않습니다. 목록에서 빠진 악보를 로컬 원본에서 자동 삭제하지 않습니다.
목록 커서는 1시간 유효하고 로그인 사용자에 연결됩니다. 잘못되거나 만료된 커서는 버리고 첫 페이지부터 다시 조회합니다.
`notes`는 step → 기타 줄 순으로 반환되며, API는 연습 시간·즐겨찾기·선택 버전을 GET 요청으로 변경하지 않습니다.

### 4. 오류와 클라이언트 처리

일반적인 API 오류는 `{"error":"오류코드"}` 형식입니다.
악보 입력 오류에는 `{"error":"invalid_score","detail":"필드 설명"}`이 포함됩니다.

| HTTP | 대표 오류 | 클라이언트 처리 |
| --- | --- | --- |
| 400 | `invalid_score`, `invalid_pagination`, `invalid_json`, `invalid_id_token` | 입력·페이지 인자 확인 |
| 401 | `authentication_required`, `invalid_or_expired_token`, `invalid_google_token` | 토큰 종류 확인, 필요 시 재로그인 |
| 403 | `account_disabled` | 사용 중지 안내, 재시도 중단 |
| 404 | `score_version_not_found` | 접근 불가·삭제 가능성 안내, 목록 다시 조회 |
| 405 | 메서드 불일치 | 요청 메서드 확인 |
| 413 | `request_too_large` | 업로드 용량 축소 |
| 415 | `json_required` | Content-Type 확인 |
| 503 | `database_unavailable`, `google_unavailable`, `google_login_not_configured` | 일시 장애·서버 설정 확인 |

없는 URL이나 프록시 오류(502 등)는 HTML일 수 있으므로 모든 응답을 무조건 JSON으로 파싱하지 않습니다.
HTTP 상태와 Content-Type을 먼저 확인하고 네트워크 타임아웃도 처리합니다.
PySide에서는 요청 완료를 비동기로 받아 UI가 멈추지 않도록 연결합니다.

### 5. 웹 프론트와 앱 담당자 확인사항

- **브라우저 프론트:** 현재 CORS 허용 설정과 OPTIONS preflight 처리가 구현되어 있지 않습니다.
  다른 origin에서 바로 호출할 수 있다고 가정하지 말고, 같은 origin의 `/api` 프록시 또는 허용 origin 설정을 API 담당자와 먼저 정합니다.
- **PySide 앱:** 현재 서버 API와 Postman 도우미가 제공되며 앱 화면의 로그인·업로드·동기화 연결은 별도 구현 대상입니다.
- **미구현:** 관리자/비밀번호 로그인, refresh token, 서버 로그아웃, 기존 악보 수정·새 버전 추가,
  악보 삭제·공유 설정·공개 검색, 원본 파일 업로드, 즐겨찾기·선택 버전 변경 API.
- 사용자 `role`과 PostgreSQL의 `guitar_api` DB role은 다른 개념입니다. 클라이언트에는 DB 계정 정보가 필요하지 않습니다.

연동 순서는 **health 확인 → Google 로그인 → 내 정보 → 악보 업로드 → 목록 → 버전 상세**입니다.
먼저 [Postman](#postman)에서 같은 요청이 성공하는지 확인하면 UI 문제와 서버 문제를 구분하기 쉽습니다.

<a id="setup"></a>

## Django 실행 및 DB 연결 확인

GET `/health/`는 Django 응답을, GET `/health/db/`는 `SELECT 1` 실행을 확인합니다.
DB 확인은 접속·쿼리 실행 검사이며 업무 테이블의 존재·조회 권한 검사는 아닙니다.
테이블 생성·변경은 하지 않습니다. `manage.py migrate`도 실행하지 않습니다.

### 서버 준비

기존 DB와 API는 같은 Backend EC2에 실행합니다. API 전용 Compose는 DB 네트워크
`guitar-practice-db_default`를 공유하며 DB를 새로 만들지 않습니다.

통합된 코드를 서버에 받은 뒤 같은 저장소의 `api/`에서 실행합니다.

```bash
cd ~/Guitar_Practice_Support_SW/api
sudo docker network inspect guitar-practice-db_default --format '{{.Name}}'
```

네트워크가 없으면 DB부터 실행합니다. 이미지 빌드 전 `df -h /`로 여유 공간도 확인합니다.

### 환경 설정과 실행

```bash
# 기존 설정 파일은 덮어쓰지 않습니다.
test -f .env.local || cp .env.example .env.local
chmod 600 .env.local
# 출력된 값을 DJANGO_SECRET_KEY에 입력합니다.
python3 -c 'import secrets; print(secrets.token_urlsafe(50))'
vi .env.local
```

DB_PASSWORD에는 실제 `guitar_api` 비밀번호를 입력합니다.
환경 파일은 Compose가 읽으며 Git과 Docker 이미지에는 포함되지 않습니다.

```bash
sudo docker compose config -q
sudo docker compose up -d --build
sudo docker compose logs --tail 30 api
curl -i http://127.0.0.1:8000/health/
curl -i http://127.0.0.1:8000/health/db/
```

두 요청 모두 HTTP 200과 `{"status": "ok"}`가 나오면 확인 완료입니다.
DB 연결 실패 시 두 번째 요청은 HTTP 503을 반환합니다.
기동 직후 연결이 거부되면 로그에서 시작 여부를 확인하고 다시 요청합니다.
Nginx는 위 두 요청이 성공한 다음 단계에서 연결합니다.

<a id="authentication"></a>

## Google 로그인 실행

기존 `users`, `oauth_identities` 테이블을 사용합니다. DB migrate는 실행하지 않습니다.
이 문서는 Google 로그인 안내입니다. 악보 업로드는 [SCORE_UPLOAD.md](#upload)를 참고하세요.
악보 목록·버전 조회는 [동기화 안내](#sync)를 참고하세요.

### 서버

1. 이 변경을 서버에 받은 뒤 `api/.env.local`에 아래 값을 추가합니다.
   Google Cloud에서 생성한 **데스크톱 앱 클라이언트 ID**를 넣습니다.
   다운로드한 JSON 전체나 client secret은 서버에 넣지 않습니다.

```dotenv
GOOGLE_OAUTH_CLIENT_ID=실제클라이언트ID.apps.googleusercontent.com
```

2. 통합 저장소의 API 디렉토리에서 재빌드합니다. 기존 서버 API 환경 파일이
   `~/Guitar_Practice_API/api/.env.local`에만 있다면 새 작업 경로에도 준비합니다.
   기존 `api/.env.local`이 있다면 덮어쓰지 말고 값을 확인합니다.

```bash
cd ~/Guitar_Practice_Support_SW/api
sudo docker compose config -q
sudo docker compose up -d --build
curl -i https://CURRENT_EC2_PUBLIC_IP/health/db/
# 잘못된 토큰은 HTTP 401이어야 합니다. 성공 응답이 나오면 안 됩니다.
curl -i https://CURRENT_EC2_PUBLIC_IP/api/auth/google \
  -H 'Content-Type: application/json' -d '{"id_token":"invalid"}'
```

실제 서버 공인 IP가 다르면 URL도 변경합니다. 503이면 클라이언트 ID 설정,
Google 인증 서버 연결 또는 DB 접근 상태를 확인합니다.

### 로컬 로그인 시험

PySide 앱의 서버 로그인 연결은 별도 작업입니다.
[Postman 안내](#postman)에 따라 `api/postman/.env.local`을 준비하고
Google 인증 도우미로 환경 파일을 생성한 뒤 로그인 요청을 전송합니다.

### API 계약과 제한

- `POST /api/auth/google`: JSON `id_token` → `access_token`, `token_type`, `expires_in`, `user`.
- `GET /api/auth/me`: `Authorization: Bearer <access_token>` → 현재 활성 사용자 정보.
- 서비스 토큰은 Django 서명 토큰이며 JWT가 아닙니다. 클라이언트는 불투명 문자열로 취급합니다.
- 서비스 토큰 수명은 1시간이며 자동 갱신은 없습니다. 만료 또는 앱 재시작 후 재로그인합니다.
- 동일 Google 계정은 `provider=google` + `sub`로 연결합니다. 이메일로 기존 계정을 자동 병합하지 않습니다.
- 신규 회원 role은 DB 기본값 `user`입니다. 정지·삭제 회원은 로그인할 수 없습니다.
- 기존 이메일/비밀번호 로그인·게스트 모드는 로컬 전용이며 서버 토큰을 발급하지 않습니다.
- 앱에서 토큰을 지워도 이미 복사된 토큰은 만료 전까지 유효합니다. 서버의 계정 정지로 접근을 차단할 수 있습니다.

인증 테스트(별도 환경에 API requirements, google-auth-oauthlib, python-dotenv 설치 후):

```bash
python -m unittest discover -s api -p 'test_*.py'
```

이 테스트는 Google 검증·DB 연결을 mock 처리합니다. 실제 검증은 Postman 로그인 성공,
동일 계정 재로그인, DB 회원 연결 및 `/api/auth/me` 응답으로 확인해야 합니다.

<a id="upload"></a>

## 일반 사용자 악보 업로드

앱 화면 없이 시험하려면 [Postman 테스트 안내](#postman)를 사용하세요.

`POST /api/scores`에 서비스 토큰(`Authorization: Bearer ...`)과 JSON을 보냅니다.
기존 Google 로그인과 동일한 토큰을 사용합니다. 신규 테이블이나 migrate는 필요 없습니다.

```json
{
  "title": "연습 악보",
  "source_type": "import",
  "notes": [
    {"step": 1, "string": 1, "fret": 0, "duration_ms": 500, "midi_pitch": 64},
    {"step": 1, "string": 2, "fret": 1, "duration_ms": 500, "midi_pitch": 60}
  ]
}
```

- 소유자는 인증된 사용자로 고정하고 공개 범위는 `private`로 저장합니다.
- `scores`, `score_versions`, `score_steps`, `score_notes`, `user_score_library`를 한 트랜잭션으로 저장합니다.
- 기존 파서의 `score_line`, `measure`, `beat`도 지원합니다. 같은 step의 박자·위치 값은 같아야 합니다.
- 성공 시 201과 `score.id`, `version_id`, `version_number`, 음표·step 수를 반환합니다.
- 첫 버전 제한: 요청 본문 64 KiB, 음표 최대 1,000개. 초과 시 413 또는 400을 반환합니다.
- 원본 파일/S3 업로드, 기존 악보 수정은 포함하지 않습니다. 목록·버전 조회는 [동기화 API](#sync)를 사용합니다.
- 요청마다 새 악보가 생성됩니다. 자동 재시도·중복 제거는 없으므로 응답 유실 시 DB 확인 후 재시도합니다.

### 서버 적용

변경사항을 commit/push하고 서버에서 받은 후, API만 다시 빌드합니다.

```bash
cd ~/Guitar_Practice_Support_SW
git pull --ff-only origin feat/server-integration
cd api
sudo docker compose up -d --build
# 기동 후 200 확인. 재배포 직후에는 잠시 502가 발생할 수 있습니다.
curl -i https://CURRENT_EC2_PUBLIC_IP/health/db/
```

### 업로드 확인

PySide 앱의 업로드 버튼 연결은 별도 작업입니다.
[Postman 안내](#postman)의 18 → 19 요청으로 실제 파싱 샘플을 업로드하고
저장된 음표 전체를 조회하여 입력과 비교합니다.
서버는 악보를 다시 파싱하지 않고 전달받은 음표 배열을 검증·저장합니다.

```sql
-- 비공개 악보의 소유자와 저장된 버전의 개수를 확인합니다.
SELECT s.id, s.title, u.display_name, s.visibility,
       v.version_number, v.note_count, v.step_count
FROM public.scores s
JOIN public.users u ON u.id = s.owner_user_id
JOIN public.score_versions v ON v.score_id = s.id
ORDER BY s.created_at DESC LIMIT 10;
```

인증 없는 요청은 401, 정지 계정은 403, 잘못된 악보는 400입니다.
테스트는 `python -m unittest discover -s api -p 'test_*.py'`로 실행합니다.
API 단위 테스트는 DB 저장을 mock 처리하며 실제 PostgreSQL 저장은 서버에서 확인해야 합니다.

<a id="sync"></a>

## 개인 악보함 동기화 API

기존 서비스 Bearer 토큰을 사용합니다. DB 구조·앱 코드는 변경하지 않습니다.
GET 요청은 선택 버전, 즐겨찾기, 열람 시간 등 DB 값을 수정하지 않습니다.

### 1. 악보함 목록

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

### 2. 버전 상세

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

### 접근 권한

- 활성 회원에게만 허용합니다.
- 소유자, score_members의 공유 회원, public 읽기 권한을 검사합니다.
- unlisted는 링크 정책 구현 전까지 소유자·공유 회원만 허용합니다.
- 악보함에 저장되어 있어도 공유 취소 후 private 악보를 읽을 수 없습니다.
- 삭제된 악보는 제외합니다. 상세 요청의 접근 불가·없는 버전·다른 악보의 버전은 모두 404입니다.
- 본문 조회는 개인 악보함 등록 여부와 무관하게 위 접근 권한으로 판단합니다.

응답: 정상 200, 미인증/만료 401, 비활성 계정 403, 잘못된 페이지 인자 400, DB 장애 503.

### 서버 적용 및 Postman 확인

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

<a id="postman"></a>

## Postman으로 로그인·악보 업로드 테스트

PySide 화면 없이 요청을 직접 전송합니다. 서버에 `/api/scores` 구현이 배포되어 있어야 합니다.
아직 배포하지 않았다면 변경사항을 commit/push하고 서버에서 pull한 뒤 API를 재빌드합니다.
DB migrate 및 Nginx 변경은 필요 없습니다.

### 1. 테스트 계정으로 Google 인증

로컬 `api/postman/.env.local`에 OAuth JSON 경로와 HTTPS API 주소를 설정합니다.
이 파일에 GOOGLE_OAUTH_CLIENT_FILE과 GUITAR_API_BASE_URL을 설정합니다. 앱 코드에는 의존하지 않습니다.

```bash
# 로컬 저장소 루트에서 실행합니다. Google 인증 브라우저만 열립니다.
uv sync --frozen
uv run python -m api.postman.generate_environment
```

성공하면 `api/postman/.env.postman.json`이 생성됩니다. 실제 Google ID 토큰이 들어 있으며 Git에서
제외됩니다. 파일 내용을 공유하지 말고 테스트 후 삭제합니다. 이 명령은 API에 로그인하거나
악보를 저장하지 않습니다. ID 토큰 만료 시 다시 실행하고 환경 파일을 다시 가져옵니다.

### 2. Postman Import

Postman 데스크톱 앱의 **Import**에서 아래 두 파일을 가져옵니다.

- `api/postman/postman_collection.json`
- `api/postman/.env.postman.json` (위 명령으로 생성한 환경 파일)

우측 상단 환경에서 **Guitar Practice EC2**를 선택하고 `base_url`을 확인합니다.
SSL 인증서 검증은 켜둡니다. 토큰 변수는 민감한 값으로 취급하고 공유·공개하지 않습니다.
템플릿 `postman_environment.example.json`에는 토큰이 없으므로 로그인 테스트에는 생성 파일을 사용합니다.

### 3. 번호 순서대로 Send

| 요청 | 정상 결과 |
| --- | --- |
| 01 Health | 200 |
| 02 DB health | 200 |
| 03 Google login | 200, 서비스 토큰·사용자 ID 자동 저장 |
| 04 Authenticated user | 200, 로그인한 사용자와 일치 |
| 05 Upload private score | 201, 음표 3개·step 2개·비공개 악보 생성 |
| 06 Upload without token | 401 |
| 07 Reject invalid fret | 400 |
| 08 Reject owner override | 400 |
| 09 Reject duplicate string | 400 |
| 10 Reject invalid Google token | 401 |
| 11 Sync library | 200, 첫 페이지·선택 버전·다음 커서 저장 |
| 12 Selected version content | 200, 버전 ID·음표 순서 확인 |
| 13 Missing version | 404 |
| 14 Sync without token | 401 |
| 15 Invalid page size | 400 |
| 16 Invalid cursor | 400 |
| 17 Sync next page | 200, 다음 페이지 확인 |
| 18 Upload actual parsed score | 201, 실제 파싱 샘플의 음표 146개·step 130개 저장 |
| 19 Compare stored parsed score | 200, 업로드한 음표 전체와 DB 조회 결과 비교 |

12는 첫 페이지에 선택 가능한 버전이 없으면 건너뜁니다. 17은 다음 커서가 없으면 건너뜁니다.
13은 05에서 저장한 score_id를 사용합니다. 17을 다시 보내면 다음 페이지로 계속 이동합니다.
공유 취소·다른 사용자의 비공개 악보·삭제 상태 검사는 별도 계정과 테스트 데이터가 필요합니다.

각 응답의 **Test Results**에서 검사 결과를 확인합니다. 03 성공 시 Google ID 토큰 변수는
비우고 서비스 토큰으로 이후 요청을 인증합니다. 따라서 03을 다시 실행하거나 Collection 전체를
재실행하려면 새 환경 파일을 생성·가져옵니다. 04 이후만 재실행하는 경우 서비스 토큰은 1시간 유효합니다.

05는 실행할 때마다 새 DB 악보가 생성됩니다. 자동 삭제나 중복 제거는 없습니다.
응답이 끊기면 저장 여부를 확인한 뒤 재시도합니다. 05의 성공 시 `score_id`, `version_id`가
환경 변수에 저장됩니다. 본문 `notes`를 직접 수정해 다른 악보도 시험할 수 있습니다.

#### 실제 파싱 결과로 확인

로그인된 상태에서 **18 → 19**를 실행합니다. 18 본문에는
`examples/anonimo-romance-romance_parsed.json`의 음표 배열을 넣어두었습니다.
다른 파싱 파일을 시험하려면 Body의 `notes` 배열을 해당 파일 내용으로 교체하고 `title`을 수정합니다.
18도 실행할 때마다 새 악보를 생성합니다.

19는 방금 저장한 버전을 조회하여 step, 줄, 마디, 박자, 기타 줄 번호, 프렛,
길이, MIDI 음높이를 전부 비교합니다. DB가 생성한 `score_step_id`만 비교에서 제외합니다.
18이 실패하면 19는 건너뜁니다. **Test Results가 모두 통과해야** 저장·조회 일치를 확인한 것입니다.
이 테스트는 파서 자체의 음악적 정확성까지 검증하지는 않습니다.

아래 SQL로 18 결과를 확인할 때는 `SCORE_ID`에 `parsed_score_id`를 사용합니다.
기본 샘플의 예상 값은 `actual_steps=130`, `actual_notes=146`, `in_library=t`입니다.

### 4. 실제 DB 저장 확인

DB psql에서 `SCORE_ID`를 Postman 환경의 `score_id` 값으로 바꿔 실행합니다.

```sql
-- 응답의 집계 값뿐 아니라 실제 저장된 행 수와 소유자도 확인합니다.
SELECT s.id, s.owner_user_id, s.title, s.visibility, v.version_number,
       (SELECT count(*) FROM public.score_steps st WHERE st.score_version_id=v.id) AS actual_steps,
       (SELECT count(*) FROM public.score_notes n JOIN public.score_steps st ON st.id=n.score_step_id
        WHERE st.score_version_id=v.id) AS actual_notes,
       EXISTS (SELECT 1 FROM public.user_score_library l
               WHERE l.score_id=s.id AND l.user_id=s.owner_user_id) AS in_library
FROM public.scores s JOIN public.score_versions v ON v.score_id=s.id
WHERE s.id='SCORE_ID';
```

05 기본 본문은 `actual_steps=2`, `actual_notes=3`, `in_library=t`여야 합니다.
`owner_user_id`는 Postman의 `user_id`와 같아야 합니다.

404는 서버 코드 배포 여부, 401은 서비스 토큰 만료, 503은 서버 설정·연결·DB 로그를 확인합니다.
[Postman 환경 변수 안내](https://learning.postman.com/docs/tests-and-scripts/write-scripts/postman-sandbox-reference/pm-variables)

<a id="tests"></a>

## API 테스트

자동 테스트는 기능 수정 후 기존 동작이 깨지지 않았는지 확인하기 위해 유지합니다.
`api/Dockerfile`은 테스트를 복사하지 않으므로 운영 이미지에 포함되지 않습니다.

테스트 파일은 `api/test_api.py` 하나로 관리하며, 기능별 클래스로 구분합니다.

| 클래스 | 확인 대상 |
| --- | --- |
| `GoogleLoginTests` | Google 로그인, 서비스 토큰, 사용자 인증 오류 |
| `ScoreTests`, `SyncTests`, `SyncAuthenticationTests` | 악보 입력 검증, 업로드, 목록·버전 조회, 접근 권한 |
| `DesktopGoogleTests`, `LoginSettingsTests` | Postman OAuth 도우미의 설정·HTTPS 요청 처리. PySide 화면 테스트가 아님 |

API requirements와 `google-auth-oauthlib`, `python-dotenv`가 설치된 Python 환경에서
저장소 루트 기준으로 실행합니다.

```bash
python -m unittest discover -s api -p 'test_*.py'
```

Google·DB 호출은 mock 처리하므로 실제 계정이나 운영 DB가 필요하지 않습니다.
실제 PostgreSQL 저장·조회와 네트워크 연결은 [Postman 테스트](#postman)로 별도 확인합니다.

루트의 `tests/`, `test/`는 앱·영상 처리 관련 테스트이며 이번 API 문서 정리 대상이 아닙니다.
