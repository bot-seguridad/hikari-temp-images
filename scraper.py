"""Inspección limitada del HTML público de mp3yt.is; no descarga ni convierte."""
from html.parser import HTMLParser
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse

TARGET = "https://mp3yt.is/"
MAX_BYTES = 1_500_000

class Inspector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.description = ""
        self.headings = []
        self.inputs = []
        self.buttons = []
        self.forms = []
        self.links = []
        self.capture = None
        self.parts = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta" and a.get("name", "").lower() == "description":
            self.description = a.get("content", "")
        if tag in ("title", "h1", "h2", "h3", "button") and self.capture is None:
            self.capture = tag
            self.parts = []
        if tag == "form" and len(self.forms) < 20:
            self.forms.append({"method": a.get("method", "GET").upper(), "action": a.get("action", "")})
        if tag == "input" and len(self.inputs) < 30:
            self.inputs.append({"name": a.get("name", ""), "type": a.get("type", "text"), "placeholder": a.get("placeholder", "")})
        if tag == "a" and a.get("href") and len(self.links) < 30:
            href = a["href"]
            if href.startswith("/") or href.startswith("https://") or href.startswith("http://"):
                self.links.append(urljoin(TARGET, href))

    def handle_data(self, data):
        if self.capture:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag != self.capture:
            return
        value = " ".join(" ".join(self.parts).split())[:280]
        if tag == "title":
            self.title = value
        elif tag.startswith("h") and len(self.headings) < 25:
            self.headings.append({"level": tag, "text": value})
        elif tag == "button" and len(self.buttons) < 25:
            self.buttons.append(value)
        self.capture = None
        self.parts = []

def inspect_html(html, final_url=TARGET):
    parser = Inspector()
    parser.feed(html)
    return {
        "target": TARGET, "final_url": final_url,
        "title": parser.title, "description": parser.description,
        "headings": parser.headings, "inputs": parser.inputs,
        "buttons": parser.buttons, "forms": parser.forms,
        "links": parser.links,
        "note": "Solo inspección de HTML público; no usa APIs internas ni descargas."
    }

class LimitedRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        p = urlparse(newurl)
        if p.scheme != "https" or p.hostname != "mp3yt.is":
            raise ValueError("Redirección fuera del dominio autorizado")
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def scrape_public_page():
    request = Request(TARGET, headers={
        "User-Agent": "MP3YT-PublicInspector/1.0 (single-page research test)",
        "Accept": "text/html,application/xhtml+xml"
    })
    try:
        with build_opener(LimitedRedirects()).open(request, timeout=12) as response:
            if response.status != 200:
                raise ValueError(f"HTTP {response.status}")
            content_type = response.headers.get("Content-Type", "").lower()
            if "text/html" not in content_type:
                raise ValueError("El servidor no devolvió HTML")
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError("HTML demasiado grande")
            charset = response.headers.get_content_charset() or "utf-8"
            final_url = response.geturl()
            p = urlparse(final_url)
            if p.scheme != "https" or p.hostname != "mp3yt.is":
                raise ValueError("URL de respuesta fuera del sitio")
            return inspect_html(raw.decode(charset, errors="replace"), final_url)
    except HTTPError as exc:
        if exc.code in (403, 429):
            raise RuntimeError(f"MP3YT limitó esta prueba (HTTP {exc.code}). No se evade el bloqueo.") from exc
        raise RuntimeError(f"MP3YT devolvió HTTP {exc.code}") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"No se pudo conectar a MP3YT: {exc}") from exc
