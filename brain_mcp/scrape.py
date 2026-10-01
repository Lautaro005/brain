"""URL -> texto limpio con trafilatura, con fallback a Playwright para sitios con mucho JS."""
import atexit
import ipaddress
import logging
import os
import queue
import socket
import threading
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import datetime, timezone
from urllib.parse import urlparse

import trafilatura
import trafilatura.utils

log = logging.getLogger(__name__)

# Menos palabras que esto = trafilatura no encontró contenido real. Las páginas con JS casi
# nunca devuelven None: devuelven el esqueleto ("You need to enable JavaScript…", menú, footer).
MIN_WORDS = 30
RENDER_TIMEOUT_MS = 15_000


class ScrapeError(RuntimeError):
    pass


# ---------- Playwright ----------
# La API sync de Playwright queda atada al thread que la creó, y las tools MCP corren en threads
# de anyio que cambian en cada llamada. Por eso el navegador vive en un único thread dedicado:
# todo lo de Playwright se ejecuta ahí, y la instancia de Chromium se reusa entre URLs.

# Thread propio (daemon) y no un ThreadPoolExecutor: concurrent.futures apaga sus executors antes
# de que corran los handlers de atexit, y ahí ya no se podría cerrar Chromium.
_jobs: queue.Queue = queue.Queue()
_worker: threading.Thread | None = None
_worker_lock = threading.Lock()
_pw = None
_browser = None


def _worker_loop() -> None:
    while True:
        fn, args, fut = _jobs.get()
        if fut.set_running_or_notify_cancel():
            try:
                fut.set_result(fn(*args))
            except BaseException as e:
                fut.set_exception(e)


def _submit(fn, *args) -> Future:
    global _worker
    with _worker_lock:
        if _worker is None:
            _worker = threading.Thread(target=_worker_loop, name="playwright", daemon=True)
            _worker.start()
    fut: Future = Future()
    _jobs.put((fn, args, fut))
    return fut


def _get_browser():
    """Solo se llama desde el thread de Playwright. Levanta Chromium la primera vez."""
    global _pw, _browser
    if _browser is None or not _browser.is_connected():
        from playwright.sync_api import sync_playwright

        if _pw is None:
            _pw = sync_playwright().start()
        try:
            _browser = _pw.chromium.launch(headless=True)
        except Exception as e:
            raise ScrapeError(
                "No se pudo abrir Chromium de Playwright. Corré una vez: "
                f"`uv run playwright install chromium` ({e})"
            ) from e
        log.info("Chromium headless iniciado")
    return _browser


def _render_in_thread(url: str) -> str:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    page = _get_browser().new_page()
    try:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=RENDER_TIMEOUT_MS)
        except PlaywrightError as e:
            raise ScrapeError(f"Playwright no pudo abrir {url}: {e.message.splitlines()[0]}") from e
        try:
            page.wait_for_load_state("networkidle", timeout=RENDER_TIMEOUT_MS)
        except PlaywrightTimeout:
            # sitios con polling/websockets nunca llegan a networkidle: usamos lo que haya
            log.info("networkidle no llegó en %d ms para %s, sigo igual", RENDER_TIMEOUT_MS, url)
        return page.content()
    finally:
        page.close()


def _close_in_thread() -> None:
    global _pw, _browser
    if _browser is not None:
        _browser.close()
        _browser = None
    if _pw is not None:
        _pw.stop()
        _pw = None


@atexit.register
def _shutdown() -> None:
    if _browser is None and _pw is None:
        return
    try:
        _submit(_close_in_thread).result(timeout=10)
    except Exception as e:
        log.warning("No se pudo cerrar Chromium prolijo: %s", e)


def render_html(url: str) -> str:
    """Renderiza la URL en Chromium headless y devuelve el HTML ya ejecutado el JS."""
    try:
        return _submit(_render_in_thread, url).result(timeout=RENDER_TIMEOUT_MS * 3 / 1000)
    except FuturesTimeout as e:
        raise ScrapeError(f"Playwright tardó demasiado renderizando {url}.") from e


# ---------- extracción ----------

def _extract(html: str) -> str:
    text = trafilatura.extract(html, include_comments=False, include_tables=True)
    return (text or "").strip()


def check_public(url: str) -> str:
    """Para el acceso remoto (BRAIN_REMOTE=1): un agente que llega por la URL pública no puede usar a brain
    para leer servicios de esta máquina o de la red local (127.0.0.1, 192.168.x.x…). Solo direcciones públicas.
    Devuelve una IP ya validada, para conectarse a esa misma (y no resolver de nuevo: DNS rebinding)."""
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ScrapeError(f"URL inválida: {url}")
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except OSError as e:
        raise ScrapeError(f"No se pudo resolver {u.hostname}: {e}") from e
    ips = [ipaddress.ip_address(info[4][0].split("%")[0]) for info in infos]
    for ip in ips:
        if not ip.is_global:
            raise ScrapeError(f"Por el acceso remoto solo se pueden leer direcciones públicas ({u.hostname} → {ip}).")
    if not ips:
        raise ScrapeError(f"No se pudo resolver {u.hostname}")
    return str(ips[0])


REMOTE_MAX_BYTES = 5 * 1024 * 1024
REMOTE_MAX_REDIRECTS = 5


def fetch_public(url: str) -> str:
    """Descarga para el acceso remoto: cada salto (redirecciones incluidas) se valida con check_public y la
    conexión va a la IP validada (con el hostname para TLS/SNI), así ni una redirección ni un DNS que cambia
    entre la validación y la conexión llevan a una dirección local. Sin Playwright: el JS de la página podría
    pedir direcciones locales desde el navegador."""
    import certifi
    import urllib3
    from urllib.parse import urljoin

    for _ in range(REMOTE_MAX_REDIRECTS + 1):
        u = urlparse(url)
        ip = check_public(url)
        port = u.port or (443 if u.scheme == "https" else 80)
        path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        if u.scheme == "https":
            pool = urllib3.HTTPSConnectionPool(ip, port, server_hostname=u.hostname, assert_hostname=u.hostname,
                                               cert_reqs="CERT_REQUIRED", ca_certs=certifi.where(), retries=False)
        else:
            pool = urllib3.HTTPConnectionPool(ip, port, retries=False)
        try:
            r = pool.request("GET", path, redirect=False, preload_content=False, timeout=urllib3.Timeout(connect=10, read=20),
                             headers={"Host": u.netloc.rsplit("@", 1)[-1], "User-Agent": "Mozilla/5.0 (brain)", "Accept": "text/html,*/*"})
        except urllib3.exceptions.HTTPError as e:
            raise ScrapeError(f"No se pudo descargar {url}: {e}") from e
        try:
            if r.status in (301, 302, 303, 307, 308) and r.headers.get("Location"):
                url = urljoin(url, r.headers["Location"])
                continue
            if r.status >= 400:
                raise ScrapeError(f"No se pudo descargar {url}: HTTP {r.status}")
            data = r.read(REMOTE_MAX_BYTES)
        finally:
            r.release_conn()
            pool.close()
        return trafilatura.utils.decode_file(data)
    raise ScrapeError(f"Demasiadas redirecciones: {url}")


def scrape(url: str, render_js: bool = False) -> dict:
    """render_js=True fuerza Playwright sin probar primero el fetch plano de trafilatura."""
    if os.environ.get("BRAIN_REMOTE") == "1":
        # por el acceso remoto: descarga validada salto por salto y sin Playwright (ver fetch_public)
        html = fetch_public(url)
        text = _extract(html)
        if not text:
            raise ScrapeError(f"No se pudo extraer texto de {url} (por el acceso remoto no se renderiza JS).")
        meta = trafilatura.extract_metadata(html)
        return {"title": (meta.title if meta and meta.title else None) or text.split("\n")[0][:120], "text": text,
                "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "rendered_js": False}
    html, text, rendered = None, "", False

    if not render_js:
        html = trafilatura.fetch_url(url)
        if html is not None:
            text = _extract(html)

    if len(text.split()) < MIN_WORDS:
        if not render_js:
            log.info(
                "trafilatura sacó %d palabras de %s, pruebo con Playwright",
                len(text.split()), url,
            )
        try:
            rendered_html = render_html(url)
        except Exception as e:
            # forzado o sin nada que devolver: el error de Playwright es el relevante
            if render_js or not text:
                raise ScrapeError(str(e)) if not isinstance(e, ScrapeError) else e
            log.warning("Playwright falló para %s (%s), me quedo con el texto de trafilatura", url, e)
        else:
            rendered_text = _extract(rendered_html)
            if len(rendered_text.split()) >= len(text.split()):
                html, text, rendered = rendered_html, rendered_text, True

    if not text:
        if html is None:
            raise ScrapeError(f"No se pudo descargar {url} (red, bloqueo o URL inválida).")
        raise ScrapeError(
            f"No se pudo extraer texto de {url}, ni renderizando con JS (¿paywall o login?)."
        )

    meta = trafilatura.extract_metadata(html)
    title = (meta.title if meta and meta.title else None) or text.split("\n")[0][:120]
    return {
        "title": title,
        "text": text,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rendered_js": rendered,
    }
