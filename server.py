#!/usr/bin/env python3
"""
Publish — Personal book delivery server.
Drop files in, Kindle pulls them on wake.
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
app.secret_key = os.environ.get("PUBLISH_SECRET_KEY") or os.urandom(32)
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True

INBOX_DIR: Path = Path(os.environ.get("PUBLISH_INBOX", "./inbox"))
API_TOKEN: str  = os.environ.get("PUBLISH_TOKEN", "")
ALLOWED_EXTENSIONS = {".epub", ".mobi", ".pdf", ".fb2", ".azw3", ".lit"}
START_TIME = time.time()

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
    for unit in ("B","KB","MB","GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit=="B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"

def get_files():
    out = []
    try:
        entries = sorted(INBOX_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True)
    except FileNotFoundError:
        return out
    for f in entries:
        if not f.is_file(): continue
        ext = f.suffix.lower().lstrip(".")
        stat = f.stat()
        out.append({
            "name": f.name,
            "ext": ext if ext in ("epub","pdf","mobi","azw3","fb2") else "other",
            "size": fmt_size(stat.st_size),
            "date": datetime.fromtimestamp(stat.st_mtime).strftime("%b %d, %Y"),
            "ts": int(stat.st_mtime),
        })
    return out

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
:root{
  --ink:#1a1814;--paper:#f5f2eb;--cream:#ede9df;
  --rule:#d4cfc4;--accent:#c0392b;--muted:#8a8478;
  --mono:'IBM Plex Mono',monospace;--serif:'Playfair Display',Georgia,serif;
}
html,body{height:100%;background:var(--paper);color:var(--ink);font-family:var(--mono)}
body{display:flex;align-items:center;justify-content:center;
  background-image:repeating-linear-gradient(0deg,transparent,transparent 27px,var(--rule) 28px);
  background-size:100% 28px;
}
.card{
  background:var(--paper);border:2px solid var(--ink);
  padding:clamp(32px,5vw,56px) clamp(28px,5vw,52px);
  width:min(420px,92vw);position:relative;
  box-shadow:6px 6px 0 var(--ink);
}
.masthead{
  border-bottom:3px double var(--ink);padding-bottom:20px;margin-bottom:28px;
  text-align:center;
}
.title{font-family:var(--serif);font-size:clamp(32px,6vw,48px);font-weight:700;letter-spacing:-.02em;line-height:1}
.subtitle{font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:var(--muted);margin-top:6px}
label{display:block;font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);margin-bottom:8px}
input[type=password]{
  width:100%;background:var(--cream);border:1px solid var(--rule);border-bottom:2px solid var(--ink);
  color:var(--ink);font-family:var(--mono);font-size:15px;padding:11px 14px;
  outline:none;transition:border-color .15s;
}
input[type=password]:focus{border-color:var(--accent);border-bottom-color:var(--accent)}
.btn{
  margin-top:18px;width:100%;background:var(--ink);color:var(--paper);
  border:none;font-family:var(--mono);font-size:11px;letter-spacing:.14em;
  text-transform:uppercase;padding:14px;cursor:pointer;transition:background .15s;
}
.btn:hover{background:var(--accent)}
.error{color:var(--accent);font-size:11px;margin-top:12px;letter-spacing:.04em}
.corner{position:absolute;width:10px;height:10px;border-color:var(--ink);border-style:solid}
.tl{top:-2px;left:-2px;border-width:2px 0 0 2px}
.tr{top:-2px;right:-2px;border-width:2px 2px 0 0}
.bl{bottom:-2px;left:-2px;border-width:0 0 2px 2px}
.br{bottom:-2px;right:-2px;border-width:0 2px 2px 0}
</style>
</head>
<body>
<div class="card">
  <div class="corner tl"></div><div class="corner tr"></div>
  <div class="corner bl"></div><div class="corner br"></div>
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
  --rule:#d4cfc4;--rule2:#c8c3b7;--accent:#c0392b;--accent2:#e74c3c;
  --muted:#8a8478;--muted2:#b5b0a6;
  --epub:#2c6fad;--pdf:#b35c22;--mobi:#6b3fa0;--other:#2a7a4e;
  --mono:'IBM Plex Mono',monospace;
  --serif:'Playfair Display',Georgia,serif;
  --col-left:clamp(260px,28vw,340px);
  --header-h:52px;
}
html,body{height:100%;overflow:hidden}
body{background:var(--paper);color:var(--ink);font-family:var(--mono);font-size:13px;
  display:flex;flex-direction:column;
  background-image:repeating-linear-gradient(90deg,transparent,transparent calc(var(--col-left) - 1px),var(--rule) var(--col-left));
}

/* ── Header ── */
header{
  height:var(--header-h);border-bottom:2px solid var(--ink);
  display:grid;grid-template-columns:var(--col-left) 1fr;
  flex-shrink:0;background:var(--white);
}
.header-brand{
  border-right:1px solid var(--ink);
  display:flex;align-items:center;padding:0 clamp(16px,2.5vw,28px);gap:12px;
}
.brand-title{font-family:var(--serif);font-size:clamp(18px,2.5vw,24px);font-weight:700;letter-spacing:-.02em;line-height:1}
.brand-rule{width:1px;height:22px;background:var(--rule2)}
.brand-sub{font-size:9px;letter-spacing:.16em;text-transform:uppercase;color:var(--muted);line-height:1.4}
.header-meta{
  display:flex;align-items:center;justify-content:space-between;
  padding:0 clamp(16px,2.5vw,28px);
}
.header-stats{display:flex;align-items:center;gap:clamp(12px,2vw,24px)}
.stat{display:flex;align-items:center;gap:7px;font-size:11px;color:var(--muted)}
.stat-dot{width:6px;height:6px;border-radius:50%;background:#2ecc71;flex-shrink:0;animation:blink 2.5s ease-in-out infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.3}}
.stat-val{color:var(--ink);font-weight:400}
.header-actions{display:flex;align-items:center;gap:8px}
.btn-sm{
  font-family:var(--mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase;
  padding:5px 12px;border:1px solid var(--rule2);background:none;color:var(--muted);
  cursor:pointer;text-decoration:none;transition:all .15s;display:inline-flex;align-items:center;gap:5px;
}
.btn-sm:hover{color:var(--ink);border-color:var(--ink)}

/* ── Layout ── */
.body{display:grid;grid-template-columns:var(--col-left) 1fr;flex:1;overflow:hidden}

/* ── Left panel ── */
.panel-l{
  border-right:1px solid var(--ink);overflow-y:auto;
  display:flex;flex-direction:column;background:var(--white);
}
.panel-section{border-bottom:1px solid var(--rule);padding:clamp(16px,2vw,24px) clamp(16px,2.5vw,24px)}
.panel-label{
  font-size:9px;letter-spacing:.18em;text-transform:uppercase;color:var(--muted2);
  margin-bottom:14px;display:flex;align-items:center;gap:8px;
}
.panel-label::after{content:'';flex:1;height:1px;background:var(--rule)}

/* Drop zone */
.dropzone{
  border:1px dashed var(--rule2);padding:clamp(20px,3vw,32px) 16px;
  text-align:center;cursor:pointer;transition:all .2s;background:var(--paper);
  position:relative;
}
.dropzone:hover,.dropzone.over{border-color:var(--accent);background:rgba(192,57,43,.03)}
.dropzone.over{border-style:solid}
.dz-icon{font-size:clamp(22px,3vw,28px);opacity:.35;margin-bottom:8px}
.dz-title{font-family:var(--serif);font-size:clamp(14px,1.8vw,17px);margin-bottom:4px;font-style:italic}
.dz-sub{font-size:10px;color:var(--muted);line-height:1.7}
.dz-formats{margin-top:10px;display:flex;gap:4px;justify-content:center;flex-wrap:wrap}
.fmt{font-size:9px;padding:2px 6px;border:1px solid var(--rule2);color:var(--muted2);letter-spacing:.06em;text-transform:uppercase}
input[type=file]{display:none}
.progress-list{margin-top:10px;display:none}
.progress-list.active{display:block}
.prog-row{display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--rule);font-size:11px}
.prog-row:last-child{border:none}
.prog-name{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--ink)}
.prog-track{width:60px;height:2px;background:var(--rule)}
.prog-fill{height:100%;background:var(--accent);width:0%;transition:width .3s}
.prog-st{font-size:10px;width:40px;text-align:right}
.prog-st.ok{color:#27ae60}.prog-st.err{color:var(--accent)}

/* API block */
.api-table{width:100%;border-collapse:collapse;font-size:11px}
.api-table tr{border-bottom:1px solid var(--rule)}
.api-table tr:last-child{border:none}
.api-table td{padding:6px 0}
.api-table td:first-child{width:42px}
.method{font-size:9px;letter-spacing:.06em;padding:2px 5px;font-weight:400}
.m-get{background:rgba(39,174,96,.1);color:#27ae60;border:1px solid rgba(39,174,96,.2)}
.m-post{background:rgba(192,57,43,.08);color:var(--accent);border:1px solid rgba(192,57,43,.15)}
.m-del{background:rgba(192,57,43,.06);color:#888;border:1px solid var(--rule)}
.api-path{color:var(--ink);font-family:var(--mono)}
.api-desc{color:var(--muted);font-style:italic;font-size:10px}
.curl-pre{
  background:var(--cream);border-left:2px solid var(--ink);
  padding:10px 12px;font-size:10px;color:var(--muted);line-height:1.8;
  word-break:break-all;margin-top:10px;
}
.curl-pre em{color:var(--accent);font-style:normal}
.copy-curl{
  margin-top:6px;width:100%;background:none;border:1px solid var(--rule2);
  color:var(--muted);font-family:var(--mono);font-size:10px;letter-spacing:.1em;
  text-transform:uppercase;padding:6px;cursor:pointer;transition:all .15s;
}
.copy-curl:hover{color:var(--ink);border-color:var(--ink)}
.copy-curl.copied{color:#27ae60;border-color:#27ae60}

/* ── Right panel ── */
.panel-r{display:flex;flex-direction:column;overflow:hidden}
.panel-r-head{
  border-bottom:1px solid var(--rule);padding:14px clamp(16px,2.5vw,28px);
  display:flex;align-items:center;justify-content:space-between;flex-shrink:0;
  background:var(--white);
}
.filters{display:flex;gap:4px;flex-wrap:wrap}
.f-btn{
  font-family:var(--mono);font-size:10px;letter-spacing:.08em;text-transform:uppercase;
  padding:4px 10px;border:1px solid var(--rule2);background:none;color:var(--muted);
  cursor:pointer;transition:all .15s;
}
.f-btn:hover,.f-btn.on{color:var(--ink);border-color:var(--ink)}
.f-btn.on{background:var(--cream)}
.file-count{font-size:11px;color:var(--muted);white-space:nowrap}

/* File list */
.file-list{flex:1;overflow-y:auto;padding:clamp(12px,1.5vw,20px) clamp(16px,2.5vw,28px)}
.file-item{
  display:grid;
  grid-template-columns:40px 1fr auto auto 72px;
  align-items:center;gap:clamp(8px,1.5vw,16px);
  padding:clamp(8px,1vw,12px) clamp(10px,1.5vw,14px);
  border:1px solid transparent;border-bottom-color:var(--rule);
  transition:all .15s;cursor:default;
}
.file-item:last-child{border-bottom-color:transparent}
.file-item:hover{background:var(--white);border-color:var(--rule2);border-bottom-color:var(--rule2)}
.file-badge{
  font-size:9px;letter-spacing:.06em;text-transform:uppercase;
  padding:3px 0;text-align:center;border:1px solid;
}
.epub{color:var(--epub);border-color:rgba(44,111,173,.25);background:rgba(44,111,173,.06)}
.pdf{color:var(--pdf);border-color:rgba(179,92,34,.25);background:rgba(179,92,34,.06)}
.mobi,.azw3{color:var(--mobi);border-color:rgba(107,63,160,.25);background:rgba(107,63,160,.06)}
.fb2,.other{color:var(--other);border-color:rgba(42,122,78,.25);background:rgba(42,122,78,.06)}
.file-name{
  font-size:clamp(11px,1.2vw,13px);color:var(--ink);
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
}
.file-size{font-size:11px;color:var(--muted);white-space:nowrap;text-align:right}
.file-date{font-size:10px;color:var(--muted2);white-space:nowrap}
.file-acts{display:flex;gap:5px;justify-content:flex-end}
.act{
  font-size:10px;letter-spacing:.06em;text-transform:uppercase;text-decoration:none;
  padding:3px 7px;border:1px solid var(--rule2);transition:all .15s;color:var(--muted);
  background:none;cursor:pointer;font-family:var(--mono);
}
.act.dl:hover{color:var(--epub);border-color:var(--epub)}
.act.rm:hover{color:var(--accent);border-color:var(--accent)}

.empty-state{
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  height:100%;gap:14px;color:var(--muted);text-align:center;padding:40px;
}
.empty-icon{font-family:var(--serif);font-size:clamp(48px,8vw,72px);font-weight:700;font-style:italic;opacity:.08;line-height:1;color:var(--ink)}
.empty-title{font-family:var(--serif);font-size:clamp(16px,2vw,20px);color:var(--muted);font-style:italic}
.empty-sub{font-size:11px;color:var(--muted2)}

/* Toast */
#toast{
  position:fixed;bottom:clamp(16px,2vw,24px);right:clamp(16px,2vw,24px);
  background:var(--ink);color:var(--paper);
  padding:10px 16px;font-size:11px;letter-spacing:.04em;z-index:999;
  transform:translateY(12px);opacity:0;transition:all .2s;pointer-events:none;
  border-left:3px solid var(--accent);max-width:300px;
}
#toast.show{transform:translateY(0);opacity:1}

::-webkit-scrollbar{width:4px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--rule2)}

/* Responsive: collapse left panel on narrow screens */
@media(max-width:640px){
  body{background-image:none}
  header{grid-template-columns:1fr}
  .header-brand{border-right:none}
  .header-meta{display:none}
  .body{grid-template-columns:1fr}
  .panel-l{display:none}
  .file-item{grid-template-columns:36px 1fr 60px}
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
      <div class="stat"><div class="stat-dot"></div><span id="hCount"><span class="stat-val">{{ file_count }}</span> file{{ 's' if file_count != 1 else '' }} queued</span></div>
    </div>
    <div class="header-actions">
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
      <div class="progress-list" id="progList"></div>
    </div>

    <div class="panel-section">
      <div class="panel-label">API</div>
      <table class="api-table">
        <tr><td><span class="method m-get">GET</span></td><td><div class="api-path">/manifest</div><div class="api-desc">file list for Kindle</div></td></tr>
        <tr><td><span class="method m-get">GET</span></td><td><div class="api-path">/download/&lt;file&gt;</div><div class="api-desc">fetch a file</div></td></tr>
        <tr><td><span class="method m-post">POST</span></td><td><div class="api-path">/upload</div><div class="api-desc">deliver a file</div></td></tr>
        <tr><td><span class="method m-del">DEL</span></td><td><div class="api-path">/file/&lt;name&gt;</div><div class="api-desc">remove from queue</div></td></tr>
      </table>
      <div class="curl-pre" id="curlBox">curl -X POST <em>{{ base_url }}/upload</em> \<br>&nbsp;&nbsp;-H <em>"X-Publish-Token: &lt;token&gt;"</em> \<br>&nbsp;&nbsp;-F <em>"file=@book.epub"</em></div>
      <button class="copy-curl" id="copyBtn" onclick="copyCurl()">Copy curl</button>
    </div>

  </div>

  <!-- Right panel -->
  <div class="panel-r">
    <div class="panel-r-head">
      <div class="filters" id="filters">
        <button class="f-btn on" onclick="filter(this,'all')">All</button>
        <button class="f-btn" onclick="filter(this,'epub')">EPUB</button>
        <button class="f-btn" onclick="filter(this,'pdf')">PDF</button>
        <button class="f-btn" onclick="filter(this,'mobi')">Mobi</button>
        <button class="f-btn" onclick="filter(this,'azw3')">AZW3</button>
      </div>
      <div class="file-count" id="fCount">{{ file_count }} item{{ 's' if file_count != 1 else '' }}</div>
    </div>
    <div class="file-list" id="fileList">
    {% if files %}
      {% for f in files %}
      <div class="file-item" data-ext="{{ f.ext }}">
        <span class="file-badge {{ f.ext }}">{{ f.ext }}</span>
        <span class="file-name" title="{{ f.name }}">{{ f.name }}</span>
        <span class="file-size">{{ f.size }}</span>
        <span class="file-date">{{ f.date }}</span>
        <div class="file-acts">
          <a href="/download/{{ f.name | urlencode }}" class="act dl">↓</a>
          <button class="act rm" onclick="del(event,'{{ f.name | replace("'","\\'") }}')">✕</button>
        </div>
      </div>
      {% endfor %}
    {% else %}
      <div class="empty-state">
        <div class="empty-icon">P</div>
        <div class="empty-title">The queue is empty</div>
        <div class="empty-sub">Drop books on the left to get started</div>
      </div>
    {% endif %}
    </div>
  </div>

</div>
<div id="toast"></div>

<script>
function toast(msg,ok=true){
  const t=document.getElementById('toast');
  t.textContent=msg;t.className='show';
  clearTimeout(t._t);t._t=setTimeout(()=>t.className='',3000);
}

// Drop zone
const dz=document.getElementById('dz'),fi=document.getElementById('fi');
dz.onclick=()=>fi.click();
dz.ondragover=e=>{e.preventDefault();dz.classList.add('over')};
dz.ondragleave=()=>dz.classList.remove('over');
dz.ondrop=e=>{e.preventDefault();dz.classList.remove('over');upload(Array.from(e.dataTransfer.files))};
fi.onchange=()=>upload(Array.from(fi.files));

async function upload(files){
  if(!files.length)return;
  const pl=document.getElementById('progList');
  pl.innerHTML='';pl.classList.add('active');
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
    const fd=new FormData();fd.append('file',f);
    const x=new XMLHttpRequest();
    x.upload.onprogress=e=>{if(e.lengthComputable){const b=document.getElementById(id+'f');if(b)b.style.width=Math.round(e.loaded/e.total*100)+'%'}};
    x.onload=()=>{
      const s=document.getElementById(id+'s');
      if(x.status===200){if(s){s.textContent='done';s.classList.add('ok')}toast(f.name+' delivered')}
      else{if(s){s.textContent='err';s.classList.add('err')}toast(f.name+' failed',false)}
      r();
    };
    x.onerror=()=>{toast('Network error',false);r()};
    x.open('POST','/upload');x.send(fd);
  });
}

async function del(e,name){
  e.preventDefault();
  if(!confirm('Remove "'+name+'" from the queue?'))return;
  const r=await fetch('/file/'+encodeURIComponent(name),{method:'DELETE'});
  if(r.ok){e.target.closest('.file-item').remove();adj(-1);toast(name+' removed')}
  else toast('Could not remove',false);
}

function filter(btn,ext){
  document.querySelectorAll('.f-btn').forEach(b=>b.classList.remove('on'));
  btn.classList.add('on');
  document.querySelectorAll('.file-item').forEach(r=>r.style.display=(ext==='all'||r.dataset.ext===ext)?'':'none');
}

function adj(d){
  const fc=document.getElementById('fCount');
  const hc=document.getElementById('hCount');
  const n=Math.max(0,(parseInt(fc.textContent)||0)+d);
  fc.textContent=n+' item'+(n!==1?'s':'');
  if(hc){const sp=hc.querySelector('.stat-val');if(sp)sp.textContent=n;hc.lastChild.textContent=' file'+(n!==1?'s':'')+' queued';}
}

function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}

function copyCurl(){
  navigator.clipboard.writeText(document.getElementById('curlBox').innerText).then(()=>{
    const b=document.getElementById('copyBtn');
    b.textContent='Copied!';b.classList.add('copied');
    setTimeout(()=>{b.textContent='Copy curl';b.classList.remove('copied')},2000);
  });
}
</script>
</body>
</html>"""

# ── Routes ──────────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        if request.form.get("token")==API_TOKEN:
            session["authed"]=True
            return redirect(url_for("index"))
        return render_template_string(LOGIN_HTML,error="Invalid token.")
    return render_template_string(LOGIN_HTML,error=None)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/")
def index():
    r=check_web_auth()
    if r: return r
    files=get_files()
    return render_template_string(MAIN_HTML,files=files,file_count=len(files),base_url=request.host_url.rstrip("/"))

@app.route("/upload",methods=["POST"])
def upload():
    wa=bool(session.get("authed"))
    aa=(not API_TOKEN) or (request.headers.get("X-Publish-Token")==API_TOKEN)
    if not(wa or aa): abort(401)
    files=request.files.getlist("file")
    if not files: abort(400)
    saved,errors=[],[]
    for f in files:
        name=safe_name(f.filename)
        if not name or not is_allowed(name): errors.append(f"{f.filename}: unsupported"); continue
        f.save(INBOX_DIR/name); saved.append(name)
    if errors and not saved: return jsonify({"error":errors}),400
    return jsonify({"saved":saved,"errors":errors}),200

@app.route("/manifest")
def manifest():
    check_api_auth()
    files=sorted(f.name for f in INBOX_DIR.iterdir() if f.is_file() and f.suffix.lower() in ALLOWED_EXTENSIONS)
    return jsonify({"files":files})

@app.route("/download/<path:filename>")
def download(filename):
    wa=bool(session.get("authed"))
    aa=(not API_TOKEN) or (request.headers.get("X-Publish-Token")==API_TOKEN)
    if not(wa or aa): abort(401)
    name=safe_name(filename)
    if not(INBOX_DIR/name).is_file(): abort(404)
    return send_from_directory(INBOX_DIR.resolve(),name,as_attachment=True)

@app.route("/file/<path:filename>",methods=["DELETE"])
def delete_file(filename):
    wa=bool(session.get("authed"))
    aa=(not API_TOKEN) or (request.headers.get("X-Publish-Token")==API_TOKEN)
    if not(wa or aa): abort(401)
    name=safe_name(filename)
    target=INBOX_DIR/name
    if not target.is_file(): abort(404)
    target.unlink()
    return jsonify({"deleted":name})

@app.route("/health")
def health():
    try: fc=len([f for f in INBOX_DIR.iterdir() if f.is_file()])
    except: fc=-1
    return jsonify({"status":"ok","inbox":str(INBOX_DIR),"file_count":fc,"uptime_s":round(time.time()-START_TIME)})

# ── Entry point ──────────────────────────────────────────────────────────────

def main():
    global INBOX_DIR,API_TOKEN
    parser=argparse.ArgumentParser()
    parser.add_argument("--inbox",default=os.environ.get("PUBLISH_INBOX","./inbox"))
    parser.add_argument("--port",default=int(os.environ.get("PUBLISH_PORT",8765)),type=int)
    parser.add_argument("--host",default=os.environ.get("PUBLISH_HOST","127.0.0.1"))
    parser.add_argument("--token",default=os.environ.get("PUBLISH_TOKEN",""))
    args=parser.parse_args()
    INBOX_DIR=Path(args.inbox).expanduser().resolve()
    INBOX_DIR.mkdir(parents=True,exist_ok=True)
    API_TOKEN=args.token
    if not API_TOKEN: print("WARNING: No token set.",file=sys.stderr)
    print(f"Publish  |  inbox: {INBOX_DIR}  |  http://{args.host}:{args.port}")
    app.run(host=args.host,port=args.port,debug=False)

if __name__=="__main__":
    main()
