"""Validation and authorization tests; no production DB or tokens required."""
import copy
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("DJANGO_SECRET_KEY", "isolated-test-key-not-for-deployment")
os.environ.setdefault("DB_PASSWORD", "test-unused")
import django
django.setup()
from django.test import SimpleTestCase, override_settings
from django.db import DatabaseError
from config.scores import validate_score
from config.scores import CURSOR_SALT, library_page, version_content
from django.core import signing
from uuid import UUID


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
