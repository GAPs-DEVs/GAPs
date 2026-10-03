"""Exercise the desktop HTTP boundary without a browser, Qt or credentials."""
import os
import unittest
from unittest.mock import MagicMock, patch
from app.google_login import LoginError, login_with_google


@patch.dict(os.environ, {"GOOGLE_OAUTH_CLIENT_FILE": "outside-repo.json",
                         "GUITAR_API_BASE_URL": "https://example.test"})
class DesktopGoogleTests(unittest.TestCase):
    @patch("app.google_login.os.path.isfile", return_value=True)
    def test_http_server_rejected_before_browser_opens(self, unused):
        with patch.dict(os.environ, {"GUITAR_API_BASE_URL": "http://example.test"}), \
             patch("app.google_login.InstalledAppFlow.from_client_secrets_file") as factory:
            with self.assertRaises(LoginError):
                login_with_google()
            factory.assert_not_called()

    @patch("app.google_login.os.path.isfile", return_value=True)
    def test_only_id_token_sent_over_https_and_redirects_rejected(self, unused):
        flow = MagicMock(client_type="installed")
        flow.run_local_server.return_value.id_token = "test-google-token"
        with patch("app.google_login.InstalledAppFlow.from_client_secrets_file", return_value=flow) as factory, \
             patch("app.google_login.requests.post") as post:
            post.return_value.status_code = 302
            with self.assertRaises(LoginError):
                login_with_google()
            self.assertTrue(factory.call_args.kwargs["autogenerate_code_verifier"])
            self.assertEqual(flow.run_local_server.call_args.kwargs["host"], "127.0.0.1")
            self.assertEqual(post.call_args.kwargs["json"], {"id_token": "test-google-token"})
            self.assertFalse(post.call_args.kwargs["allow_redirects"])

    def test_missing_credentials_does_not_start_login(self):
        with patch("app.google_login.os.path.isfile", return_value=False):
            with self.assertRaises(LoginError):
                login_with_google()
