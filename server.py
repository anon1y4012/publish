#!/usr/bin/env python3
"""
Publish — Personal book delivery server.
First-run setup wizard, persistent config, delivery tracking, auto-archive.
"""

import argparse
import hashlib
import json
import os
import random
import secrets
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from threading import Lock

# ---------------------------------------------------------------------------
# Word list for human-readable API keys  e.g. "swift-maple-frost"
# 200 short, unambiguous, easy-to-type words.
# ---------------------------------------------------------------------------
_WORDS = [
    "amber","anchor","apple","arrow","atlas","azure","badge","basin","birch","blade",
    "blaze","bloom","bough","brave","brook","cargo","cedar","chalk","chase","cliff",
    "cloak","cloud","cobalt","coral","crane","crest","crisp","crown","cubic","cycle",
    "delta","depot","draft","drift","dunes","eagle","ember","epoch","falls","fauna",
    "fawn","feast","fern","field","flame","flare","fleet","flint","flora","flume",
    "focus","forge","forte","frost","grain","grand","grove","guide","haven","hazel",
    "heath","hedge","helix","holly","honey","hound","inlet","ivory","kelp","lance",
    "larch","laser","latch","layer","ledge","lever","light","linen","lodge","lunar",
    "maple","marsh","merit","metro","mirth","mocha","mount","noble","nomad","north",
    "notch","novel","oaken","ocean","olive","onset","ozone","paint","patch","pearl",
    "petal","pilot","pitch","pixel","plain","plank","plaza","plume","polar","prism",
    "prose","pulse","quartz","radar","rally","ranch","rapid","raven","realm","resin",
    "ridge","rivet","robin","rocky","roost","rowan","sable","scout","shelf","shift",
    "shore","sigma","slate","solar","solid","sonic","spark","spire","spray","sprig",
    "stave","steam","steel","stern","stoke","stone","storm","stout","straw","suite",
    "surge","swale","swift","sword","talon","tawny","tenor","terra","thorn","tidal",
    "tiger","timer","torch","totem","tower","trace","track","trade","trail","trait",
    "trout","trove","tulip","tundra","ultra","unity","upper","vapor","vault","vigor",
    "viola","viper","visor","vista","vocal","voter","wader","waltz","watch","water",
]

def _gen_api_key() -> str:
    """Generate a human-readable 3-word hyphenated API key."""
    return "-".join(random.choices(_WORDS, k=3))

from flask import (
    Flask, abort, jsonify, redirect, render_template_string,
    request, send_from_directory, session, url_for
)

app = Flask(__name__)
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True

INBOX_DIR: Path    = Path(os.environ.get("PUBLISH_INBOX", "./inbox"))
ALLOWED_EXTENSIONS = {".epub", ".mobi", ".pdf", ".fb2", ".azw3", ".lit"}
START_TIME         = time.time()
_cfg_lock          = Lock()
_log_lock          = Lock()

# ---------------------------------------------------------------------------
# Config  (inbox/.publish_config.json)
# Stores password hash, api key, secret key, archive days.
# Everything the user configures lives here — not in env vars.
# ---------------------------------------------------------------------------

def _cfg_path() -> Path:
    return INBOX_DIR / ".publish_config.json"

def _read_cfg() -> dict:
    try:
        return json.loads(_cfg_path().read_text())
    except Exception:
        return {}

def _write_cfg(cfg: dict):
    with _cfg_lock:
        _cfg_path().write_text(json.dumps(cfg, indent=2))

def cfg_is_setup() -> bool:
    cfg = _read_cfg()
    return bool(cfg.get("password_hash") and cfg.get("api_key"))

def cfg_get(key: str, default=None):
    return _read_cfg().get(key, default)

def hash_password(pw: str) -> str:
    salt = "publish_v1"
    return hashlib.sha256(f"{salt}:{pw}".encode()).hexdigest()

def check_password(pw: str) -> bool:
    return hash_password(pw) == cfg_get("password_hash", "")

def check_api_key(key: str) -> bool:
    stored = cfg_get("api_key", "")
    return bool(stored) and secrets.compare_digest(key, stored)

def get_archive_days() -> int:
    return int(cfg_get("archive_days", 5))

# Bootstrap Flask secret key from config (stable across restarts)
def _get_secret_key() -> str:
    cfg = _read_cfg()
    if not cfg.get("secret_key"):
        cfg["secret_key"] = secrets.token_hex(32)
        _write_cfg(cfg)
    return cfg["secret_key"]

# ---------------------------------------------------------------------------
# Delivery log  (inbox/.publish_log.json)
# ---------------------------------------------------------------------------

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
            log[filename] = {"delivered_at": int(time.time()), "delivered_by": source, "archived_at": None}
            _write_log(log)

def mark_undelivered(filename: str):
    with _log_lock:
        log = _read_log()
        log.pop(filename, None)
        _write_log(log)

def remove_from_log(filename: str):
    with _log_lock:
        log = _read_log()
        log.pop(filename, None)
        _write_log(log)

# ---------------------------------------------------------------------------
# Auto-archive
# ---------------------------------------------------------------------------

def archive_dir() -> Path:
    d = INBOX_DIR / ".archive"
    d.mkdir(exist_ok=True)
    return d

def run_archive_pass():
    cutoff = time.time() - (get_archive_days() * 86400)
    with _log_lock:
        log = _read_log()
        changed = False
        for fname, meta in list(log.items()):
            if meta.get("archived_at"):
                continue
            if meta.get("delivered_at", 0) < cutoff:
                src = INBOX_DIR / fname
                dst = archive_dir() / fname
                if src.is_file():
                    if dst.exists():
                        dst = archive_dir() / f"{dst.stem}_{int(time.time())}{dst.suffix}"
                    shutil.move(str(src), str(dst))
                    meta["archived_at"] = int(time.time())
                    meta["archive_name"] = dst.name
                    changed = True
        if changed:
            _write_log(log)

# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def is_allowed(filename):
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS

def safe_name(filename):
    return Path(filename).name

def fmt_size(n):
    for unit in ("B","KB","MB","GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit=="B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"

def check_web_auth():
    if not session.get("authed"):
        return redirect(url_for("login"))

def check_api_auth():
    key = request.headers.get("X-Publish-Token","")
    if not check_api_key(key):
        abort(401, description="Invalid or missing X-Publish-Token header")

def get_inbox_files():
    out = []
    log = _read_log()
    try:
        entries = sorted(INBOX_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True)
    except FileNotFoundError:
        return out
    for f in entries:
        if not f.is_file() or f.name.startswith("."): continue
        ext = f.suffix.lower().lstrip(".")
        stat = f.stat()
        dl = log.get(f.name)
        out.append({
            "name": f.name,
            "ext": ext if ext in ("epub","pdf","mobi","azw3","fb2") else "other",
            "size": fmt_size(stat.st_size),
            "date": datetime.fromtimestamp(stat.st_mtime).strftime("%b %d, %Y"),
            "ts": int(stat.st_mtime),
            "delivered": bool(dl),
            "delivered_at": datetime.fromtimestamp(dl["delivered_at"]).strftime("%b %d, %Y") if dl else None,
            "delivered_by": dl.get("delivered_by","") if dl else None,
        })
    return out

def get_archive_files():
    out = []
    log = _read_log()
    archive_map = {}
    for fname, meta in log.items():
        if meta.get("archived_at") and meta.get("archive_name"):
            archive_map[meta["archive_name"]] = {**meta, "original_name": fname}
    try:
        entries = sorted(archive_dir().iterdir(), key=lambda x: x.stat().st_mtime, reverse=True)
    except FileNotFoundError:
        return out
    for f in entries:
        if not f.is_file() or f.name.startswith("."): continue
        ext = f.suffix.lower().lstrip(".")
        stat = f.stat()
        meta = archive_map.get(f.name, {})
        out.append({
            "name": f.name,
            "original_name": meta.get("original_name", f.name),
            "ext": ext if ext in ("epub","pdf","mobi","azw3","fb2") else "other",
            "size": fmt_size(stat.st_size),
            "delivered_at": datetime.fromtimestamp(meta["delivered_at"]).strftime("%b %d, %Y") if meta.get("delivered_at") else "—",
            "archived_at": datetime.fromtimestamp(meta["archived_at"]).strftime("%b %d, %Y") if meta.get("archived_at") else "—",
            "delivered_by": meta.get("delivered_by",""),
        })
    return out

# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

_FONTS = '<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,700;1,400&family=IBM+Plex+Mono:ital,wght@0,300;0,400;1,300&display=swap" rel="stylesheet">'

_BASE_CSS = """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --ink:#1a1814;--paper:#f5f2eb;--cream:#ede9df;--white:#faf8f4;
  --rule:#d4cfc4;--rule2:#c8c3b7;--accent:#c0392b;
  --muted:#8a8478;--muted2:#b5b0a6;
  --epub:#2c6fad;--pdf:#b35c22;--mobi:#6b3fa0;--other:#2a7a4e;
  --q-bg:#fdf6f0;--q-border:#e8d0b0;--q-text:#8a5a20;
  --d-bg:#f0f7f0;--d-border:#b8d8b8;--d-text:#3a7a3a;
  --a-bg:#f4f2f8;--a-border:#c8c0dc;--a-text:#5a4a80;
  --mono:'IBM Plex Mono',monospace;--serif:'Playfair Display',Georgia,serif;
}
body{background:var(--paper);color:var(--ink);font-family:var(--mono);font-size:13px}
"""

SETUP_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Publish · Setup</title>
""" + _FONTS + """
<style>
""" + _BASE_CSS + """
html,body{height:100%}
body{display:flex;align-items:center;justify-content:center;min-height:100vh;
  background-image:repeating-linear-gradient(0deg,transparent,transparent 27px,var(--rule) 28px);
  background-size:100% 28px}
.wrap{width:min(520px,94vw);padding:clamp(24px,4vw,48px) 0}
.masthead{text-align:center;margin-bottom:36px}
.title{font-family:var(--serif);font-size:clamp(28px,5vw,42px);font-weight:700;letter-spacing:-.02em;line-height:1}
.subtitle{font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:var(--muted);margin-top:6px}
.steps{display:flex;justify-content:center;gap:0;margin-bottom:32px}
.step{font-size:10px;letter-spacing:.1em;text-transform:uppercase;padding:6px 16px;border:1px solid var(--rule2);color:var(--muted2);position:relative}
.step.done{background:var(--cream);color:var(--muted);border-color:var(--rule)}
.step.active{background:var(--ink);color:var(--paper);border-color:var(--ink)}
.card{background:var(--white);border:1px solid var(--rule2);padding:clamp(24px,4vw,40px);box-shadow:4px 4px 0 var(--rule)}
.card-title{font-family:var(--serif);font-size:clamp(18px,2.5vw,22px);font-style:italic;margin-bottom:6px}
.card-sub{font-size:11px;color:var(--muted);margin-bottom:24px;line-height:1.7}
.field{margin-bottom:20px}
label{display:block;font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);margin-bottom:7px}
input[type=password],input[type=text],input[type=number]{
  width:100%;background:var(--cream);border:1px solid var(--rule);border-bottom:2px solid var(--ink);
  color:var(--ink);font-family:var(--mono);font-size:14px;padding:10px 14px;outline:none;transition:border-color .15s}
input:focus{border-bottom-color:var(--accent)}
.api-display{
  background:var(--cream);border:1px solid var(--rule);border-left:3px solid var(--ink);
  padding:12px 14px;font-size:13px;font-family:var(--mono);color:var(--ink);
  display:flex;align-items:center;justify-content:space-between;gap:12px;word-break:break-all}
.api-key-text{flex:1;line-height:1.5}
.copy-key{flex-shrink:0;background:var(--ink);color:var(--paper);border:none;font-family:var(--mono);
  font-size:10px;letter-spacing:.1em;text-transform:uppercase;padding:6px 12px;cursor:pointer;transition:background .15s}
.copy-key:hover{background:var(--accent)}
.copy-key.copied{background:#27ae60}
.hint{font-size:10px;color:var(--muted);margin-top:6px;line-height:1.7}
.hint strong{color:var(--ink)}
.btn-row{display:flex;gap:10px;margin-top:28px}
.btn{flex:1;background:var(--ink);color:var(--paper);border:none;font-family:var(--mono);
  font-size:11px;letter-spacing:.14em;text-transform:uppercase;padding:13px;cursor:pointer;transition:background .15s}
.btn:hover{background:var(--accent)}
.btn.secondary{background:none;color:var(--muted);border:1px solid var(--rule2);flex:0 0 auto;padding:13px 20px}
.btn.secondary:hover{color:var(--ink);border-color:var(--ink)}
.error{color:var(--accent);font-size:11px;margin-top:10px}
.regen{background:none;border:none;font-family:var(--mono);font-size:10px;color:var(--muted);
  cursor:pointer;text-decoration:underline;padding:0;margin-top:6px;letter-spacing:.04em}
.regen:hover{color:var(--ink)}
.success-icon{font-size:40px;text-align:center;margin-bottom:16px;opacity:.7}
.checklist{list-style:none;margin:16px 0}
.checklist li{padding:5px 0;font-size:12px;color:var(--muted);display:flex;align-items:center;gap:8px}
.checklist li::before{content:'✓';color:#27ae60;font-weight:bold}
</style>
</head>
<body>
<div class="wrap">
  <div class="masthead">
    <div class="title">Publish</div>
    <div class="subtitle">First-time setup</div>
  </div>

  <div class="steps">
    <div class="step {{ 'active' if step==1 else 'done' if step>1 else '' }}">1 · Password</div>
    <div class="step {{ 'active' if step==2 else 'done' if step>2 else '' }}">2 · API Key</div>
    <div class="step {{ 'active' if step==3 else '' }}">3 · Ready</div>
  </div>

  {% if step == 1 %}
  <div class="card">
    <div class="card-title">Set your access password</div>
    <div class="card-sub">This is used to log in to the web UI. Pick something memorable — you'll enter it on each new device or browser.</div>
    <form method="POST" action="/setup">
      <input type="hidden" name="step" value="1">
      <div class="field">
        <label for="pw">Password</label>
        <input type="password" id="pw" name="password" autofocus placeholder="Choose a password" minlength="4" required>
      </div>
      <div class="field">
        <label for="pw2">Confirm password</label>
        <input type="password" id="pw2" name="password2" placeholder="Repeat password" required>
      </div>
      {% if error %}<div class="error">{{ error }}</div>{% endif %}
      <div class="btn-row"><button class="btn" type="submit">Continue →</button></div>
    </form>
  </div>

  {% elif step == 2 %}
  <div class="card">
    <div class="card-title">Your API key</div>
    <div class="card-sub">This key authenticates external clients — your iOS Shortcut, Calibre script, and the KOReader plugin. It's three words separated by hyphens, easy to type on a Kindle keyboard. Copy it now.</div>
    <form method="POST" action="/setup">
      <input type="hidden" name="step" value="2">
      <input type="hidden" name="api_key" id="apiKeyField" value="{{ api_key }}">
      <div class="field">
        <label>API Key — <em style="color:var(--muted);font-style:normal">auto-generated</em></label>
        <div class="api-display">
          <span class="api-key-text" id="apiKeyDisplay">{{ api_key }}</span>
          <button type="button" class="copy-key" onclick="copyKey()">Copy</button>
        </div>
        <div class="hint">Use this as the <strong>X-Publish-Token</strong> header in API calls and as the token in the KOReader plugin settings. Three words separated by hyphens — easy to type on any device.</div>
        <button type="button" class="regen" onclick="regenKey()">↻ Generate a new key</button>
      </div>
      <div class="field">
        <label for="archdays">Auto-archive delivered files after (days)</label>
        <input type="number" id="archdays" name="archive_days" value="5" min="1" max="365">
        <div class="hint">Files move to the archive section automatically after this many days. You can change this later in Settings.</div>
      </div>
      {% if error %}<div class="error">{{ error }}</div>{% endif %}
      <div class="btn-row">
        <a href="/setup?step=1" class="btn secondary">← Back</a>
        <button class="btn" type="submit">Finish setup →</button>
      </div>
    </form>
  </div>

  {% elif step == 3 %}
  <div class="card">
    <div class="success-icon">📚</div>
    <div class="card-title">Publish is ready</div>
    <div class="card-sub">Setup complete. Here's a summary of what's configured:</div>
    <ul class="checklist">
      <li>Password protected web UI</li>
      <li>API key generated — update your KOReader plugin and iOS Shortcut</li>
      <li>Auto-archive set to {{ archive_days }} days after delivery</li>
    </ul>
    <div class="hint" style="margin-top:16px">Your API key: <strong style="color:var(--ink)">{{ api_key }}</strong></div>
    <div class="btn-row"><a href="/" class="btn" style="text-align:center;text-decoration:none">Open Publish →</a></div>
  </div>
  {% endif %}
</div>

<script>
function copyKey(){
  const key=document.getElementById('apiKeyDisplay').textContent.trim();
  navigator.clipboard.writeText(key).then(()=>{
    const b=document.querySelector('.copy-key');
    b.textContent='Copied!';b.classList.add('copied');
    setTimeout(()=>{b.textContent='Copy';b.classList.remove('copied')},2000);
  });
}
async function regenKey(){
  const r=await fetch('/setup/genkey');
  const d=await r.json();
  document.getElementById('apiKeyDisplay').textContent=d.key;
  document.getElementById('apiKeyField').value=d.key;
}
</script>
</body>
</html>"""

LOGIN_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Publish</title>
""" + _FONTS + """
<style>
""" + _BASE_CSS + """
html,body{height:100%}
body{display:flex;align-items:center;justify-content:center;
  background-image:repeating-linear-gradient(0deg,transparent,transparent 27px,var(--rule) 28px);
  background-size:100% 28px}
.card{background:var(--paper);border:2px solid var(--ink);padding:clamp(32px,5vw,56px) clamp(28px,5vw,52px);width:min(420px,92vw);position:relative;box-shadow:6px 6px 0 var(--ink)}
.card::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,var(--accent),transparent)}
.masthead{border-bottom:3px double var(--ink);padding-bottom:20px;margin-bottom:28px;text-align:center}
.title{font-family:var(--serif);font-size:clamp(32px,6vw,48px);font-weight:700;letter-spacing:-.02em;line-height:1}
.subtitle{font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:var(--muted);margin-top:6px}
label{display:block;font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);margin-bottom:8px}
input[type=password]{width:100%;background:var(--cream);border:1px solid var(--rule);border-bottom:2px solid var(--ink);color:var(--ink);font-family:var(--mono);font-size:15px;padding:11px 14px;outline:none;transition:border-color .15s}
input[type=password]:focus{border-bottom-color:var(--accent)}
.btn{margin-top:18px;width:100%;background:var(--ink);color:var(--paper);border:none;font-family:var(--mono);font-size:11px;letter-spacing:.14em;text-transform:uppercase;padding:14px;cursor:pointer;transition:background .15s}
.btn:hover{background:var(--accent)}
.error{color:var(--accent);font-size:11px;margin-top:12px}
</style>
</head>
<body>
<div class="card">
  <div class="masthead"><div class="title">Publish</div><div class="subtitle">Personal Book Delivery</div></div>
  <form method="POST" action="/login">
    <label for="token">Password</label>
    <input type="password" id="token" name="password" autofocus placeholder="••••••••••••">
    <button class="btn" type="submit">Enter the Library</button>
    {% if error %}<div class="error">{{ error }}</div>{% endif %}
  </form>
</div>
</body>
</html>"""

SETTINGS_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Publish · Settings</title>
""" + _FONTS + """
<style>
""" + _BASE_CSS + """
html,body{height:100%}
body{display:flex;flex-direction:column;min-height:100vh}
header{height:52px;border-bottom:2px solid var(--ink);display:flex;align-items:center;justify-content:space-between;padding:0 clamp(20px,3vw,40px);background:var(--white);flex-shrink:0}
.brand{font-family:var(--serif);font-size:20px;font-weight:700;letter-spacing:-.02em;text-decoration:none;color:var(--ink)}
.brand em{color:var(--accent);font-style:italic}
.nav{display:flex;gap:6px}
.nav a{font-family:var(--mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase;padding:5px 10px;border:1px solid var(--rule2);color:var(--muted);text-decoration:none;transition:all .15s}
.nav a:hover{color:var(--ink);border-color:var(--ink)}
.content{max-width:560px;margin:0 auto;padding:clamp(24px,4vw,48px) clamp(20px,3vw,40px);width:100%}
.page-title{font-family:var(--serif);font-size:clamp(22px,3vw,30px);font-weight:700;margin-bottom:4px}
.page-sub{font-size:11px;color:var(--muted);margin-bottom:32px}
.settings-group{border:1px solid var(--rule2);background:var(--white);margin-bottom:20px}
.settings-head{padding:14px 20px;border-bottom:1px solid var(--rule);background:var(--cream)}
.settings-head h3{font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}
.settings-row{padding:16px 20px;border-bottom:1px solid var(--rule);display:flex;align-items:flex-start;justify-content:space-between;gap:16px}
.settings-row:last-child{border:none}
.settings-row-info{flex:1}
.settings-row-label{font-size:12px;color:var(--ink);margin-bottom:2px}
.settings-row-desc{font-size:11px;color:var(--muted);line-height:1.6}
.settings-row-ctrl{flex-shrink:0;display:flex;align-items:center;gap:8px}
label{font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}
input[type=password],input[type=number]{background:var(--cream);border:1px solid var(--rule);border-bottom:2px solid var(--ink);color:var(--ink);font-family:var(--mono);font-size:13px;padding:7px 10px;outline:none;width:140px;transition:border-color .15s}
input:focus{border-bottom-color:var(--accent)}
.api-row{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.api-val{font-family:var(--mono);font-size:11px;color:var(--ink);background:var(--cream);border:1px solid var(--rule);padding:6px 10px;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.btn{background:var(--ink);color:var(--paper);border:none;font-family:var(--mono);font-size:10px;letter-spacing:.12em;text-transform:uppercase;padding:8px 14px;cursor:pointer;transition:background .15s;white-space:nowrap}
.btn:hover{background:var(--accent)}
.btn.danger{background:var(--accent)}
.btn.danger:hover{background:#a93226}
.btn.ghost{background:none;color:var(--muted);border:1px solid var(--rule2)}
.btn.ghost:hover{color:var(--ink);border-color:var(--ink)}
.btn.sm{padding:5px 10px;font-size:9px}
#toast{position:fixed;bottom:24px;right:24px;background:var(--ink);color:var(--paper);padding:10px 16px;font-size:11px;z-index:999;transform:translateY(12px);opacity:0;transition:all .2s;pointer-events:none;border-left:3px solid var(--accent);max-width:300px}
#toast.show{transform:translateY(0);opacity:1}
#toast.good{border-left-color:#27ae60}
</style>
</head>
<body>
<header>
  <a href="/" class="brand">Pub<em>lish</em></a>
  <div class="nav">
    <a href="/">← Back to inbox</a>
    <a href="/logout">Sign out</a>
  </div>
</header>

<div class="content">
  <div class="page-title">Settings</div>
  <div class="page-sub">Configure Publish. Changes take effect immediately.</div>

  <!-- Password -->
  <div class="settings-group">
    <div class="settings-head"><h3>Access</h3></div>
    <div class="settings-row">
      <div class="settings-row-info">
        <div class="settings-row-label">Password</div>
        <div class="settings-row-desc">Used to log in to the web UI.</div>
      </div>
      <div class="settings-row-ctrl">
        <input type="password" id="newPw" placeholder="New password" autocomplete="new-password">
        <button class="btn sm" onclick="changePassword()">Update</button>
      </div>
    </div>
  </div>

  <!-- API Key -->
  <div class="settings-group">
    <div class="settings-head"><h3>API Key</h3></div>
    <div class="settings-row">
      <div class="settings-row-info">
        <div class="settings-row-label">Current key</div>
        <div class="settings-row-desc">Used as <code>X-Publish-Token</code> in API calls, iOS Shortcuts, and the KOReader plugin. Regenerating invalidates the old key immediately.</div>
      </div>
      <div class="settings-row-ctrl">
        <div class="api-row">
          <span class="api-val" id="apiVal">{{ api_key }}</span>
          <button class="btn sm ghost" onclick="copyApiKey()">Copy</button>
          <button class="btn sm danger" onclick="regenApiKey()">Regen</button>
        </div>
      </div>
    </div>
  </div>

  <!-- Archive -->
  <div class="settings-group">
    <div class="settings-head"><h3>Auto-archive</h3></div>
    <div class="settings-row">
      <div class="settings-row-info">
        <div class="settings-row-label">Archive delivered files after</div>
        <div class="settings-row-desc">Files move from Delivered to Archive this many days after being downloaded. Archive is permanent — files are not deleted.</div>
      </div>
      <div class="settings-row-ctrl">
        <input type="number" id="archDays" value="{{ archive_days }}" min="1" max="365" style="width:80px">
        <span style="font-size:11px;color:var(--muted)">days</span>
        <button class="btn sm" onclick="saveArchiveDays()">Save</button>
      </div>
    </div>
  </div>

</div>
<div id="toast"></div>

<script>
function toast(msg,good=false){
  const t=document.getElementById('toast');
  t.textContent=msg;t.className='show'+(good?' good':'');
  clearTimeout(t._t);t._t=setTimeout(()=>t.className='',3000);
}

async function changePassword(){
  const pw=document.getElementById('newPw').value.trim();
  if(pw.length<4){toast('Password must be at least 4 characters');return}
  const r=await fetch('/settings/password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:pw})});
  if(r.ok){document.getElementById('newPw').value='';toast('Password updated',true)}
  else toast('Failed to update password');
}

async function copyApiKey(){
  const key=document.getElementById('apiVal').textContent.trim();
  await navigator.clipboard.writeText(key);
  toast('API key copied',true);
}

async function regenApiKey(){
  if(!confirm('Regenerate API key? The old key will stop working immediately.'))return;
  const r=await fetch('/settings/apikey',{method:'POST'});
  if(r.ok){
    const d=await r.json();
    document.getElementById('apiVal').textContent=d.api_key;
    toast('New API key generated — copy it now',true);
  } else toast('Failed to regenerate key');
}

async function saveArchiveDays(){
  const days=parseInt(document.getElementById('archDays').value);
  if(!days||days<1){toast('Enter a valid number of days');return}
  const r=await fetch('/settings/archive',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({days})});
  if(r.ok) toast(`Auto-archive set to ${days} days`,true);
  else toast('Failed to save');
}
</script>
</body>
</html>"""

MAIN_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Publish</title>
""" + _FONTS + """
<style>
""" + _BASE_CSS + """
html,body{height:100%;overflow:hidden}
body{display:flex;flex-direction:column;
  background-image:repeating-linear-gradient(90deg,transparent,transparent calc(var(--col-left) - 1px),var(--rule) var(--col-left))}
:root{--col-left:clamp(260px,28vw,340px);--header-h:52px}

header{height:var(--header-h);border-bottom:2px solid var(--ink);display:grid;grid-template-columns:var(--col-left) 1fr;flex-shrink:0;background:var(--white)}
.header-brand{border-right:1px solid var(--ink);display:flex;align-items:center;padding:0 clamp(16px,2.5vw,28px);gap:12px}
.brand-title{font-family:var(--serif);font-size:clamp(18px,2.5vw,24px);font-weight:700;letter-spacing:-.02em;line-height:1}
.brand-rule{width:1px;height:22px;background:var(--rule2)}
.brand-sub{font-size:9px;letter-spacing:.16em;text-transform:uppercase;color:var(--muted);line-height:1.4}
.header-meta{display:flex;align-items:center;justify-content:space-between;padding:0 clamp(16px,2.5vw,28px)}
.header-stats{display:flex;align-items:center;gap:clamp(8px,1.5vw,18px);flex-wrap:wrap}
.stat{display:flex;align-items:center;gap:6px;font-size:11px;color:var(--muted)}
.stat-dot{width:6px;height:6px;border-radius:50%;background:#2ecc71;flex-shrink:0;animation:blink 2.5s ease-in-out infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.3}}
.stat-val{color:var(--ink)}
.stat-sep{color:var(--rule2)}
.hdr-btns{display:flex;gap:6px}
.btn-sm{font-family:var(--mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase;padding:5px 10px;border:1px solid var(--rule2);background:none;color:var(--muted);cursor:pointer;text-decoration:none;transition:all .15s;display:inline-flex;align-items:center;white-space:nowrap}
.btn-sm:hover{color:var(--ink);border-color:var(--ink)}

.body{display:grid;grid-template-columns:var(--col-left) 1fr;flex:1;overflow:hidden}
.panel-l{border-right:1px solid var(--ink);overflow-y:auto;display:flex;flex-direction:column;background:var(--white)}
.panel-section{border-bottom:1px solid var(--rule);padding:clamp(14px,2vw,22px) clamp(16px,2.5vw,24px)}
.panel-label{font-size:9px;letter-spacing:.18em;text-transform:uppercase;color:var(--muted2);margin-bottom:12px;display:flex;align-items:center;gap:8px}
.panel-label::after{content:'';flex:1;height:1px;background:var(--rule)}

.dropzone{border:1px dashed var(--rule2);padding:clamp(18px,2.5vw,28px) 16px;text-align:center;cursor:pointer;transition:all .2s;background:var(--paper)}
.dropzone:hover,.dropzone.over{border-color:var(--accent);background:rgba(192,57,43,.03)}
.dropzone.over{border-style:solid}
.dz-icon{font-size:26px;opacity:.35;margin-bottom:8px}
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

.api-block{background:var(--paper);border:1px solid var(--rule)}
.api-row-item{display:flex;align-items:flex-start;gap:8px;padding:5px 10px;border-bottom:1px solid var(--rule);font-size:11px}
.api-row-item:last-child{border:none}
.method{font-size:9px;letter-spacing:.06em;padding:2px 4px;flex-shrink:0;margin-top:1px}
.m-get{background:rgba(39,174,96,.1);color:#27ae60;border:1px solid rgba(39,174,96,.2)}
.m-post{background:rgba(192,57,43,.08);color:var(--accent);border:1px solid rgba(192,57,43,.15)}
.m-del{background:rgba(192,57,43,.06);color:#888;border:1px solid var(--rule)}
.m-patch{background:rgba(44,111,173,.08);color:var(--epub);border:1px solid rgba(44,111,173,.2)}
.api-path{color:var(--ink);font-size:11px}
.api-desc{color:var(--muted);font-style:italic;font-size:10px}
.curl-pre{background:var(--cream);border-left:2px solid var(--ink);padding:10px 12px;font-size:10px;color:var(--muted);line-height:1.8;word-break:break-all;margin-top:10px}
.curl-pre em{color:var(--accent);font-style:normal}
.copy-curl{margin-top:6px;width:100%;background:none;border:1px solid var(--rule2);color:var(--muted);font-family:var(--mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase;padding:6px;cursor:pointer;transition:all .15s}
.copy-curl:hover{color:var(--ink);border-color:var(--ink)}
.copy-curl.copied{color:#27ae60;border-color:#27ae60}

.panel-r{display:flex;flex-direction:column;overflow:hidden}
.panel-r-head{border-bottom:1px solid var(--rule);padding:0 clamp(16px,2.5vw,28px);display:flex;align-items:center;justify-content:space-between;flex-shrink:0;background:var(--white);height:44px;gap:12px}
.filters{display:flex;gap:3px;flex-wrap:wrap}
.f-btn{font-family:var(--mono);font-size:10px;letter-spacing:.07em;text-transform:uppercase;padding:3px 8px;border:1px solid var(--rule2);background:none;color:var(--muted);cursor:pointer;transition:all .15s}
.f-btn:hover,.f-btn.on{color:var(--ink);border-color:var(--ink)}
.f-btn.on{background:var(--cream)}
.f-btn.fq.on{background:var(--q-bg);border-color:var(--q-border);color:var(--q-text)}
.f-btn.fd.on{background:var(--d-bg);border-color:var(--d-border);color:var(--d-text)}
.f-btn.fa.on{background:var(--a-bg);border-color:var(--a-border);color:var(--a-text)}
.file-count{font-size:11px;color:var(--muted);white-space:nowrap;flex-shrink:0}

.file-list{flex:1;overflow-y:auto}
.sec-head{display:flex;align-items:center;gap:10px;padding:9px clamp(16px,2.5vw,28px) 5px;position:sticky;top:0;z-index:10;border-bottom:1px solid var(--rule)}
.sec-head.sq{background:var(--q-bg)}.sec-head.sd{background:var(--d-bg)}.sec-head.sa{background:var(--a-bg)}
.sec-label{font-size:9px;letter-spacing:.15em;text-transform:uppercase}
.sq .sec-label{color:var(--q-text)}.sd .sec-label{color:var(--d-text)}.sa .sec-label{color:var(--a-text)}
.sec-count{font-size:9px;color:var(--muted2)}
.sec-action{margin-left:auto;font-family:var(--mono);font-size:9px;letter-spacing:.08em;text-transform:uppercase;background:none;border:1px solid var(--rule2);color:var(--muted2);padding:2px 7px;cursor:pointer;transition:all .15s}
.sec-action:hover{color:var(--ink);border-color:var(--ink)}

.archive-toggle{width:100%;background:none;border:none;border-top:1px solid var(--rule);padding:10px clamp(16px,2.5vw,28px);font-family:var(--mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted2);cursor:pointer;display:flex;align-items:center;gap:8px;transition:all .15s}
.archive-toggle:hover{color:var(--ink);background:var(--a-bg)}
.archive-toggle .chev{transition:transform .2s;font-style:normal}
.archive-toggle.open .chev{transform:rotate(90deg)}

.file-item{display:grid;grid-template-columns:40px 1fr auto auto auto 88px;align-items:center;gap:clamp(8px,1.5vw,14px);padding:clamp(7px,1vw,10px) clamp(10px,2vw,20px) clamp(7px,1vw,10px) clamp(16px,2.5vw,28px);border-bottom:1px solid var(--rule);transition:background .12s}
.file-item:last-child{border-bottom:none}
.file-item:hover{background:var(--white)}
.file-item.is-delivered{opacity:.7}.file-item.is-delivered:hover{opacity:1}
.file-item.is-archived{opacity:.55}.file-item.is-archived:hover{opacity:.9}
.file-badge{font-size:9px;letter-spacing:.06em;text-transform:uppercase;padding:3px 0;text-align:center;border:1px solid}
.epub{color:var(--epub);border-color:rgba(44,111,173,.25);background:rgba(44,111,173,.06)}
.pdf{color:var(--pdf);border-color:rgba(179,92,34,.25);background:rgba(179,92,34,.06)}
.mobi,.azw3{color:var(--mobi);border-color:rgba(107,63,160,.25);background:rgba(107,63,160,.06)}
.fb2,.other{color:var(--other);border-color:rgba(42,122,78,.25);background:rgba(42,122,78,.06)}
.file-name{font-size:clamp(11px,1.2vw,13px);color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.is-delivered .file-name,.is-archived .file-name{color:var(--muted)}
.file-size{font-size:11px;color:var(--muted);white-space:nowrap;text-align:right}
.file-date{font-size:10px;color:var(--muted2);white-space:nowrap}
.pill{font-size:9px;letter-spacing:.07em;text-transform:uppercase;padding:2px 7px;border:1px solid;white-space:nowrap}
.pill-q{color:var(--q-text);border-color:var(--q-border);background:var(--q-bg)}
.pill-d{color:var(--d-text);border-color:var(--d-border);background:var(--d-bg)}
.pill-a{color:var(--a-text);border-color:var(--a-border);background:var(--a-bg)}
.file-acts{display:flex;gap:4px;justify-content:flex-end}
.act{font-size:10px;letter-spacing:.05em;text-transform:uppercase;text-decoration:none;padding:3px 6px;border:1px solid var(--rule2);transition:all .15s;color:var(--muted);background:none;cursor:pointer;font-family:var(--mono)}
.act.dl:hover{color:var(--epub);border-color:var(--epub)}
.act.rm:hover{color:var(--accent);border-color:var(--accent)}
.act.tog:hover{color:var(--d-text);border-color:var(--d-border)}
.act.tog.on:hover{color:var(--q-text);border-color:var(--q-border)}
.act.restore:hover{color:var(--q-text);border-color:var(--q-border)}

.empty-state{display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;gap:14px;color:var(--muted);text-align:center;padding:40px}
.empty-icon{font-family:var(--serif);font-size:clamp(48px,8vw,72px);font-weight:700;font-style:italic;opacity:.08;line-height:1;color:var(--ink)}
.empty-title{font-family:var(--serif);font-size:clamp(16px,2vw,20px);color:var(--muted);font-style:italic}
.empty-sub{font-size:11px;color:var(--muted2)}

#toast{position:fixed;bottom:clamp(16px,2vw,24px);right:clamp(16px,2vw,24px);background:var(--ink);color:var(--paper);padding:10px 16px;font-size:11px;z-index:999;transform:translateY(12px);opacity:0;transition:all .2s;pointer-events:none;border-left:3px solid var(--accent);max-width:300px}
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
      <div class="stat"><div class="stat-dot"></div><span><span class="stat-val" id="hQ">{{ queued_count }}</span> queued</span></div>
      <span class="stat-sep">·</span>
      <div class="stat"><span><span class="stat-val" id="hD">{{ delivered_count }}</span> delivered</span></div>
      <span class="stat-sep">·</span>
      <div class="stat"><span><span class="stat-val" id="hA">{{ archive_count }}</span> archived</span></div>
    </div>
    <div class="hdr-btns">
      <a href="/settings" class="btn-sm">Settings</a>
      <button class="btn-sm" onclick="clearDelivered()">Clear delivered</button>
      <a href="/logout" class="btn-sm">Sign out</a>
    </div>
  </div>
</header>

<div class="body">
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
      <div class="api-block">
        <div class="api-row-item"><span class="method m-get">GET</span><div><div class="api-path">/manifest</div><div class="api-desc">undelivered files</div></div></div>
        <div class="api-row-item"><span class="method m-get">GET</span><div><div class="api-path">/download/&lt;f&gt;</div><div class="api-desc">fetch + mark delivered</div></div></div>
        <div class="api-row-item"><span class="method m-post">POST</span><div><div class="api-path">/upload</div><div class="api-desc">add to queue</div></div></div>
        <div class="api-row-item"><span class="method m-patch">PATCH</span><div><div class="api-path">/file/&lt;f&gt;</div><div class="api-desc">toggle delivered</div></div></div>
        <div class="api-row-item"><span class="method m-del">DEL</span><div><div class="api-path">/file/&lt;f&gt;</div><div class="api-desc">delete from inbox</div></div></div>
      </div>
      <div class="curl-pre" id="curlBox">curl -X POST <em>{{ base_url }}/upload</em> \<br>&nbsp;&nbsp;-H <em>"X-Publish-Token: &lt;your-api-key&gt;"</em> \<br>&nbsp;&nbsp;-F <em>"file=@book.epub"</em></div>
      <button class="copy-curl" id="copyBtn" onclick="copyCurl()">Copy curl</button>
    </div>
  </div>

  <div class="panel-r">
    <div class="panel-r-head">
      <div class="filters">
        <button class="f-btn on" onclick="filter(this,'all')">All</button>
        <button class="f-btn fq"  onclick="filter(this,'queued')">Queued</button>
        <button class="f-btn fd"  onclick="filter(this,'delivered')">Delivered</button>
        <button class="f-btn fa"  onclick="filter(this,'archived')">Archive</button>
        <button class="f-btn"     onclick="filter(this,'epub')">EPUB</button>
        <button class="f-btn"     onclick="filter(this,'pdf')">PDF</button>
        <button class="f-btn"     onclick="filter(this,'mobi')">Mobi</button>
      </div>
      <div class="file-count" id="fCount">{{ file_count }} total</div>
    </div>

    <div class="file-list" id="fileList">
    {% set queued    = files|selectattr("delivered","equalto",false)|list %}
    {% set delivered = files|selectattr("delivered","equalto",true)|list %}

    {% if not files and not archive %}
      <div class="empty-state">
        <div class="empty-icon">P</div>
        <div class="empty-title">The queue is empty</div>
        <div class="empty-sub">Drop books on the left to get started</div>
      </div>
    {% else %}
      {% if queued %}
      <div class="sec-head sq" id="head-queued">
        <span class="sec-label">Queued for Kindle</span>
        <span class="sec-count" id="cnt-queued">{{ queued|length }}</span>
      </div>
      {% for f in queued %}
      <div class="file-item" data-ext="{{ f.ext }}" data-status="queued" data-name="{{ f.name }}">
        <span class="file-badge {{ f.ext }}">{{ f.ext }}</span>
        <span class="file-name" title="{{ f.name }}">{{ f.name }}</span>
        <span class="file-size">{{ f.size }}</span>
        <span class="file-date">{{ f.date }}</span>
        <span class="pill pill-q">Queued</span>
        <div class="file-acts">
          <a href="/download/{{ f.name|urlencode }}" class="act dl">↓</a>
          <button class="act tog"   onclick="toggleStatus(event,'{{ f.name|replace("'","\\'") }}',false)">✓</button>
          <button class="act rm"    onclick="delFile(event,'{{ f.name|replace("'","\\'") }}')">✕</button>
        </div>
      </div>
      {% endfor %}
      {% endif %}

      {% if delivered %}
      <div class="sec-head sd" id="head-delivered">
        <span class="sec-label">Delivered to Kindle</span>
        <span class="sec-count" id="cnt-delivered">{{ delivered|length }}</span>
        <button class="sec-action" onclick="clearDelivered()">Clear all</button>
      </div>
      {% for f in delivered %}
      <div class="file-item is-delivered" data-ext="{{ f.ext }}" data-status="delivered" data-name="{{ f.name }}">
        <span class="file-badge {{ f.ext }}">{{ f.ext }}</span>
        <span class="file-name" title="{{ f.name }}">{{ f.name }}</span>
        <span class="file-size">{{ f.size }}</span>
        <span class="file-date">{{ f.delivered_at }}</span>
        <span class="pill pill-d">{{ f.delivered_by or "delivered" }}</span>
        <div class="file-acts">
          <a href="/download/{{ f.name|urlencode }}" class="act dl">↓</a>
          <button class="act tog on" onclick="toggleStatus(event,'{{ f.name|replace("'","\\'") }}',true)">↩</button>
          <button class="act rm"     onclick="delFile(event,'{{ f.name|replace("'","\\'") }}')">✕</button>
        </div>
      </div>
      {% endfor %}
      {% endif %}

      {% if archive %}
      <button class="archive-toggle" id="archiveToggle" onclick="toggleArchive()">
        <em class="chev">▶</em>
        <span>Archive</span>
        <span style="color:var(--muted2);font-size:10px">({{ archive|length }} file{{ 's' if archive|length!=1 else '' }})</span>
      </button>
      <div id="archiveRows" style="display:none">
        <div class="sec-head sa" id="head-archived">
          <span class="sec-label">Archived</span>
          <span class="sec-count">{{ archive|length }}</span>
        </div>
        {% for f in archive %}
        <div class="file-item is-archived" data-ext="{{ f.ext }}" data-status="archived" data-name="{{ f.name }}">
          <span class="file-badge {{ f.ext }}">{{ f.ext }}</span>
          <span class="file-name" title="{{ f.original_name }}">{{ f.original_name }}</span>
          <span class="file-size">{{ f.size }}</span>
          <span class="file-date">{{ f.archived_at }}</span>
          <span class="pill pill-a">{{ f.delivered_by or "archived" }}</span>
          <div class="file-acts">
            <a href="/archive/{{ f.name|urlencode }}/download" class="act dl">↓</a>
            <button class="act restore" onclick="restoreFile(event,'{{ f.name|replace("'","\\'") }}')">↑</button>
            <button class="act rm"      onclick="delArchive(event,'{{ f.name|replace("'","\\'") }}')">✕</button>
          </div>
        </div>
        {% endfor %}
      </div>
      {% endif %}
    {% endif %}
    </div>
  </div>
</div>
<div id="toast"></div>

<script>
function toast(msg,good=false){const t=document.getElementById('toast');t.textContent=msg;t.className='show'+(good?' good':'');clearTimeout(t._t);t._t=setTimeout(()=>t.className='',3200)}
const dz=document.getElementById('dz'),fi=document.getElementById('fi');
dz.onclick=()=>fi.click();
dz.ondragover=e=>{e.preventDefault();dz.classList.add('over')};
dz.ondragleave=()=>dz.classList.remove('over');
dz.ondrop=e=>{e.preventDefault();dz.classList.remove('over');upload(Array.from(e.dataTransfer.files))};
fi.onchange=()=>upload(Array.from(fi.files));
async function upload(files){
  if(!files.length)return;
  const pl=document.getElementById('progList');pl.innerHTML='';pl.classList.add('active');
  for(const f of files){const id='p'+Math.random().toString(36).slice(2);const row=document.createElement('div');row.className='prog-row';row.innerHTML=`<span class="prog-name">${esc(f.name)}</span><div class="prog-track"><div class="prog-fill" id="${id}f"></div></div><span class="prog-st" id="${id}s">…</span>`;pl.appendChild(row);await up1(f,id)}
  setTimeout(()=>{pl.classList.remove('active');pl.innerHTML=''},2800);setTimeout(()=>location.reload(),1200)}
function up1(f,id){return new Promise(r=>{const fd=new FormData();fd.append('file',f);const x=new XMLHttpRequest();x.upload.onprogress=e=>{if(e.lengthComputable){const b=document.getElementById(id+'f');if(b)b.style.width=Math.round(e.loaded/e.total*100)+'%'}};x.onload=()=>{const s=document.getElementById(id+'s');if(x.status===200){if(s){s.textContent='done';s.classList.add('ok')}toast(f.name+' queued',true)}else{if(s){s.textContent='err';s.classList.add('err')}toast(f.name+' failed')}r()};x.onerror=()=>{toast('Network error');r()};x.open('POST','/upload');x.send(fd)})}
async function toggleStatus(e,name,cur){e.preventDefault();const r=await fetch('/file/'+encodeURIComponent(name),{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({delivered:!cur})});if(r.ok){toast(cur?name+' moved to queue':name+' marked delivered',true);setTimeout(()=>location.reload(),600)}else toast('Could not update')}
async function delFile(e,name){e.preventDefault();if(!confirm('Remove "'+name+'" from inbox?'))return;const r=await fetch('/file/'+encodeURIComponent(name),{method:'DELETE'});if(r.ok){const row=e.target.closest('.file-item');const st=row.dataset.status;row.remove();adjCounts(st==='queued'?-1:0,st==='delivered'?-1:0,0);toast(name+' removed')}else toast('Could not remove')}
async function clearDelivered(){const rows=document.querySelectorAll('[data-status="delivered"]');if(!rows.length){toast('No delivered files');return}if(!confirm(`Remove ${rows.length} delivered file(s)?`))return;let n=0;for(const row of rows){const res=await fetch('/file/'+encodeURIComponent(row.dataset.name),{method:'DELETE'});if(res.ok){row.remove();n++}}adjCounts(0,-n,0);toast(`Cleared ${n} file${n!==1?'s':''}`,true)}
async function restoreFile(e,name){e.preventDefault();const r=await fetch('/archive/'+encodeURIComponent(name)+'/restore',{method:'POST'});if(r.ok){toast(name+' restored',true);setTimeout(()=>location.reload(),600)}else toast('Could not restore')}
async function delArchive(e,name){e.preventDefault();if(!confirm('Permanently delete "'+name+'"?'))return;const r=await fetch('/archive/'+encodeURIComponent(name),{method:'DELETE'});if(r.ok){e.target.closest('.file-item').remove();adjCounts(0,0,-1);toast(name+' deleted')}else toast('Could not delete')}
function toggleArchive(){const btn=document.getElementById('archiveToggle');const rows=document.getElementById('archiveRows');const open=rows.style.display==='none';rows.style.display=open?'block':'none';btn.classList.toggle('open',open)}
let currentFilter='all';
function filter(btn,f){currentFilter=f;document.querySelectorAll('.f-btn').forEach(b=>b.classList.remove('on'));btn.classList.add('on');if(f==='archived'){const rows=document.getElementById('archiveRows');if(rows)rows.style.display='block';const toggle=document.getElementById('archiveToggle');if(toggle)toggle.classList.add('open')}document.querySelectorAll('.file-item').forEach(row=>{row.style.display=(f==='all'||f===row.dataset.status||f===row.dataset.ext)?'':'none'});['queued','delivered','archived'].forEach(s=>{const head=document.getElementById('head-'+s);if(head)head.style.display=(f==='all'||f===s||document.querySelector(`[data-status="${s}"]:not([style*="none"])`)?'':'')})}
function adjCounts(qd,dd,ad){[['hQ',qd],['hD',dd],['hA',ad]].forEach(([id,d])=>{const el=document.getElementById(id);if(el)el.textContent=Math.max(0,(parseInt(el.textContent)||0)+d)});const t=(parseInt(document.getElementById('hQ')?.textContent)||0)+(parseInt(document.getElementById('hD')?.textContent)||0)+(parseInt(document.getElementById('hA')?.textContent)||0);const fc=document.getElementById('fCount');if(fc)fc.textContent=t+' total'}
function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function copyCurl(){navigator.clipboard.writeText(document.getElementById('curlBox').innerText).then(()=>{const b=document.getElementById('copyBtn');b.textContent='Copied!';b.classList.add('copied');setTimeout(()=>{b.textContent='Copy curl';b.classList.remove('copied')},2000)})}
</script>
</body>
</html>"""

# ---------------------------------------------------------------------------
# Routes — setup wizard
# ---------------------------------------------------------------------------

@app.route("/setup/genkey")
def genkey():
    return jsonify({"key": _gen_api_key()})

@app.route("/setup", methods=["GET","POST"])
def setup():
    if cfg_is_setup():
        return redirect(url_for("index"))

    step = int(request.args.get("step", 1))

    if request.method == "POST":
        step = int(request.form.get("step", 1))

        if step == 1:
            pw  = request.form.get("password","").strip()
            pw2 = request.form.get("password2","").strip()
            if len(pw) < 4:
                return render_template_string(SETUP_HTML, step=1, error="Password must be at least 4 characters.", api_key="")
            if pw != pw2:
                return render_template_string(SETUP_HTML, step=1, error="Passwords don't match.", api_key="")
            # Store password hash in session temporarily until step 2 completes
            session["setup_pw_hash"] = hash_password(pw)
            new_key = _gen_api_key()
            return render_template_string(SETUP_HTML, step=2, error=None, api_key=new_key)

        if step == 2:
            pw_hash = session.pop("setup_pw_hash", None)
            if not pw_hash:
                return redirect("/setup")
            api_key     = request.form.get("api_key","").strip()
            archive_days = int(request.form.get("archive_days", 5))
            if not api_key:
                api_key = _gen_api_key()
            cfg = _read_cfg()
            cfg["password_hash"]  = pw_hash
            cfg["api_key"]        = api_key
            cfg["archive_days"]   = archive_days
            if not cfg.get("secret_key"):
                cfg["secret_key"] = secrets.token_hex(32)
            _write_cfg(cfg)
            app.secret_key = cfg["secret_key"]
            session["authed"] = True
            return render_template_string(SETUP_HTML, step=3, error=None,
                                          api_key=api_key, archive_days=archive_days)

    api_key = _gen_api_key() if step == 2 else ""
    return render_template_string(SETUP_HTML, step=step, error=None, api_key=api_key)

# ---------------------------------------------------------------------------
# Routes — auth
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET","POST"])
def login():
    if not cfg_is_setup():
        return redirect(url_for("setup"))
    if request.method == "POST":
        if check_password(request.form.get("password","")):
            session["authed"] = True
            return redirect(url_for("index"))
        return render_template_string(LOGIN_HTML, error="Incorrect password.")
    return render_template_string(LOGIN_HTML, error=None)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ---------------------------------------------------------------------------
# Routes — settings
# ---------------------------------------------------------------------------

@app.route("/settings")
def settings():
    r = check_web_auth()
    if r: return r
    return render_template_string(SETTINGS_HTML,
        api_key=cfg_get("api_key",""),
        archive_days=get_archive_days())

@app.route("/settings/password", methods=["POST"])
def settings_password():
    r = check_web_auth()
    if r: return r
    pw = (request.get_json(silent=True) or {}).get("password","").strip()
    if len(pw) < 4:
        abort(400)
    cfg = _read_cfg()
    cfg["password_hash"] = hash_password(pw)
    _write_cfg(cfg)
    return jsonify({"ok": True})

@app.route("/settings/apikey", methods=["POST"])
def settings_apikey():
    r = check_web_auth()
    if r: return r
    cfg = _read_cfg()
    cfg["api_key"] = _gen_api_key()
    _write_cfg(cfg)
    return jsonify({"api_key": cfg["api_key"]})

@app.route("/settings/archive", methods=["POST"])
def settings_archive():
    r = check_web_auth()
    if r: return r
    days = (request.get_json(silent=True) or {}).get("days", 5)
    cfg = _read_cfg()
    cfg["archive_days"] = int(days)
    _write_cfg(cfg)
    return jsonify({"archive_days": cfg["archive_days"]})

# ---------------------------------------------------------------------------
# Routes — main
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    if not cfg_is_setup():
        return redirect(url_for("setup"))
    r = check_web_auth()
    if r: return r
    run_archive_pass()
    files   = get_inbox_files()
    archive = get_archive_files()
    queued    = [f for f in files if not f["delivered"]]
    delivered = [f for f in files if f["delivered"]]
    return render_template_string(MAIN_HTML,
        files=files, archive=archive,
        file_count=len(files)+len(archive),
        queued_count=len(queued),
        delivered_count=len(delivered),
        archive_count=len(archive),
        archive_days=get_archive_days(),
        base_url=request.host_url.rstrip("/"))

@app.route("/upload", methods=["POST"])
def upload():
    wa = bool(session.get("authed"))
    aa = check_api_key(request.headers.get("X-Publish-Token",""))
    if not (wa or aa): abort(401)
    files = request.files.getlist("file")
    if not files: abort(400)
    saved, errors = [], []
    for f in files:
        name = safe_name(f.filename)
        if not name or not is_allowed(name): errors.append(f"{f.filename}: unsupported"); continue
        f.save(INBOX_DIR/name)
        mark_undelivered(name)
        saved.append(name)
    if errors and not saved: return jsonify({"error":errors}),400
    return jsonify({"saved":saved,"errors":errors}),200

@app.route("/manifest")
def manifest():
    check_api_auth()
    run_archive_pass()
    log = _read_log()
    show_all = request.args.get("all")=="1"
    files = sorted(
        f.name for f in INBOX_DIR.iterdir()
        if f.is_file() and not f.name.startswith(".")
        and f.suffix.lower() in ALLOWED_EXTENSIONS
        and (show_all or f.name not in log)
    )
    return jsonify({"files": files})

@app.route("/download/<path:filename>")
def download(filename):
    wa = bool(session.get("authed"))
    aa = check_api_key(request.headers.get("X-Publish-Token",""))
    if not (wa or aa): abort(401)
    name = safe_name(filename)
    if not (INBOX_DIR/name).is_file(): abort(404)
    source = "kindle" if request.headers.get("X-Publish-Token") else ("web" if session.get("authed") else "api")
    mark_delivered(name, source)
    return send_from_directory(INBOX_DIR.resolve(), name, as_attachment=True)

@app.route("/file/<path:filename>", methods=["DELETE","PATCH"])
def manage_file(filename):
    wa = bool(session.get("authed"))
    aa = check_api_key(request.headers.get("X-Publish-Token",""))
    if not (wa or aa): abort(401)
    name = safe_name(filename)
    target = INBOX_DIR/name
    if request.method == "DELETE":
        if not target.is_file(): abort(404)
        target.unlink(); remove_from_log(name)
        return jsonify({"deleted":name})
    body = request.get_json(silent=True) or {}
    if body.get("delivered") is True: mark_delivered(name,"manual")
    else: mark_undelivered(name)
    return jsonify({"name":name,"delivered":bool(body.get("delivered"))})

@app.route("/archive")
def list_archive():
    wa = bool(session.get("authed"))
    aa = check_api_key(request.headers.get("X-Publish-Token",""))
    if not (wa or aa): abort(401)
    return jsonify({"files":[f["name"] for f in get_archive_files()]})

@app.route("/archive/<path:filename>/download")
def download_archive(filename):
    wa = bool(session.get("authed"))
    aa = check_api_key(request.headers.get("X-Publish-Token",""))
    if not (wa or aa): abort(401)
    name = safe_name(filename)
    if not (archive_dir()/name).is_file(): abort(404)
    return send_from_directory(archive_dir().resolve(), name, as_attachment=True)

@app.route("/archive/<path:filename>/restore", methods=["POST"])
def restore_archive(filename):
    wa = bool(session.get("authed"))
    aa = check_api_key(request.headers.get("X-Publish-Token",""))
    if not (wa or aa): abort(401)
    name = safe_name(filename)
    src = archive_dir()/name
    if not src.is_file(): abort(404)
    with _log_lock:
        log = _read_log()
        original_name = name
        for fname, meta in log.items():
            if meta.get("archive_name") == name:
                original_name = fname
                meta.pop("archived_at",None); meta.pop("archive_name",None)
                break
        shutil.move(str(src), str(INBOX_DIR/original_name))
        _write_log(log)
    return jsonify({"restored":original_name})

@app.route("/archive/<path:filename>", methods=["DELETE"])
def delete_archive(filename):
    wa = bool(session.get("authed"))
    aa = check_api_key(request.headers.get("X-Publish-Token",""))
    if not (wa or aa): abort(401)
    name = safe_name(filename)
    target = archive_dir()/name
    if not target.is_file(): abort(404)
    target.unlink()
    with _log_lock:
        log = _read_log()
        for fname, meta in list(log.items()):
            if meta.get("archive_name")==name: del log[fname]; break
        _write_log(log)
    return jsonify({"deleted":name})

@app.route("/health")
def health():
    setup = cfg_is_setup()
    try:
        inbox_files  = [f for f in INBOX_DIR.iterdir() if f.is_file() and not f.name.startswith(".")]
        archive_files = list(archive_dir().iterdir()) if archive_dir().exists() else []
        log = _read_log()
        delivered = sum(1 for f in inbox_files if f.name in log)
        queued = len(inbox_files) - delivered
    except Exception:
        queued = delivered = 0; archive_files = []
    return jsonify({"status":"ok","setup":setup,"inbox":str(INBOX_DIR),
                    "queued":queued,"delivered":delivered,"archived":len(archive_files),
                    "archive_days":get_archive_days(),"uptime_s":round(time.time()-START_TIME)})

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    global INBOX_DIR
    parser = argparse.ArgumentParser()
    parser.add_argument("--inbox", default=os.environ.get("PUBLISH_INBOX","./inbox"))
    parser.add_argument("--port",  default=int(os.environ.get("PUBLISH_PORT",8765)), type=int)
    parser.add_argument("--host",  default=os.environ.get("PUBLISH_HOST","0.0.0.0"))
    args = parser.parse_args()
    INBOX_DIR = Path(args.inbox).expanduser().resolve()
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    # Bootstrap secret key from config (or generate one)
    app.secret_key = _get_secret_key()
    print(f"Publish  |  inbox: {INBOX_DIR}  |  http://{args.host}:{args.port}")
    if not cfg_is_setup():
        print("  → First run: open the UI to complete setup")
    run_archive_pass()
    app.run(host=args.host, port=args.port, debug=False)

if __name__ == "__main__":
    main()
