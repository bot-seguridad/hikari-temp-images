"""Automatización de la interfaz pública mp3yt.is; sin yt-dlp ni APIs privadas."""
import importlib
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from tempfile import gettempdir
from urllib.parse import parse_qs, urlsplit

SITE = "https://mp3yt.is/en3Pt"
MAX_RESULT_TTL = 600
PATH = Path(gettempdir()) / "bepo-mp3yt-browser"
PATH.mkdir(parents=True, exist_ok=True)
install_lock = threading.Lock()
browser_lock = threading.Lock()
_state = {"status": "pending", "error": ""}
_ready = False
screenshots = {}
screens_lock = threading.Lock()


def engine_status():
    with install_lock:
        return dict(_state)


def validate_url(value):
    if not isinstance(value, str) or len(value) > 1500:
        raise ValueError("Ingresá un enlace individual de YouTube.")
    try:
        p = urlsplit(value.strip())
    except ValueError as exc:
        raise ValueError("El enlace no es válido.") from exc
    if p.scheme != "https" or p.username or p.password:
        raise ValueError("Solo se permiten enlaces HTTPS de YouTube.")
    try:
        port = p.port
    except ValueError as exc:
        raise ValueError("Puerto inválido.") from exc
    if port is not None:
        raise ValueError("No se permiten puertos personalizados.")
    hostname = (p.hostname or "").lower()
    video_id = ""
    if hostname in {"www.youtube.com", "youtube.com", "m.youtube.com", "music.youtube.com"}:
        if p.path in {"/watch", "/watch/"}:
            video_id = parse_qs(p.query).get("v", [""])[0]
        else:
            match = re.fullmatch(r"/(?:shorts|live|embed)/([A-Za-z0-9_-]{11})/?", p.path)
            if match:
                video_id = match.group(1)
    elif hostname in {"youtu.be", "www.youtu.be"}:
        video_id = p.path.strip("/")
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise ValueError("Usá un enlace de video YouTube, Shorts o youtu.be (no playlists).")
    return "https://www.youtube.com/watch?v=" + video_id


def prepare_browser():
    with install_lock:
        if _state["status"] in {"ready", "installing"}:
            return
        _state["status"] = "installing"
        _state["error"] = ""
    try:
        try:
            importlib.import_module("playwright.sync_api")
        except (ImportError, ModuleNotFoundError):
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--quiet", "--no-input",
                 "--disable-pip-version-check", "playwright==1.56.0"],
                text=True, capture_output=True, timeout=160)
            if result.returncode:
                raise RuntimeError("No se pudo instalar Playwright: " + (result.stderr or "")[-650:])
            importlib.invalidate_caches()
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = None
            try:
                browser = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            except Exception:
                result = subprocess.run(
                    [sys.executable, "-m", "playwright", "install", "chromium-headless-shell"],
                    text=True, capture_output=True, timeout=230)
                if result.returncode:
                    raise RuntimeError("No se pudo instalar Chromium: " + (result.stderr or result.stdout)[-900:])
                browser = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            finally:
                if browser:
                    browser.close()
        with install_lock:
            _state["status"] = "ready"
        print("[mp3yt] Navegador Playwright + Chromium listo", flush=True)
    except Exception as exc:
        with install_lock:
            _state["status"] = "error"
            _state["error"] = str(exc)[:700]
        print("[mp3yt] Falló la preparación del navegador: " + str(exc)[:700], flush=True)


def begin_setup():
    threading.Thread(target=prepare_browser, daemon=True, name="mp3yt-browser-setup").start()


def record_screenshot(data):
    cleanup()
    token = uuid.uuid4().hex
    file = PATH / (token + ".png")
    file.write_bytes(data)
    with screens_lock:
        screenshots[token] = {"path": file, "expiry": time.time() + MAX_RESULT_TTL}
    return "/api/screenshot/" + token


def cleanup():
    now = time.time()
    with screens_lock:
        expired = [key for key, value in screenshots.items() if value["expiry"] < now]
        for token in expired:
            value = screenshots.pop(token)
            value["path"].unlink(missing_ok=True)


def get_screenshot(token):
    if not re.fullmatch(r"[a-f0-9]{32}", token):
        return None
    cleanup()
    with screens_lock:
        info = screenshots.get(token)
        if not info or not info["path"].is_file():
            return None
        return info["path"]


def _summary(page):
    return page.evaluate("""() => ({
        title: document.title,
        heading: [...document.querySelectorAll('h1,h2,h3')].map(el => el.innerText.trim()).filter(Boolean).slice(0,12),
        buttons: [...document.querySelectorAll('button,[role="button"],input[type=submit]')].map(el => (el.innerText || el.value || el.getAttribute('aria-label') || '').trim()).filter(Boolean).slice(0,30),
        buttonStates: [...document.querySelectorAll('button,[role="button"],input[type=submit]')].filter(e=>e.getClientRects().length).slice(0,20).map(el=>({text:(el.innerText||el.value||el.getAttribute('aria-label')||'').trim().slice(0,110), disabled:!!el.disabled,ariaDisabled:el.getAttribute('aria-disabled')})),
        inputs: [...document.querySelectorAll('input')].filter(el => el.type !== 'hidden').map(el => ({type:el.type,id:el.id,placeholder:el.placeholder})).slice(0,15),
        visibleText: (document.body?.innerText || '').slice(0,1900),
        downloads: [...document.querySelectorAll('a[href]')].filter(el => /download|\\.mp[34](?:$|[?#])/i.test((el.innerText || '')+' '+el.href)).slice(0,10).map(el => ({text:(el.innerText||'').trim().slice(0,90),href:el.href}))
    })""")


def _first_visible(locators):
    for loc in locators:
        try:
            if loc.count() and loc.first.is_visible(timeout=800):
                return loc.first
        except Exception:
            continue
    return None


def _download_controls(page):
    """Estado de botones visibles, sin hacer clic en elementos de descarga."""
    return page.evaluate("""() =>
      [...document.querySelectorAll('button,a,[role="button"],input[type="submit"]')]
        .filter(e => e.getClientRects().length)
        .slice(0, 65)
        .map(e => {
            const label = (e.innerText || e.value || e.getAttribute('aria-label') || '').trim().replace(/\\s+/g,' ').slice(0,110);
            const parent = (e.parentElement?.innerText || '').trim().replace(/\\s+/g,' ').slice(0,180);
            return {
                label, tag: e.tagName.toLowerCase(),
                enabled: !(e.disabled || e.getAttribute('aria-disabled')==='true'),
                has_href: !!e.getAttribute('href'),
                parent_text:parent
            };
        }).filter(x => x.label && /download|prepar|convert|ready|failed|error|search|retry|descarg|esper/i.test(x.label+' '+x.parent_text)).slice(0,22)
    """)


def _target_state(controls, fmt):
    """No confundir un clic o un botón de otro formato con una conversión lista."""
    upper = fmt.upper()
    ready = [c for c in controls if re.search(r"\b(?:download|descargar|baixar)\s+" + upper + r"\b", c["label"], re.I) and c["enabled"]]
    if ready:
        return {"status": "ready_to_download", "detail": "MP3YT muestra un control habilitado de descarga " + upper + ". No se descargó ni validó el archivo.", "control": ready[0]}
    disabled_ready = [c for c in controls if re.search(r"\b(?:download|descargar|baixar)\s+" + upper + r"\b", c["label"], re.I)]
    if disabled_ready:
        return {"status": "download_disabled", "detail": "El botón de " + upper + " sigue deshabilitado.", "control": disabled_ready[0]}
    preparing = [c for c in controls if re.search(r"prepar|converting|processing|generating|please wait|cargando|procesando", c["label"], re.I)]
    if preparing:
        return {"status": "preparing", "detail": "MP3YT sigue mostrando «" + preparing[0]["label"] + "». La preparación no se confirmó.", "control": preparing[0]}
    return {"status": "not_available", "detail": "Aún no apareció un botón de descarga habilitado para " + upper + "."}


def _wait_for_format(page, fmt, deadline_seconds=45):
    """Esperar la opción de descarga sin entrar en bucles infinitos ni descargar archivos."""
    started = time.monotonic()
    observations = []
    last_status = None
    state = {"status": "not_available", "detail": "Sin controles detectados."}
    controls = []
    while time.monotonic() - started < deadline_seconds:
        controls = _download_controls(page)
        state = _target_state(controls, fmt)
        if state["status"] != last_status:
            observations.append({
                "at_seconds": round(time.monotonic() - started,1),
                "status": state["status"],
                "detail": state["detail"],
                "controls": [{"label":c["label"],"enabled":c["enabled"]} for c in controls[:8]],
            })
            last_status = state["status"]
        if state["status"] == "ready_to_download":
            break
        page.wait_for_timeout(1500)
    elapsed = round(time.monotonic() - started,1)
    if state["status"] != "ready_to_download":
        state = {**state, "status": "timeout_preparing" if state["status"] in {"preparing", "download_disabled"} else "not_ready_timeout",
            "detail": state["detail"] + " Se agotó la espera de " + str(deadline_seconds) + " segundos."}
    return state, observations, elapsed, controls


def run_test(raw_url, fmt):
    if fmt not in {"mp3", "mp4"}:
        raise ValueError("El formato debe ser MP3 o MP4.")
    url = validate_url(raw_url)
    if engine_status()["status"] != "ready":
        raise RuntimeError("El navegador aún no está listo: " + str(engine_status()))
    if not browser_lock.acquire(blocking=False):
        raise RuntimeError("Hay otra prueba en marcha. Intentá nuevamente al terminar.")
    started = time.monotonic()
    events = []
    network_errors = []
    console_errors = []
    steps = []
    final = {}
    screen_url = None
    verdict = {"status": "not_started", "detail": "No se empezó la prueba."}
    observations = []
    final_controls = []
    wait_seconds = 0
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                accept_downloads=False,
                locale="en-US",
                service_workers="block",
            )
            page = context.new_page()
            page.set_default_timeout(6500)
            page.on("response", lambda res: events.append({
                "status": res.status,
                "path": urlsplit(res.url).path[:130],
                "host": urlsplit(res.url).hostname,
            }) if len(events) < 65 else None)
            page.on("requestfailed", lambda req: network_errors.append({
                "host": urlsplit(req.url).hostname,
                "path": urlsplit(req.url).path[:110],
                "error": str(req.failure or "Solicitud fallida")[:180],
            }) if len(network_errors) < 20 else None)
            page.on("pageerror", lambda exc: console_errors.append(str(exc)[:260]) if len(console_errors) < 12 else None)
            try:
                nav = page.goto(SITE, wait_until="domcontentloaded", timeout=30000)
                steps.append({"step":"open","ok":bool(nav and nav.status < 400), "http":nav.status if nav else None})
                page.wait_for_timeout(900)
                if nav is None or nav.status >= 400:
                    raise RuntimeError("MP3YT rechazó el acceso: HTTP " + str(nav.status if nav else "sin respuesta"))
                form = _first_visible([
                    page.get_by_placeholder(re.compile("YouTube|paste|search", re.I)),
                    page.locator("main input[type=text], main input[type=search]"),
                    page.locator("input[type=search], input[type=text], input:not([type])"),
                ])
                if form is None:
                    steps.append({"step":"find_form","ok":False,"detail":"No aparece un campo para pegar el enlace."})
                    verdict = {"status": "form_not_found", "detail": "MP3YT abrió pero no mostró el formulario para pegar el enlace."}
                    final = _summary(page)
                else:
                    form.fill(url, timeout=6500)
                    steps.append({"step":"paste_url","ok":True})
                    submit = _first_visible([
                        page.get_by_role("button", name=re.compile(r"^(search|convert|buscar|convertir)$", re.I)),
                        page.locator("button[type=submit], input[type=submit]"),
                    ])
                    if submit is not None:
                        submit.click(timeout=6500)
                    else:
                        form.press("Enter", timeout=6500)
                    steps.append({"step":"submit_search","ok":True})
                    page.wait_for_timeout(1800)
                    steps.append({
                        "step": "format_requested",
                        "ok": True,
                        "detail": "Se solicitó " + fmt.upper() + "; MP3YT puede prepararlo automáticamente. No se asumió que hubiera una pestaña ni un segundo botón «Convert»."
                    })
                    verdict, observations, wait_seconds, final_controls = _wait_for_format(page, fmt, deadline_seconds=45)
                    steps.append({
                        "step": "wait_for_download_option",
                        "ok": verdict["status"] == "ready_to_download",
                        "detail": verdict["detail"],
                        "seconds": wait_seconds,
                    })
                    steps.append({
                        "step": "file_download",
                        "ok": None,
                        "attempted": False,
                        "detail": "No se inició una descarga. Se comprobó únicamente la disponibilidad del botón de " + fmt.upper() + ".",
                    })
                    final = _summary(page)
                screen_url = record_screenshot(page.screenshot(type="png", full_page=False, timeout=10000))
            except Exception as exc:
                verdict = {"status": "browser_error", "detail": str(exc)[:320]}
                steps.append({"step":"browser_error","ok":False,"detail":str(exc)[:320]})
                try:
                    final = _summary(page)
                    screen_url = record_screenshot(page.screenshot(type="png", full_page=False, timeout=10000))
                except Exception:
                    pass
            finally:
                context.close()
                browser.close()
        return {
            "target_site": SITE, "video_url":url, "format":fmt,
            "method":"Playwright browser UI automation — not yt-dlp",
            "steps":steps, "page":final, "http_responses":events,
            "network_errors":network_errors, "page_errors":console_errors,
            "verdict":verdict, "format_observations":observations, "final_controls":final_controls,
            "wait_seconds":wait_seconds, "download_verified":False,
            "screenshot_url":screen_url, "elapsed_seconds":round(time.monotonic()-started,1),
            "note":"Ready_to_download solo significa que MP3YT mostró un botón de descarga habilitado. No se descargó ni validó el archivo. No se evaden bloqueos ni APIs privadas."
        }
    finally:
        browser_lock.release()
