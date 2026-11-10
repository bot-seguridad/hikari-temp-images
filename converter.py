"""Conversor de prueba: solo enlaces de video YouTube, límites y diagnósticos."""
import importlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

MAX_SIZE = 35 * 1024 * 1024
MAX_DURATION = 600
FILE_TTL = 10 * 60
ROOT = Path(tempfile.gettempdir()) / "bepo-media-lab"
ROOT.mkdir(parents=True, exist_ok=True)

state = {"status": "pending", "error": ""}
state_lock = threading.Lock()
job_lock = threading.Lock()
saved = {}
saved_lock = threading.Lock()
ffmpeg_bin = None

def normalize_youtube_url(url):
    if not isinstance(url, str) or len(url) > 1500:
        raise ValueError("Ingresá una URL válida de YouTube.")
    p = urlsplit(url.strip())
    if p.scheme != "https" or p.username or p.password or p.port is not None:
        raise ValueError("Solo se aceptan enlaces HTTPS de YouTube.")
    host = (p.hostname or "").lower()
    ident = ""
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
        if p.path in {"/watch", "/watch/"}:
            ident = parse_qs(p.query).get("v", [""])[0]
        else:
            match = re.fullmatch(r"/(?:shorts|live|embed)/([A-Za-z0-9_-]{11})/?", p.path)
            if match:
                ident = match.group(1)
    elif host in {"youtu.be", "www.youtu.be"}:
        ident = p.path.strip("/")
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", ident):
        raise ValueError("Se acepta un enlace individual de YouTube (watch, shorts, live o youtu.be). No playlists.")
    return "https://www.youtube.com/watch?v=" + ident

def engine_status():
    with state_lock:
        return dict(state)

def ensure_tools():
    global ffmpeg_bin
    with state_lock:
        if state["status"] in ("ready", "installing"):
            return state["status"] == "ready"
        state["status"] = "installing"
        state["error"] = ""
    try:
        try:
            importlib.import_module("yt_dlp")
            imageio = importlib.import_module("imageio_ffmpeg")
        except ImportError:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
                 "--no-input", "--quiet", "yt-dlp[default]", "imageio-ffmpeg"],
                capture_output=True, text=True, timeout=160,
            )
            if result.returncode != 0:
                raise RuntimeError("No se pudieron instalar las dependencias: " + (result.stderr or result.stdout)[-850:])
            importlib.invalidate_caches()
            imageio = importlib.import_module("imageio_ffmpeg")
            importlib.import_module("yt_dlp")
        ffmpeg_bin = imageio.get_ffmpeg_exe()
        if not Path(ffmpeg_bin).is_file():
            raise RuntimeError("No se encontró el ejecutable FFmpeg.")
        with state_lock:
            state["status"] = "ready"
        return True
    except Exception as exc:
        with state_lock:
            state["status"] = "error"
            state["error"] = str(exc)[:600]
        return False

def begin_setup():
    threading.Thread(target=ensure_tools, daemon=True, name="media-engine-setup").start()

def cleanup():
    now = time.time()
    with saved_lock:
        expired = [(k,v) for k,v in saved.items() if v["expiry"] < now]
        for token, v in expired:
            saved.pop(token, None)
            shutil.rmtree(v["directory"], ignore_errors=True)

def get_download(token):
    if not re.fullmatch(r"[a-f0-9]{32}", token):
        return None
    cleanup()
    with saved_lock:
        item = saved.get(token)
        if not item or not item["path"].is_file():
            return None
        return dict(item)

def run_conversion(url, fmt):
    if fmt not in {"mp3", "mp4"}:
        raise ValueError("Elegí MP3 o MP4.")
    canonical = normalize_youtube_url(url)
    if engine_status()["status"] != "ready":
        if engine_status()["status"] == "pending":
            begin_setup()
        raise RuntimeError("El motor todavía no está preparado: " + str(engine_status()))
    if not job_lock.acquire(blocking=False):
        raise RuntimeError("Ya hay una conversión en curso. Probá nuevamente después.")
    folder = None
    try:
        cleanup()
        token = uuid.uuid4().hex
        folder = ROOT / token
        folder.mkdir(parents=True, exist_ok=False)
        cmd = [
            sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-playlist",
            "--no-progress", "--no-part", "--socket-timeout", "12",
            "--retries", "1", "--fragment-retries", "1", "--extractor-retries", "1",
            "--max-filesize", "35M", "--match-filter", "duration <= 600",
            "--ffmpeg-location", ffmpeg_bin,
            "--output", str(folder / "media.%(ext)s"),
        ]
        if fmt == "mp3":
            cmd += ["-f", "bestaudio[ext=m4a]/bestaudio", "--extract-audio", "--audio-format", "mp3", "--audio-quality", "5"]
        else:
            cmd += [
                "-f", "b[ext=mp4][height<=480]/bv*[ext=mp4][height<=480]+ba[ext=m4a]/b[ext=mp4]/b[height<=480]",
                "--merge-output-format", "mp4", "--remux-video", "mp4"
            ]
        cmd.append(canonical)
        started = time.monotonic()
        try:
            result = subprocess.run(cmd, text=True, capture_output=True, timeout=115, cwd=str(folder))
            log = (result.stdout or "") + "\n" + (result.stderr or "")
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Tiempo agotado (115 s). El servidor no terminó la operación.") from exc

        # No se muestran URLs firmadas ni tokens de CDN en el diagnóstico público.
        log = re.sub(r"https?://[^\s<>\"']+", "[URL]", log)[-4500:]
        if result.returncode:
            raise RuntimeError("yt-dlp devolvió código " + str(result.returncode) + ": " + log[-2500:])
        candidates = [x for x in folder.iterdir() if x.is_file() and x.suffix.lower() == "." + fmt]
        if not candidates:
            raise RuntimeError("El proceso terminó, pero no generó un archivo " + fmt.upper() + ". " + log[-1800:])
        candidate = max(candidates, key=lambda x: x.stat().st_size)
        size = candidate.stat().st_size
        if size <= 0 or size > MAX_SIZE:
            raise RuntimeError("El archivo está vacío o supera el límite de 35 MB.")
        with saved_lock:
            saved[token] = {
                "path": candidate, "directory": folder, "expiry": time.time() + FILE_TTL,
                "type": "audio/mpeg" if fmt == "mp3" else "video/mp4", "name": "youtube-prueba." + fmt
            }
        return {
            "format": fmt, "bytes": size, "seconds": round(time.monotonic() - started, 1),
            "download_url": "/api/download/" + token,
            "video_url": canonical, "engine": "yt-dlp + FFmpeg (no MP3YT)",
            "log": log[-3000:], "expires_minutes": 10
        }
    finally:
        with saved_lock:
            keep = bool(folder and any(v["directory"] == folder for v in saved.values()))
        if folder and not keep:
            shutil.rmtree(folder, ignore_errors=True)
        job_lock.release()
