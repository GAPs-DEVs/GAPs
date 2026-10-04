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
