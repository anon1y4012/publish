#!/usr/bin/env python3
"""
EpubSync Server
---------------
Personal EPUB inbox with a web UI. Drop books in via browser, Calibre script,
iOS Shortcut, or API call. KOReader plugin pulls new files on Kindle wake.

Environment variables (all optional):
  EPUBSYNC_TOKEN   Shared secret for auth. Strongly recommended.
  EPUBSYNC_INBOX   Path to inbox folder (default: /inbox in Docker, ./inbox locally)
  EPUBSYNC_PORT    Port to listen on (default: 8765)
  EPUBSYNC_HOST    Bind host (default: 0.0.0.0 in Docker)

Endpoints:
  GET  /                  Web UI (login-gated if token set)
  GET  /manifest          JSON file list — used by KOReader plugin
  GET  /download/<file>   Download a file
  POST /upload            Upload file(s) via multipart form
  DELETE /file/<name>     Remove a file from inbox
  GET  /health            Status JSON
"""

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from flask import (
    Flask, abort, jsonify, redirect, render_template_string,
    request, send_from_directory, session, url_for
)

app = Flask(__name__)
app.secret_key = os.environ.get("EPUBSYNC_SECRET_KEY") or os.urandom(32)
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# ---------------------------------------------------------------------------
# Config (environment-first, then CLI args, then defaults)
# ---------------------------------------------------------------------------

INBOX_DIR: Path = Path(os.environ.get("EPUBSYNC_INBOX", "./inbox"))
API_TOKEN: str = os.environ.get("EPUBSYNC_TOKEN", "")
ALLOWED_EXTENSIONS = {".epub", ".mobi", ".pdf", ".fb2", ".azw3", ".lit"}
START_TIME = time.time()

# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def check_api_auth():
    if API_TOKEN and request.headers.get("X-EpubSync-Token") != API_TOKEN:
        abort(401, description="Invalid or missing X-EpubSync-Token header")


def check_web_auth():
    if API_TOKEN and not session.get("authed"):
        return redirect(url_for("login"))


def is_allowed(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


def safe_name(filename: str) -> str:
    """Strip path components to prevent directory traversal."""
    return Path(filename).name


def fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def get_files() -> list:
    out = []
    try:
        entries = sorted(INBOX_DIR.iterdir(),
                         key=lambda x: x.stat().st_mtime, reverse=True)
    except FileNotFoundError:
        return out
    for f in entries:
        if not f.is_file():
            continue
        ext = f.suffix.lower().lstrip(".")
        stat = f.stat()
        out.append({
            "name": f.name,
            "ext": ext if ext in ("epub", "pdf", "mobi", "azw3", "fb2") else "other",
            "size": fmt_size(stat.st_size),
            "date": datetime.fromtimestamp(stat.st_mtime).strftime("%b %d, %Y"),
        })
    return out

# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EpubSync · Sign In</title>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=DM+Mono:wght@300;400&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0f0f0e;--surface:#181817;--border:#2a2a28;--accent:#c8a96e;--accent-dim:#8a7249;--text:#e8e4dc;--muted:#6b6860;--danger:#c0574a}
body{background:var(--bg);color:var(--text);font-family:'DM Mono',monospace;min-height:100vh;display:flex;align-items:center;justify-content:center;background-image:radial-gradient(ellipse at 20% 50%,rgba(200,169,110,.04) 0%,transparent 60%)}
.card{width:360px;padding:48px 40px;border:1px solid var(--border);background:var(--surface);position:relative}
.card::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,transparent,var(--accent),transparent)}
.wordmark{font-family:'Instrument Serif',serif;font-size:26px;letter-spacing:-.02em;margin-bottom:6px}
.wordmark em{color:var(--accent);font-style:italic}
.sub{font-size:11px;color:var(--muted);letter-spacing:.08em;text-transform:uppercase;margin-bottom:36px}
label{display:block;font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-bottom:8px}
input[type=password]{width:100%;background:var(--bg);border:1px solid var(--border);color:var(--text);font-family:'DM Mono',monospace;font-size:14px;padding:12px 14px;outline:none;transition:border-color .2s}
input[type=password]:focus{border-color:var(--accent-dim)}
button{margin-top:20px;width:100%;background:var(--accent);color:var(--bg);border:none;font-family:'DM Mono',monospace;font-size:12px;letter-spacing:.1em;text-transform:uppercase;padding:13px;cursor:pointer;transition:opacity .15s}
button:hover{opacity:.85}
.error{color:var(--danger);font-size:12px;margin-top:14px}
</style>
</head>
<body>
<div class="card">
  <div class="wordmark">Epub<em>Sync</em></div>
  <div class="sub">Personal Book Inbox</div>
  <form method="POST" action="/login">
    <label for="token">Access Token</label>
    <input type="password" id="token" name="token" autofocus placeholder="••••••••••••">
    <button type="submit">Enter</button>
    {% if error %}<div class="error">{{ error }}</div>{% endif %}
  </form>
</div>
</body>
</html>"""

MAIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EpubSync</title>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=DM+Mono:ital,wght@0,300;0,400;1,300&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0f0f0e;--surface:#161615;--surface2:#1d1d1b;
  --border:#252523;--border2:#2f2f2c;
  --accent:#c8a96e;--accent-dim:rgba(200,169,110,.15);--accent-glow:rgba(200,169,110,.06);
  --text:#e8e4dc;--muted:#68655e;--muted2:#4a4844;
  --danger:#c0574a;--success:#6a9e6f;
  --epub:#7b9ed4;--pdf:#c07b5a;--mobi:#8a72c4;--other:#6b9e8a
}
html,body{height:100%}
body{background:var(--bg);color:var(--text);font-family:'DM Mono',monospace;font-size:13px;line-height:1.6;display:flex;flex-direction:column;min-height:100vh;background-image:radial-gradient(ellipse at 0% 0%,rgba(200,169,110,.03) 0%,transparent 50%),radial-gradient(ellipse at 100% 100%,rgba(100,120,180,.03) 0%,transparent 50%)}
header{border-bottom:1px solid var(--border);padding:0 32px;height:56px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;background:rgba(15,15,14,.95);backdrop-filter:blur(8px);z-index:100}
.wordmark{font-family:'Instrument Serif',serif;font-size:20px;letter-spacing:-.02em}
.wordmark em{color:var(--accent);font-style:italic}
.header-right{display:flex;align-items:center;gap:20px}
.stat-pill{font-size:11px;color:var(--muted);letter-spacing:.05em;display:flex;align-items:center;gap:6px}
.stat-pill .dot{width:6px;height:6px;border-radius:50%;background:var(--success);animation:pulse 2s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.logout-btn{font-family:'DM Mono',monospace;font-size:11px;color:var(--muted);text-decoration:none;letter-spacing:.06em;text-transform:uppercase;padding:5px 10px;border:1px solid var(--border2);transition:all .15s}
.logout-btn:hover{color:var(--text);border-color:var(--muted)}
.layout{display:grid;grid-template-columns:340px 1fr;flex:1;min-height:0}
.panel-left{border-right:1px solid var(--border);padding:28px 24px;overflow-y:auto;display:flex;flex-direction:column;gap:28px}
.section-label{font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin-bottom:14px}
.dropzone{border:1px dashed var(--border2);padding:32px 20px;text-align:center;cursor:pointer;transition:all .2s;background:var(--surface)}
.dropzone:hover,.dropzone.drag-over{border-color:var(--accent-dim);background:var(--accent-glow)}
.dropzone.drag-over{border-style:solid;border-color:var(--accent)}
.drop-icon{font-size:28px;margin-bottom:10px;opacity:.5}
.drop-title{font-family:'Instrument Serif',serif;font-size:16px;color:var(--text);margin-bottom:5px}
.drop-sub{font-size:11px;color:var(--muted);line-height:1.8}
.drop-formats{margin-top:12px;display:flex;gap:5px;justify-content:center;flex-wrap:wrap}
.fmt-tag{font-size:10px;padding:2px 7px;border:1px solid var(--border2);color:var(--muted);letter-spacing:.06em;text-transform:uppercase}
input[type=file]{display:none}
.upload-progress{display:none}
.upload-progress.active{display:block;margin-top:12px}
.prog-item{display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--border);font-size:12px}
.prog-item:last-child{border-bottom:none}
.prog-name{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.prog-bar-wrap{width:80px;height:3px;background:var(--border2)}
.prog-bar{height:100%;background:var(--accent);transition:width .3s;width:0%}
.prog-status{font-size:10px;width:50px;text-align:right}
.prog-status.ok{color:var(--success)}
.prog-status.err{color:var(--danger)}
.api-block{background:var(--surface);border:1px solid var(--border);padding:14px 16px}
.api-row{display:flex;align-items:flex-start;gap:8px;padding:4px 0;font-size:11px;line-height:1.7}
.api-method{font-size:9px;letter-spacing:.08em;padding:2px 5px;flex-shrink:0;margin-top:2px}
.get{background:rgba(106,158,111,.15);color:var(--success);border:1px solid rgba(106,158,111,.2)}
.post{background:rgba(200,169,110,.12);color:var(--accent);border:1px solid rgba(200,169,110,.2)}
.del{background:rgba(192,87,74,.12);color:var(--danger);border:1px solid rgba(192,87,74,.2)}
.api-path{color:var(--text)}
.api-desc{color:var(--muted);font-style:italic}
.curl-box{background:var(--bg);border:1px solid var(--border);padding:10px 12px;font-size:10px;color:var(--muted);word-break:break-all;line-height:1.8}
.curl-box span{color:var(--accent)}
.copy-btn{margin-top:6px;background:none;border:1px solid var(--border2);color:var(--muted);font-family:'DM Mono',monospace;font-size:10px;letter-spacing:.08em;text-transform:uppercase;padding:5px 10px;cursor:pointer;transition:all .15s;width:100%}
.copy-btn:hover{color:var(--text);border-color:var(--muted)}
.copy-btn.copied{color:var(--success);border-color:var(--success)}
.panel-right{padding:28px 32px;overflow-y:auto}
.panel-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px}
.file-count{font-size:11px;color:var(--muted)}
.filter-bar{display:flex;gap:8px;margin-bottom:20px;flex-wrap:wrap}
.filter-btn{background:none;border:1px solid var(--border2);color:var(--muted);font-family:'DM Mono',monospace;font-size:10px;letter-spacing:.08em;text-transform:uppercase;padding:5px 12px;cursor:pointer;transition:all .15s}
.filter-btn:hover,.filter-btn.active{color:var(--text);border-color:var(--text)}
.filter-btn.active{background:var(--surface2)}
.file-grid{display:flex;flex-direction:column;gap:1px}
.file-row{display:grid;grid-template-columns:36px 1fr 90px 110px 80px;align-items:center;padding:10px 12px;background:var(--surface);border:1px solid transparent;transition:all .15s;gap:12px}
.file-row:hover{border-color:var(--border2);background:var(--surface2)}
.file-type-badge{font-size:9px;letter-spacing:.06em;text-transform:uppercase;padding:3px 5px;text-align:center}
.epub{background:rgba(123,158,212,.12);color:var(--epub);border:1px solid rgba(123,158,212,.2)}
.pdf{background:rgba(192,123,90,.12);color:var(--pdf);border:1px solid rgba(192,123,90,.2)}
.mobi,.azw3{background:rgba(138,114,196,.12);color:var(--mobi);border:1px solid rgba(138,114,196,.2)}
.fb2,.other{background:rgba(107,158,138,.12);color:var(--other);border:1px solid rgba(107,158,138,.2)}
.file-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;color:var(--text)}
.file-size{font-size:11px;color:var(--muted);text-align:right}
.file-date{font-size:11px;color:var(--muted2)}
.file-actions{display:flex;gap:8px;justify-content:flex-end}
.action-link{font-size:10px;letter-spacing:.06em;text-transform:uppercase;text-decoration:none;padding:3px 8px;border:1px solid var(--border2);transition:all .15s}
.action-link.dl{color:var(--muted)}
.action-link.dl:hover{color:var(--accent);border-color:var(--accent-dim)}
.action-link.rm{color:var(--muted2)}
.action-link.rm:hover{color:var(--danger);border-color:var(--danger)}
.empty{display:flex;flex-direction:column;align-items:center;justify-content:center;padding:80px 20px;color:var(--muted);text-align:center;gap:12px}
.empty-icon{font-size:36px;opacity:.3}
.empty-title{font-family:'Instrument Serif',serif;font-size:18px;color:var(--muted)}
.empty-sub{font-size:11px;color:var(--muted2)}
#toast{position:fixed;bottom:24px;right:24px;background:var(--surface2);border:1px solid var(--border2);padding:12px 18px;font-size:12px;color:var(--text);z-index:999;transform:translateY(20px);opacity:0;transition:all .25s;pointer-events:none;max-width:320px}
#toast.show{transform:translateY(0);opacity:1}
#toast.ok::before{content:'✓  ';color:var(--success)}
#toast.err::before{content:'✗  ';color:var(--danger)}
::-webkit-scrollbar{width:4px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--border2)}
@media(max-width:768px){.layout{grid-template-columns:1fr}.panel-left{border-right:none;border-bottom:1px solid var(--border)}.file-row{grid-template-columns:36px 1fr 60px}.file-size,.file-date{display:none}}
</style>
</head>
<body>
<header>
  <div class="wordmark">Epub<em>Sync</em></div>
  <div class="header-right">
    <div class="stat-pill">
      <div class="dot"></div>
      <span id="headerCount">{{ file_count }} file{{ 's' if file_count != 1 else '' }} in inbox</span>
    </div>
    <a href="/logout" class="logout-btn">Sign out</a>
  </div>
</header>
<div class="layout">
  <div class="panel-left">
    <div>
      <div class="section-label">Drop files</div>
      <div class="dropzone" id="dropzone">
        <input type="file" id="fileInput" multiple accept=".epub,.mobi,.pdf,.fb2,.azw3,.lit">
        <div class="drop-icon">📚</div>
        <div class="drop-title">Drop books here</div>
        <div class="drop-sub">or click to browse<br>Kindle will sync on next wake</div>
        <div class="drop-formats">
          <span class="fmt-tag">epub</span><span class="fmt-tag">mobi</span>
          <span class="fmt-tag">pdf</span><span class="fmt-tag">fb2</span><span class="fmt-tag">azw3</span>
        </div>
      </div>
      <div class="upload-progress" id="uploadProgress"></div>
    </div>
    <div>
      <div class="section-label">API reference</div>
      <div class="api-block">
        <div class="api-row"><span class="api-method get">GET</span><div><div class="api-path">/manifest</div><div class="api-desc">file list for KOReader plugin</div></div></div>
        <div class="api-row"><span class="api-method get">GET</span><div><div class="api-path">/download/&lt;file&gt;</div><div class="api-desc">download a file</div></div></div>
        <div class="api-row"><span class="api-method post">POST</span><div><div class="api-path">/upload</div><div class="api-desc">upload via multipart form</div></div></div>
        <div class="api-row"><span class="api-method del">DEL</span><div><div class="api-path">/file/&lt;name&gt;</div><div class="api-desc">remove from inbox</div></div></div>
      </div>
      <div style="margin-top:16px">
        <div class="section-label" style="margin-bottom:8px">curl upload example</div>
        <div class="curl-box" id="curlExample">curl -X POST <span>{{ base_url }}/upload</span> \<br>&nbsp;&nbsp;-H <span>"X-EpubSync-Token: &lt;token&gt;"</span> \<br>&nbsp;&nbsp;-F <span>"file=@book.epub"</span></div>
        <button class="copy-btn" id="copyBtn" onclick="copyCurl()">Copy curl command</button>
      </div>
    </div>
  </div>
  <div class="panel-right">
    <div class="panel-header">
      <div class="section-label" style="margin:0">Inbox</div>
      <div class="file-count" id="fileCount">{{ file_count }} item{{ 's' if file_count != 1 else '' }}</div>
    </div>
    <div class="filter-bar">
      <button class="filter-btn active" onclick="filterFiles(this,'all')">All</button>
      <button class="filter-btn" onclick="filterFiles(this,'epub')">EPUB</button>
      <button class="filter-btn" onclick="filterFiles(this,'pdf')">PDF</button>
      <button class="filter-btn" onclick="filterFiles(this,'mobi')">Mobi</button>
      <button class="filter-btn" onclick="filterFiles(this,'azw3')">AZW3</button>
    </div>
    <div class="file-grid" id="fileGrid">
    {% if files %}
      {% for f in files %}
      <div class="file-row" data-ext="{{ f.ext }}">
        <span class="file-type-badge {{ f.ext }}">{{ f.ext }}</span>
        <span class="file-name" title="{{ f.name }}">{{ f.name }}</span>
        <span class="file-size">{{ f.size }}</span>
        <span class="file-date">{{ f.date }}</span>
        <div class="file-actions">
          <a href="/download/{{ f.name | urlencode }}" class="action-link dl" title="Download">↓</a>
          <a href="#" class="action-link rm" title="Delete" onclick="deleteFile(event,'{{ f.name | replace("'", "\\'") }}')">✕</a>
        </div>
      </div>
      {% endfor %}
    {% else %}
      <div class="empty">
        <div class="empty-icon">📭</div>
        <div class="empty-title">Inbox is empty</div>
        <div class="empty-sub">Drop some EPUBs to get started</div>
      </div>
    {% endif %}
    </div>
  </div>
</div>
<div id="toast"></div>
<script>
function showToast(msg,type='ok'){const t=document.getElementById('toast');t.textContent=msg;t.className='show '+type;clearTimeout(t._t);t._t=setTimeout(()=>t.className='',3200)}
const dz=document.getElementById('dropzone'),fi=document.getElementById('fileInput');
dz.addEventListener('click',()=>fi.click());
dz.addEventListener('dragover',e=>{e.preventDefault();dz.classList.add('drag-over')});
dz.addEventListener('dragleave',()=>dz.classList.remove('drag-over'));
dz.addEventListener('drop',e=>{e.preventDefault();dz.classList.remove('drag-over');uploadFiles(Array.from(e.dataTransfer.files))});
fi.addEventListener('change',()=>uploadFiles(Array.from(fi.files)));
async function uploadFiles(files){
  if(!files.length)return;
  const prog=document.getElementById('uploadProgress');
  prog.innerHTML='';prog.classList.add('active');
  for(const f of files){
    const id='f'+Math.random().toString(36).slice(2);
    const item=document.createElement('div');
    item.className='prog-item';
    item.innerHTML=`<span class="prog-name">${esc(f.name)}</span><div class="prog-bar-wrap"><div class="prog-bar" id="${id}b"></div></div><span class="prog-status" id="${id}s">…</span>`;
    prog.appendChild(item);
    await uploadOne(f,id);
  }
  setTimeout(()=>{prog.classList.remove('active');prog.innerHTML=''},3000);
  setTimeout(()=>location.reload(),1400);
}
function uploadOne(file,id){
  return new Promise(resolve=>{
    const fd=new FormData();fd.append('file',file);
    const xhr=new XMLHttpRequest();
    xhr.upload.onprogress=e=>{if(e.lengthComputable){const b=document.getElementById(id+'b');if(b)b.style.width=Math.round(e.loaded/e.total*100)+'%'}};
    xhr.onload=()=>{const s=document.getElementById(id+'s');if(xhr.status===200){if(s){s.textContent='done';s.classList.add('ok')}showToast(file.name+' uploaded')}else{if(s){s.textContent='failed';s.classList.add('err')}showToast(file.name+' failed','err')}resolve()};
    xhr.onerror=()=>{showToast('Network error: '+file.name,'err');resolve()};
    xhr.open('POST','/upload');xhr.send(fd);
  });
}
async function deleteFile(e,name){
  e.preventDefault();
  if(!confirm('Remove "'+name+'" from inbox?'))return;
  const r=await fetch('/file/'+encodeURIComponent(name),{method:'DELETE'});
  if(r.ok){e.target.closest('.file-row').remove();adjustCount(-1);showToast(name+' removed')}
  else showToast('Could not remove file','err');
}
function filterFiles(btn,ext){
  document.querySelectorAll('.filter-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  document.querySelectorAll('.file-row').forEach(row=>{row.style.display=(ext==='all'||row.dataset.ext===ext)?'':'none'});
}
function adjustCount(d){
  ['fileCount','headerCount'].forEach(id=>{
    const el=document.getElementById(id);if(!el)return;
    const n=(parseInt(el.textContent)||0)+d;
    el.textContent=id==='fileCount'?n+' item'+(n!==1?'s':''):n+' file'+(n!==1?'s':'')+' in inbox';
  });
}
function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
function copyCurl(){
  navigator.clipboard.writeText(document.getElementById('curlExample').innerText).then(()=>{
    const b=document.getElementById('copyBtn');b.textContent='Copied!';b.classList.add('copied');
    setTimeout(()=>{b.textContent='Copy curl command';b.classList.remove('copied')},2000);
  });
}
</script>
</body>
</html>"""

# ---------------------------------------------------------------------------
# Routes — web
# ---------------------------------------------------------------------------


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("token") == API_TOKEN:
            session["authed"] = True
            return redirect(url_for("index"))
        return render_template_string(LOGIN_HTML, error="Invalid token.")
    return render_template_string(LOGIN_HTML, error=None)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def index():
    redir = check_web_auth()
    if redir:
        return redir
    files = get_files()
    base_url = request.host_url.rstrip("/")
    return render_template_string(MAIN_HTML, files=files, file_count=len(files), base_url=base_url)

# ---------------------------------------------------------------------------
# Routes — API
# ---------------------------------------------------------------------------


@app.route("/upload", methods=["POST"])
def upload():
    web_authed = bool(session.get("authed"))
    api_authed = (not API_TOKEN) or (
        request.headers.get("X-EpubSync-Token") == API_TOKEN)
    if not (web_authed or api_authed):
        abort(401)
    files = request.files.getlist("file")
    if not files:
        abort(400, description="No file(s) provided")
    saved, errors = [], []
    for f in files:
        name = safe_name(f.filename)
        if not name or not is_allowed(name):
            errors.append(f"{f.filename}: unsupported format")
            continue
        f.save(INBOX_DIR / name)
        saved.append(name)
    if errors and not saved:
        return jsonify({"error": errors}), 400
    return jsonify({"saved": saved, "errors": errors}), 200


@app.route("/manifest")
def manifest():
    check_api_auth()
    files = sorted(
        f.name for f in INBOX_DIR.iterdir()
        if f.is_file() and f.suffix.lower() in ALLOWED_EXTENSIONS
    )
    return jsonify({"files": files})


@app.route("/download/<path:filename>")
def download(filename):
    web_authed = bool(session.get("authed"))
    api_authed = (not API_TOKEN) or (
        request.headers.get("X-EpubSync-Token") == API_TOKEN)
    if not (web_authed or api_authed):
        abort(401)
    name = safe_name(filename)
    if not (INBOX_DIR / name).is_file():
        abort(404)
    return send_from_directory(INBOX_DIR.resolve(), name, as_attachment=True)


@app.route("/file/<path:filename>", methods=["DELETE"])
def delete_file(filename):
    web_authed = bool(session.get("authed"))
    api_authed = (not API_TOKEN) or (
        request.headers.get("X-EpubSync-Token") == API_TOKEN)
    if not (web_authed or api_authed):
        abort(401)
    name = safe_name(filename)
    target = INBOX_DIR / name
    if not target.is_file():
        abort(404)
    target.unlink()
    return jsonify({"deleted": name})


@app.route("/health")
def health():
    try:
        file_count = len([f for f in INBOX_DIR.iterdir() if f.is_file()])
    except Exception:
        file_count = -1
    return jsonify({
        "status": "ok",
        "inbox": str(INBOX_DIR),
        "file_count": file_count,
        "uptime_s": round(time.time() - START_TIME),
    })

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    global INBOX_DIR, API_TOKEN

    parser = argparse.ArgumentParser(description="EpubSync server")
    parser.add_argument(
        "--inbox", default=os.environ.get("EPUBSYNC_INBOX", "./inbox"))
    parser.add_argument(
        "--port",  default=int(os.environ.get("EPUBSYNC_PORT", 8765)), type=int)
    parser.add_argument(
        "--host",  default=os.environ.get("EPUBSYNC_HOST", "127.0.0.1"))
    parser.add_argument(
        "--token", default=os.environ.get("EPUBSYNC_TOKEN", ""))
    args = parser.parse_args()

    INBOX_DIR = Path(args.inbox).expanduser().resolve()
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    API_TOKEN = args.token

    if not API_TOKEN:
        print("WARNING: No token set — unauthenticated access enabled.",
              file=sys.stderr)

    print(
        f"EpubSync  |  inbox: {INBOX_DIR}  |  http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
