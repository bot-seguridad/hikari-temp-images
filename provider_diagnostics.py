"""Diagnóstico controlado de los dos perfiles CNV del cnv.mjs original.

El perfil MP3YT y el perfil Y2meta consultan ambos a cnv.cx.
Se evalúan por separado y se informa qué etapa falla sin guardar enlaces
firmados, claves temporales o archivos. No se evaden 403/429.
"""
import re
import threading
import time
from urllib.parse import urlencode, urlsplit

from fast_cnv import BASE_URL, _request_json, validate_youtube

PROFILES = (
    {"name": "mp3yt", "origin": "https://mp3yt.is", "referer": "https://mp3yt.is/"},
    {"name": "y2meta-uk", "origin": "https://iframe.y2meta-uk.com",
     "referer": "https://iframe.y2meta-uk.com/"},
)
TEST_LOCK = threading.Lock()
COOLDOWN_SECONDS = 12
LAST_TEST = {"time": 0.0}


def _step(name, ok, started, **details):
    return {"step": name, "ok": ok,
            "elapsed_ms": int((time.monotonic() - started) * 1000), **details}


def _probe(profile, video_id, payload):
    started = time.monotonic()
    outcome = {
        "profile": profile["name"],
        "origin": profile["origin"],
        "referer": profile["referer"],
        "target_api": BASE_URL,
        "status": "unknown",
        "key_http_status": None,
        "converter_http_status": None,
        "steps": [],
        "download_verified": False,
        "download_url_returned": False,
    }
    phase_started = time.monotonic()
    try:
        key_data, code = _request_json(
            BASE_URL + "/v2/sanity/key?" + urlencode({"id": video_id}),
            profile, timeout=10
        )
        outcome["key_http_status"] = code
        key = key_data.get("key")
        if not isinstance(key, str) or not key:
            raise ValueError("CNV no devolvió una clave temporal válida.")
        outcome["steps"].append(_step("get_key", True, phase_started, http_status=code))
    except Exception as exc:
        detail = str(exc)[:400]
        match = re.search(r"\bHTTP\s+(\d{3})\b", detail)
        if match:
            outcome["key_http_status"] = int(match.group(1))
        outcome.update(status="failed_at_key", error=detail)
        outcome["steps"].append(_step(
            "get_key", False, phase_started,
            http_status=outcome["key_http_status"], error=detail))
        outcome["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        return outcome

    phase_started = time.monotonic()
    try:
        data, code = _request_json(
            BASE_URL + "/v2/converter", profile, method="POST",
            body=urlencode(payload).encode("utf-8"), key=key, timeout=24
        )
        outcome["converter_http_status"] = code
        link = data.get("url")
        if data.get("error") is True or not isinstance(link, str) or not link:
            message = data.get("errorMsg") or data.get("message") or data.get("status")
            raise ValueError(str(message or "CNV no devolvió una URL.")[:220])
        parsed = urlsplit(link)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("CNV devolvió una URL de descarga no HTTPS.")
        outcome.update(
            status="url_received", download_url_returned=True,
            download_host=parsed.hostname[:150],
            api_status=str(data.get("status") or "")[:90],
            filename=str(data.get("filename") or "")[:180],
        )
        outcome["steps"].append(_step("convert", True, phase_started, http_status=code))
    except Exception as exc:
        detail = str(exc)[:400]
        match = re.search(r"\bHTTP\s+(\d{3})\b", detail)
        if match:
            outcome["converter_http_status"] = int(match.group(1))
        outcome.update(status="failed_at_converter", error=detail)
        outcome["steps"].append(_step(
            "convert", False, phase_started,
            http_status=outcome["converter_http_status"], error=detail))
    outcome["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    return outcome


def compare_profiles(raw_url, fmt, audio_bitrate="320", video_quality="720"):
    video_id, canonical = validate_youtube(raw_url)
    if fmt not in {"mp3", "mp4"}:
        raise ValueError("Formato inválido: MP3 o MP4.")
    if str(audio_bitrate) not in {"128", "192", "256", "320"}:
        raise ValueError("Bitrate MP3 inválido.")
    if str(video_quality) not in {"360", "480", "720", "1080"}:
        raise ValueError("Calidad MP4 inválida.")

    if not TEST_LOCK.acquire(blocking=False):
        raise RuntimeError("Ya hay otra comparación de proveedores en curso.")
    started = time.monotonic()
    try:
        now = time.monotonic()
        remaining = COOLDOWN_SECONDS - (now - LAST_TEST["time"])
        if remaining > 0:
            raise RuntimeError("Prueba comparativa en pausa para evitar sobrecargar CNV. "
                               "Reintentá en " + str(int(remaining + 1)) + " s.")
        LAST_TEST["time"] = now
        payload = {
            "link": canonical,
            "format": fmt,
            "audioBitrate": str(audio_bitrate),
            "videoQuality": str(video_quality),
            "filenameStyle": "pretty",
            "vCodec": "h264",
        }
        results = []
        for idx, profile in enumerate(PROFILES):
            if idx:
                time.sleep(0.7)  # Evitar dos solicitudes simultáneas al mismo CNV.
            # Consume un espacio compartido con el modo rápido.
            from fast_cnv import SLOTS
            if not SLOTS.acquire(timeout=6):
                results.append({
                    "profile": profile["name"], "origin": profile["origin"],
                    "status": "skipped_local_overload",
                    "reason": "El servicio está ocupado; no se contactó CNV.",
                    "steps": [],
                })
                continue
            try:
                result = _probe(profile, video_id, payload)
            finally:
                SLOTS.release()
            results.append(result)
            if result.get("key_http_status") in (403, 429) or result.get("converter_http_status") in (403, 429):
                # No cambiar de origen para intentar eludir una denegación explícita.
                for later in PROFILES[idx+1:]:
                    results.append({
                        "profile": later["name"], "origin": later["origin"],
                        "status": "skipped_upstream_denial",
                        "reason": "La API CNV devolvió 403/429. No se prueban otros perfiles tras la denegación.",
                        "steps": [],
                    })
                break

        worked = [r["profile"] for r in results if r.get("status") == "url_received"]
        failed = [r["profile"] for r in results if r.get("status", "").startswith("failed")]
        skipped = [r["profile"] for r in results if r.get("status", "").startswith("skipped")]
        return {
            "video_url": canonical,
            "format": fmt,
            "mode": "per-profile-diagnostic",
            "api": BASE_URL,
            "profiles_are_distinct_servers": False,
            "profiles": results,
            "working_profiles": worked,
            "failed_profiles": failed,
            "skipped_profiles": skipped,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "conclusion": ("Ambos perfiles responden."
                           if len(worked) == len(PROFILES)
                           else "Uno o más perfiles fallaron o fueron omitidos; inspeccioná cada etapa."),
            "note": "Se prueba la obtención de clave y URL de CNV por perfil, sin descargar el archivo, sin conservar claves ni URLs firmadas. Una prueba puntual no garantiza disponibilidad bajo carga."
        }
    finally:
        TEST_LOCK.release()
