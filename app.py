"""Servidor del laboratorio MP3YT: scraping público + pruebas del formulario real."""
import json
import os
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from urllib.parse import urlsplit
from scraper import scrape_public_page
from mp3yt_browser import begin_setup, engine_status, get_screenshot, run_test

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
        self.send_response(200 if path in {"/", "/api/health"} else 404)
        self.headers_common()
        self.end_headers()

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/health":
            return self.send_json({"ok": True, "target": "https://mp3yt.is/",
                "mode": "mp3yt-public-browser-automation", "browser": engine_status()})
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
        if path.startswith("/api/screenshot/"):
            token = path.removeprefix("/api/screenshot/")
            file = get_screenshot(token)
            if file is None:
                return self.send_json({"ok": False, "error":"Captura no disponible o caducada."}, 404)
            return self.send_bytes(file.read_bytes(), "image/png")
        if path in {"/", "/index.html"}:
            try:
                return self.send_bytes((BASE / "public/index.html").read_bytes(), "text/html; charset=utf-8")
            except FileNotFoundError:
                return self.send_bytes(b"Missing asset", "text/plain; charset=utf-8", 500)
        return self.send_bytes(b"Not found", "text/plain; charset=utf-8", 404)

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
            data = run_test(payload.get("url", ""), payload.get("format", ""))
            return self.send_json({"ok": True, "data": data})
        except ValueError as exc:
            return self.send_json({"ok": False, "error": str(exc)}, 400)
        except RuntimeError as exc:
            error = str(exc)
            status = 503 if "aún no está listo" in error else 429 if "otra prueba" in error else 502
            return self.send_json({"ok": False, "error": error}, status)
        except Exception as exc:
            print("[mp3yt] Error inesperado en prueba: " + str(exc)[:300], flush=True)
            return self.send_json({"ok": False, "error": "Error interno al ejecutar el navegador.",
                "type": type(exc).__name__}, 500)

if __name__ == "__main__":
    begin_setup()
    port = int(os.environ.get("PORT", "10000"))
    print(f"Serving MP3YT browser lab on 0.0.0.0:{port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
