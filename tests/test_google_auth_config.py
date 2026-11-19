from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app import google_auth


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    monkeypatch.setattr(google_auth, "_ENV_PATH", path)
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    return path


def test_reads_local_env_without_changing_process_environment(env_file):
    import os

    env_file.write_text('GOOGLE_CLIENT_ID="test-client"\nGOOGLE_CLIENT_SECRET=test-secret # local\n')
    config = google_auth.load_client_config()["web"]
    assert config["client_id"] == "test-client"
    assert config["client_secret"] == "test-secret"
    assert "GOOGLE_CLIENT_SECRET" not in os.environ


def test_shell_environment_overrides_file(env_file, monkeypatch):
    env_file.write_text("GOOGLE_CLIENT_ID=file-client\nGOOGLE_CLIENT_SECRET=file-secret\n")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "shell-client")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "shell-secret")
    config = google_auth.load_client_config()["web"]
    assert config["client_id"] == "shell-client"
    assert config["client_secret"] == "shell-secret"


def test_environment_works_without_env_file(env_file, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-secret")
    assert google_auth.load_client_config()["web"]["client_id"] == "test-client"


def test_missing_settings_do_not_open_browser(env_file, monkeypatch):
    factory = Mock()
    monkeypatch.setattr(google_auth.InstalledAppFlow, "from_client_config", factory)
    with pytest.raises(google_auth.GoogleAuthConfigurationError, match="GOOGLE_CLIENT_ID"):
        google_auth.fetch_google_id_token()
    factory.assert_not_called()


@pytest.mark.parametrize("value", ['"unterminated', 'unquoted extra'])
def test_invalid_file_does_not_expose_value(env_file, value):
    env_file.write_text("GOOGLE_CLIENT_ID=test-client\nGOOGLE_CLIENT_SECRET=" + value)
    with pytest.raises(google_auth.GoogleAuthConfigurationError) as result:
        google_auth.load_client_config()
    assert value not in str(result.value)


def test_browser_login_uses_loaded_settings_and_returns_fresh_token(env_file, monkeypatch):
    env_file.write_text("GOOGLE_CLIENT_ID=test-client\nGOOGLE_CLIENT_SECRET=test-secret\n")
    flow = Mock()
    flow.run_local_server.return_value = SimpleNamespace(id_token="fresh-test-token")
    factory = Mock(return_value=flow)
    monkeypatch.setattr(google_auth.InstalledAppFlow, "from_client_config", factory)
    assert google_auth.fetch_google_id_token() == "fresh-test-token"
    assert factory.call_args.args[0]["web"]["client_secret"] == "test-secret"
    assert flow.run_local_server.call_args.kwargs["port"] == 8080
    assert flow.run_local_server.call_args.kwargs["timeout_seconds"] == google_auth.AUTH_TIMEOUT_SECONDS


def test_provider_error_does_not_log_secret(env_file, monkeypatch, capsys):
    env_file.write_text("GOOGLE_CLIENT_ID=test-client\nGOOGLE_CLIENT_SECRET=test-secret\n")
    monkeypatch.setattr(google_auth.InstalledAppFlow, "from_client_config", Mock(
        side_effect=RuntimeError("provider rejected test-secret"),
    ))
    assert google_auth.fetch_google_id_token() is None
    assert "test-secret" not in capsys.readouterr().out


def test_login_page_explains_missing_settings_before_api_request(env_file, monkeypatch):
    from app.pages import login_page

    warning = Mock()
    login = Mock()
    monkeypatch.setattr(login_page.QMessageBox, "warning", warning)
    monkeypatch.setattr(login_page.api_client, "login_with_google_id_token", login)
    login_page.LoginPage.handle_google_login(SimpleNamespace())
    warning.assert_called_once()
    assert warning.call_args.args[1] == "Google 로그인 설정 필요"
    login.assert_not_called()

