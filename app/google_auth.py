# app/google_auth.py
import socket
from google_auth_oauthlib.flow import InstalledAppFlow
from wsgiref.simple_server import WSGIServer

GOOGLE_CLIENT_ID = "29689475906-8esc2uvfuu6493rbnrho19v3kcu5mmmh.apps.googleusercontent.com"
GOOGLE_CLIENT_SECRET = "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"

CLIENT_CONFIG = {
    "web": {
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost:8080/"]
    }
}

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile"
]

# 소켓 즉시 재사용(SO_REUSEADDR)이 켜진 WSGI 서버 클래스
class ReusableWSGIServer(WSGIServer):
    allow_reuse_address = True

def fetch_google_id_token():
    """브라우저 기반 Google OAuth 인증 후 id_token 반환"""
    try:
        flow = InstalledAppFlow.from_client_config(CLIENT_CONFIG, scopes=SCOPES)
        
        # server_class에 ReusableWSGIServer를 넘겨 TIME_WAIT 상태에서도 8080 포트를 즉시 재사용
        credentials = flow.run_local_server(
            host="localhost",
            port=8080,
            server_class=ReusableWSGIServer,
            prompt="select_account",
            success_message="인증이 완료되었습니다! 창을 닫고 프로그램으로 돌아가세요."
        )
        return credentials.id_token
    except Exception as e:
        print(f"[구글 로그인 에러] {e}")
        return None