#!/usr/bin/env python3
"""
Publish — Personal book delivery server.
Tracks which files have been delivered to the Kindle vs still queued.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from threading import Lock

from flask import (
    Flask, abort, jsonify, redirect, render_template_string,
    request, send_from_directory, session, url_for
)

app = Flask(__name__)
app.secret_key = os.environ.get("PUBLISH_SECRET_KEY") or os.urandom(32)
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True

INBOX_DIR: Path = Path(os.environ.get("PUBLISH_INBOX", "./inbox"))
API_TOKEN: str = os.environ.get("PUBLISH_TOKEN", "")
ALLOWED_EXTENSIONS = {".epub", ".mobi", ".pdf", ".fb2", ".azw3", ".lit"}
START_TIME = time.time()

# Delivery log lives alongside the inbox as .publish_log.json
# Schema: { "filename": { "delivered_at": epoch, "delivered_by": "kindle"|"web"|"api" } }
_log_lock = Lock()


def _log_path() -> Path:
    return INBOX_DIR / ".publish_log.json"


def _read_log() -> dict:
    try:
        return json.loads(_log_path().read_text())
    except Exception:
        return {}


def _write_log(log: dict):
    _log_path().write_text(json.dumps(log, indent=2))


def mark_delivered(filename: str, source: str = "api"):
    with _log_lock:
        log = _read_log()
        if filename not in log:
            log[filename] = {
                "delivered_at": int(time.time()),
                "delivered_by": source,
            }
            _write_log(log)


def mark_undelivered(filename: str):
    with _log_lock:
        log = _read_log()
        if filename in log:
            del log[filename]
            _write_log(log)


def remove_from_log(filename: str):
    with _log_lock:
        log = _read_log()
        log.pop(filename, None)
        _write_log(log)

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def check_api_auth():
    if API_TOKEN and request.headers.get("X-Publish-Token") != API_TOKEN:
        abort(401, description="Invalid or missing X-Publish-Token header")


def check_web_auth():
    if API_TOKEN and not session.get("authed"):
        return redirect(url_for("login"))


def is_allowed(filename):
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


def safe_name(filename):
    return Path(filename).name


def fmt_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def get_files():
    out = []
    log = _read_log()
    try:
        entries = sorted(INBOX_DIR.iterdir(),
                         key=lambda x: x.stat().st_mtime, reverse=True)
    except FileNotFoundError:
        return out
    for f in entries:
        if not f.is_file() or f.name.startswith("."):
            continue
        ext = f.suffix.lower().lstrip(".")
        stat = f.stat()
        dl = log.get(f.name)
        out.append({
            "name": f.name,
            "ext": ext if ext in ("epub", "pdf", "mobi", "azw3", "fb2") else "other",
            "size": fmt_size(stat.st_size),
            "date": datetime.fromtimestamp(stat.st_mtime).strftime("%b %d, %Y"),
            "ts": int(stat.st_mtime),
            "delivered": bool(dl),
            "delivered_at": datetime.fromtimestamp(dl["delivered_at"]).strftime("%b %d, %Y") if dl else None,
            "delivered_by": dl.get("delivered_by", "") if dl else None,
        })
    return out

# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


LOGIN_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Publish</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,700;1,400&family=IBM+Plex+Mono:wght@300;400&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{--ink:#1a1814;--paper:#f5f2eb;--cream:#ede9df;--rule:#d4cfc4;--accent:#c0392b;--muted:#8a8478;--mono:'IBM Plex Mono',monospace;--serif:'Playfair Display',Georgia,serif}
html,body{height:100%;background:var(--paper);color:var(--ink);font-family:var(--mono)}
body{display:flex;align-items:center;justify-content:center;background-image:repeating-linear-gradient(0deg,transparent,transparent 27px,var(--rule) 28px);background-size:100% 28px}
.card{background:var(--paper);border:2px solid var(--ink);padding:clamp(32px,5vw,56px) clamp(28px,5vw,52px);width:min(420px,92vw);position:relative;box-shadow:6px 6px 0 var(--ink)}
.card::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,var(--accent),transparent)}
.masthead{border-bottom:3px double var(--ink);padding-bottom:20px;margin-bottom:28px;text-align:center}
.title{font-family:var(--serif);font-size:clamp(32px,6vw,48px);font-weight:700;letter-spacing:-.02em;line-height:1}
.subtitle{font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:var(--muted);margin-top:6px}
label{display:block;font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);margin-bottom:8px}
input[type=password]{width:100%;background:var(--cream);border:1px solid var(--rule);border-bottom:2px solid var(--ink);color:var(--ink);font-family:var(--mono);font-size:15px;padding:11px 14px;outline:none;transition:border-color .15s}
input[type=password]:focus{border-color:var(--accent);border-bottom-color:var(--accent)}
.btn{margin-top:18px;width:100%;background:var(--ink);color:var(--paper);border:none;font-family:var(--mono);font-size:11px;letter-spacing:.14em;text-transform:uppercase;padding:14px;cursor:pointer;transition:background .15s}
.btn:hover{background:var(--accent)}
.error{color:var(--accent);font-size:11px;margin-top:12px;letter-spacing:.04em}
</style>
</head>
<body>
<div class="card">
  <div class="masthead">
    <div class="title">Publish</div>
    <div class="subtitle">Personal Book Delivery</div>
  </div>
  <form method="POST" action="/login">
    <label for="token">Access Token</label>
    <input type="password" id="token" name="token" autofocus placeholder="••••••••••••">
    <button class="btn" type="submit">Enter the Library</button>
    {% if error %}<div class="error">{{ error }}</div>{% endif %}
  </form>
</div>
</body>
</html>"""

MAIN_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Publish</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,600;0,700;1,400&family=IBM+Plex+Mono:ital,wght@0,300;0,400;1,300&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --ink:#1a1814;--paper:#f5f2eb;--cream:#ede9df;--white:#faf8f4;
  --rule:#d4cfc4;--rule2:#c8c3b7;--accent:#c0392b;
  --muted:#8a8478;--muted2:#b5b0a6;
  --epub:#2c6fad;--pdf:#b35c22;--mobi:#6b3fa0;--other:#2a7a4e;
  --delivered-bg:#f0f7f0;--delivered-border:#b8d8b8;--delivered-text:#3a7a3a;
  --queued-bg:#fdf6f0;--queued-border:#e8d0b0;--queued-text:#8a5a20;
  --mono:'IBM Plex Mono',monospace;
  --serif:'Playfair Display',Georgia,serif;
  --col-left:clamp(260px,28vw,340px);
  --header-h:52px;
}
html,body{height:100%;overflow:hidden}
body{background:var(--paper);color:var(--ink);font-family:var(--mono);font-size:13px;display:flex;flex-direction:column;
  background-image:repeating-linear-gradient(90deg,transparent,transparent calc(var(--col-left) - 1px),var(--rule) var(--col-left))}

/* Header */
header{height:var(--header-h);border-bottom:2px solid var(--ink);display:grid;grid-template-columns:var(--col-left) 1fr;flex-shrink:0;background:var(--white)}
.header-brand{border-right:1px solid var(--ink);display:flex;align-items:center;padding:0 clamp(16px,2.5vw,28px);gap:12px}
.brand-title{font-family:var(--serif);font-size:clamp(18px,2.5vw,24px);font-weight:700;letter-spacing:-.02em;line-height:1}
.brand-rule{width:1px;height:22px;background:var(--rule2)}
.brand-sub{font-size:9px;letter-spacing:.16em;text-transform:uppercase;color:var(--muted);line-height:1.4}
.header-meta{display:flex;align-items:center;justify-content:space-between;padding:0 clamp(16px,2.5vw,28px)}
.header-stats{display:flex;align-items:center;gap:clamp(12px,2vw,24px)}
.stat{display:flex;align-items:center;gap:7px;font-size:11px;color:var(--muted)}
.stat-dot{width:6px;height:6px;border-radius:50%;background:#2ecc71;flex-shrink:0;animation:blink 2.5s ease-in-out infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.3}}
.stat-val{color:var(--ink)}
.stat-sep{color:var(--rule2)}
.btn-sm{font-family:var(--mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase;padding:5px 12px;border:1px solid var(--rule2);background:none;color:var(--muted);cursor:pointer;text-decoration:none;transition:all .15s;display:inline-flex;align-items:center;gap:5px}
.btn-sm:hover{color:var(--ink);border-color:var(--ink)}

/* Layout */
.body{display:grid;grid-template-columns:var(--col-left) 1fr;flex:1;overflow:hidden}

/* Left panel */
.panel-l{border-right:1px solid var(--ink);overflow-y:auto;display:flex;flex-direction:column;background:var(--white)}
.panel-section{border-bottom:1px solid var(--rule);padding:clamp(16px,2vw,24px) clamp(16px,2.5vw,24px)}
.panel-label{font-size:9px;letter-spacing:.18em;text-transform:uppercase;color:var(--muted2);margin-bottom:14px;display:flex;align-items:center;gap:8px}
.panel-label::after{content:'';flex:1;height:1px;background:var(--rule)}

/* Drop zone */
.dropzone{border:1px dashed var(--rule2);padding:clamp(20px,3vw,32px) 16px;text-align:center;cursor:pointer;transition:all .2s;background:var(--paper)}
.dropzone:hover,.dropzone.over{border-color:var(--accent);background:rgba(192,57,43,.03)}
.dropzone.over{border-style:solid}
.dz-icon{font-size:clamp(22px,3vw,28px);opacity:.35;margin-bottom:8px}
.dz-title{font-family:var(--serif);font-size:clamp(14px,1.8vw,17px);margin-bottom:4px;font-style:italic}
.dz-sub{font-size:10px;color:var(--muted);line-height:1.7}
.dz-formats{margin-top:10px;display:flex;gap:4px;justify-content:center;flex-wrap:wrap}
.fmt{font-size:9px;padding:2px 6px;border:1px solid var(--rule2);color:var(--muted2);letter-spacing:.06em;text-transform:uppercase}
input[type=file]{display:none}
.prog-list{margin-top:10px;display:none}
.prog-list.active{display:block}
.prog-row{display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--rule);font-size:11px}
.prog-row:last-child{border:none}
.prog-name{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.prog-track{width:60px;height:2px;background:var(--rule)}
.prog-fill{height:100%;background:var(--accent);width:0%;transition:width .3s}
.prog-st{font-size:10px;width:40px;text-align:right}
.prog-st.ok{color:#27ae60}.prog-st.err{color:var(--accent)}

/* API */
.api-table{width:100%;border-collapse:collapse;font-size:11px}
.api-table tr{border-bottom:1px solid var(--rule)}
.api-table tr:last-child{border:none}
.api-table td{padding:6px 0}
.api-table td:first-child{width:42px}
.method{font-size:9px;letter-spacing:.06em;padding:2px 5px}
.m-get{background:rgba(39,174,96,.1);color:#27ae60;border:1px solid rgba(39,174,96,.2)}
.m-post{background:rgba(192,57,43,.08);color:var(--accent);border:1px solid rgba(192,57,43,.15)}
.m-del{background:rgba(192,57,43,.06);color:#888;border:1px solid var(--rule)}
.m-patch{background:rgba(44,111,173,.08);color:var(--epub);border:1px solid rgba(44,111,173,.2)}
.api-path{color:var(--ink)}
.api-desc{color:var(--muted);font-style:italic;font-size:10px}
.curl-pre{background:var(--cream);border-left:2px solid var(--ink);padding:10px 12px;font-size:10px;color:var(--muted);line-height:1.8;word-break:break-all;margin-top:10px}
.curl-pre em{color:var(--accent);font-style:normal}
.copy-curl{margin-top:6px;width:100%;background:none;border:1px solid var(--rule2);color:var(--muted);font-family:var(--mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase;padding:6px;cursor:pointer;transition:all .15s}
.copy-curl:hover{color:var(--ink);border-color:var(--ink)}
.copy-curl.copied{color:#27ae60;border-color:#27ae60}

/* Right panel */
.panel-r{display:flex;flex-direction:column;overflow:hidden}
.panel-r-head{border-bottom:1px solid var(--rule);padding:0 clamp(16px,2.5vw,28px);display:flex;align-items:center;justify-content:space-between;flex-shrink:0;background:var(--white);height:44px;gap:12px}
.filters{display:flex;gap:4px;flex-wrap:wrap}
.f-btn{font-family:var(--mono);font-size:10px;letter-spacing:.08em;text-transform:uppercase;padding:3px 9px;border:1px solid var(--rule2);background:none;color:var(--muted);cursor:pointer;transition:all .15s}
.f-btn:hover,.f-btn.on{color:var(--ink);border-color:var(--ink)}
.f-btn.on{background:var(--cream)}
.f-btn.f-queued.on{background:var(--queued-bg);border-color:var(--queued-border);color:var(--queued-text)}
.f-btn.f-delivered.on{background:var(--delivered-bg);border-color:var(--delivered-border);color:var(--delivered-text)}
.file-count{font-size:11px;color:var(--muted);white-space:nowrap;flex-shrink:0}

/* File list */
.file-list{flex:1;overflow-y:auto;padding:0}

/* Section headers inside file list */
.list-section-head{
  display:flex;align-items:center;gap:10px;
  padding:10px clamp(16px,2.5vw,28px) 6px;
  position:sticky;top:0;z-index:10;
  background:var(--paper);border-bottom:1px solid var(--rule);
}
.list-section-label{font-size:9px;letter-spacing:.16em;text-transform:uppercase;font-weight:400}
.list-section-count{font-size:9px;color:var(--muted2)}
.section-queued .list-section-label{color:var(--queued-text)}
.section-delivered .list-section-label{color:var(--delivered-text)}
.section-queued{background:var(--queued-bg) !important}
.section-delivered{background:var(--delivered-bg) !important}

/* File rows */
.file-item{
  display:grid;
  grid-template-columns:40px 1fr auto auto auto 88px;
  align-items:center;gap:clamp(8px,1.5vw,16px);
  padding:clamp(7px,1vw,11px) clamp(10px,2vw,20px) clamp(7px,1vw,11px) clamp(16px,2.5vw,28px);
  border-bottom:1px solid var(--rule);
  transition:background .12s;
}
.file-item:last-child{border-bottom:none}
.file-item:hover{background:var(--white)}
.file-item.is-delivered{opacity:.72}
.file-item.is-delivered:hover{opacity:1}

.file-badge{font-size:9px;letter-spacing:.06em;text-transform:uppercase;padding:3px 0;text-align:center;border:1px solid}
.epub{color:var(--epub);border-color:rgba(44,111,173,.25);background:rgba(44,111,173,.06)}
.pdf{color:var(--pdf);border-color:rgba(179,92,34,.25);background:rgba(179,92,34,.06)}
.mobi,.azw3{color:var(--mobi);border-color:rgba(107,63,160,.25);background:rgba(107,63,160,.06)}
.fb2,.other{color:var(--other);border-color:rgba(42,122,78,.25);background:rgba(42,122,78,.06)}

.file-name{font-size:clamp(11px,1.2vw,13px);color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.file-item.is-delivered .file-name{color:var(--muted)}
.file-size{font-size:11px;color:var(--muted);white-space:nowrap;text-align:right}
.file-date{font-size:10px;color:var(--muted2);white-space:nowrap}

/* Delivery status pill */
.status-pill{
  font-size:9px;letter-spacing:.07em;text-transform:uppercase;
  padding:2px 7px;border:1px solid;white-space:nowrap;
}
.pill-queued{color:var(--queued-text);border-color:var(--queued-border);background:var(--queued-bg)}
.pill-delivered{color:var(--delivered-text);border-color:var(--delivered-border);background:var(--delivered-bg)}

.file-acts{display:flex;gap:5px;justify-content:flex-end}
.act{font-size:10px;letter-spacing:.06em;text-transform:uppercase;text-decoration:none;padding:3px 7px;border:1px solid var(--rule2);transition:all .15s;color:var(--muted);background:none;cursor:pointer;font-family:var(--mono)}
.act.dl:hover{color:var(--epub);border-color:var(--epub)}
.act.rm:hover{color:var(--accent);border-color:var(--accent)}
.act.toggle:hover{color:var(--delivered-text);border-color:var(--delivered-border)}
.act.toggle.is-delivered:hover{color:var(--queued-text);border-color:var(--queued-border)}

.empty-state{display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;gap:14px;color:var(--muted);text-align:center;padding:40px}
.empty-icon{font-family:var(--serif);font-size:clamp(48px,8vw,72px);font-weight:700;font-style:italic;opacity:.08;line-height:1;color:var(--ink)}
.empty-title{font-family:var(--serif);font-size:clamp(16px,2vw,20px);color:var(--muted);font-style:italic}
.empty-sub{font-size:11px;color:var(--muted2)}

#toast{position:fixed;bottom:clamp(16px,2vw,24px);right:clamp(16px,2vw,24px);background:var(--ink);color:var(--paper);padding:10px 16px;font-size:11px;letter-spacing:.04em;z-index:999;transform:translateY(12px);opacity:0;transition:all .2s;pointer-events:none;border-left:3px solid var(--accent);max-width:300px}
#toast.show{transform:translateY(0);opacity:1}
#toast.good{border-left-color:#27ae60}

::-webkit-scrollbar{width:4px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--rule2)}

@media(max-width:640px){
  body{background-image:none}
  header,.body{grid-template-columns:1fr}
  .header-brand{border-right:none}
  .header-meta{display:none}
  .panel-l{display:none}
  .file-item{grid-template-columns:36px 1fr auto 72px}
  .file-size,.file-date{display:none}
}
</style>
</head>
<body>

<header>
  <div class="header-brand">
    <div class="brand-title">Publish</div>
    <div class="brand-rule"></div>
    <div class="brand-sub">Personal Book<br>Delivery System</div>
  </div>
  <div class="header-meta">
    <div class="header-stats">
      <div class="stat">
        <div class="stat-dot"></div>
        <span><span class="stat-val" id="hQueued">{{ queued_count }}</span> queued</span>
      </div>
      <span class="stat-sep">·</span>
      <div class="stat">
        <span><span class="stat-val" id="hDelivered">{{ delivered_count }}</span> delivered</span>
      </div>
    </div>
    <div style="display:flex;gap:8px;align-items:center">
      <button class="btn-sm" onclick="clearDelivered()" title="Remove all delivered files from inbox">Clear delivered</button>
      <a href="/logout" class="btn-sm">Sign out</a>
    </div>
  </div>
</header>

<div class="body">

  <!-- Left panel -->
  <div class="panel-l">
    <div class="panel-section">
      <div class="panel-label">Deliver</div>
      <div class="dropzone" id="dz">
        <input type="file" id="fi" multiple accept=".epub,.mobi,.pdf,.fb2,.azw3,.lit">
        <div class="dz-icon">📚</div>
        <div class="dz-title">Drop files here</div>
        <div class="dz-sub">or click to browse<br>Kindle pulls on next wake</div>
        <div class="dz-formats">
          <span class="fmt">epub</span><span class="fmt">mobi</span>
          <span class="fmt">pdf</span><span class="fmt">fb2</span><span class="fmt">azw3</span>
        </div>
      </div>
      <div class="prog-list" id="progList"></div>
    </div>

    <div class="panel-section">
      <div class="panel-label">API</div>
      <table class="api-table">
        <tr><td><span class="method m-get">GET</span></td><td><div class="api-path">/manifest</div><div class="api-desc">undelivered files only</div></td></tr>
        <tr><td><span class="method m-get">GET</span></td><td><div class="api-path">/download/&lt;file&gt;</div><div class="api-desc">fetch + auto-mark delivered</div></td></tr>
        <tr><td><span class="method m-post">POST</span></td><td><div class="api-path">/upload</div><div class="api-desc">deliver a file</div></td></tr>
        <tr><td><span class="method m-patch">PATCH</span></td><td><div class="api-path">/file/&lt;name&gt;</div><div class="api-desc">toggle delivered status</div></td></tr>
        <tr><td><span class="method m-del">DEL</span></td><td><div class="api-path">/file/&lt;name&gt;</div><div class="api-desc">remove from inbox</div></td></tr>
      </table>
      <div class="curl-pre" id="curlBox">curl -X POST <em>{{ base_url }}/upload</em> \<br>&nbsp;&nbsp;-H <em>"X-Publish-Token: &lt;token&gt;"</em> \<br>&nbsp;&nbsp;-F <em>"file=@book.epub"</em></div>
      <button class="copy-curl" id="copyBtn" onclick="copyCurl()">Copy curl</button>
    </div>
  </div>

  <!-- Right panel -->
  <div class="panel-r">
    <div class="panel-r-head">
      <div class="filters" id="filters">
        <button class="f-btn on"          onclick="filter(this,'all')">All</button>
        <button class="f-btn f-queued"    onclick="filter(this,'queued')">Queued</button>
        <button class="f-btn f-delivered" onclick="filter(this,'delivered')">Delivered</button>
        <button class="f-btn"             onclick="filter(this,'epub')">EPUB</button>
        <button class="f-btn"             onclick="filter(this,'pdf')">PDF</button>
        <button class="f-btn"             onclick="filter(this,'mobi')">Mobi</button>
      </div>
      <div class="file-count" id="fCount">{{ file_count }} total</div>
    </div>

    <div class="file-list" id="fileList">
    {% set queued = files | selectattr("delivered", "equalto", false) | list %}
    {% set delivered = files | selectattr("delivered", "equalto", true) | list %}

    {% if not files %}
      <div class="empty-state">
        <div class="empty-icon">P</div>
        <div class="empty-title">The queue is empty</div>
        <div class="empty-sub">Drop books on the left to get started</div>
      </div>
    {% else %}

      {% if queued %}
      <div class="list-section-head section-queued" id="head-queued">
        <span class="list-section-label">Queued for Kindle</span>
        <span class="list-section-count">{{ queued | length }}</span>
      </div>
      {% for f in queued %}
      <div class="file-item" data-ext="{{ f.ext }}" data-status="queued" data-name="{{ f.name }}">
        <span class="file-badge {{ f.ext }}">{{ f.ext }}</span>
        <span class="file-name" title="{{ f.name }}">{{ f.name }}</span>
        <span class="file-size">{{ f.size }}</span>
        <span class="file-date">{{ f.date }}</span>
        <span class="status-pill pill-queued">Queued</span>
        <div class="file-acts">
          <a href="/download/{{ f.name | urlencode }}" class="act dl" title="Download">↓</a>
          <button class="act toggle" title="Mark as delivered" onclick="toggleStatus(event,'{{ f.name | replace("'","\\'") }}',false)">✓</button>
          <button class="act rm" title="Delete" onclick="del(event,'{{ f.name | replace("'","\\'") }}')">✕</button>
        </div>
      </div>
      {% endfor %}
      {% endif %}

      {% if delivered %}
      <div class="list-section-head section-delivered" id="head-delivered">
        <span class="list-section-label">Delivered to Kindle</span>
        <span class="list-section-count">{{ delivered | length }}</span>
      </div>
      {% for f in delivered %}
      <div class="file-item is-delivered" data-ext="{{ f.ext }}" data-status="delivered" data-name="{{ f.name }}">
        <span class="file-badge {{ f.ext }}">{{ f.ext }}</span>
        <span class="file-name" title="{{ f.name }}">{{ f.name }}</span>
        <span class="file-size">{{ f.size }}</span>
        <span class="file-date">{{ f.delivered_at }}</span>
        <span class="status-pill pill-delivered">{{ f.delivered_by or 'delivered' }}</span>
        <div class="file-acts">
          <a href="/download/{{ f.name | urlencode }}" class="act dl" title="Download">↓</a>
          <button class="act toggle is-delivered" title="Mark as queued again" onclick="toggleStatus(event,'{{ f.name | replace("'","\\'") }}',true)">↩</button>
          <button class="act rm" title="Delete" onclick="del(event,'{{ f.name | replace("'","\\'") }}')">✕</button>
        </div>
      </div>
      {% endfor %}
      {% endif %}

    {% endif %}
    </div>
  </div>
</div>
<div id="toast"></div>

<script>
function toast(msg, good=false){
  const t=document.getElementById('toast');
  t.textContent=msg; t.className='show'+(good?' good':'');
  clearTimeout(t._t); t._t=setTimeout(()=>t.className='',3000);
}

// Drop zone
const dz=document.getElementById('dz'), fi=document.getElementById('fi');
dz.onclick=()=>fi.click();
dz.ondragover=e=>{e.preventDefault();dz.classList.add('over')};
dz.ondragleave=()=>dz.classList.remove('over');
dz.ondrop=e=>{e.preventDefault();dz.classList.remove('over');upload(Array.from(e.dataTransfer.files))};
fi.onchange=()=>upload(Array.from(fi.files));

async function upload(files){
  if(!files.length) return;
  const pl=document.getElementById('progList');
  pl.innerHTML=''; pl.classList.add('active');
  for(const f of files){
    const id='p'+Math.random().toString(36).slice(2);
    const row=document.createElement('div');
    row.className='prog-row';
    row.innerHTML=`<span class="prog-name">${esc(f.name)}</span><div class="prog-track"><div class="prog-fill" id="${id}f"></div></div><span class="prog-st" id="${id}s">…</span>`;
    pl.appendChild(row);
    await up1(f,id);
  }
  setTimeout(()=>{pl.classList.remove('active');pl.innerHTML=''},2800);
  setTimeout(()=>location.reload(),1200);
}

function up1(f,id){
  return new Promise(r=>{
    const fd=new FormData(); fd.append('file',f);
    const x=new XMLHttpRequest();
    x.upload.onprogress=e=>{
      if(e.lengthComputable){const b=document.getElementById(id+'f');if(b)b.style.width=Math.round(e.loaded/e.total*100)+'%'}
    };
    x.onload=()=>{
      const s=document.getElementById(id+'s');
      if(x.status===200){if(s){s.textContent='done';s.classList.add('ok')}toast(f.name+' queued',true)}
      else{if(s){s.textContent='err';s.classList.add('err')}toast(f.name+' failed')}
      r();
    };
    x.onerror=()=>{toast('Network error'); r()};
    x.open('POST','/upload'); x.send(fd);
  });
}

// Toggle delivered status
async function toggleStatus(e, name, currentlyDelivered){
  e.preventDefault();
  const r=await fetch('/file/'+encodeURIComponent(name), {
    method:'PATCH',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({delivered: !currentlyDelivered})
  });
  if(r.ok){
    toast(currentlyDelivered ? name+' moved back to queue' : name+' marked delivered', true);
    setTimeout(()=>location.reload(), 600);
  } else {
    toast('Could not update status');
  }
}

// Delete
async function del(e, name){
  e.preventDefault();
  if(!confirm('Remove "'+name+'" from inbox?')) return;
  const r=await fetch('/file/'+encodeURIComponent(name),{method:'DELETE'});
  if(r.ok){
    const row=e.target.closest('.file-item');
    const wasDelivered=row.dataset.status==='delivered';
    row.remove();
    updateCounts(wasDelivered ? 0 : -1, wasDelivered ? -1 : 0);
    toast(name+' removed');
  } else {
    toast('Could not remove');
  }
}

// Clear all delivered
async function clearDelivered(){
  const delivered=document.querySelectorAll('[data-status="delivered"]');
  if(!delivered.length){toast('No delivered files to clear');return}
  if(!confirm(`Remove ${delivered.length} delivered file(s) from inbox?`)) return;
  let removed=0;
  for(const row of delivered){
    const name=row.dataset.name;
    const r=await fetch('/file/'+encodeURIComponent(name),{method:'DELETE'});
    if(r.ok){row.remove();removed++}
  }
  updateCounts(0,-removed);
  // Hide section header if empty
  const head=document.getElementById('head-delivered');
  if(head && !document.querySelector('[data-status="delivered"]')) head.remove();
  toast(`Cleared ${removed} delivered file(s)`,true);
}

// Filter
let currentFilter='all';
function filter(btn, f){
  currentFilter=f;
  document.querySelectorAll('.f-btn').forEach(b=>b.classList.remove('on'));
  btn.classList.add('on');
  document.querySelectorAll('.file-item').forEach(row=>{
    const show = f==='all'
      || f===row.dataset.status
      || f===row.dataset.ext;
    row.style.display=show?'':'none';
  });
  // Show/hide section headers
  ['queued','delivered'].forEach(s=>{
    const head=document.getElementById('head-'+s);
    if(!head) return;
    const visible=document.querySelector(`[data-status="${s}"]:not([style*="none"])`);
    head.style.display=(f==='all'||f===s||visible)?'':'none';
  });
}

function updateCounts(qDelta, dDelta){
  const qEl=document.getElementById('hQueued');
  const dEl=document.getElementById('hDelivered');
  const fEl=document.getElementById('fCount');
  if(qEl) qEl.textContent=Math.max(0,(parseInt(qEl.textContent)||0)+qDelta);
  if(dEl) dEl.textContent=Math.max(0,(parseInt(dEl.textContent)||0)+dDelta);
  const total=(parseInt(qEl?.textContent)||0)+(parseInt(dEl?.textContent)||0);
  if(fEl) fEl.textContent=total+' total';
}

function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}

function copyCurl(){
  navigator.clipboard.writeText(document.getElementById('curlBox').innerText).then(()=>{
    const b=document.getElementById('copyBtn');
    b.textContent='Copied!'; b.classList.add('copied');
    setTimeout(()=>{b.textContent='Copy curl';b.classList.remove('copied')},2000);
  });
}
</script>
</body>
</html>"""

# ---------------------------------------------------------------------------
# Routes
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
    r = check_web_auth()
    if r:
        return r
    files = get_files()
    queued = [f for f in files if not f["delivered"]]
    delivered = [f for f in files if f["delivered"]]
    return render_template_string(
        MAIN_HTML,
        files=files,
        file_count=len(files),
        queued_count=len(queued),
        delivered_count=len(delivered),
        base_url=request.host_url.rstrip("/")
    )


@app.route("/upload", methods=["POST"])
def upload():
    wa = bool(session.get("authed"))
    aa = (not API_TOKEN) or (request.headers.get(
        "X-Publish-Token") == API_TOKEN)
    if not (wa or aa):
        abort(401)
    files = request.files.getlist("file")
    if not files:
        abort(400)
    saved, errors = [], []
    for f in files:
        name = safe_name(f.filename)
        if not name or not is_allowed(name):
            errors.append(f"{f.filename}: unsupported")
            continue
        f.save(INBOX_DIR/name)
        # Reset delivery status when a file is re-uploaded
        mark_undelivered(name)
        saved.append(name)
    if errors and not saved:
        return jsonify({"error": errors}), 400
    return jsonify({"saved": saved, "errors": errors}), 200


@app.route("/manifest")
def manifest():
    check_api_auth()
    log = _read_log()
    # Only return files not yet delivered — this is what the Kindle plugin fetches
    show_all = request.args.get("all") == "1"
    files = sorted(
        f.name for f in INBOX_DIR.iterdir()
        if f.is_file()
        and not f.name.startswith(".")
        and f.suffix.lower() in ALLOWED_EXTENSIONS
        and (show_all or f.name not in log)
    )
    return jsonify({"files": files})


@app.route("/download/<path:filename>")
def download(filename):
    wa = bool(session.get("authed"))
    aa = (not API_TOKEN) or (request.headers.get(
        "X-Publish-Token") == API_TOKEN)
    if not (wa or aa):
        abort(401)
    name = safe_name(filename)
    if not (INBOX_DIR/name).is_file():
        abort(404)
    # Determine source: Kindle plugin sends the token header but no session;
    # web browser has a session cookie.
    if request.headers.get("X-Publish-Token"):
        source = "kindle"
    elif session.get("authed"):
        source = "web"
    else:
        source = "api"
    mark_delivered(name, source)
    return send_from_directory(INBOX_DIR.resolve(), name, as_attachment=True)


@app.route("/file/<path:filename>", methods=["DELETE", "PATCH"])
def manage_file(filename):
    wa = bool(session.get("authed"))
    aa = (not API_TOKEN) or (request.headers.get(
        "X-Publish-Token") == API_TOKEN)
    if not (wa or aa):
        abort(401)
    name = safe_name(filename)
    target = INBOX_DIR/name

    if request.method == "DELETE":
        if not target.is_file():
            abort(404)
        target.unlink()
        remove_from_log(name)
        return jsonify({"deleted": name})

    if request.method == "PATCH":
        # Toggle or explicitly set delivered status
        body = request.get_json(silent=True) or {}
        if body.get("delivered") is True:
            mark_delivered(name, "manual")
        else:
            mark_undelivered(name)
        return jsonify({"name": name, "delivered": bool(body.get("delivered"))})


@app.route("/health")
def health():
    try:
        all_files = [f for f in INBOX_DIR.iterdir() if f.is_file()
                     and not f.name.startswith(".")]
        log = _read_log()
        delivered = sum(1 for f in all_files if f.name in log)
        queued = len(all_files)-delivered
    except Exception:
        queued = delivered = -1
    return jsonify({"status": "ok", "inbox": str(INBOX_DIR), "queued": queued, "delivered": delivered, "uptime_s": round(time.time()-START_TIME)})

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    global INBOX_DIR, API_TOKEN
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--inbox", default=os.environ.get("PUBLISH_INBOX", "./inbox"))
    parser.add_argument(
        "--port", default=int(os.environ.get("PUBLISH_PORT", 8765)), type=int)
    parser.add_argument(
        "--host", default=os.environ.get("PUBLISH_HOST", "127.0.0.1"))
    parser.add_argument("--token", default=os.environ.get("PUBLISH_TOKEN", ""))
    args = parser.parse_args()
    INBOX_DIR = Path(args.inbox).expanduser().resolve()
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    API_TOKEN = args.token
    if not API_TOKEN:
        print("WARNING: No token set.", file=sys.stderr)
    print(f"Publish  |  inbox: {INBOX_DIR}  |  http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
