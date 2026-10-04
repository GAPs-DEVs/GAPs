"""Exercise the desktop HTTP boundary without a browser, Qt or credentials."""
import os
import unittest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
from api.postman.oauth_client import LoginError, login_with_google, login_settings


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
