"""Desktop OAuth and HTTPS API exchange, executed outside the UI thread."""
import os
from functools import partial
from pathlib import Path
from urllib.parse import urlsplit

import requests
from dotenv import dotenv_values
from google_auth_oauthlib.flow import InstalledAppFlow


class LoginError(Exception):
    """Only messages deliberately written for users may be shown in the UI."""


APP_ENV_FILE = Path(__file__).resolve().parent / ".env.local"


def login_settings():
    # Resolve from this module, not the terminal's working directory. Read on each
    # login attempt so editing the file does not require restarting VS Code.
    # File values take priority over old Windows/shell variables. Only the two
    # login settings are consumed; no global process environment is modified.
    values = dotenv_values(APP_ENV_FILE, encoding="utf-8-sig", interpolate=False)
    return {
        key: (values.get(key, os.environ.get(key, "")) or "").strip()
        for key in ("GOOGLE_OAUTH_CLIENT_FILE", "GUITAR_API_BASE_URL")
    }


def get_google_id_token():
    """Browser authentication only; the caller decides when to contact the API."""
    # Keep the downloaded desktop JSON outside Git and pass only its path.
    settings = login_settings()
    credentials_path = settings["GOOGLE_OAUTH_CLIENT_FILE"]
    base_url = settings["GUITAR_API_BASE_URL"].rstrip("/")
    parsed = urlsplit(base_url)
    if not credentials_path or not os.path.isfile(credentials_path):
        raise LoginError("GOOGLE_OAUTH_CLIENT_FILE에 다운로드한 JSON 경로를 설정해주세요.")
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
        raise LoginError("GUITAR_API_BASE_URL에 서버 HTTPS 주소를 설정해주세요.")

    # PKCE protects the authorization code; the library also verifies OAuth state.
    # Listen only on loopback, using an available random port on this PC.
    flow = InstalledAppFlow.from_client_secrets_file(
        credentials_path, scopes=["openid", "https://www.googleapis.com/auth/userinfo.email",
                                  "https://www.googleapis.com/auth/userinfo.profile"],
        autogenerate_code_verifier=True)
    if flow.client_type != "installed":
        raise LoginError("Google Cloud에서 데스크톱 앱 유형의 JSON을 다운로드해주세요.")
    # The callback wait and token exchange each need their own timeout.
    flow.oauth2session.request = partial(flow.oauth2session.request, timeout=15)
    credentials = flow.run_local_server(
        host="127.0.0.1", port=0, timeout_seconds=120,
        authorization_prompt_message="",  # Never print authorization URLs/tokens.
        success_message="Google authorization received. You may return to the app.",
        access_type="online", prompt="select_account")
    if not credentials.id_token:
        raise LoginError("Google 인증 결과에 ID 토큰이 없습니다. 다시 로그인해주세요.")
    return credentials.id_token


def login_with_google():
    google_token = get_google_id_token()
    base_url = login_settings()["GUITAR_API_BASE_URL"].rstrip("/")

    # Send the ID token only to the configured HTTPS endpoint. Do not follow
    # redirects to an unexpected host or persist Google/service tokens to disk.
    result = requests.post(base_url + "/api/auth/google",
                           json={"id_token": google_token},
                           timeout=(5, 20), allow_redirects=False)
    if result.status_code != 200:
        raise LoginError(f"서버 로그인 실패 (HTTP {result.status_code}). 서버 설정을 확인해주세요.")
    data = result.json()
    if not isinstance(data, dict) or not isinstance(data.get("user"), dict) or not data.get("access_token"):
        raise LoginError("서버 로그인 응답 형식이 올바르지 않습니다.")
    return data
