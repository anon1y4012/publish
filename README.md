# EpubSync

A self-hosted EPUB inbox server with a web UI. Drop books in from any device — browser, iOS Shortcut, Calibre script, or API call — and your jailbroken Kindle running KOReader pulls them automatically on wake.

![EpubSync UI](https://raw.githubusercontent.com/YOUR_USERNAME/epubsync/main/.github/screenshot.png)

## Features

- **Drag-and-drop web UI** — dark, fast, works on mobile
- **KOReader plugin** — Kindle pulls new files on every wake, no manual sync
- **REST API** — upload via curl, iOS Shortcuts, Calibre scripts, or AI agents
- **Token auth** — single shared secret protects all endpoints
- **Docker-first** — one `docker compose up` and it's running
- **Multi-arch** — `linux/amd64` and `linux/arm64` (works on a Pi)
- **Supported formats** — `.epub`, `.mobi`, `.pdf`, `.fb2`, `.azw3`, `.lit`

---

## Quick start

### Docker Compose (recommended)

```bash
git clone https://github.com/YOUR_USERNAME/epubsync
cd epubsync

cp .env.example .env
# Edit .env — set EPUBSYNC_TOKEN to a long random secret

docker compose up -d
```

The UI is now at `http://localhost:8765`.

### Cloudflare Tunnel

Add a public hostname to one of your existing named tunnels pointing at `localhost:8765`. No new tunnel needed — just a new route in the Cloudflare dashboard under Zero Trust → Networks → Tunnels → your tunnel → Public Hostnames → Add.

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `EPUBSYNC_TOKEN` | *(empty)* | Shared secret. **Strongly recommended.** Leave blank to disable auth. |
| `EPUBSYNC_INBOX` | `/inbox` | Path to inbox folder inside container |
| `EPUBSYNC_PORT` | `8765` | Port to listen on |
| `EPUBSYNC_HOST` | `0.0.0.0` | Bind host |
| `EPUBSYNC_SECRET_KEY` | *(random)* | Flask session key. Set to a fixed value if you want sessions to survive restarts. |

---

## API

All endpoints (except `/health`) require either:
- A valid session cookie (web UI login), or
- The `X-EpubSync-Token: <token>` header

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Web UI |
| `GET` | `/manifest` | JSON list of files — used by KOReader plugin |
| `GET` | `/download/<filename>` | Download a file |
| `POST` | `/upload` | Upload one or more files (multipart form, field name `file`) |
| `DELETE` | `/file/<filename>` | Remove a file |
| `GET` | `/health` | Status JSON, no auth required |

### Upload examples

**curl:**
```bash
curl -X POST https://your-tunnel.example.com/upload \
  -H "X-EpubSync-Token: your-secret" \
  -F "file=@mybook.epub"
```

**Multiple files:**
```bash
curl -X POST https://your-tunnel.example.com/upload \
  -H "X-EpubSync-Token: your-secret" \
  -F "file=@book1.epub" \
  -F "file=@book2.pdf"
```

**Python:**
```python
import requests

requests.post(
    "https://your-tunnel.example.com/upload",
    headers={"X-EpubSync-Token": "your-secret"},
    files={"file": open("book.epub", "rb")},
)
```

**iOS Shortcut:** Use the "Get Contents of URL" action with method POST, add the token header, and attach the file from the Files app.

---

## KOReader plugin

### Installation

Copy the `koplugin/` folder to your Kindle:

```
/mnt/us/koreader/plugins/epubsync.koplugin/
  _meta.lua
  main.lua
```

### Configuration

In KOReader: **≡ Menu → Tools → EpubSync**

| Setting | Value |
|---|---|
| Server URL | `https://your-tunnel.example.com` (no trailing slash) |
| API token | Your `EPUBSYNC_TOKEN` value |
| Inbox folder | `/mnt/us/books` or any folder KOReader can see |
| Auto-sync on wake | ON |

The plugin fires on every device resume. It fetches `/manifest`, diffs against the local folder, downloads anything new, then broadcasts a `FileManagerRefresh` event so books appear immediately without manual navigation.

---

## Calibre integration

**Option 1 — Watch folder:** Point Calibre's "Auto-add" feature at the same folder mounted in `docker-compose.yml` (`./inbox`). Books added via Calibre appear in the inbox automatically.

**Option 2 — Post-processing script:** In Calibre Preferences → Adding books → Post-import script:

```bash
#!/bin/bash
curl -s -X POST https://your-tunnel.example.com/upload \
  -H "X-EpubSync-Token: your-secret" \
  -F "file=@$1"
```

---

## Running without Docker

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

EPUBSYNC_TOKEN=your-secret \
EPUBSYNC_INBOX=~/epubsync-inbox \
python server.py --host 0.0.0.0 --port 8765
```

### macOS launchd (auto-start on login)

Create `~/Library/LaunchAgents/com.epubsync.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.epubsync</string>
  <key>ProgramArguments</key>
  <array>
    <string>/path/to/.venv/bin/python</string>
    <string>/path/to/server.py</string>
    <string>--host</string><string>127.0.0.1</string>
    <string>--port</string><string>8765</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>EPUBSYNC_TOKEN</key><string>your-secret</string>
    <key>EPUBSYNC_INBOX</key><string>/Users/you/epubsync-inbox</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/epubsync.log</string>
  <key>StandardErrorPath</key><string>/tmp/epubsync.err</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.epubsync.plist
```

---

## Repository structure

```
epubsync/
├── server.py              Flask server + web UI
├── requirements.txt       Python dependencies
├── Dockerfile             Container definition
├── docker-compose.yml     Compose config
├── .env.example           Environment variable template
├── .gitignore
├── .dockerignore
├── .github/
│   └── workflows/
│       └── docker-publish.yml   Builds + pushes to ghcr.io on push/tag
└── koplugin/              KOReader plugin (copy to Kindle)
    ├── _meta.lua
    └── main.lua
```

---

## License

MIT
