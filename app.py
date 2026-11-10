"""Servidor HTTP de pruebas MP3YT + laboratorio de conversión."""
import json
import os
import re
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from urllib.parse import urlsplit
from scraper import scrape_public_page
from converter import begin_setup, engine_status, get_download, run_conversion

BASE = Path(__file__).resolve().parent
CACHE_SECONDS = 90
cache = {"at": 0, "result": None}
lock = Lock()

class Handler(BaseHTTPRequestHandler):
    def headers_common(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")

    def send_bytes(self, data, mime, status=200):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.headers_common()
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, obj, status=200):
        self.send_bytes(json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", status)

    def do_HEAD(self):
        path = urlsplit(self.path).path
        if path in {"/", "/api/health"}:
            self.send_response(200)
            self.headers_common()
            self.end_headers()
        else:
            self.send_response(404)
            self.headers_common()
            self.end_headers()

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/health":
            return self.send_json({"ok": True, "target": "https://mp3yt.is/",
                                   "mode": "public-scraper-and-independent-youtube-test",
                                   "conversion_engine": engine_status()})
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
        if path.startswith("/api/download/"):
            token = path.removeprefix("/api/download/")
            file = get_download(token)
            if not file:
                return self.send_json({"ok": False, "error": "Archivo no disponible o caducado."}, 404)
            size = file["path"].stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", file["type"])
            self.send_header("Content-Disposition", 'attachment; filename="' + file["name"] + '"')
            self.send_header("Content-Length", str(size))
            self.headers_common()
            self.end_headers()
            try:
                with file["path"].open("rb") as stream:
                    while chunk := stream.read(64 * 1024):
                        self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        assets = {
            "/": ("public/index.html", "text/html; charset=utf-8"),
            "/index.html": ("public/index.html", "text/html; charset=utf-8"),
        }
        if path not in assets:
            return self.send_bytes(b"Not found", "text/plain; charset=utf-8", 404)
        name, mime = assets[path]
        try:
            return self.send_bytes((BASE / name).read_bytes(), mime)
        except FileNotFoundError:
            return self.send_bytes(b"Missing asset", "text/plain; charset=utf-8", 500)

    def do_POST(self):
        if urlsplit(self.path).path != "/api/convert":
            return self.send_json({"ok": False, "error": "Endpoint desconocido."}, 404)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 2 or length > 3500:
                return self.send_json({"ok": False, "error": "Petición demasiado grande o vacía."}, 413)
            if "application/json" not in self.headers.get("Content-Type", ""):
                return self.send_json({"ok": False, "error": "Solo se acepta JSON."}, 415)
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                return self.send_json({"ok": False, "error": "JSON inválido."}, 400)
            data = run_conversion(payload.get("url", ""), payload.get("format", ""))
            return self.send_json({"ok": True, "data": data})
        except ValueError as exc:
            return self.send_json({"ok": False, "error": str(exc)}, 400)
        except RuntimeError as exc:
            error = str(exc)
            status = 503 if "todavía no está preparado" in error else 429 if "Ya hay una conversión" in error else 502
            return self.send_json({"ok": False, "error": error}, status)
        except Exception as exc:
            return self.send_json({"ok": False, "error": "Error interno al procesar la prueba.", "type": type(exc).__name__}, 500)

if __name__ == "__main__":
    begin_setup()
    port = int(os.environ.get("PORT", "10000"))
    print(f"Serving Bepo media lab on 0.0.0.0:{port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
