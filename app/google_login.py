"""Desktop OAuth and HTTPS API exchange, executed outside the UI thread."""
import os
from functools import partial
from urllib.parse import urlsplit

import requests
from google_auth_oauthlib.flow import InstalledAppFlow


class LoginError(Exception):
    """Only messages deliberately written for users may be shown in the UI."""


def login_with_google():
    # Keep the downloaded desktop JSON outside Git and pass only its path.
    credentials_path = os.environ.get("GOOGLE_OAUTH_CLIENT_FILE", "")
    base_url = os.environ.get("GUITAR_API_BASE_URL", "").rstrip("/")
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

    # Send the ID token only to the configured HTTPS endpoint. Do not follow
    # redirects to an unexpected host or persist Google/service tokens to disk.
    result = requests.post(base_url + "/api/auth/google",
                           json={"id_token": credentials.id_token},
                           timeout=(5, 20), allow_redirects=False)
    if result.status_code != 200:
        raise LoginError(f"서버 로그인 실패 (HTTP {result.status_code}). 서버 설정을 확인해주세요.")
    data = result.json()
    if not isinstance(data, dict) or not isinstance(data.get("user"), dict) or not data.get("access_token"):
        raise LoginError("서버 로그인 응답 형식이 올바르지 않습니다.")
    return data
