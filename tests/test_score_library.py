import json
import os
from pathlib import Path
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import requests
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget, QLabel, QLineEdit, QGridLayout

from app.api_client import APIClient, api_client
from app.features.score_library import RemoveScoreThread
from app.pages.select_page import ScoreCard, SelectPage
from practice.score_steps import build_practice_steps_from_json


SCORE = "22222222-2222-4222-8222-222222222222"
OTHER = "33333333-3333-4333-8333-333333333333"
NOTES = [{"step": 1, "string": 1, "fret": 3}]


def signed_client():
    client = APIClient("https://example.invalid")
    client.set_token("mock-token")
    return client


def test_delete_204_does_not_parse_json_or_send_body(monkeypatch):
    response = Mock(status_code=204)
    response.json.side_effect = AssertionError("204 has no JSON")
    delete = Mock(return_value=response)
    monkeypatch.setattr(requests, "delete", delete)
    assert signed_client().remove_library_score(SCORE) == (True, "")
    assert delete.call_args.args == (f"https://example.invalid/api/me/scores/{SCORE}",)
    assert delete.call_args.kwargs["headers"] == {"Authorization": "Bearer mock-token"}
    assert "json" not in delete.call_args.kwargs
    assert "data" not in delete.call_args.kwargs
    response.json.assert_not_called()


@pytest.mark.parametrize("status", [401, 403, 404, 405, 503, 500])
def test_delete_failure_accepts_html_body(monkeypatch, status):
    response = Mock(status_code=status)
    monkeypatch.setattr(requests, "delete", Mock(return_value=response))
    success, message = signed_client().remove_library_score(SCORE)
    assert not success and message
    response.json.assert_not_called()


def test_delete_timeout_and_guest(monkeypatch):
    delete = Mock(side_effect=requests.Timeout())
    monkeypatch.setattr(requests, "delete", delete)
    assert not APIClient().remove_library_score(SCORE)[0]
    assert not signed_client().remove_library_score("invalid")[0]
    delete.assert_not_called()
    assert not signed_client().remove_library_score(SCORE)[0]


def test_cache_keeps_same_title_scores_and_local_original_separate(tmp_path):
    original = tmp_path / "same_parsed.json"
    original.write_text(json.dumps(NOTES))
    client = signed_client()
    first = client.cache_server_score(tmp_path, SCORE, "Same", NOTES, False)
    second = client.cache_server_score(tmp_path, OTHER, "Same", NOTES, False)
    assert first != second
    assert json.loads(Path(first).read_text()) == NOTES
    assert build_practice_steps_from_json(first)[0].step == 1
    assert original.exists()


def test_sync_preserves_existing_download_flow_and_uploaded_identity(tmp_path, monkeypatch):
    client = signed_client()
    client.cache_server_score(tmp_path, SCORE, "Before", NOTES, False)
    page = {"items": [
        {"score_id": SCORE, "title": "After"},
        {"score_id": OTHER, "title": "Default", "data": NOTES, "is_default": True},
    ]}
    get = Mock(return_value=Mock(status_code=200, json=Mock(return_value=page)))
    monkeypatch.setattr(requests, "get", get)
    assert client.sync_scores(tmp_path) == (True, 1)
    get.assert_called_once()
    meta = json.loads((tmp_path / "server" / f"{SCORE}.meta.json").read_text())
    assert meta["is_default"] is False and meta["title"] == "After"


@pytest.mark.parametrize("api_success", [True, False])
def test_worker_removes_only_confirmed_server_cache(tmp_path, monkeypatch, api_success):
    client = signed_client()
    original = tmp_path / "local_parsed.json"
    original.write_text(json.dumps(NOTES))
    path = client.cache_server_score(tmp_path, SCORE, "Private", NOTES, False)
    remove = Mock(return_value=(api_success, "" if api_success else "failed"))
    monkeypatch.setattr(api_client, "remove_library_score", remove)
    worker = RemoveScoreThread(SCORE, path, tmp_path)
    worker.run()
    assert worker.success == api_success
    assert Path(path).exists() != api_success
    assert (tmp_path / "server" / f"{SCORE}.meta.json").exists() != api_success
    assert original.exists()


def test_worker_blocks_explicit_default(tmp_path, monkeypatch):
    path = signed_client().cache_server_score(tmp_path, SCORE, "Protected", NOTES, True)
    remove = Mock()
    monkeypatch.setattr(api_client, "remove_library_score", remove)
    worker = RemoveScoreThread(SCORE, path, tmp_path)
    worker.run()
    assert not worker.success and Path(path).exists()
    remove.assert_not_called()


def test_relogin_sync_score_can_be_removed_without_default_flag(tmp_path, monkeypatch):
    application = QApplication.instance() or QApplication([])
    client = signed_client()
    path = client.cache_server_score(tmp_path, SCORE, "Private", NOTES, False)
    page = SelectPage.__new__(SelectPage)
    QWidget.__init__(page)
    page.db_mock_dir = str(tmp_path)
    page.grid_layout = QGridLayout(page)
    page.lbl_count = QLabel()
    page.remove_thread = None
    page.clear_local_scores()
    assert not Path(path).exists()
    client.logout()
    client.set_token("mock-token")
    response = {"items": [{"score_id": SCORE, "title": "Private", "data": NOTES}]}
    monkeypatch.setattr(requests, "get", Mock(return_value=Mock(
        status_code=200, json=Mock(return_value=response),
    )))
    assert client.sync_scores(tmp_path) == (True, 1)
    page.refresh_song_list()
    card = page.cards[0][1]
    assert card.score_id == SCORE
    assert card.is_default is None
    assert card.remove_button.isEnabled()
    remove = Mock(return_value=(True, ""))
    monkeypatch.setattr(api_client, "remove_library_score", remove)
    worker = RemoveScoreThread(card.score_id, card.file_path, tmp_path)
    worker.run()
    assert worker.success
    remove.assert_called_once_with(SCORE)
    assert not Path(path).exists()
    page.close()
    application.processEvents()


def test_card_button_and_cancel_confirmation(tmp_path, monkeypatch):
    application = QApplication.instance() or QApplication([])
    monkeypatch.setattr(api_client, "access_token", "mock-token")
    path = signed_client().cache_server_score(tmp_path, SCORE, "Private", NOTES, False)
    page = SelectPage.__new__(SelectPage)
    # Exercise the real slot without touching the user's actual score directory.
    QWidget.__init__(page)
    page.remove_thread = None
    page.db_mock_dir = str(tmp_path)
    confirm = Mock(return_value=QMessageBox.StandardButton.No)
    monkeypatch.setattr(QMessageBox, "question", confirm)
    remove = Mock()
    monkeypatch.setattr(api_client, "remove_library_score", remove)
    card = ScoreCard("Private", path, lambda _: None, lambda _: None, page.remove_score, SCORE, False)
    card.show()
    QTest.mouseClick(card.remove_button, Qt.MouseButton.LeftButton)
    assert confirm.call_count == 1
    assert page.remove_thread is None
    remove.assert_not_called()
    assert Path(path).exists()
    card.close()
    page.close()
    application.processEvents()


def test_confirmed_removal_refreshes_cards_and_keeps_local_score(tmp_path, monkeypatch):
    application = QApplication.instance() or QApplication([])
    monkeypatch.setattr(api_client, "access_token", "mock-token")
    local = tmp_path / "local_parsed.json"
    local.write_text(json.dumps(NOTES))
    signed_client().cache_server_score(tmp_path, SCORE, "Private", NOTES, False)
    page = SelectPage.__new__(SelectPage)
    QWidget.__init__(page)
    page.remove_thread = None
    page.db_mock_dir = str(tmp_path)
    page.grid_layout = QGridLayout(page)
    page.lbl_count = QLabel()
    page.search_input = QLineEdit()
    page.refresh_song_list()
    server_card = next(card for _, card in page.cards if card.score_id == SCORE)
    monkeypatch.setattr(QMessageBox, "question", Mock(return_value=QMessageBox.StandardButton.Yes))
    delete = Mock(return_value=Mock(status_code=204))
    monkeypatch.setattr(requests, "delete", delete)
    page.remove_score(server_card)
    assert not page.isEnabled()
    for _ in range(200):
        application.processEvents()
        if page.remove_thread is None:
            break
        QTest.qWait(5)
    assert page.remove_thread is None
    assert page.isEnabled()
    assert [card.score_id for _, card in page.cards] == [None]
    delete.assert_called_once()
    assert local.exists()
    page.close()
    application.processEvents()
