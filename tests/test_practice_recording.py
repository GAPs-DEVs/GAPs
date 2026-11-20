import ast
import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from app.api_client import APIClient
from app.practice_identity import resolve_practice_identity
from app.practice_recorder import PracticeResultCollector

SCORE = '22222222-2222-4222-8222-222222222222'
VERSION = '33333333-3333-4333-8333-333333333333'
STEP = '44444444-4444-4444-8444-444444444444'
NOTES = [{'step':1,'string':1,'fret':3,'score_step_id':STEP}]


def test_identity_resolves_selected_score_and_step_even_for_old_cache(tmp_path, monkeypatch):
    client = APIClient('https://example.invalid')
    client.set_token('test-token')
    path = client.cache_server_score(tmp_path, SCORE, 'Different song', NOTES)
    get = Mock(side_effect=[
        Mock(json=lambda:{'items':[{'score_id':SCORE,'selected_version_id':VERSION}]}),
        Mock(json=lambda:{'score_id':SCORE,'version_id':VERSION,'notes':NOTES}),
    ])
    monkeypatch.setattr(requests, 'get', get)
    assert resolve_practice_identity(client, path) == (VERSION, {1:STEP})
    assert f'/api/scores/{SCORE}/versions/{VERSION}' in get.call_args.args[0]


def test_identity_never_uses_romance_for_local_or_changed_score(tmp_path, monkeypatch):
    client = APIClient('https://example.invalid')
    path = client.cache_server_score(tmp_path, SCORE, 'Edited', [{'step':1,'string':1,'fret':9}])
    monkeypatch.setattr(requests, 'get', Mock(side_effect=[
        Mock(json=lambda:{'items':[{'score_id':SCORE,'selected_version_id':VERSION}]}),
        Mock(json=lambda:{'score_id':SCORE,'version_id':VERSION,'notes':NOTES}),
    ]))
    with pytest.raises(ValueError): resolve_practice_identity(client, path)
    with pytest.raises(ValueError): resolve_practice_identity(client, tmp_path/'local.json')


def test_identity_follows_sync_pages(tmp_path, monkeypatch):
    client = APIClient('https://example.invalid')
    path = client.cache_server_score(tmp_path, SCORE, 'Song', NOTES)
    get = Mock(side_effect=[Mock(json=lambda:{'items':[],'next_cursor':'page2'}),
        Mock(json=lambda:{'items':[{'score_id':SCORE,'selected_version_id':VERSION}]}),
        Mock(json=lambda:{'score_id':SCORE,'version_id':VERSION,'notes':NOTES})])
    monkeypatch.setattr(requests, 'get', get)
    assert resolve_practice_identity(client, path)[0] == VERSION
    assert get.call_args_list[1].kwargs['params']['cursor'] == 'page2'


# Exercise production event handlers without loading camera/model hardware.
def handlers():
    source = ast.parse(Path('app/ui/main_window.py').read_text())
    cls = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == 'MainWindow')
    names = {'_handle_audio_result','on_note_evaluated','_complete_practice','next_step','_handle_interactive_advance_timeout'}
    subset = ast.Module(body=[n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names], type_ignores=[])
    api = SimpleNamespace(save_practice_record=Mock(return_value=(True,{})))
    scope = {'time':time,'api_client':api,'QMessageBox':Mock(),
        'with_step_context': lambda r,*a:dict(r)}
    exec(compile(subset,'main_window_handlers','exec'),scope)
    obj = SimpleNamespace()
    for name in names: setattr(obj,name,scope[name].__get__(obj))
    obj.recorder = PracticeResultCollector(VERSION)
    obj.start_time = time.time()
    obj.current_index = 0
    obj.practice_steps = [SimpleNamespace(step=1)]
    obj.current_step = lambda:obj.practice_steps[obj.current_index]
    obj.current_mode = lambda:'interactive'
    obj.practice_running = True
    obj._pending_advance_index = None
    obj._recording_step_ids = {1:STEP}
    obj.retrigger_gate = SimpleNamespace(apply=lambda r:r)
    obj._render_audio_result = Mock()
    obj._schedule_interactive_advance = Mock()
    obj.auto_flow_timer = Mock()
    obj._cancel_pending_interactive_advance = Mock()
    obj._stop_audio_worker = Mock()
    obj.practice_panel = Mock()
    return obj, api


def test_real_judgments_record_once_per_transition_ignore_held_and_silence():
    obj,_ = handlers()
    for r in [{'status':'incorrect','is_correct':False}]*3 + [
        {'status':'correct','is_correct':True,'held':True},
        {'status':'no_signal','is_correct':False},
        {'status':'incorrect','is_correct':False},
        {'status':'correct','is_correct':True}]: obj._handle_audio_result(r)
    assert [r['outcome'] for r in obj.recorder.results] == ['incorrect','incorrect','correct']
    assert {r['score_step_id'] for r in obj.recorder.results} == {STEP}


def test_interactive_final_step_saves_completed_once():
    obj,api = handlers()
    obj.on_note_evaluated(STEP,True)
    obj._pending_advance_index = 0
    obj._handle_interactive_advance_timeout()
    payload = api.save_practice_record.call_args.args[0]
    assert payload['status'] == 'completed'
    assert payload['score_version_id'] == VERSION
    assert payload['results'][0]['score_step_id'] == STEP
    obj._complete_practice()
    api.save_practice_record.assert_called_once()


def test_manual_next_is_skip_not_correct():
    obj,api = handlers()
    obj.next_step()
    assert api.save_practice_record.call_args.args[0]['results'][0]['outcome'] == 'skipped'
