"""Background work, cooperative cancellation, and a persistent activity history."""
import json
import os
import secrets
import threading
import time
from pathlib import Path

import safety

LOG_PATH = Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local' / 'state')) / 'FolderCleanup' / 'activity.jsonl'
JOBS = {}
GUARD = threading.Lock()
WORK = threading.Lock()


def record(entry):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(entry) + '\n')


def history():
    from collections import deque
    if not LOG_PATH.exists():
        return []
    with LOG_PATH.open(encoding='utf-8') as stream:
        lines = deque(stream, maxlen=500)
    entries = []
    seen = set()
    for line in reversed(lines):
        try:
            entry = json.loads(line)
            if entry['id'] in seen:
                continue
            seen.add(entry['id'])
            if entry['status'] == 'running':
                with GUARD:
                    active = JOBS.get(entry['id'], {}).get('status') == 'running'
                if not active:
                    entry.update(status='interrupted', error='The server stopped before recording completion. Inspect or restore this cleanup before retrying.')
            entries.append(entry)
        except ValueError:
            continue  # tolerate an interrupted final write
    return entries[:100]


def start(kind, task, initial=None):
    with GUARD:
        if any(j['status'] == 'running' for j in JOBS.values()):
            raise ValueError('Another task is running. Wait or cancel it first.')
        while len(JOBS) >= 32:
            JOBS.pop(next(iter(JOBS)))
        job_id = secrets.token_urlsafe(24)
        cancel = threading.Event()
        job = {'id': job_id, 'kind': kind, 'status': 'running', 'phase': 'Starting',
               'completed': 0, 'total': None, 'path': '', 'started': time.time(),
               'result': {}, 'error': None, 'cancel': cancel}
        JOBS[job_id] = job

    def progress(phase, completed, total, path):
        with GUARD:
            job.update(phase=phase, completed=completed, total=total, path=path)

    def worker():
        report = dict(initial or {})
        log_errors = []
        def record_running(info):
            try:
                record({'id': job_id, 'kind': kind, 'status': 'running', 'started': job['started'],
                        'folder': info.get('folder'), 'session_id': info.get('session_id'),
                        'completed_actions': len(info.get('completed_actions', [])), 'error': None})
            except OSError as exc:
                log_errors.append(str(exc))
        record_running(report)
        safety.CONTROL.value = (cancel, progress, record_running)
        try:
            with WORK:
                safety.checkpoint()
                result = task(report)
            report.update(result)
            status, error = 'complete', None
        except safety.Cancelled as exc:
            status, error = 'cancelled', str(exc)
        except Exception as exc:
            status, error = 'failed', str(exc)
        finally:
            safety.CONTROL.value = None
        finished = time.time()
        entry = {'id': job_id, 'kind': kind, 'status': status, 'started': job['started'],
                 'finished': finished, 'folder': report.get('folder'),
                 'session_id': report.get('session_id'), 'error': error,
                 'completed_actions': len(report.get('completed_actions', []))}
        warning = 'Activity log warning: ' + '; '.join(log_errors) if log_errors else None
        try:
            record(entry)
        except OSError as exc:
            warning = f'Activity log could not be written: {exc}'
        with GUARD:
            job.update(status=status, error=error, result=report, finished=finished, warning=warning)

    threading.Thread(target=worker, daemon=True).start()
    return {'job_id': job_id}


def status(job_id, cancel=False):
    with GUARD:
        if not isinstance(job_id, str) or job_id not in JOBS:
            raise ValueError('Unknown job. Reload activity history for previous cleanups.')
        job = JOBS[job_id]
        if cancel and job['status'] == 'running':
            job['cancel'].set()
        return {key: value for key, value in job.items() if key != 'cancel'}


def shortcuts():
    paths = {'Downloads': Path.home() / 'Downloads', 'Desktop': Path.home() / 'Desktop'}
    if os.name == 'nt':
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                               r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders') as key:
                for label, value in [('Desktop', 'Desktop'), ('Downloads', '{374DE290-123F-4565-9164-39C4925E467B}')]:
                    try:
                        paths[label] = Path(os.path.expandvars(winreg.QueryValueEx(key, value)[0]))
                    except OSError:
                        pass
        except OSError:
            pass
    return {name: str(path) for name, path in paths.items() if path.is_dir()}


def choose_folder():
    """Tk owns its own process/main thread, separate from HTTP worker threads."""
    import subprocess
    import sys
    code = ('import tkinter as tk; from tkinter import filedialog; '
            'root=tk.Tk(); root.withdraw(); root.attributes("-topmost",True); '
            'folder=filedialog.askdirectory(parent=root,title="Choose folder to clean"); '
            'print(folder); root.destroy()')
    try:
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
                                encoding='utf-8', timeout=180,
                                env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except subprocess.TimeoutExpired as exc:
        raise ValueError('Folder picker timed out. Try again or paste a folder path.') from exc
    if result.returncode:
        raise ValueError('Folder picker is unavailable. Paste a folder path instead.')
    return {'folder': result.stdout.strip()}
