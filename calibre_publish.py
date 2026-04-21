#!/usr/bin/env python3
"""
calibre_publish.py
------------------
Calibre "Send to device" integration for Publish.

Usage — two ways to trigger:

1. MANUAL (Calibre's post-send script):
   In Calibre: Preferences → Sending books to devices
   → "Run program after sending" → point to this script
   Calibre calls it as: calibre_publish.py <format> <book_path> <title> <author>

2. COMMAND LINE (direct send):
   python calibre_publish.py path/to/book.epub
   python calibre_publish.py path/to/book.epub path/to/other.epub ...

Config: edit the three variables below, or set env vars.
"""

import os
import sys
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────────
# Override with env vars: PUBLISH_URL, PUBLISH_TOKEN

PUBLISH_URL   = os.environ.get("PUBLISH_URL",   "https://mini-publish.silentmail.org")
PUBLISH_TOKEN = os.environ.get("PUBLISH_TOKEN", "")
ALLOWED_EXT   = {".epub", ".mobi", ".pdf", ".fb2", ".azw3", ".lit"}

# ────────────────────────────────────────────────────────────────────────────

import urllib.request
import urllib.error
import mimetypes
import uuid


def upload(filepath: str) -> bool:
    path = Path(filepath)
    if not path.is_file():
        print(f"[Publish] Not found: {filepath}", file=sys.stderr)
        return False
    if path.suffix.lower() not in ALLOWED_EXT:
        print(f"[Publish] Skipping unsupported format: {path.name}", file=sys.stderr)
        return False

    url = f"{PUBLISH_URL.rstrip('/')}/upload"
    boundary = uuid.uuid4().hex

    with open(path, "rb") as f:
        file_data = f.read()

    # Build multipart body manually (no external deps)
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        f"Content-Type: {mimetypes.guess_type(path.name)[0] or 'application/octet-stream'}\r\n"
        f"\r\n"
    ).encode() + file_data + f"\r\n--{boundary}--\r\n".encode()

    headers = {
        "Content-Type":    f"multipart/form-data; boundary={boundary}",
        "Content-Length":  str(len(body)),
        "X-Publish-Token": PUBLISH_TOKEN,
    }

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print(f"[Publish] ✓ Queued: {path.name}  ({resp.status})")
            return True
    except urllib.error.HTTPError as e:
        print(f"[Publish] ✗ Failed {path.name}: HTTP {e.code} {e.reason}", file=sys.stderr)
        return False
    except urllib.error.URLError as e:
        print(f"[Publish] ✗ Network error for {path.name}: {e.reason}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[Publish] ✗ Unexpected error for {path.name}: {e}", file=sys.stderr)
        return False


def main():
    args = sys.argv[1:]

    if not args:
        print("Usage: calibre_publish.py <file> [file ...]")
        print("       calibre_publish.py <format> <book_path> <title> <author>  (Calibre post-send)")
        sys.exit(1)

    # Calibre calls post-send scripts as: script <format> <path> <title> <author>
    # Detect this by checking if argv[1] looks like a format string (e.g. "EPUB", "MOBI")
    if len(args) >= 2 and args[0].upper() in {"EPUB","MOBI","PDF","AZW3","FB2","LIT"} and Path(args[1]).is_file():
        fmt, path, *rest = args
        print(f"[Publish] Calibre post-send: {fmt} → {Path(path).name}")
        success = upload(path)
        sys.exit(0 if success else 1)

    # Direct file list mode
    ok = failed = 0
    for f in args:
        if upload(f):
            ok += 1
        else:
            failed += 1

    print(f"[Publish] Done: {ok} queued, {failed} failed")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
