# 데이터베이스 인수인계 문서

이 디렉터리는 PostgreSQL 데이터베이스 구성물을 전달하기 위한 패키지입니다. EC2 서버, API, OAuth 구현은 다른 담당자가 진행하며, 이 패키지는 그 구현과 독립적으로 적용할 수 있습니다.

명령은 별도 안내가 없으면 저장소 루트에서 실행합니다. 구성 파일 설명은 `db/` 기준입니다.

## 기준 파일

`migrations/` 디렉터리가 데이터베이스 스키마의 기준입니다. 파일명 번호가 낮은 순서부터 한 번씩 적용합니다. 이미 공유 환경에 적용된 마이그레이션 파일은 수정하지 말고, 다음 번호의 새 파일을 추가합니다.

`seed/development.sql`은 로컬 개발과 시연을 위한 선택적 샘플 데이터입니다. 운영 데이터베이스에는 절대 적용하지 않습니다.

## EC2 최초 준비

이 안내는 Ubuntu 기반 EC2를 기준으로 합니다. Docker Engine과 Compose 플러그인은 운영체제와 버전에 맞는 [Docker 공식 설치 안내](https://docs.docker.com/engine/install/ubuntu/)로 설치합니다. 단순 설치 스크립트보다 Docker의 공식 apt 저장소 방식이 운영 환경에 적합합니다.

설치가 끝난 뒤 Docker 데몬을 부팅 시 자동 실행하도록 설정하고, 정상 동작을 확인합니다.

```bash
sudo systemctl enable --now docker
sudo docker run --rm hello-world
docker --version
docker compose version
```

현재 EC2 사용자로 `sudo` 없이 Docker 명령을 실행하려면 아래 명령을 실행한 뒤 SSH 접속을 다시 시작합니다. Docker 그룹의 사용자는 사실상 root 수준의 권한을 갖게 되므로, 신뢰하는 배포 사용자에게만 부여합니다.

```bash
sudo usermod -aG docker "$USER"
```

EC2 Security Group은 다음 기준으로 설정합니다.

| 포트 | 허용 대상 | 용도 |
| --- | --- | --- |
| `22` | 관리자 본인 IP 또는 SSM | SSH 관리 |
| `80`, `443` | 인터넷 | 이후 API/HTTPS 서비스 |
| `5432` | 허용하지 않음 | PostgreSQL 외부 공개 금지 |

Docker는 컨테이너 포트 공개 시 호스트 방화벽 규칙과 다르게 동작할 수 있으므로, 보안 그룹에서 `5432`를 열지 않는 것이 최우선입니다. PostgreSQL 데이터가 보관되는 EC2 EBS 볼륨도 생성 시 암호화를 활성화합니다.

## Docker Compose로 실행하기

`docker-compose.yml`은 PostgreSQL과 일회성 마이그레이션 도구만 포함합니다. API 서버 담당자는 이후 같은 Compose 파일에 API 서비스를 추가하거나, 같은 Docker 네트워크에 연결하면 됩니다.

먼저 환경변수 예시를 복사하고, 두 비밀번호를 서로 다른 강한 값으로 변경합니다.

```bash
cp db/.env.example db/.env.local
chmod 600 db/.env.local
```

`db/.env.local`은 서버에만 두며 Git에 추가하지 않습니다. `POSTGRES_PASSWORD`와 `GUITAR_API_PASSWORD`에는 서로 다른 실제 비밀번호를 설정합니다.

### 최초 DB 실행

Compose 설정을 먼저 확인한 뒤 PostgreSQL을 실행합니다.

```bash
docker compose -f db/docker-compose.yml config
docker compose -f db/docker-compose.yml up -d postgres
docker compose -f db/docker-compose.yml ps
```

PostgreSQL의 상태가 `healthy`가 될 때까지 로그를 확인합니다.

```bash
docker compose -f db/docker-compose.yml logs -f postgres
```

`database system is ready to accept connections`가 보이면 `Ctrl+C`로 로그 보기만 종료합니다. PostgreSQL 컨테이너는 계속 실행됩니다.

마이그레이션과 API 계정 권한을 적용합니다. `migrate`는 기본적으로 실행되지 않는 일회성 컨테이너이므로, 새 배포 또는 스키마 변경 시에만 실행합니다.

```bash
docker compose -f db/docker-compose.yml --profile tools run --rm migrate
```

개발·시연용 데이터를 넣어야 할 때만 다음을 실행합니다.

```bash
docker compose -f db/docker-compose.yml --profile tools run --rm migrate seed
```

동작 확인은 다음과 같이 합니다.

```bash
docker compose -f db/docker-compose.yml exec postgres \
  psql -U guitar_migrator -d guitar_practice -c '\dt'
```

Compose 설정은 PostgreSQL 포트를 `127.0.0.1:5432`에만 바인딩합니다. 같은 Compose 네트워크의 API 컨테이너는 호스트 주소가 아닌 `postgres:5432`로 연결해야 합니다. EC2 보안 그룹에는 PostgreSQL 포트 `5432` 인바운드 규칙을 추가하지 않습니다.

`db/init/01-create-api-role.sh`은 **데이터 볼륨이 비어 있을 때 한 번만** 실행됩니다. 이미 생성된 볼륨에서는 `.env.local`의 API 계정 비밀번호를 바꿔도 자동 반영되지 않으므로, 운영 환경에서는 PostgreSQL 관리자만 별도 비밀번호 변경 명령을 실행해야 합니다.

### 이후 스키마 배포

저장소의 변경 사항이 서버 작업 경로에 반영됐다는 전제에서, 새 마이그레이션을 적용하기 전에 먼저 백업합니다. 백업 파일은 저장소나 Docker volume 내부가 아닌 별도 백업 경로에 두고, 이후 S3에도 복사합니다.

```bash
sudo mkdir -p /opt/backups/guitar-practice
docker compose -f db/docker-compose.yml exec -T postgres \
  pg_dump -U guitar_migrator -d guitar_practice \
  > "/opt/backups/guitar-practice/guitar_practice_$(date +%F_%H%M%S).sql"

docker compose -f db/docker-compose.yml config
docker compose -f db/docker-compose.yml --profile tools run --rm migrate
docker compose -f db/docker-compose.yml ps
```

운영 환경에서는 `seed` 명령을 실행하지 않습니다. 마이그레이션 실패 시에는 컨테이너 로그를 보존하고, 실패한 SQL이 어느 환경에도 적용되지 않은 경우에만 수정합니다.

## API 서버 연결 정보

API 컨테이너가 같은 Compose 구성에 포함되면, API 환경변수에는 아래 형식의 연결 주소를 설정합니다. 데스크톱 클라이언트에는 이 값을 절대 전달하지 않습니다.

```text
DATABASE_URL=postgresql://guitar_api:<GUITAR_API_PASSWORD>@postgres:5432/guitar_practice
```

같은 EC2의 API 컨테이너는 Docker 서비스 이름인 `postgres`로 연결합니다. `localhost`는 API 컨테이너 자신을 가리키므로 사용하면 안 됩니다.

## OAuth 연동 규칙

API/로그인 서버는 OAuth 제공자의 응답을 검증한 뒤에만 데이터베이스를 조회하거나 변경해야 합니다. 로그인 성공 시 아래 값으로 기존 회원을 찾거나 새 회원을 생성합니다.

```text
oauth_identities.provider          # 예: google, kakao, naver, apple
oauth_identities.provider_subject  # OIDC sub 등 제공자가 보장하는 불변 식별자
```

회원 식별의 유니크 기준은 이메일이 아닌 `(provider, provider_subject)`입니다.

비밀번호, OAuth client secret, access token, refresh token, 원본 오디오, 원본 영상은 이 데이터베이스에 저장하면 안 됩니다.

## 필요한 데이터베이스 역할

EC2에서 PostgreSQL을 준비할 때 아래 역할을 만듭니다. 역할의 비밀번호는 저장소에 넣지 않습니다.

| 역할 | 사용 주체 | 필요한 권한 |
| --- | --- | --- |
| `guitar_migrator` | 배포·마이그레이션 작업 | 데이터베이스 소유자, 확장 기능 생성 및 스키마 변경 |
| `guitar_api` | 실행 중인 API 서버 | API에 필요한 데이터 권한만 사용 (`SELECT`, `INSERT`, `UPDATE`, `DELETE`) |
| PostgreSQL 관리자 | 서버 운영 | 역할/데이터베이스 생성, 초기 스키마 소유권 설정, 백업 |

API 프로세스에서 PostgreSQL 관리자 계정을 사용하면 안 됩니다. 서버 담당자는 EC2 환경변수 또는 비밀 관리 서비스를 통해 자격 증명을 제공하고, Git에는 저장하지 않습니다.

역할을 만들고 전체 마이그레이션을 적용한 뒤, PostgreSQL 관리자(또는 현재 `public` 스키마 소유자)가 `grant_api_role.sql.example`을 실행합니다. 이 파일은 애플리케이션 스키마를 마이그레이션 역할에 연결하고, API 계정을 데이터 접근 권한으로 제한하며, 이후 새 테이블에도 같은 권한이 적용되게 합니다.

## 마이그레이션 적용

Docker Compose를 쓰지 않는 환경에서는 PostgreSQL 준비가 끝난 뒤, 저장소 루트에서 아래 명령을 실행합니다. `DATABASE_URL`은 반드시 `guitar_migrator` 역할의 연결 주소여야 합니다.

```bash
for migration in db/migrations/*.sql; do
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$migration"
done
```

로컬 개발 데이터베이스에서만 샘플 데이터를 추가합니다.

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/seed/development.sql
```

EC2 배포 환경에서는 마이그레이션 적용 후 PostgreSQL 관리자가 다음 명령을 한 번 실행합니다.

```bash
psql "$DATABASE_ADMIN_URL" -v ON_ERROR_STOP=1 -f db/grant_api_role.sql.example
```

`DATABASE_ADMIN_URL`은 초기 설정을 위한 일회성 연결 주소입니다. 실행 중인 API 서버의 환경변수로 설정하지 않습니다.

각 마이그레이션은 트랜잭션으로 처리됩니다. 실패하면 해당 마이그레이션은 롤백됩니다. 공유 환경에 한 번도 적용되지 않은 파일만 수정해 재시도하고, 이미 적용된 스키마를 변경해야 한다면 새 번호의 마이그레이션을 추가합니다.

## 서버 담당자 배포 점검표

1. PostgreSQL 데이터는 암호화된 영구 EBS 볼륨에 저장합니다. `5432` 포트를 인터넷에 공개하지 않습니다.
2. 데이터베이스 접속은 같은 EC2 서버의 API 프로세스 또는 허용된 private security group으로만 제한합니다.
3. 운영 자격 증명은 Git 외부에 저장하고, API에는 권한이 제한된 `guitar_api` 역할을 사용합니다.
4. 운영 마이그레이션 전에는 백업을 수행합니다. `pg_dump` 자동 백업을 S3에 보관하고, 주기적으로 복구를 검증합니다.
5. 새 API 버전에 트래픽을 연결하기 전에 배포 단계에서 마이그레이션을 적용합니다.

## 현재 포함 범위

- OAuth 연동 회원: `users`, `oauth_identities`
- 악보와 파일: `scores`, `score_assets`, `score_versions`, `score_steps`, `score_notes`
- 악보 공유와 개인 악보함: `score_members`, `user_score_library`
- 연습: `user_instruments`, `practice_sessions`, `practice_step_results`, `user_score_progress`

테이블 관계와 현재 JSON 악보의 필드 매핑은 [ERD 문서](database-erd.md)를 참고합니다.
