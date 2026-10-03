# Google 로그인 실행

기존 `users`, `oauth_identities` 테이블을 사용합니다. DB migrate는 실행하지 않습니다.
이번 범위는 Google 로그인입니다. 악보 업로드·동기화는 아직 구현하지 않았습니다.

## 서버

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
curl -i https://43.201.62.126/health/db/
# 잘못된 토큰은 HTTP 401이어야 합니다. 성공 응답이 나오면 안 됩니다.
curl -i https://43.201.62.126/api/auth/google \
  -H 'Content-Type: application/json' -d '{"id_token":"invalid"}'
```

실제 서버 공인 IP가 다르면 URL도 변경합니다. 503이면 클라이언트 ID 설정,
Google 인증 서버 연결 또는 DB 접근 상태를 확인합니다.

## 로컬 앱 (Git Bash)

```bash
# 저장소 루트에서 앱 의존성을 설치합니다.
uv sync --frozen
# JSON은 프로젝트 밖에 보관하고 실제 경로를 지정합니다.
export GOOGLE_OAUTH_CLIENT_FILE='C:/Users/jjs_0/Downloads/실제다운로드파일.json'
export GUITAR_API_BASE_URL='https://43.201.62.126'
uv run python -m app.main
```

이 환경변수는 같은 터미널에서 실행하는 앱에 전달됩니다. `.env` 파일을 앱이 자동으로
읽는 방식은 아닙니다. Google 로그인 버튼을 누르고 브라우저에서 테스트 계정을 선택합니다.
2분 이내에 인증을 완료하세요. 실패·취소 시 다시 시도할 수 있습니다.

성공하면 서버 사용자 정보를 받아 악보 선택 화면으로 이동합니다.
앱의 `main_window.api_session`에 서비스 토큰과 사용자 정보가 메모리로만 보관됩니다.
Google 토큰이나 서비스 토큰은 파일·로그에 기록하지 않습니다.

## API 계약과 제한

- `POST /api/auth/google`: JSON `id_token` → `access_token`, `token_type`, `expires_in`, `user`.
- `GET /api/auth/me`: `Authorization: Bearer <access_token>` → 현재 활성 사용자 정보.
- 서비스 토큰은 Django 서명 토큰이며 JWT가 아닙니다. 클라이언트는 불투명 문자열로 취급합니다.
- 서비스 토큰 수명은 1시간이며 자동 갱신은 없습니다. 만료 또는 앱 재시작 후 재로그인합니다.
- 동일 Google 계정은 `provider=google` + `sub`로 연결합니다. 이메일로 기존 계정을 자동 병합하지 않습니다.
- 신규 회원 role은 DB 기본값 `user`입니다. 정지·삭제 회원은 로그인할 수 없습니다.
- 기존 이메일/비밀번호 로그인·게스트 모드는 로컬 전용이며 서버 토큰을 발급하지 않습니다.
- 앱에서 토큰을 지워도 이미 복사된 토큰은 만료 전까지 유효합니다. 서버의 계정 정지로 접근을 차단할 수 있습니다.

인증 테스트(별도 환경에 API requirements와 google-auth-oauthlib 설치 후):

```bash
python -m unittest discover -s api -p 'test_*.py'
```

이 테스트는 Google 검증·DB 연결을 mock 처리합니다. 실제 검증은 앱 로그인 성공,
동일 계정 재로그인, DB 회원 연결 및 `/api/auth/me` 응답으로 확인해야 합니다.
