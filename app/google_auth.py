"""Google browser login with credentials from the environment or a local .env."""
import os
from pathlib import Path
import shlex
import socket
import struct
import sys

from google_auth_oauthlib import flow as _flow
from google_auth_oauthlib.flow import InstalledAppFlow


# A packaged app prefers an external .env beside its executable, then the bundled one.
def _env_path():
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parents[1] / ".env"
    external = Path(sys.executable).resolve().parent / ".env"
    if external.exists():
        return external
    return Path(getattr(sys, "_MEIPASS", external.parent)) / ".env"


_ENV_PATH = _env_path()
REDIRECT_PORT = 8080
AUTH_TIMEOUT_SECONDS = 180
SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]

if sys.platform != "win32":
    _flow._ExclusiveWSGIServer.allow_reuse_address = True


class GoogleAuthConfigurationError(ValueError):
    """A local OAuth setting is missing or malformed."""


def load_client_config():
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


def fetch_google_id_token(config=None):
    """Return a fresh Google ID token, or None if browser authentication fails."""
    config = config or load_client_config()
    try:
        flow = InstalledAppFlow.from_client_config(config, scopes=SCOPES)
        credentials = flow.run_local_server(
            host="localhost",
            port=REDIRECT_PORT,
            prompt="select_account",
            success_message="인증이 완료되었습니다! 창을 닫고 프로그램으로 돌아가세요.",
            timeout_seconds=AUTH_TIMEOUT_SECONDS,
        )
        return credentials.id_token
    except Exception as error:
        # Provider exception text can contain credential-bearing URLs.
        print(f"[구글 로그인 에러] {type(error).__name__}")
        return None


def cancel_google_login():
    try:
        with socket.create_connection(("127.0.0.1", REDIRECT_PORT), timeout=1) as sock:
            sock.sendall(b"GET /?error=access_denied HTTP/1.0\r\n\r\n")
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    except OSError:
        pass
