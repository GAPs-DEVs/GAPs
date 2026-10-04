# Postman으로 로그인·악보 업로드 테스트

PySide 화면 없이 요청을 직접 전송합니다. 서버에 `/api/scores` 구현이 배포되어 있어야 합니다.
아직 배포하지 않았다면 변경사항을 commit/push하고 서버에서 pull한 뒤 API를 재빌드합니다.
DB migrate 및 Nginx 변경은 필요 없습니다.

## 1. 테스트 계정으로 Google 인증

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

## 2. Postman Import

Postman 데스크톱 앱의 **Import**에서 아래 두 파일을 가져옵니다.

- `api/postman/postman_collection.json`
- `api/postman/.env.postman.json` (위 명령으로 생성한 환경 파일)

우측 상단 환경에서 **Guitar Practice EC2**를 선택하고 `base_url`을 확인합니다.
SSL 인증서 검증은 켜둡니다. 토큰 변수는 민감한 값으로 취급하고 공유·공개하지 않습니다.
템플릿 `postman_environment.example.json`에는 토큰이 없으므로 로그인 테스트에는 생성 파일을 사용합니다.

## 3. 번호 순서대로 Send

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

12는 첫 페이지에 선택 가능한 버전이 없으면 건너뜁니다. 17은 다음 커서가 없으면 건너뜁니다.
13은 05에서 저장한 score_id를 사용합니다. 17을 다시 보내면 다음 페이지로 계속 이동합니다.
공유 취소·다른 사용자의 비공개 악보·삭제 상태 검사는 별도 계정과 테스트 데이터가 필요합니다.

각 응답의 **Test Results**에서 검사 결과를 확인합니다. 03 성공 시 Google ID 토큰 변수는
비우고 서비스 토큰으로 이후 요청을 인증합니다. 따라서 03을 다시 실행하거나 Collection 전체를
재실행하려면 새 환경 파일을 생성·가져옵니다. 04 이후만 재실행하는 경우 서비스 토큰은 1시간 유효합니다.

05는 실행할 때마다 새 DB 악보가 생성됩니다. 자동 삭제나 중복 제거는 없습니다.
응답이 끊기면 저장 여부를 확인한 뒤 재시도합니다. 05의 성공 시 `score_id`, `version_id`가
환경 변수에 저장됩니다. 본문 `notes`를 직접 수정해 다른 악보도 시험할 수 있습니다.

## 4. 실제 DB 저장 확인

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
