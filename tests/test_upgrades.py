import json
import threading
import time

import pytest

import jobs
import safety
import webui


def wait_job(job_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = jobs.status(job_id)
        if state['status'] != 'running':
            return state
        time.sleep(.01)
    pytest.fail('Background task did not finish')


@pytest.fixture(autouse=True)
def isolated_log(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, 'LOG_PATH', tmp_path / 'logs' / 'activity.jsonl')


def test_choose_keeper_and_apply_subset(tmp_path):
    root = tmp_path / 'files'
    root.mkdir()
    for name in ['a.txt', 'b.txt', 'c.txt']:
        (root / name).write_text('same content')
    preview = webui.run({'folder': str(root), 'duplicates': True, 'keepers': ['c.txt']}, False)
    group = preview['duplicate_groups'][0]
    assert group['keeper'] == 'c.txt'
    assert {f['path'] for f in group['files']} == {'a.txt', 'b.txt', 'c.txt'}
    assert all(f['size'] == 12 and f['modified'] > 0 for f in group['files'])
    chosen = next(a['id'] for a in preview['actions'] if a['src'] == 'a.txt')
    result = webui.run({'plan_id': preview['plan_id'], 'selected': [chosen]}, True)
    assert not (root / 'a.txt').exists()
    assert (root / 'b.txt').exists() and (root / 'c.txt').exists()
    assert result['summary']['duplicate_bytes'] == 12
    assert result['summary']['freed_bytes'] == 0
    safety.undo(root, result['session_id'])
    assert (root / 'a.txt').read_text() == 'same content'


def test_nondefault_keeper_can_be_sorted(tmp_path):
    for name in ['a.txt', 'b.txt']:
        (tmp_path / name).write_text('same')
    preview = webui.run({'folder': str(tmp_path), 'duplicates': True, 'sort': True, 'keepers': ['b.txt']}, False)
    result = webui.run({'plan_id': preview['plan_id']}, True)
    assert (tmp_path / 'Documents' / 'b.txt').read_text() == 'same'
    safety.undo(tmp_path, result['session_id'])
    assert (tmp_path / 'a.txt').exists() and (tmp_path / 'b.txt').exists()


@pytest.mark.parametrize('keepers', [['a.txt', 'b.txt'], ['../outside.txt'], ['gone.txt']])
def test_invalid_keepers_rejected(tmp_path, keepers):
    for name in ['a.txt', 'b.txt']:
        (tmp_path / name).write_text('same')
    with pytest.raises(ValueError):
        webui.run({'folder': str(tmp_path), 'duplicates': True, 'keepers': keepers}, False)
    assert not (tmp_path / safety.RECOVERY).exists()


@pytest.mark.parametrize('selected', [[-1], [999], [True], [0, 0], ['0'], 'all'])
def test_invalid_action_selection_rejected(tmp_path, selected):
    (tmp_path / 'a.txt').write_text('a')
    preview = webui.run({'folder': str(tmp_path), 'sort': True}, False)
    with pytest.raises(ValueError, match='Invalid selected'):
        webui.run({'plan_id': preview['plan_id'], 'selected': selected}, True)
    assert (tmp_path / 'a.txt').exists()


def test_background_cancel_records_recoverable_partial_work(tmp_path, monkeypatch):
    import shutil
    root = tmp_path / 'files'
    root.mkdir()
    for name in ['a.txt', 'b.txt']:
        (root / name).write_text(name)
    preview = webui.run({'folder': str(root), 'sort': True}, False)
    move = shutil.move
    def move_then_cancel(src, dest):
        result = move(src, dest)
        safety.CONTROL.value[0].set()
        return result
    monkeypatch.setattr(shutil, 'move', move_then_cancel)
    job = webui.dispatch('/api/start', {'kind': 'apply', 'options': {'plan_id': preview['plan_id']}})
    state = wait_job(job['job_id'])
    assert state['status'] == 'cancelled'
    result = state['result']
    assert len(result['completed_actions']) == 1
    assert (root / 'b.txt').exists()
    journal = root / safety.RECOVERY / result['session_id'] / 'journal.json'
    assert json.loads(journal.read_text())['state'] == 'cancelled'
    assert jobs.history()[0]['status'] == 'cancelled'
    assert jobs.history()[0]['session_id'] == result['session_id']
    monkeypatch.setattr(shutil, 'move', move)
    safety.undo(root, result['session_id'])
    assert (root / 'a.txt').read_text() == 'a.txt'


def test_cancel_endpoint_during_scan_and_single_job(tmp_path):
    entered = threading.Event()
    def task(report):
        entered.set()
        while True:
            safety.checkpoint('Scanning', 1, None, tmp_path)
            time.sleep(.005)
    job = jobs.start('preview', task)
    assert entered.wait(2)
    try:
        assert jobs.status(job['job_id'])['phase'] == 'Scanning'
        with pytest.raises(ValueError, match='Another task'):
            jobs.start('preview', task)
    finally:
        webui.dispatch('/api/cancel', {'job_id': job['job_id']})
    assert wait_job(job['job_id'])['status'] == 'cancelled'
    assert not (tmp_path / safety.RECOVERY).exists()


def test_failed_job_keeps_restore_session(tmp_path, monkeypatch):
    import shutil
    root = tmp_path / 'files'
    root.mkdir()
    (root / 'a.txt').write_text('a')
    preview = webui.run({'folder': str(root), 'sort': True}, False)
    def locked(*args):
        raise PermissionError('Locked by another application')
    monkeypatch.setattr(shutil, 'move', locked)
    job = webui.dispatch('/api/start', {'kind': 'apply', 'options': {'plan_id': preview['plan_id']}})
    state = wait_job(job['job_id'])
    assert state['status'] == 'failed'
    assert state['result']['session_id']
    assert 'Locked' in jobs.history()[0]['error']


def test_history_survives_in_memory_reset(tmp_path):
    job = jobs.start('preview', lambda report: {'folder': str(tmp_path)})
    assert wait_job(job['job_id'])['status'] == 'complete'
    with jobs.GUARD:
        jobs.JOBS.clear()
    assert jobs.history()[0]['folder'] == str(tmp_path)


def test_interrupted_session_is_discoverable_after_restart(tmp_path):
    jobs.record({'id': 'interrupted-job', 'kind': 'apply', 'status': 'running',
                 'started': time.time(), 'folder': str(tmp_path), 'session_id': 'a' * 32})
    entry = jobs.history()[0]
    assert entry['status'] == 'interrupted'
    assert entry['session_id'] == 'a' * 32
    assert 'server stopped' in entry['error']
