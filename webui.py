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

PLANS = {}
LOCK = threading.Lock()

TOKEN = secrets.token_urlsafe(24)  # required on every POST; blocks other sites driving the API

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Folder Cleanup</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--fg:#1c2330;--mut:#667085;--bd:#e2e5ea;--acc:#2f6fed;--del:#d92d20;--mv:#1a7f4b}
@media(prefers-color-scheme:dark){:root{--bg:#12151b;--card:#1b2029;--fg:#e8ebf0;--mut:#98a2b3;--bd:#2c3340;--acc:#6b9bff;--del:#ff7b72;--mv:#56d391}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}
main{max-width:960px;margin:0 auto;padding:24px 16px}h1{font-size:22px;margin:0 0 16px}
.card{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:16px;margin-bottom:16px}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);margin:0 0 10px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}
label{display:block;font-size:13px;color:var(--mut)}input[type=text],input[type=number]{width:100%;padding:8px;border:1px solid var(--bd);border-radius:6px;background:var(--bg);color:var(--fg);font:inherit;margin-top:3px}
.chk{display:flex;gap:8px;align-items:center;color:var(--fg);font-size:15px;margin:4px 0}
button{font:inherit;padding:9px 18px;border-radius:6px;border:1px solid var(--bd);background:var(--card);color:var(--fg);cursor:pointer}
button.p{background:var(--acc);border-color:var(--acc);color:#fff}button.d{background:var(--del);border-color:var(--del);color:#fff}button:disabled{opacity:.5;cursor:not-allowed}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.msg{color:var(--mut)}.err{color:var(--del)}
table{width:100%;border-collapse:collapse;font-size:14px}td,th{text-align:left;padding:6px 8px;border-bottom:1px solid var(--bd);word-break:break-all}
.k{font-weight:600;white-space:nowrap}.k.move{color:var(--mv)}.k.delete,.k.rmdir{color:var(--del)}
</style></head><body><main>
<h1>Folder Cleanup</h1>
<div class="card"><h2>Folder</h2><label>Path<input type="text" id="folder" placeholder="/home/you/Downloads" autofocus></label></div>
<div class="card"><h2>Actions</h2><div class="grid">
<div><div class="chk"><input type="checkbox" id="sort"><label for="sort" style="color:inherit">Sort into category folders</label></div>
<div class="chk"><input type="checkbox" id="duplicates"><label for="duplicates" style="color:inherit">Move duplicates to recovery folder</label></div>
<div class="chk"><input type="checkbox" id="empty"><label for="empty" style="color:inherit">Remove empty folders</label></div></div>
<div><div class="chk"><input type="checkbox" id="oldOn"><label for="oldOn" style="color:inherit">Move old files to _Old</label></div>
<label>Older than (days)<input type="number" id="old" value="90" min="0"></label></div></div></div>
<div class="card"><h2>Filters (optional)</h2><div class="grid">
<label>Include patterns<input type="text" id="include" placeholder="*.pdf, report*"></label>
<label>Exclude patterns<input type="text" id="exclude" placeholder="*.tmp, keep*"></label>
<label>Extensions<input type="text" id="ext" placeholder="jpg, png"></label>
<label>Min size<input type="text" id="min" placeholder="500KB"></label>
<label>Max size<input type="text" id="max" placeholder="10MB"></label></div></div>
<div class="row" style="margin-bottom:16px"><button class="p" id="prev">Preview</button>
<button class="d" id="apply" disabled>Apply changes</button><button id="undo" disabled>Undo last cleanup</button><span id="status" class="msg"></span></div>
<div class="card" id="results" hidden><h2 id="rh">Plan</h2><table id="tbl"></table></div>
</main><script>
const $=id=>document.getElementById(id),TOKEN="__TOKEN__";
const list=v=>v.split(",").map(s=>s.trim()).filter(Boolean);
const opts=()=>({folder:$("folder").value.trim(),sort:$("sort").checked,duplicates:$("duplicates").checked,
 empty_dirs:$("empty").checked,old:$("oldOn").checked?parseInt($("old").value,10):null,
 include:list($("include").value),exclude:list($("exclude").value),ext:list($("ext").value),
 min_size:$("min").value.trim(),max_size:$("max").value.trim()});
async function call(path,body){const r=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json","X-Token":TOKEN},body:JSON.stringify(body)});
 const j=await r.json();if(!r.ok)throw new Error(j.error||r.statusText);return j}
function show(actions,title){$("results").hidden=false;$("rh").textContent=title;const t=$("tbl");t.textContent="";
 for(const a of actions){const tr=t.insertRow();const k=tr.insertCell();k.className="k "+a.kind;k.textContent=a.kind;
  tr.insertCell().textContent=a.src;tr.insertCell().textContent=a.detail}
 if(!actions.length)t.insertRow().insertCell().textContent="Nothing to do."}
function busy(b,msg,err){$("prev").disabled=b;$("apply").disabled=b||!window.planned;$("undo").disabled=b||!window.recovery;for(const el of document.querySelectorAll("input"))el.disabled=b;$("status").textContent=msg||"";$("status").className=err?"err":"msg"}
$("prev").onclick=async()=>{window.planned=false;busy(true,"Scanning…");
 try{const j=await call("/api/plan",opts());window.planId=j.plan_id;window.planned=j.actions.length>0;show(j.actions,j.actions.length+" planned action(s)");busy(false,"Review, then apply.")}
 catch(e){busy(false,e.message,true)}};
$("apply").onclick=async()=>{if(!confirm("Apply the previewed changes? Duplicates will be saved in a recovery folder."))return;
 busy(true,"Applying…");try{const j=await call("/api/apply",{plan_id:window.planId});window.planned=false;window.recovery=j;$("undo").disabled=!j.session_id;show(j.actions,j.actions.length+" action(s) applied");busy(false,"Done. Recovery session: "+(j.session_id||"none"))}
 catch(e){busy(false,e.message,true)}};
$("undo").onclick=async()=>{if(!window.recovery)return;busy(true,"Restoring…");$("undo").disabled=true;
 try{await call("/api/undo",{folder:window.recovery.folder,session_id:window.recovery.session_id});window.planned=false;window.recovery=null;show([],"Cleanup restored");busy(false,"Restored.")}
 catch(e){$("undo").disabled=false;busy(false,e.message,true)}};
for(const el of document.querySelectorAll("input"))el.addEventListener("input",()=>{window.planned=false;$("apply").disabled=true});
</script></body></html>"""


def run(opts: dict, apply: bool):
    if not isinstance(opts, dict):
        raise ValueError('Expected cleanup options.')
    if apply:
        plan_id = opts.get('plan_id')
        if not isinstance(plan_id, str) or plan_id not in PLANS:
            raise ValueError('Preview first. This preview is missing or already used.')
        root, plan, expected, expires = PLANS.pop(plan_id)
        if time.monotonic() > expires:
            raise ValueError('Preview expired. Preview again.')
        report = {'folder': str(root)}
        actions = fc.apply_plan(plan, root, expected, report)
        report['actions'] = describe_actions(actions, root)
        return report
    root = safety.validate_root(opts.get("folder", ""))
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
    expected = safety.snapshot(root)
    plan = fc.build_plan(root, sort=bool(opts.get("sort")), duplicates=bool(opts.get("duplicates")),
                         empty_dirs_=bool(opts.get("empty_dirs")), old=old, ok=ok)
    if safety.snapshot(root) != expected:
        raise ValueError('Folder changed during scanning. Preview again.')
    while len(PLANS) >= 32:
        PLANS.pop(next(iter(PLANS)))
    plan_id = secrets.token_urlsafe(24)
    PLANS[plan_id] = (root, plan, expected, time.monotonic() + 1800)
    return {'actions': describe_actions(plan, root), 'plan_id': plan_id}


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
        self._send(200, PAGE.replace("__TOKEN__", TOKEN).encode(), "text/html; charset=utf-8")

    def do_POST(self):
        if not self._host_ok() or not secrets.compare_digest(self.headers.get("X-Token", ""), TOKEN):
            return self._send(403, {"error": "forbidden"})
        if self.path not in ("/api/plan", "/api/apply", "/api/undo"):
            return self._send(404, {"error": "not found"})
        try:
            length = int(self.headers.get('Content-Length', 0))
            if not 0 < length <= 65536:
                raise ValueError('Invalid request size.')
            opts = json.loads(self.rfile.read(length))
            if not isinstance(opts, dict):
                raise ValueError('Expected cleanup options.')
            with LOCK:
                if self.path == '/api/undo':
                    result = {'session_id': safety.undo(opts.get('folder', ''), opts.get('session_id'))}
                else:
                    result = run(opts, self.path == '/api/apply')
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
