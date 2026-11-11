"""Acceso rápido a CNV basado en cnv.mjs aportado por el usuario.
Solicita una URL con el perfil MP3YT exclusivamente; no baja el archivo.
No emplea yt-dlp, Playwright, API keys hardcoded ni evasión de CAPTCHA.
"""
import json
import re
import threading
import time
from urllib.parse import urlencode, urlsplit, parse_qs
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

BASE_URL = "https://cnv.cx"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")
# Un solo perfil: mp3yt fue el que respondió en las pruebas del usuario.
# Ambos perfiles previos consultaban el MISMO servidor cnv.cx.
PROFILE = {"name": "mp3yt", "origin": "https://mp3yt.is", "referer": "https://mp3yt.is/"}
SLOTS = threading.BoundedSemaphore(value=2)
QUEUE_SLOTS = threading.BoundedSemaphore(value=8)


def validate_youtube(value):
    if not isinstance(value, str) or len(value) > 1500:
        raise ValueError("Ingresá una URL de YouTube.")
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError as exc:
        raise ValueError("URL inválida.") from exc
    if parsed.scheme != "https" or parsed.username or parsed.password or port is not None:
        raise ValueError("Solo se aceptan URLs HTTPS de YouTube sin puertos personalizados.")
    host = (parsed.hostname or "").lower()
    video_id = ""
    if host in {"www.youtube.com", "youtube.com", "m.youtube.com", "music.youtube.com"}:
        if parsed.path in {"/watch", "/watch/"}:
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        else:
            match = re.fullmatch(r"/(?:shorts|embed|live)/([A-Za-z0-9_-]{11})/?", parsed.path)
            if match:
                video_id = match.group(1)
    elif host in {"youtu.be", "www.youtu.be"}:
        video_id = parsed.path.strip("/")
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise ValueError("Ingresá un enlace individual de YouTube, Shorts o youtu.be.")
    return video_id, "https://www.youtube.com/watch?v=" + video_id


def _headers(profile):
    return {
        "Accept": "*/*",
        "Origin": profile["origin"],
        "Referer": profile["referer"],
        "User-Agent": USER_AGENT,
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }


def _request_json(url, profile, method="GET", body=None, key=None, timeout=12):
    headers = _headers(profile)
    if method == "POST":
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        headers["key"] = key
    request = Request(url, data=body, method=method, headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            code = response.status
            raw = response.read(256_001)
            if len(raw) > 256_000:
                raise RuntimeError("Respuesta JSON excesivamente grande.")
    except HTTPError as exc:
        code = exc.code
        raw = exc.read(4000)
    except (URLError, TimeoutError) as exc:
        raise RuntimeError("Fallo de red o timeout: " + str(exc)[:250]) from exc

    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except (UnicodeError, ValueError) as exc:
        # Mostrar HTTP 403/429/5xx aunque el proveedor devuelva una página HTML.
        if code < 200 or code >= 300:
            raise RuntimeError("HTTP " + str(code) + " (el proveedor no devolvió JSON)") from exc
        raise RuntimeError("Respuesta no JSON, HTTP " + str(code)) from exc

    if code < 200 or code >= 300:
        detail = data.get("errorMsg") or data.get("message") or data.get("status") if isinstance(data, dict) else None
        raise RuntimeError("HTTP " + str(code) + (": " + str(detail)[:160] if detail else ""))

    if not isinstance(data, dict):
        raise RuntimeError("El servidor devolvió un JSON inesperado.")
    return data, code


def _try_provider(profile, video_id, payload, started):
    name = profile["name"]
    start = time.monotonic()
    try:
        key_url = BASE_URL + "/v2/sanity/key?" + urlencode({"id": video_id})
        key_data, key_code = _request_json(key_url, profile, timeout=10)
        key = key_data.get("key")
        if not isinstance(key, str) or not key:
            raise RuntimeError("No se obtuvo una clave temporal válida.")
        conversion_data, convert_code = _request_json(
            BASE_URL + "/v2/converter", profile,
            method="POST", body=urlencode(payload).encode("utf-8"), key=key, timeout=32
        )
        link = conversion_data.get("url")
        if conversion_data.get("error") is True or not isinstance(link, str):
            reason = conversion_data.get("errorMsg") or conversion_data.get("message") or conversion_data.get("status")
            raise RuntimeError(str(reason or "La respuesta no incluyó URL de descarga.")[:220])
        parsed = urlsplit(link)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise RuntimeError("CNV devolvió una URL de descarga no HTTPS.")
        return {
            "ok": True,
            "provider": name,
            "url": link,
            "download_host": parsed.hostname,
            "filename": str(conversion_data.get("filename") or "")[:200],
            "api_status": str(conversion_data.get("status") or "")[:100],
            "http_key": key_code,
            "http_convert": convert_code,
            "provider_elapsed_ms": round((time.monotonic()-start)*1000),
        }
    except Exception as exc:
        return {
            "ok": False, "provider": name,
            "error": str(exc)[:450],
            "provider_elapsed_ms": round((time.monotonic()-start)*1000),
        }


def fast_convert(url, fmt, audio_bitrate="320", video_quality="720"):
    """Ask CNV once using MP3YT profile; return the URL, not the file."""
    video_id, canonical = validate_youtube(url)
    if fmt not in {"mp3", "mp4"}:
        raise ValueError("Formato inválido, elegí MP3 o MP4.")
    if str(audio_bitrate) not in {"128", "192", "256", "320"}:
        raise ValueError("Bitrate de MP3 inválido.")
    if str(video_quality) not in {"360", "480", "720", "1080"}:
        raise ValueError("Calidad de MP4 inválida.")

    # No abrir cientos de threads si todos pulsan a la vez.
    if not QUEUE_SLOTS.acquire(blocking=False):
        raise RuntimeError("Demasiadas consultas en espera. Reintentá en unos segundos.")
    acquired = False
    try:
        # Una espera breve es preferible a rechazar a todos inmediatamente.
        acquired = SLOTS.acquire(timeout=6)
        if not acquired:
            raise RuntimeError("Servidor ocupado: las dos consultas CNV en curso aún no terminaron.")
        started = time.monotonic()
        payload = {
            "link": canonical,
            "format": fmt,
            "audioBitrate": str(audio_bitrate),
            "videoQuality": str(video_quality),
            "filenameStyle": "pretty",
            "vCodec": "h264",
        }
        result = _try_provider(PROFILE, video_id, payload, started)
        # Un reintento corto solo ante error temporal del servicio.
        # En HTTP 403/429 se respeta la denegación: no se intenta saltar bloqueos.
        attempts = [result]
        if not result["ok"]:
            problem = result.get("error", "")
            transient = any(x in problem for x in (
                "HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504",
                "Timeout", "timed out", "Fallo de red"
            ))
            if transient:
                time.sleep(1)
                result = _try_provider(PROFILE, video_id, payload, started)
                attempts.append(result)
        if not result["ok"]:
            reason = result.get("error") or "Fallo desconocido"
            if "HTTP 429" in reason:
                reason += " (CNV limitó las solicitudes; reducir frecuencia)"
            elif "HTTP 403" in reason:
                reason += " (CNV denegó el acceso)"
            raise RuntimeError("CNV con MP3YT falló tras " + str(len(attempts)) +
                               " intento(s): " + reason[:600])
        return {
            "ok": True,
            "format": fmt,
            "quality": payload["audioBitrate"] + "kbps" if fmt == "mp3" else payload["videoQuality"] + "p",
            "video_url": canonical,
            "provider": "mp3yt",
            "url": result["url"],
            "download_host": result["download_host"],
            "filename": result["filename"],
            "api_status": result["api_status"],
            "key_http_status": result["http_key"],
            "convert_http_status": result["http_convert"],
            "attempts": len(attempts),
            "elapsed_ms": round((time.monotonic()-started)*1000),
            "note": "CNV devolvió una URL vía MP3YT. No se descargó ni verificó el archivo."
        }
    finally:
        if acquired:
            SLOTS.release()
        QUEUE_SLOTS.release()
