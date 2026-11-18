"""Google browser login with credentials from the environment or a local .env."""
import os
from pathlib import Path
import shlex
import sys
from wsgiref.simple_server import WSGIServer

from google_auth_oauthlib.flow import InstalledAppFlow


# A packaged app reads an external .env beside its executable.
_ENV_PATH = (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
             else Path(__file__).resolve().parents[1]) / ".env"
SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]


class GoogleAuthConfigurationError(ValueError):
    """A local OAuth setting is missing or malformed."""


def _load_client_config():
    names = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET")
    values = {}
    try:
        if _ENV_PATH.exists():
            for line in _ENV_PATH.read_text(encoding="utf-8-sig").splitlines():
                key, separator, value = line.partition("=")
                key = key.strip()
                if separator and key in names:
                    parts = shlex.split(value, comments=True, posix=True)
                    if len(parts) > 1:
                        raise ValueError()
                    values[key] = parts[0] if parts else ""
    except (OSError, UnicodeError, ValueError):
        raise GoogleAuthConfigurationError(
            ".env 파일의 Google 로그인 설정을 확인해주세요."
        ) from None

    for name in names:
        values[name] = os.environ.get(name, values.get(name, "")).strip()
    missing = [name for name in names if not values[name]]
    if missing:
        raise GoogleAuthConfigurationError(
            "Google 로그인 설정이 없습니다. 프로젝트의 .env 파일 또는 환경변수에 "
            + ", ".join(missing) + " 값을 설정해주세요."
        )
    return {
        "web": {
            "client_id": values["GOOGLE_CLIENT_ID"],
            "client_secret": values["GOOGLE_CLIENT_SECRET"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost:8080/"],
        }
    }


class ReusableWSGIServer(WSGIServer):
    allow_reuse_address = True


def fetch_google_id_token():
    """Return a fresh Google ID token, or None if browser authentication fails."""
    config = _load_client_config()
    try:
        flow = InstalledAppFlow.from_client_config(config, scopes=SCOPES)
        credentials = flow.run_local_server(
            host="localhost",
            port=8080,
            server_class=ReusableWSGIServer,
            prompt="select_account",
            success_message="인증이 완료되었습니다! 창을 닫고 프로그램으로 돌아가세요.",
        )
        return credentials.id_token
    except Exception as error:
        # Provider exception text can contain credential-bearing URLs.
        print(f"[구글 로그인 에러] {type(error).__name__}")
        return None
