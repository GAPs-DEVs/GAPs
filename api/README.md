# Django 실행 및 DB 연결 확인

GET `/health/`는 Django 응답을, GET `/health/db/`는 `SELECT 1` 실행을 확인합니다.
DB 확인은 접속·쿼리 실행 검사이며 업무 테이블의 존재·조회 권한 검사는 아닙니다.
테이블 생성·변경은 하지 않습니다. `manage.py migrate`도 실행하지 않습니다.

## 서버 준비

기존 DB와 API는 같은 Backend EC2에 실행합니다. API 전용 Compose는 DB 네트워크
`guitar-practice-db_default`를 공유하며 DB를 새로 만들지 않습니다.

DB 작업 폴더에서 API 브랜치로 전환하면 DB 파일이 사라지므로, API 커밋을 push한 뒤
별도 작업 폴더로 받습니다. 아래 worktree 생성은 최초 한 번만 실행합니다.

```bash
cd ~/Guitar_Practice_Support_SW
git fetch origin
git worktree add --detach ../Guitar_Practice_API origin/feat/api-setup
cd ~/Guitar_Practice_API/api
sudo docker network inspect guitar-practice-db_default --format '{{.Name}}'
```

네트워크가 없으면 DB부터 실행합니다. 이미지 빌드 전 `df -h /`로 여유 공간도 확인합니다.

## 환경 설정과 실행

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
