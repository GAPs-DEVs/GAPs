"""Create a git-ignored Postman environment after interactive Google login.

Run from the repository root: uv run python -m api.postman.generate_environment
This does not call our API or upload scores. Google login remains interactive;
the actual /api/auth/google exchange is performed by Postman.
"""
import json
import os
from pathlib import Path

from api.postman.oauth_client import get_google_id_token, login_settings


def main():
    # Read the public template, never an existing file containing credentials.
    postman_dir = Path(__file__).resolve().parent
    template = postman_dir / "postman_environment.example.json"
    output = postman_dir / ".env.postman.json"
    try:
        environment = json.loads(template.read_text(encoding="utf-8"))
        token = get_google_id_token()
        for item in environment["values"]:
            if item["key"] == "base_url":
                item["value"] = login_settings()["GUITAR_API_BASE_URL"].rstrip("/")
            elif item["key"] == "google_id_token":
                item["value"] = token
        # Restrict newly created file permissions on POSIX; Windows uses its ACLs.
        # The .env.* ignore rule excludes this file. It contains a real ID token.
        old_mask = os.umask(0o077)
        try:
            output.write_text(json.dumps(environment, ensure_ascii=False, indent=2), encoding="utf-8")
        finally:
            os.umask(old_mask)
    except Exception:
        # Third-party exceptions can embed OAuth URLs; do not print their details.
        print("환경 파일 생성 실패: api/postman/.env.local 설정과 Google 인증 완료 여부를 확인해주세요.")
        return 1
    print(f"Postman에서 가져올 환경 파일: {output}")
    print("이 파일에는 로그인 토큰이 있습니다. 공유하지 말고 테스트 후 삭제해주세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
