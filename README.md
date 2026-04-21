# Publish

Personal book delivery server. Drop EPUBs in via browser, iOS Shortcut, Calibre, or API — your Kindle pulls them automatically on wake via KOReader.

## Quick start

```bash
cp .env.example .env
# Set PUBLISH_TOKEN and PUBLISH_SECRET_KEY in .env
docker compose up -d
```

Open `http://localhost:8765` — or expose via Cloudflare Tunnel.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `PUBLISH_TOKEN` | *(empty)* | Shared secret. Strongly recommended. |
| `PUBLISH_SECRET_KEY` | *(random)* | Flask session key. Set fixed value to persist sessions across restarts. |
| `PUBLISH_INBOX` | `/inbox` | Inbox path inside container |
| `PUBLISH_PORT` | `8765` | Port |
| `PUBLISH_HOST` | `0.0.0.0` | Bind host |

## API

All endpoints require session cookie (web login) or `X-Publish-Token: <token>` header.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/manifest` | JSON file list — KOReader plugin |
| `GET` | `/download/<file>` | Download a file |
| `POST` | `/upload` | Upload files (multipart, field: `file`) |
| `DELETE` | `/file/<name>` | Remove a file |
| `GET` | `/health` | Status — no auth required |

```bash
curl -X POST https://your-domain/upload \
  -H "X-Publish-Token: your-secret" \
  -F "file=@book.epub"
```

## KOReader plugin

Copy `koplugin/` to `/mnt/us/koreader/plugins/publish.koplugin/` on the Kindle.

Configure in KOReader: **≡ → Tools → Publish**

| Setting | Value |
|---|---|
| Server URL | `https://your-domain.com` (no trailing slash) |
| API token | Your `PUBLISH_TOKEN` |
| Inbox folder | `/mnt/us/books` or any KOReader-visible folder |

## Cloudflare Tunnel

Add to your existing tunnel's config file:

```yaml
- hostname: your-subdomain.yourdomain.com
  service: http://127.0.0.1:8765
```

Then add a CNAME DNS record pointing to your tunnel UUID.

## Structure

```
publish/
├── server.py              App + embedded UI
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── .github/workflows/
│   └── docker-publish.yml  Auto-builds to ghcr.io on push
└── koplugin/              KOReader plugin
    ├── _meta.lua
    └── main.lua
```
