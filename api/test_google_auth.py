"""Run: python -m unittest discover -s api -p 'test_*.py' (no live DB/Google)."""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("DJANGO_SECRET_KEY", "isolated-test-key-not-for-deployment")
os.environ.setdefault("DB_PASSWORD", "test-unused")

import django
django.setup()
from django.core import signing
from django.db import DatabaseError
from django.test import SimpleTestCase, override_settings
from google.auth.exceptions import TransportError
from config.auth import TOKEN_SALT


@override_settings(GOOGLE_OAUTH_CLIENT_ID="desktop.apps.googleusercontent.com", ALLOWED_HOSTS=["testserver"])
class GoogleLoginTests(SimpleTestCase):
    def post(self, body):
        return self.client.post("/api/auth/google", json.dumps(body), content_type="application/json")

    def test_rejects_invalid_body_without_verification(self):
        with patch("config.auth.verify_oauth2_token") as verify:
            for body in ([], {}, {"id_token": 1}, {"id_token": "x" * 16385}):
                self.assertEqual(self.post(body).status_code, 400)
            verify.assert_not_called()

    def test_post_only(self):
        self.assertEqual(self.client.get("/api/auth/google").status_code, 405)

    @override_settings(GOOGLE_OAUTH_CLIENT_ID="")
    def test_unconfigured_login_is_unavailable(self):
        self.assertEqual(self.post({"id_token": "test"}).status_code, 503)

    def test_rejected_google_token_never_reaches_db(self):
        with patch("config.auth.verify_oauth2_token", side_effect=ValueError("bad audience")), \
             patch("config.auth.member_from_google") as member:
            self.assertEqual(self.post({"id_token": "test"}).status_code, 401)
            member.assert_not_called()

    def test_google_outage_is_not_invalid_credentials(self):
        with patch("config.auth.verify_oauth2_token", side_effect=TransportError("timeout")):
            self.assertEqual(self.post({"id_token": "test"}).status_code, 503)

    def test_success_uses_configured_audience_and_signed_service_token(self):
        user = {"id": "a8821675-c8e0-4e5c-9d5d-1b90859692de", "username": "Test", "role": "user"}
        with patch("config.auth.verify_oauth2_token", return_value={"sub": "subject"}) as verify, \
             patch("config.auth.member_from_google", return_value=user):
            result = self.post({"id_token": "google-token", "role": "admin"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(verify.call_args.args[2], "desktop.apps.googleusercontent.com")
        data = result.json()
        self.assertEqual(signing.loads(data["access_token"], salt=TOKEN_SALT)["user_id"], user["id"])
        self.assertEqual(data["user"]["role"], "user")
        self.assertEqual(result["Cache-Control"], "no-store")

    def test_disabled_account_and_database_failure(self):
        for error, status in ((PermissionError(), 403), (DatabaseError(), 503)):
            with patch("config.auth.verify_oauth2_token", return_value={"sub": "subject"}), \
                 patch("config.auth.member_from_google", side_effect=error):
                self.assertEqual(self.post({"id_token": "test"}).status_code, status)

    def test_missing_tampered_and_expired_service_tokens(self):
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)
        self.assertEqual(self.client.get("/api/auth/me", HTTP_AUTHORIZATION="Bearer invalid").status_code, 401)
        with patch("django.core.signing.time.time", return_value=1):
            token = signing.dumps({"user_id": "a8821675-c8e0-4e5c-9d5d-1b90859692de"}, salt=TOKEN_SALT)
        self.assertEqual(self.client.get("/api/auth/me", HTTP_AUTHORIZATION="Bearer " + token).status_code, 401)


if __name__ == "__main__":
    unittest.main()
