"""Servidor web sin dependencias para el inspector público MP3YT."""
import json
import os
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from urllib.parse import urlsplit
from scraper import scrape_public_page

BASE = Path(__file__).resolve().parent
CACHE_SECONDS = 90
cache = {"at": 0, "result": None}
lock = Lock()

class Handler(BaseHTTPRequestHandler):
    def send_bytes(self, data, mime, status=200):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, obj, status=200):
        self.send_bytes(json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", status)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/health":
            return self.send_json({"ok": True, "target": "https://mp3yt.is/", "mode": "public-html-only"})
        if path == "/api/scrape":
            try:
                with lock:
                    now = time.monotonic()
                    if cache["result"] is None or now - cache["at"] > CACHE_SECONDS:
                        cache["result"] = scrape_public_page()
                        cache["at"] = time.monotonic()
                    result = dict(cache["result"])
                    result["cache_age_seconds"] = round(time.monotonic() - cache["at"])
                return self.send_json({"ok": True, "data": result})
            except Exception as exc:
                return self.send_json({"ok": False, "error": str(exc)}, 502)
        assets = {
            "/": ("public/index.html", "text/html; charset=utf-8"),
            "/index.html": ("public/index.html", "text/html; charset=utf-8"),
            "/style.css": ("public/style.css", "text/css; charset=utf-8"),
            "/app.js": ("public/app.js", "text/javascript; charset=utf-8"),
        }
        if path not in assets:
            return self.send_bytes(b"Not found", "text/plain; charset=utf-8", 404)
        name, mime = assets[path]
        try:
            return self.send_bytes((BASE / name).read_bytes(), mime)
        except FileNotFoundError:
            return self.send_bytes(b"Missing asset", "text/plain; charset=utf-8", 500)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    print(f"Serving MP3YT inspector on 0.0.0.0:{port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
