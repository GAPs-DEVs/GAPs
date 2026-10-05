# 서버 문서

서버 관련 상세 문서는 이 디렉터리에서 관리합니다. 실행 파일과 환경 파일은 기존 `api/`, `db/`, `nginx/`에 둡니다.
앱·영상·오디오 개발 문서는 담당 영역에 그대로 유지합니다.

| 문서 | 내용 |
| --- | --- |
| [DB 구성](db/README.md) | PostgreSQL 실행, 스키마, 권한, 백업·복원 |
| [DB ERD](database-erd.md) | 테이블 관계와 필드 설명 |
| [API 실행](api/README.md) | 환경 설정, 컨테이너 실행, health 확인 |
| [Google 로그인](api/README.md#authentication) | 로그인 요청과 서비스 토큰 |
| [악보 업로드](api/README.md#upload) | 파싱된 음표 검증·저장 |
| [악보 동기화](api/README.md#sync) | 개인 악보함 목록·버전 상세 조회 |
| [Postman](api/README.md#postman) | 실제 서버 요청·응답 확인 |
| [자동 테스트](api/README.md#tests) | 로컬 테스트의 역할과 실행 |
| [Nginx·HTTPS](nginx/README.md) | 프록시, 인증서 발급·갱신 |

최초 구성 순서는 DB → API → Nginx·HTTPS → Postman 확인입니다.
이미 운영 중인 DB에는 최초 초기화·마이그레이션 명령을 다시 실행하지 않습니다.
