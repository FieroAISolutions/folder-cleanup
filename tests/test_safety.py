import json
from pathlib import Path

import pytest
import foldercleanup as fc
import safety
import webui


def test_cleanup_and_undo_restores_files(tmp_path):
    (tmp_path / 'a.txt').write_text('same')
    (tmp_path / 'b.txt').write_text('same')
    (tmp_path / 'empty').mkdir()
    before = safety.snapshot(tmp_path)
    preview = webui.run({'folder': str(tmp_path), 'duplicates': True,
                         'sort': True, 'empty_dirs': True}, False)
    result = webui.run({'plan_id': preview['plan_id']}, True)
    assert (tmp_path / 'Documents' / 'a.txt').read_text() == 'same'
    assert not (tmp_path / 'b.txt').exists()
    assert list((tmp_path / safety.RECOVERY).rglob('*.saved'))
    safety.undo(tmp_path, result['session_id'])
    assert (tmp_path / 'a.txt').read_text() == 'same'
    assert (tmp_path / 'b.txt').read_text() == 'same'
    assert (tmp_path / 'empty').is_dir()
    assert not (tmp_path / 'Documents').exists()
    assert set(safety.snapshot(tmp_path)) == set(before)


@pytest.mark.parametrize('change', ['add', 'edit', 'remove'])
def test_changed_preview_is_rejected(tmp_path, change):
    file = tmp_path / 'a.txt'
    file.write_text('original')
    preview = webui.run({'folder': str(tmp_path), 'sort': True}, False)
    if change == 'add':
        (tmp_path / 'b.txt').write_text('new')
    elif change == 'edit':
        file.write_text('modified')
    else:
        file.unlink()
    with pytest.raises(ValueError, match='changed since preview'):
        webui.run({'plan_id': preview['plan_id']}, True)
    assert not (tmp_path / 'Documents').exists()
    assert not (tmp_path / safety.RECOVERY).exists()


def test_apply_requires_preview_and_is_single_use(tmp_path):
    (tmp_path / 'a.txt').write_text('a')
    with pytest.raises(ValueError, match='Preview first'):
        webui.run({'folder': str(tmp_path), 'sort': True}, True)
    preview = webui.run({'folder': str(tmp_path), 'sort': True}, False)
    webui.run({'plan_id': preview['plan_id']}, True)
    with pytest.raises(ValueError, match='Preview first'):
        webui.run({'plan_id': preview['plan_id']}, True)


def test_protected_folders_are_pruned(tmp_path):
    (tmp_path / 'a.txt').write_text('same')
    for name in ['.git', 'node_modules', '.secret', safety.RECOVERY]:
        folder = tmp_path / name
        folder.mkdir()
        (folder / 'b.txt').write_text('same')
        (folder / 'empty').mkdir()
    assert fc.build_plan(tmp_path, duplicates=True, empty_dirs_=True) == []


def test_empty_path_rejected():
    with pytest.raises(ValueError, match='Choose a folder'):
        webui.run({'folder': ' ', 'sort': True}, False)


def test_expired_preview_rejected(tmp_path, monkeypatch):
    (tmp_path / 'a.txt').write_text('a')
    preview = webui.run({'folder': str(tmp_path), 'sort': True}, False)
    now = webui.time.monotonic()
    monkeypatch.setattr(webui.time, 'monotonic', lambda: now + 1801)
    with pytest.raises(ValueError, match='expired'):
        webui.run({'plan_id': preview['plan_id']}, True)
    assert (tmp_path / 'a.txt').exists()


def test_undo_refuses_changed_file(tmp_path):
    (tmp_path / 'a.txt').write_text('original')
    preview = webui.run({'folder': str(tmp_path), 'sort': True}, False)
    result = webui.run({'plan_id': preview['plan_id']}, True)
    (tmp_path / 'Documents' / 'a.txt').write_text('updated')
    with pytest.raises(ValueError, match='missing or changed'):
        safety.undo(tmp_path, result['session_id'])
    assert (tmp_path / 'Documents' / 'a.txt').read_text() == 'updated'


def test_http_preview_apply_undo(tmp_path):
    import threading
    import urllib.request
    import urllib.error
    server = webui.ThreadingHTTPServer(('127.0.0.1', 0), webui.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}'
    def call(path, data, token=webui.TOKEN):
        req = urllib.request.Request(url + path, data=json.dumps(data).encode(),
                                     headers={'Content-Type': 'application/json', 'X-Token': token})
        with urllib.request.urlopen(req) as response:
            return json.load(response)
    try:
        (tmp_path / 'a.txt').write_text('test')
        with urllib.request.urlopen(url) as response:
            assert b'Undo last cleanup' in response.read()
        with pytest.raises(urllib.error.HTTPError) as denied:
            call('/api/plan', {'folder': str(tmp_path), 'sort': True}, 'wrong')
        assert denied.value.code == 403
        preview = call('/api/plan', {'folder': str(tmp_path), 'sort': True})
        result = call('/api/apply', {'plan_id': preview['plan_id']})
        call('/api/undo', {'folder': str(tmp_path), 'session_id': result['session_id']})
        assert (tmp_path / 'a.txt').read_text() == 'test'
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_undo_refuses_overwrite(tmp_path):
    (tmp_path / 'a.txt').write_text('original')
    preview = webui.run({'folder': str(tmp_path), 'sort': True}, False)
    result = webui.run({'plan_id': preview['plan_id']}, True)
    (tmp_path / 'a.txt').write_text('new data')
    with pytest.raises(ValueError, match='occupied'):
        safety.undo(tmp_path, result['session_id'])
    assert (tmp_path / 'a.txt').read_text() == 'new data'
    assert (tmp_path / 'Documents' / 'a.txt').read_text() == 'original'


def test_interrupted_cleanup_keeps_undo_journal(tmp_path, monkeypatch):
    import shutil
    (tmp_path / 'a.txt').write_text('a')
    (tmp_path / 'b.txt').write_text('b')
    move = shutil.move
    def fail_second(src, dest):
        if Path(src).name == 'b.txt':
            raise PermissionError('locked file')
        return move(src, dest)
    preview = webui.run({'folder': str(tmp_path), 'sort': True}, False)
    monkeypatch.setattr(shutil, 'move', fail_second)
    with pytest.raises(ValueError, match='Recovery journal'):
        webui.run({'plan_id': preview['plan_id']}, True)
    journal = next((tmp_path / safety.RECOVERY).rglob('journal.json'))
    assert json.loads(journal.read_text())['state'] == 'interrupted'
    monkeypatch.setattr(shutil, 'move', move)
    safety.undo(tmp_path, journal.parent.name)
    assert (tmp_path / 'a.txt').read_text() == 'a'
    assert (tmp_path / 'b.txt').read_text() == 'b'


def test_empty_category_is_not_removed_after_sort(tmp_path):
    (tmp_path / 'Documents').mkdir()
    (tmp_path / 'a.txt').write_text('a')
    plan = fc.build_plan(tmp_path, sort=True, empty_dirs_=True)
    fc.apply_plan(plan, tmp_path)
    assert (tmp_path / 'Documents' / 'a.txt').is_file()


def test_only_previewed_empty_directories_removed(tmp_path):
    (tmp_path / 'parent' / 'empty').mkdir(parents=True)
    plan = fc.build_plan(tmp_path, empty_dirs_=True)
    fc.apply_plan(plan, tmp_path)
    assert (tmp_path / 'parent').is_dir()


def test_windows_junction_destination_rejected(tmp_path):
    import os
    import subprocess
    if os.name != 'nt':
        pytest.skip('Windows junction regression')
    root = tmp_path / 'root'
    outside = tmp_path / 'outside'
    root.mkdir()
    outside.mkdir()
    (root / 'a.txt').write_text('a')
    junction = root / 'Documents'
    subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(outside)], check=True, capture_output=True)
    try:
        with pytest.raises(ValueError, match='Protected or linked'):
            fc.build_plan(root, sort=True)
        assert list(outside.iterdir()) == []
    finally:
        junction.rmdir()
