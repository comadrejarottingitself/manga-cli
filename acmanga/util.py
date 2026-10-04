from .i18n import tr
import gzip
import hashlib
import json
import os
import re
import socket
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .errors import SourceError

USER_AGENT = "Mozilla/5.0 (X11; Linux i686; rv:128.0) Gecko/20100101 Firefox/128.0"
RETRYABLE_HTTP = {429, 500, 502, 503, 504}
MAX_IMAGE_BYTES = 48 * 1024 * 1024


def normalize_title(value):
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.casefold()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def compact_search_title(value):
    """Forma auxiliar solo para ranking de busqueda.

    No se usa como identidad persistente ni para fusionar mangas. Su unico objetivo es
    considerar equivalentes separadores internos como ``Hima-Ten`` / ``Hima Ten`` /
    ``Himaten`` cuando el resto del titulo coincide exactamente.
    """
    return "".join(normalize_title(value).split())


def titles_equivalent(left, right):
    """Equivalencia conservadora para búsqueda y unión visual de resultados.

    La identidad persistente sigue usando IDs de proveedor. Aquí solo aceptamos:
    - título normalizado idéntico; o
    - forma compacta idéntica (sin separadores) con al menos 5 caracteres.

    No hay prefijos, distancia de edición ni fuzzy matching agresivo: evita fusionar
    obras distintas por parecerse demasiado.
    """
    a = normalize_title(left)
    b = normalize_title(right)
    if not a or not b:
        return False
    if a == b:
        return True
    ac = compact_search_title(a)
    bc = compact_search_title(b)
    return len(ac) >= 5 and ac == bc



def relevance(title, query):
    t = normalize_title(title)
    q = normalize_title(query)
    if not q:
        return 0
    if t == q:
        return 1000
    # Hotfix 0.6.0-fixed3: los separadores no deben romper una coincidencia exacta.
    # Se limita a igualdad compacta (no prefijos/subcadenas compactas) y a titulos de
    # longitud suficiente para no convertir abreviaturas cortas en falsos positivos.
    tc = compact_search_title(t)
    qc = compact_search_title(q)
    if len(qc) >= 5 and tc == qc:
        return 950
    if t.startswith(q):
        return 800 - max(0, len(t) - len(q))
    if q in t:
        return 600 - max(0, len(t) - len(q))
    qwords = q.split()
    twords = set(t.split())
    matched = sum(1 for word in qwords if word in twords)
    return matched * 100 - abs(len(t) - len(q))


def stable_digest(value, length=16):
    raw = str(value or "").encode("utf-8", "replace")
    return hashlib.sha256(raw).hexdigest()[:max(8, int(length or 16))]


def safe_extension(name, default=".jpg"):
    ext = Path(urllib.parse.urlparse(name or "").path).suffix.lower()
    if ext in (".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif"):
        return ".jpg" if ext == ".jpeg" else ext
    return default


def extension_from_bytes(data, fallback=".jpg"):
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if len(data) >= 12 and data[4:12] in (b"ftypavif", b"ftypavis"):
        return ".avif"
    return fallback


def looks_like_image(data):
    if not data or len(data) < 12:
        return False
    return (
        data.startswith(b"\xff\xd8\xff")
        or data.startswith(b"\x89PNG\r\n\x1a\n")
        or (data.startswith(b"RIFF") and data[8:12] == b"WEBP")
        or data.startswith((b"GIF87a", b"GIF89a"))
        or (len(data) >= 12 and data[4:12] in (b"ftypavif", b"ftypavis"))
    )


def atomic_json_write(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(str(tmp), "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(str(tmp), str(path))


def _sleep_for_retry(exc, attempt):
    delay = 0.35 * (attempt + 1)
    retry_after = None
    try:
        retry_after = exc.headers.get("Retry-After")
    except Exception:
        retry_after = None
    if retry_after:
        try:
            delay = min(3.0, max(delay, float(retry_after)))
        except (TypeError, ValueError):
            pass
    time.sleep(delay)


def _source_error_from_http(source_name, exc):
    code = getattr(exc, "code", None)
    if code == 404:
        return SourceError(tr('util.not_found_http_404').format(source_name), source=source_name, status=code,
                           kind="not_found", transient=False)
    if code == 429:
        return SourceError(tr('util.rate_limited_http_429').format(source_name), source=source_name, status=code,
                           kind="rate_limit", transient=True)
    if code in (401, 403):
        return SourceError(tr('util.access_rejected_http').format(source_name, code), source=source_name, status=code,
                           kind="access", transient=True)
    transient = code in RETRYABLE_HTTP
    return SourceError(tr('util.http').format(source_name, code), source=source_name, status=code,
                       kind="http", transient=transient)


def _request(url, source_name, timeout, headers, retries, consumer):
    merged = {
        "User-Agent": USER_AGENT,
        "Accept-Encoding": "gzip",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "close",
    }
    if headers:
        merged.update(headers)
    attempts = max(1, int(retries or 0) + 1)
    last_error = None
    for attempt in range(attempts):
        req = urllib.request.Request(url, headers=merged, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return consumer(response)
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code in RETRYABLE_HTTP and attempt + 1 < attempts:
                _sleep_for_retry(exc, attempt)
                continue
            raise _source_error_from_http(source_name, exc) from exc
        except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.25 * (attempt + 1))
                continue
            if isinstance(exc, urllib.error.URLError):
                reason = getattr(exc, "reason", exc)
                message = tr('util.connection_failed').format(source_name, reason)
                kind = "network"
            else:
                message = tr('util.request_timed_out').format(source_name)
                kind = "timeout"
            raise SourceError(message, source=source_name, kind=kind, transient=True) from exc
    raise SourceError(tr('util.network_request_failed').format(source_name, last_error), source=source_name,
                      kind="network", transient=True)


def request_bytes(url, source_name, timeout=20, headers=None, retries=1):
    def consume(response):
        raw = response.read()
        if (response.headers.get("Content-Encoding") or "").lower() == "gzip":
            raw = gzip.decompress(raw)
        return raw
    return _request(url, source_name, timeout, headers, retries, consume)


def _decode_text(raw):
    for encoding in ("utf-8", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", "replace")


def request_text(url, source_name, timeout=12, headers=None, retries=1):
    raw = request_bytes(url, source_name, timeout=timeout, headers=headers, retries=retries)
    return _decode_text(raw)


def request_text_info(url, source_name, timeout=12, headers=None, retries=1):
    """Devuelve (texto, URL final) conservando redirects HTTP reales.

    Varias fuentes convierten una búsqueda exacta en una redirección hacia la ficha.
    Perder response.geturl() obliga a adivinar la identidad a partir del HTML y fue una
    de las causas de las regresiones de 0.6.0-fixed.
    """
    def consume(response):
        raw = response.read()
        if (response.headers.get("Content-Encoding") or "").lower() == "gzip":
            raw = gzip.decompress(raw)
        return _decode_text(raw), str(response.geturl() or url)
    return _request(url, source_name, timeout, headers, retries, consume)


def request_json(url, source_name, timeout=20, headers=None, retries=1):
    raw = request_bytes(url, source_name, timeout=timeout, headers=headers, retries=retries)
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SourceError(tr('util.invalid_json_response').format(source_name), source=source_name,
                          kind="invalid_response") from exc


def validate_html(page, source_name, context="page", expected_any=None, strict=False):
    """Rechaza respuestas vacías/challenge sin convertir cambios menores de plantilla en fallos.

    ``expected_any`` es informativo por defecto. Sólo se vuelve vinculante con strict=True;
    los adaptadores deben decidir si la estructura concreta que necesitan está presente.
    """
    text = str(page or "")
    low = text.casefold()
    if len(text.strip()) < 40 or "<html" not in low and "<body" not in low and "<a " not in low:
        raise SourceError(tr('util.invalid_response').format(source_name, context), source=source_name,
                          kind="invalid_response", context=context)
    challenge_markers = (
        "cf-chl-", "checking your browser", "verify you are human", "cloudflare ray id",
        "enable javascript and cookies to continue", "attention required! | cloudflare",
    )
    if any(marker in low for marker in challenge_markers):
        raise SourceError(tr('util.anti_bot_challenge_response').format(source_name), source=source_name,
                          kind="challenge", transient=True, context=context)
    if strict and expected_any and not any(str(marker).casefold() in low for marker in expected_any):
        raise SourceError(tr('util.unexpected_layout').format(source_name, context), source=source_name,
                          kind="parser", transient=False, context=context)
    return text


def build_url(base, path, params=None):
    url = base.rstrip("/") + "/" + path.lstrip("/")
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    return url


def _urls_fingerprint(urls):
    normalized = "\n".join(str(url) for url in urls)
    return hashlib.sha256(normalized.encode("utf-8", "replace")).hexdigest()


def _cached_pages(manifest_path, chapter_dir, cache_identity, urls_fingerprint):
    try:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(manifest, dict) or manifest.get("schema") != 2:
        return None
    if str(manifest.get("identity") or "") != str(cache_identity or ""):
        return None
    if str(manifest.get("urls_sha256") or "") != str(urls_fingerprint or ""):
        return None
    names = manifest.get("files")
    if not isinstance(names, list) or not names or int(manifest.get("count") or 0) != len(names):
        return None
    pages = []
    for name in names:
        path = Path(chapter_dir) / str(name)
        try:
            if not path.is_file() or path.stat().st_size < 32:
                return None
            with open(str(path), "rb") as handle:
                head = handle.read(32)
            if not looks_like_image(head):
                return None
        except OSError:
            return None
        pages.append(path)
    return pages


def _stream_image(url, source_name, dest_tmp, timeout, headers, retries=2, max_bytes=MAX_IMAGE_BYTES):
    """Descarga a disco en bloques: evita mantener páginas completas en RAM."""
    head_holder = {"head": b"", "size": 0}

    def consume(response):
        encoding = (response.headers.get("Content-Encoding") or "").lower()
        if encoding == "gzip":
            # Las imágenes reales no deberían llegar gzip; usar bytes para poder descomprimir con seguridad.
            raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise SourceError(tr('util.image_exceeds_size_limit').format(source_name), source=source_name,
                                  kind="invalid_response")
            raw = gzip.decompress(raw)
            if not looks_like_image(raw[:32]):
                raise SourceError(tr('util.response_is_not_an_image').format(source_name), source=source_name,
                                  kind="invalid_response")
            with open(str(dest_tmp), "wb") as handle:
                handle.write(raw)
            head_holder["head"] = raw[:32]
            head_holder["size"] = len(raw)
            return None

        total = 0
        head = b""
        with open(str(dest_tmp), "wb") as handle:
            while True:
                block = response.read(64 * 1024)
                if not block:
                    break
                total += len(block)
                if total > max_bytes:
                    raise SourceError(tr('util.image_exceeds_size_limit').format(source_name), source=source_name,
                                      kind="invalid_response")
                if len(head) < 32:
                    head = (head + block)[:32]
                handle.write(block)
        if not looks_like_image(head):
            raise SourceError(tr('util.response_is_not_an_image').format(source_name), source=source_name,
                              kind="invalid_response")
        head_holder["head"] = head
        head_holder["size"] = total
        return None

    try:
        _request(url, source_name, timeout, headers, retries, consume)
    except Exception:
        try:
            Path(dest_tmp).unlink()
        except OSError:
            pass
        raise
    return head_holder["head"], head_holder["size"]


def download_image_set(urls, chapter_dir, source_name, referer, timeout=24, workers=3, cache_identity=None):
    chapter_dir = Path(chapter_dir)
    chapter_dir.mkdir(parents=True, exist_ok=True)
    urls = [str(url) for url in urls if str(url or "").startswith(("http://", "https://"))]
    if not urls:
        raise SourceError(tr('util.chapter_returned_no_image_urls').format(source_name), source=source_name,
                          kind="parser")
    fingerprint = _urls_fingerprint(urls)
    identity = str(cache_identity or "{}:{}".format(source_name, fingerprint[:16]))
    manifest_path = chapter_dir / "manifest.json"
    cached = _cached_pages(manifest_path, chapter_dir, identity, fingerprint)
    if cached:
        return cached

    # Payload parcial/obsoleto: sólo esta carpeta de capítulo.
    for child in list(chapter_dir.iterdir()):
        if child.is_file():
            try:
                child.unlink()
            except OSError:
                pass

    try:
        workers = int(os.environ.get("ACMANGA_DOWNLOAD_WORKERS", workers))
    except (TypeError, ValueError):
        workers = 3
    workers = max(1, min(3, workers, len(urls)))
    headers = {
        "Referer": referer,
        "Accept": "image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8,*/*;q=0.2",
    }

    def fetch(index_url):
        index, url = index_url
        provisional = chapter_dir / ("{:03d}.part".format(index))
        head, _size = _stream_image(url, source_name, provisional, timeout=timeout, headers=headers, retries=1)
        ext = extension_from_bytes(head, safe_extension(url, ".jpg"))
        dest = chapter_dir / ("{:03d}{}".format(index, ext))
        os.replace(str(provisional), str(dest))
        return index, dest

    completed = {}
    failures = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fetch, item) for item in enumerate(urls, 1)]
        for future in as_completed(futures):
            try:
                index, path = future.result()
                completed[index] = path
            except Exception as exc:
                failures.append(exc)
    if failures or len(completed) != len(urls):
        for child in list(chapter_dir.iterdir()):
            if child.is_file():
                try:
                    child.unlink()
                except OSError:
                    pass
        first = failures[0] if failures else None
        if isinstance(first, SourceError):
            raise first
        raise SourceError(tr('util.incomplete_image_download').format(source_name), source=source_name,
                          kind="download", transient=True)

    pages = [completed[index] for index in range(1, len(urls) + 1)]
    atomic_json_write(manifest_path, {
        "schema": 2,
        "identity": identity,
        "urls_sha256": fingerprint,
        "count": len(pages),
        "files": [path.name for path in pages],
    })
    return pages
