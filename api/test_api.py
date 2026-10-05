"""API regression tests. Run from the repository root:
python -m unittest discover -s api -p 'test_api.py'

Google requests and database operations are mocked; no live credentials required.
"""
import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID

# Make both config.* and api.postman.* importable, including direct execution.
# Configure Django once, before importing views or constructing test clients.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
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
from config.scores import validate_score, CURSOR_SALT, library_page, version_content
from api.postman.oauth_client import LoginError, login_with_google, login_settings

# Google login and service-token validation.
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



# Postman OAuth helper: browser/HTTP boundaries and local settings.
@patch.dict(os.environ, {"GOOGLE_OAUTH_CLIENT_FILE": "outside-repo.json",
                         "GUITAR_API_BASE_URL": "https://example.test"})
class DesktopGoogleTests(unittest.TestCase):
    def setUp(self):
        # Keep network-boundary tests independent of the developer's real file.
        patcher = patch("api.postman.oauth_client.dotenv_values", return_value={})
        patcher.start()
        self.addCleanup(patcher.stop)

    @patch("api.postman.oauth_client.os.path.isfile", return_value=True)
    def test_http_server_rejected_before_browser_opens(self, unused):
        with patch.dict(os.environ, {"GUITAR_API_BASE_URL": "http://example.test"}), \
             patch("api.postman.oauth_client.InstalledAppFlow.from_client_secrets_file") as factory:
            with self.assertRaises(LoginError):
                login_with_google()
            factory.assert_not_called()

    @patch("api.postman.oauth_client.os.path.isfile", return_value=True)
    def test_only_id_token_sent_over_https_and_redirects_rejected(self, unused):
        flow = MagicMock(client_type="installed")
        flow.run_local_server.return_value.id_token = "test-google-token"
        with patch("api.postman.oauth_client.InstalledAppFlow.from_client_secrets_file", return_value=flow) as factory, \
             patch("api.postman.oauth_client.requests.post") as post:
            post.return_value.status_code = 302
            with self.assertRaises(LoginError):
                login_with_google()
            self.assertTrue(factory.call_args.kwargs["autogenerate_code_verifier"])
            self.assertEqual(flow.run_local_server.call_args.kwargs["host"], "127.0.0.1")
            self.assertEqual(post.call_args.kwargs["json"], {"id_token": "test-google-token"})
            self.assertFalse(post.call_args.kwargs["allow_redirects"])

    def test_missing_credentials_does_not_start_login(self):
        with patch("api.postman.oauth_client.os.path.isfile", return_value=False):
            with self.assertRaises(LoginError):
                login_with_google()


class LoginSettingsTests(unittest.TestCase):
    def test_file_overrides_old_environment_and_supports_bom_spaces(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".env.local"
            path.write_text('GOOGLE_OAUTH_CLIENT_FILE="C:/My Files/client.json"\n'
                            'GUITAR_API_BASE_URL=https://example.test\n', encoding="utf-8-sig")
            with patch("api.postman.oauth_client.APP_ENV_FILE", path), \
                 patch.dict(os.environ, {"GUITAR_API_BASE_URL": "http://old.test"}):
                values = login_settings()
                self.assertEqual(values["GOOGLE_OAUTH_CLIENT_FILE"], "C:/My Files/client.json")
                self.assertEqual(values["GUITAR_API_BASE_URL"], "https://example.test")
                self.assertEqual(os.environ["GUITAR_API_BASE_URL"], "http://old.test")

    def test_missing_file_falls_back_to_environment(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch("api.postman.oauth_client.APP_ENV_FILE", Path(folder) / "missing.env"), \
             patch.dict(os.environ, {"GUITAR_API_BASE_URL": "https://example.test"}):
            self.assertEqual(login_settings()["GUITAR_API_BASE_URL"], "https://example.test")

# Score upload and library/version reads. Database access remains mocked.
PAYLOAD = {"title": "Test", "source_type": "import", "notes": [
    {"step": 1, "string": 1, "fret": 0, "duration_ms": 500},
    {"step": 1, "string": 2, "fret": 3, "duration_ms": 500}]}


@override_settings(ALLOWED_HOSTS=["testserver"])
class ScoreTests(SimpleTestCase):
    def test_chord_grouping(self):
        title, source, groups = validate_score(PAYLOAD)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[1]["notes"]), 2)

    def test_invalid_notes(self):
        for field, value in (("string", 7), ("fret", -1), ("step", True),
                             ("duration_ms", 0), ("midi_pitch", 128)):
            payload = copy.deepcopy(PAYLOAD)
            payload["notes"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_score(payload)

    def test_duplicate_string_and_inconsistent_timing(self):
        for field, value in (("string", 1), ("duration_ms", 600)):
            payload = copy.deepcopy(PAYLOAD)
            payload["notes"][1][field] = value
            with self.assertRaises(ValueError):
                validate_score(payload)

    def test_rejects_owner_override_and_empty_notes(self):
        for payload in ({**PAYLOAD, "owner_user_id": "another-user"},
                        {**PAYLOAD, "notes": []}, {**PAYLOAD, "title": " "}):
            with self.assertRaises(ValueError):
                validate_score(payload)

    def test_unauthenticated_upload_never_saves(self):
        with patch("config.scores.save_score") as save:
            result = self.client.post("/api/scores", json.dumps(PAYLOAD), content_type="application/json")
            self.assertEqual(result.status_code, 401)
            save.assert_not_called()

    def test_owner_from_authentication_and_created_response(self):
        with patch("config.scores.authenticate_user", return_value={"id": "authenticated-user"}), \
             patch("config.scores.save_score", return_value={"id": "new-score"}) as save:
            result = self.client.post("/api/scores", json.dumps(PAYLOAD), content_type="application/json")
            self.assertEqual(result.status_code, 201)
            self.assertEqual(save.call_args.args[0], "authenticated-user")

    def test_bad_payload_and_size_limit_do_not_save(self):
        with patch("config.scores.authenticate_user", return_value={"id": "user"}), \
             patch("config.scores.save_score") as save:
            result = self.client.post("/api/scores", '{"owner_user_id":"bad"}', content_type="application/json")
            self.assertEqual(result.status_code, 400)
            result = self.client.post("/api/scores", " " * 65537, content_type="application/json")
            self.assertEqual(result.status_code, 413)
            save.assert_not_called()

    def test_failed_storage_does_not_report_success(self):
        with patch("config.scores.authenticate_user", return_value={"id": "user"}), \
             patch("config.scores.save_score", side_effect=DatabaseError()):
            result = self.client.post("/api/scores", json.dumps(PAYLOAD), content_type="application/json")
            self.assertEqual(result.status_code, 503)


USER_ID = "11111111-1111-4111-8111-111111111111"
SCORE_ID = "22222222-2222-4222-8222-222222222222"
VERSION_ID = "33333333-3333-4333-8333-333333333333"


@override_settings(ALLOWED_HOSTS=["testserver"])
class SyncTests(SimpleTestCase):
    def setUp(self):
        patcher = patch("config.scores.authenticate_user", return_value={"id": USER_ID})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_empty_library(self):
        with patch("config.scores.library_page", return_value=([], False)):
            result = self.client.get("/api/scores/sync")
        self.assertEqual(result.json(), {"items": [], "next_cursor": None})

    def test_next_cursor_returns_last_id_and_is_scoped_to_user(self):
        with patch("config.scores.library_page", return_value=([{"score_id": SCORE_ID}], True)):
            result = self.client.get("/api/scores/sync?limit=1")
        cursor = result.json()["next_cursor"]
        self.assertEqual(signing.loads(cursor, salt=CURSOR_SALT), {"user_id": USER_ID, "after": SCORE_ID})
        with patch("config.scores.library_page", return_value=([], False)) as page:
            self.assertEqual(self.client.get("/api/scores/sync", {"cursor": cursor}).status_code, 200)
            page.assert_called_once_with(USER_ID, SCORE_ID, 20)

    def test_bad_limits_and_cursors_do_not_query(self):
        foreign = signing.dumps({"user_id": "someone-else", "after": SCORE_ID}, salt=CURSOR_SALT)
        with patch("config.scores.library_page") as page:
            for query in ({"limit": "0"}, {"limit": "101"}, {"limit": "abc"},
                          {"cursor": ""}, {"cursor": "tampered"}, {"cursor": foreign}):
                self.assertEqual(self.client.get("/api/scores/sync", query).status_code, 400)
            page.assert_not_called()

    def test_expired_cursor(self):
        with patch("django.core.signing.time.time", return_value=1):
            cursor = signing.dumps({"user_id": USER_ID, "after": SCORE_ID}, salt=CURSOR_SALT)
        self.assertEqual(self.client.get("/api/scores/sync", {"cursor": cursor}).status_code, 400)

    def test_last_page_has_no_cursor(self):
        with patch("config.scores.library_page", return_value=([{"score_id": SCORE_ID}], False)):
            self.assertIsNone(self.client.get("/api/scores/sync").json()["next_cursor"])

    def test_get_only(self):
        self.assertEqual(self.client.post("/api/scores/sync").status_code, 405)

    def test_read_database_failure(self):
        with patch("config.scores.library_page", side_effect=DatabaseError()):
            self.assertEqual(self.client.get("/api/scores/sync").status_code, 503)

    def test_version_returns_content_or_404(self):
        url = f"/api/scores/{SCORE_ID}/versions/{VERSION_ID}"
        with patch("config.scores.version_content", return_value=None):
            self.assertEqual(self.client.get(url).status_code, 404)
        with patch("config.scores.version_content", return_value={"notes": []}) as content:
            self.assertEqual(self.client.get(url).json(), {"notes": []})
            content.assert_called_once_with(USER_ID, UUID(SCORE_ID), UUID(VERSION_ID))

    def test_selected_version_mapping_and_page_overfetch(self):
        # SQL handles selection/permissions; this checks the wire representation
        # and that the extra pagination row is never sent to the client.
        latest = "44444444-4444-4444-8444-444444444444"
        row = (SCORE_ID, "Title", None, "private", "ready", None, True, None,
               VERSION_ID, 1, latest, 2, "editor")
        with patch("config.scores.connection") as db:
            db.cursor.return_value.__enter__.return_value.fetchall.return_value = [row, row]
            items, more = library_page(USER_ID, None, 1)
        self.assertTrue(more)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["selected_version_id"], VERSION_ID)
        self.assertTrue(items[0]["has_update"])
        self.assertEqual(items[0]["permission"], "editor")

    def test_version_decodes_json_text(self):
        notes = [{"step": 1, "string": 1, "score_step_id": "step-id"}]
        with patch("config.scores.connection") as db:
            db.cursor.return_value.__enter__.return_value.fetchone.return_value = (
                SCORE_ID, "Title", VERSION_ID, 1, 1, 1, json.dumps(notes))
            result = version_content(USER_ID, SCORE_ID, VERSION_ID)
        self.assertEqual(result["notes"], notes)


@override_settings(ALLOWED_HOSTS=["testserver"])
class SyncAuthenticationTests(SimpleTestCase):
    def test_anonymous_requests_cannot_reach_queries(self):
        with patch("config.scores.library_page") as page, patch("config.scores.version_content") as content:
            self.assertEqual(self.client.get("/api/scores/sync").status_code, 401)
            self.assertEqual(self.client.get(f"/api/scores/{SCORE_ID}/versions/{VERSION_ID}").status_code, 401)
            page.assert_not_called()
            content.assert_not_called()


if __name__ == "__main__":
    unittest.main()
