#!/usr/bin/env python3
"""Local web UI for FolderCleanup. Serves on 127.0.0.1 only.

    python3 webui.py [--port 8765] [--no-browser]
"""
import argparse
import json
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import foldercleanup as fc
import safety
import jobs

PLANS = {}
LOCK = threading.Lock()

TOKEN = secrets.token_urlsafe(24)  # required on every POST; blocks other sites driving the API


def run(opts: dict, apply: bool, report=None):
    if not isinstance(opts, dict):
        raise ValueError('Expected cleanup options.')
    if apply:
        plan_id = opts.get('plan_id')
        if not isinstance(plan_id, str) or plan_id not in PLANS:
            raise ValueError('Preview first. This preview is missing or already used.')
        root, plan, expected, expires = PLANS[plan_id]
        if time.monotonic() > expires:
            raise ValueError('Preview expired. Preview again.')
        selected = opts.get('selected', list(range(len(plan))))
        if (not isinstance(selected, list) or any(type(i) is not int or i < 0 or i >= len(plan) for i in selected)
                or len(set(selected)) != len(selected)):
            raise ValueError('Invalid selected actions. Preview again.')
        plan = [action for i, action in enumerate(plan) if i in set(selected)]
        PLANS.pop(plan_id)
        report = report if report is not None else {}
        report['folder'] = str(root)
        report['summary'] = summarize(plan, root, expected)
        actions = fc.apply_plan(plan, root, expected, report)
        report['actions'] = describe_actions(actions, root)
        return report
    root = safety.validate_root(opts.get("folder", ""))
    if report is not None:
        report['folder'] = str(root)
    if not root.is_dir():
        raise ValueError(f"not a directory: {root}")
    old = opts.get("old")
    if not (opts.get("sort") or opts.get("duplicates") or opts.get("empty_dirs") or old is not None):
        raise ValueError("choose at least one action")
    size = lambda v: fc.parse_size(v) if v else None
    try:
        ok = fc.make_filter(opts.get("include") or [], opts.get("exclude") or [], opts.get("ext") or [],
                            size(opts.get("min_size")), size(opts.get("max_size")))
    except Exception as e:
        raise ValueError(str(e))
    keepers = opts.get('keepers', [])
    if not isinstance(keepers, list) or any(not isinstance(p, str) for p in keepers):
        raise ValueError('Invalid duplicate keeper choices.')
    expected = safety.snapshot(root)
    plan = fc.build_plan(root, sort=bool(opts.get("sort")), duplicates=bool(opts.get("duplicates")),
                         empty_dirs_=bool(opts.get("empty_dirs")), old=old, ok=ok, keepers=keepers)
    if safety.snapshot(root) != expected:
        raise ValueError('Folder changed during scanning. Preview again.')
    while len(PLANS) >= 32:
        PLANS.pop(next(iter(PLANS)))
    plan_id = secrets.token_urlsafe(24)
    PLANS[plan_id] = (root, plan, expected, time.monotonic() + 1800)
    groups = {}
    for kind, src, dest in plan:
        if kind == 'delete':
            groups.setdefault(dest, [dest]).append(src)
    def details(path):
        info = expected[str(path.relative_to(root))]
        return {'path': str(path.relative_to(root)), 'size': info[3], 'modified': info[4] / 1e9}
    actions = describe_actions(plan, root)
    for index, (action, (_, src, _)) in enumerate(zip(actions, plan)):
        info = expected[str(src.relative_to(root))]
        action.update(id=index, size=info[3] if info[0] == 'file' else 0)
    return {'actions': actions, 'plan_id': plan_id, 'folder': str(root),
            'summary': summarize(plan, root, expected),
            'duplicate_groups': [{'keeper': str(keeper.relative_to(root)),
                                  'files': [details(p) for p in paths]}
                                 for keeper, paths in groups.items()]}


def summarize(plan, root, expected):
    return {'moved': sum(k == 'move' for k, _, _ in plan),
            'duplicates': sum(k == 'delete' for k, _, _ in plan),
            'folders': sum(k == 'rmdir' for k, _, _ in plan),
            'duplicate_bytes': sum(expected[str(src.relative_to(root))][3]
                                   for k, src, _ in plan if k == 'delete'), 'freed_bytes': 0}


def dispatch(path, opts):
    if path == '/api/job':
        return jobs.status(opts.get('job_id'))
    if path == '/api/cancel':
        return jobs.status(opts.get('job_id'), cancel=True)
    if path == '/api/history':
        return {'entries': jobs.history(), 'log_path': str(jobs.LOG_PATH)}
    if path == '/api/shortcuts':
        return jobs.shortcuts()
    if path == '/api/browse':
        return jobs.choose_folder()
    if path == '/api/start':
        kind = opts.get('kind')
        options = opts.get('options', {})
        if kind not in ('preview', 'apply', 'undo') or not isinstance(options, dict):
            raise ValueError('Invalid task.')
        def task(report):
            with LOCK:
                if kind == 'undo':
                    report.update(folder=options.get('folder'), session_id=options.get('session_id'))
                    safety.undo(options.get('folder', ''), options.get('session_id'))
                    return report
                return run(options, kind == 'apply', report)
        return jobs.start(kind, task, {'folder': options.get('folder'),
                                      'session_id': options.get('session_id')})
    with LOCK:
        if path == '/api/undo':
            return {'session_id': safety.undo(opts.get('folder', ''), opts.get('session_id'))}
        return run(opts, path == '/api/apply')


def describe_actions(actions, root):
    rel = lambda p: str(p.relative_to(root))
    return [{"kind": 'recover' if k == 'delete' else k, "src": rel(s),
             "detail": "" if d is None else (f"duplicate of {rel(d)}" if k == "delete" else f"→ {rel(d)}")}
            for k, s, d in actions]


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _host_ok(self):  # defeats DNS-rebinding
        return self.headers.get("Host", "").split(":")[0] in ("127.0.0.1", "localhost")

    def do_GET(self):
        if self.path != "/" or not self._host_ok():
            return self._send(404, {"error": "not found"})
        page = Path(__file__).with_name('ui.html').read_text(encoding='utf-8')
        self._send(200, page.replace("__TOKEN__", TOKEN).encode(), "text/html; charset=utf-8")

    def do_POST(self):
        if not self._host_ok() or not secrets.compare_digest(self.headers.get("X-Token", ""), TOKEN):
            return self._send(403, {"error": "forbidden"})
        if self.path not in ("/api/plan", "/api/apply", "/api/undo", '/api/start',
                             '/api/job', '/api/cancel', '/api/history', '/api/shortcuts', '/api/browse'):
            return self._send(404, {"error": "not found"})
        try:
            length = int(self.headers.get('Content-Length', 0))
            if not 0 < length <= 1048576:
                raise ValueError('Invalid request size.')
            opts = json.loads(self.rfile.read(length))
            if not isinstance(opts, dict):
                raise ValueError('Expected cleanup options.')
            result = dispatch(self.path, opts)
            self._send(200, result)
        except (ValueError, OSError) as e:
            self._send(400, {"error": str(e)})

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser(description="FolderCleanup local web UI")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Folder Cleanup UI running at {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
