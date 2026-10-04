from ..i18n import tr
import html as html_module
import re
import time
import urllib.parse

from .base import MangaSource
from ..errors import SourceError
from ..htmlutil import anchors, options, text as html_text
from ..util import (
    build_url, download_image_set, looks_like_image, normalize_title, relevance,
    request_bytes, request_text_info, stable_digest, validate_html,
)


class MangaKatanaSource(MangaSource):
    key = "mangakatana"
    name = "MangaKatana"
    base_url = "https://mangakatana.com"

    def __init__(self, timeout=8):
        self.timeout = timeout
        self._chapter_cache = {}
        self._details_cache = {}
        self.last_search_status = "unknown"

    @staticmethod
    def _manga_id_from_href(href):
        try:
            path = urllib.parse.urlparse(href or "").path
        except ValueError:
            return None
        match = re.match(r"^/manga/([^/?#]+)/?$", path)
        return urllib.parse.unquote(match.group(1)) if match else None

    @staticmethod
    def _chapter_parts(href):
        try:
            path = urllib.parse.urlparse(href or "").path
        except ValueError:
            return None, None
        match = re.match(r"^/manga/([^/?#]+)/([^/?#]+)/?$", path)
        if not match:
            return None, None
        return urllib.parse.unquote(match.group(1)), urllib.parse.unquote(match.group(2))

    @staticmethod
    def _valid_chapter_id(chapter_id):
        return bool(re.match(r"^c[0-9]+(?:\.[0-9]+)*(?:[-_][a-z0-9.]+)?$", str(chapter_id or ""), re.I))

    @staticmethod
    def _slug_title(manga_id):
        stem = (manga_id or "").rsplit(".", 1)[0]
        return stem.replace("_", " ").replace("-", " ").strip().title()

    @classmethod
    def _title_from_anchor(cls, item, manga_id, query=""):
        candidates = []
        for key in ("text", "alt", "title"):
            value = " ".join(str(item.get(key) or "").split()).strip()
            if value:
                candidates.append(value)
        candidates.append(cls._slug_title(manga_id))
        generic = {"read", "manga", "view", "details", "more", "bookmark"}
        candidates = [v for v in candidates if normalize_title(v) not in generic]
        if not candidates:
            return cls._slug_title(manga_id)
        if query:
            candidates.sort(key=lambda v: (-relevance(v, query), len(v), v.casefold()))
        return candidates[0]

    @staticmethod
    def _first_h1(page):
        match = re.search(r"<h1\b[^>]*>(.*?)</h1>", page or "", re.I | re.S)
        return html_text(match.group(1)) if match else ""

    @classmethod
    def _profile_series_id(cls, page):
        """Obtiene la identidad desde una ficha incluso sin canonical/og:url.

        La ficha real de MK expone First Chapter/Read offline. Es una señal mucho más
        fiable que barrer los enlaces a obras relacionadas de la misma página.
        """
        low = (page or "").casefold()
        if "author(s)" not in low and "alt name(s)" not in low and "first chapter" not in low:
            return None
        for item in anchors(page):
            title_id, action = cls._chapter_parts(item.get("href"))
            if title_id and str(action or "").casefold() in ("fc", "download"):
                return title_id
        cleaned = html_module.unescape(page or "").replace(r"\/", "/")
        match = re.search(r"/manga/([^/?#\"']+)/(?:fc|download)(?:[?\"'#]|$)", cleaned, re.I)
        return urllib.parse.unquote(match.group(1)) if match else None

    @classmethod
    def _direct_series(cls, page, resolved_url=""):
        # 1) La URL final HTTP es la autoridad principal cuando la búsqueda redirige.
        direct_id = cls._manga_id_from_href(resolved_url)
        if direct_id:
            return direct_id, cls._first_h1(page) or cls._slug_title(direct_id)

        # 2) Metadata cuando la plantilla la incluye.
        patterns = [
            r"<meta[^>]+property=[\"']og:url[\"'][^>]+content=[\"']([^\"']+/manga/[^\"'?]+)[\"']",
            r"<meta[^>]+content=[\"']([^\"']+/manga/[^\"'?]+)[\"'][^>]+property=[\"']og:url[\"']",
            r"<link[^>]+rel=[\"'][^\"']*canonical[^\"']*[\"'][^>]+href=[\"']([^\"']+/manga/[^\"'?]+)[\"']",
            r"<link[^>]+href=[\"']([^\"']+/manga/[^\"'?]+)[\"'][^>]+rel=[\"'][^\"']*canonical[^\"']*[\"']",
        ]
        for pattern in patterns:
            match = re.search(pattern, page or "", re.I)
            if match:
                manga_id = cls._manga_id_from_href(match.group(1))
                if manga_id:
                    return manga_id, cls._first_h1(page) or cls._slug_title(manga_id)

        # 3) Perfil real sin metadata: h1 + enlaces de acción propios.
        manga_id = cls._profile_series_id(page)
        if manga_id:
            return manga_id, cls._first_h1(page) or cls._slug_title(manga_id)
        return None

    @staticmethod
    def _block_after(page, start_pattern, end_patterns):
        start = re.search(start_pattern, page or "", re.I | re.S)
        if not start:
            return ""
        tail = (page or "")[start.end():]
        cut = len(tail)
        for pattern in end_patterns:
            match = re.search(pattern, tail, re.I | re.S)
            if match:
                cut = min(cut, match.start())
        return tail[:cut]

    @classmethod
    def _details_from_page(cls, page, expected_id=None, resolved_url=""):
        direct = cls._direct_series(page, resolved_url=resolved_url)
        manga_id = direct[0] if direct else str(expected_id or "")
        title = (direct[1] if direct else "") or cls._first_h1(page) or cls._slug_title(manga_id)
        if expected_id and manga_id and manga_id != str(expected_id):
            raise SourceError(tr('mangakatana.mangakatana_resolved_a_different_manga_than_requested'), source=cls.name,
                              kind="identity", transient=False)
        if not manga_id or not title:
            raise SourceError(tr('mangakatana.mangakatana_manga_identity_could_not_be_resolved'), source=cls.name,
                              kind="identity", transient=False)

        aliases_block = cls._block_after(page, r"Alt\s*name\(s\)\s*:", [r"Author\(s\)", r"Genres\s*:"])
        aliases_text = html_text(aliases_block)
        aliases = []
        for value in re.split(r"\s*[;|]\s*", aliases_text):
            value = " ".join(value.split()).strip(" ,")
            if value and normalize_title(value) != normalize_title(title) and value not in aliases:
                aliases.append(value)

        author_block = cls._block_after(page, r"Author\(s\)\s*/\s*Artist\(s\)\s*:", [r"Genres\s*:", r"Status\s*:"])
        author_names = []
        for item in anchors(author_block):
            name = " ".join((item.get("text") or item.get("title") or "").split())
            if name and name not in author_names:
                author_names.append(name)
        if not author_names:
            raw_author = html_text(author_block).strip(" ,")
            if raw_author:
                author_names = [raw_author]

        genre_block = cls._block_after(page, r"Genres\s*:", [r"Status\s*:", r"Latest\s*chapter", r"Update\s*at"])
        genres = []
        for item in anchors(genre_block):
            value = " ".join((item.get("text") or "").split())
            if value and value not in genres:
                genres.append(value)

        status_block = cls._block_after(page, r"Status\s*:", [r"Latest\s*chapter", r"Update\s*at", r"Description"])
        status = html_text(status_block).strip(" :-,\n\t")
        if len(status) > 80:
            status = ""
        return {
            "source": cls.key,
            "id": manga_id,
            "title": title,
            "author": ", ".join(author_names),
            "aliases": aliases,
            "year": "",
            "status": status,
            "type": "manga",
            "genres": genres,
            "language": "en",
            "ref": {"id": manga_id},
            "metadata_state": "resolved",
        }

    @staticmethod
    def _meta_refresh_target(page):
        patterns = [
            r"<meta[^>]+http-equiv=[\"']refresh[\"'][^>]+content=[\"'][^\"']*url=([^\"']+)[\"']",
            r"<meta[^>]+content=[\"'][^\"']*url=([^\"']+)[\"'][^>]+http-equiv=[\"']refresh[\"']",
        ]
        for pattern in patterns:
            match = re.search(pattern, page or "", re.I)
            if match:
                return html_module.unescape(match.group(1)).strip()
        return ""

    def _get(self, path, params=None, timeout=None, retries=1, context="page", expected_any=None, with_url=False):
        url = build_url(self.base_url, path, params)
        timeout = timeout or self.timeout
        page, final_url = request_text_info(url, self.name, timeout=timeout, retries=retries)
        # Meta-refresh existe además de redirects HTTP. Limitar a dos saltos evita bucles.
        for _ in range(2):
            target = self._meta_refresh_target(page)
            if not target:
                break
            target = urllib.parse.urljoin(final_url or self.base_url + "/", target)
            page, final_url = request_text_info(target, self.name, timeout=timeout, retries=retries)
        validate_html(page, self.name, context=context, expected_any=expected_any, strict=False)
        return (page, final_url) if with_url else page

    @staticmethod
    def _search_is_explicitly_empty(page):
        low = html_text(page).casefold()
        return any(marker in low for marker in (
            "no results", "no result", "nothing found", "no manga found", "0 manga found",
        ))

    def search(self, query):
        query = " ".join(str(query or "").split())
        if not query:
            self.last_search_status = "not_found"
            return []
        self.last_search_status = "unknown"
        payload = self._get("/page/1", {"search": query, "search_by": "m_name"}, timeout=7, retries=1,
                            context="search", expected_any=["/manga/", "search"], with_url=True)
        if isinstance(payload, tuple):
            page, final_url = payload
        else:  # compatibilidad con mocks/harness antiguos
            page, final_url = payload, ""

        direct = self._direct_series(page, resolved_url=final_url)
        if direct:
            manga_id, _title = direct
            item = self._details_from_page(page, expected_id=manga_id, resolved_url=final_url)
            names = [item.get("title") or ""] + list(item.get("aliases") or [])
            if max([relevance(name, query) for name in names] or [0]) >= 300:
                self.last_search_status = "ok"
                return [item]

        found = []
        seen = set()
        for item in anchors(page):
            manga_id = self._manga_id_from_href(item.get("href"))
            if not manga_id or manga_id in seen:
                continue
            title = self._title_from_anchor(item, manga_id, query=query)
            score = relevance(title, query)
            if not title or title.casefold().startswith("chapter ") or score <= 0:
                continue
            seen.add(manga_id)
            found.append({
                "source": self.key,
                "id": manga_id,
                "title": title,
                "author": "",
                "aliases": [],
                "year": "",
                "status": "",
                "type": "manga",
                "genres": [],
                "language": "en",
                "ref": {"id": manga_id},
                "metadata_state": "partial",
            })
        found.sort(key=lambda item: (-relevance(item.get("title"), query), item.get("title", "").casefold()))
        if found:
            self.last_search_status = "ok"
            return found[:12]
        if self._search_is_explicitly_empty(page):
            self.last_search_status = "not_found"
            return []
        # Un HTML válido pero no interpretable no demuestra ausencia.
        self.last_search_status = "unknown"
        raise SourceError(tr('mangakatana.mangakatana_search_response_could_not_be_resolved_safely'), source=self.name,
                          kind="parser", transient=False, context="search")

    def details(self, ref, force=False):
        manga_id = ref.get("id") if isinstance(ref, dict) else ref
        manga_id = str(manga_id or "")
        if not manga_id:
            raise SourceError(tr('mangakatana.mangakatana_invalid_manga_reference'), source=self.name, kind="identity")
        now = time.monotonic()
        cached = self._details_cache.get(manga_id)
        if not force and cached and now - cached[0] < 300:
            return dict(cached[1])
        payload = self._get("/manga/{}".format(urllib.parse.quote(manga_id, safe="._-")), timeout=9,
                            context="manga details", expected_any=[manga_id, "Author(s)", "Status"], with_url=True)
        page, final_url = payload if isinstance(payload, tuple) else (payload, "")
        item = self._details_from_page(page, expected_id=manga_id, resolved_url=final_url)
        self._details_cache[manga_id] = (now, dict(item))
        return item

    @staticmethod
    def _chapter_number(title, chapter_id):
        for value in (title or "", chapter_id or ""):
            match = re.search(r"(?:chapter|chap|ch|c)[ .:_-]*([0-9]+(?:\.[0-9]+)?)", value, re.I)
            if match:
                return match.group(1)
        nums = re.findall(r"([0-9]+(?:\.[0-9]+)?)", title or "")
        return nums[-1] if nums else ""

    @staticmethod
    def _stable_chapter_id(manga_id, chapter_id):
        return "mk{}-{}".format(stable_digest(manga_id, 14), str(chapter_id))

    def _chapter_item(self, manga_id, raw_chapter_id, label=""):
        if not self._valid_chapter_id(raw_chapter_id):
            return None
        number = self._chapter_number(label, raw_chapter_id)
        if not number:
            return None
        label = " ".join(str(label or "").split()) or "Chapter {}".format(number)
        return {
            "source": self.key,
            "id": self._stable_chapter_id(manga_id, raw_chapter_id),
            "title_id": manga_id,
            "number": number,
            "name": label,
            "language": "en",
            "groups": [],
            "pages_hint": 0,
            "source_ref": {"manga_id": manga_id, "chapter_id": raw_chapter_id},
        }

    def _chapter_items_from_page(self, manga_id, page):
        items = []
        seen = set()
        for anchor in anchors(page):
            title_id, raw_id = self._chapter_parts(anchor.get("href"))
            if title_id != manga_id or not self._valid_chapter_id(raw_id) or raw_id in seen:
                continue
            label = " ".join((anchor.get("text") or anchor.get("title") or "").split())
            item = self._chapter_item(manga_id, raw_id, label)
            if item:
                seen.add(raw_id)
                items.append(item)
        return items, seen

    def _chapter_items_from_reader_catalog(self, manga_id, page, seed_seen=None):
        """Fallback para títulos cuya ficha no expone los capítulos como anchors.

        Los lectores de MK tienen un selector de capítulo. Primero leemos sus <option> y,
        como red de seguridad, rutas /manga/<id>/cN embebidas en scripts/atributos.
        """
        seen = set(seed_seen or [])
        items = []
        for option in options(page):
            value = html_module.unescape(option.get("value") or "").replace(r"\/", "/")
            title_id, raw_id = self._chapter_parts(value)
            if not raw_id and self._valid_chapter_id(value):
                title_id, raw_id = manga_id, value
            if title_id != manga_id or not self._valid_chapter_id(raw_id) or raw_id in seen:
                continue
            item = self._chapter_item(manga_id, raw_id, option.get("text") or "")
            if item:
                seen.add(raw_id)
                items.append(item)

        cleaned = html_module.unescape(page or "").replace(r"\/", "/")
        manga_re = re.escape(manga_id)
        for match in re.finditer(r"/manga/{}/(c[0-9]+(?:\.[0-9]+)*(?:[-_][a-z0-9.]+)?)".format(manga_re), cleaned, re.I):
            raw_id = match.group(1)
            if raw_id in seen or not self._valid_chapter_id(raw_id):
                continue
            item = self._chapter_item(manga_id, raw_id, "")
            if item:
                seen.add(raw_id)
                items.append(item)
        return items

    def _all_chapters(self, manga_id, force=False):
        now = time.monotonic()
        cached = self._chapter_cache.get(manga_id)
        if not force and cached and now - cached[0] < 180:
            return [dict(item) for item in cached[1]]

        page = self._get("/manga/{}".format(urllib.parse.quote(manga_id, safe="._-")), timeout=9,
                         context="chapter list", expected_any=[manga_id, "Chapter"])
        items, seen = self._chapter_items_from_page(manga_id, page)

        # Algunas fichas reales (Vagabond es una regresión física) no entregan la lista
        # en la misma forma. El First Chapter redirige al lector, cuyo selector contiene
        # el catálogo. Sólo hacemos la petición extra si la ficha no produjo capítulos.
        if not items:
            try:
                reader_payload = self._get("/manga/{}/fc".format(urllib.parse.quote(manga_id, safe="._-")),
                                           timeout=10, retries=1, context="chapter selector", with_url=True)
                reader_page = reader_payload[0] if isinstance(reader_payload, tuple) else reader_payload
                items.extend(self._chapter_items_from_reader_catalog(manga_id, reader_page, seed_seen=seen))
            except SourceError:
                # Se conserva el error final homogéneo de abajo; no se fabrica una lista.
                pass

        if not items:
            raise SourceError(tr('mangakatana.mangakatana_no_valid_chapters_found_after_fiche_reader_selector_fallback'),
                              source=self.name, kind="parser", transient=False, context="chapter list")

        # Mantener el orden que ofrece la fuente. Si viene mezclado, ordenar por número descendente.
        def key(item):
            try:
                return float(item.get("number") or -1)
            except (TypeError, ValueError):
                return -1.0
        if any(key(items[i]) < key(items[i + 1]) for i in range(len(items) - 1)):
            items.sort(key=key, reverse=True)

        if len(self._chapter_cache) >= 16:
            oldest = min(self._chapter_cache, key=lambda value: self._chapter_cache[value][0])
            self._chapter_cache.pop(oldest, None)
        self._chapter_cache[manga_id] = (now, [dict(item) for item in items])
        return items

    def chapters(self, ref, offset=0, limit=100, force=False):
        manga_id = ref.get("id") if isinstance(ref, dict) else ref
        if not manga_id:
            raise SourceError(tr('mangakatana.mangakatana_invalid_manga_reference'), source=self.name, kind="identity")
        items = self._all_chapters(str(manga_id), force=force)
        offset = max(0, int(offset or 0))
        limit = max(1, int(limit or 100))
        batch = items[offset:offset + limit]
        return {"items": batch, "total": len(items), "next_offset": offset + len(batch)}

    def chapters_all(self, ref, page_size=100, max_pages=100, force=False):
        manga_id = ref.get("id") if isinstance(ref, dict) else ref
        if not manga_id:
            raise SourceError(tr('mangakatana.mangakatana_invalid_manga_reference'), source=self.name, kind="identity")
        items = self._all_chapters(str(manga_id), force=force)
        return {"items": [dict(item) for item in items], "total": len(items), "next_offset": len(items)}

    def invalidate(self, ref=None):
        if ref is None:
            self._chapter_cache.clear()
            self._details_cache.clear()
            return
        manga_id = ref.get("id") if isinstance(ref, dict) else ref
        if manga_id:
            self._chapter_cache.pop(str(manga_id), None)
            self._details_cache.pop(str(manga_id), None)

    @staticmethod
    def _decode_js_string(value):
        value = html_module.unescape(value or "")
        value = value.replace(r"\/", "/")
        value = re.sub(r"\\u002[fF]", "/", value)
        value = re.sub(r"\\x2[fF]", "/", value)
        value = value.replace(r"\\", "\\")
        return value

    @classmethod
    def _js_url_arrays(cls, page):
        sets = []
        seen = set()
        pattern = r"\b(?:var|let|const)\s+([A-Za-z_$][\w$]*)\s*=\s*\[(.*?)\]\s*;"
        for match in re.finditer(pattern, page or "", re.I | re.S):
            name = match.group(1)
            body = match.group(2)
            values = []
            for raw in re.findall(r"[\"']((?:\\.|[^\"'])+)[\"']", body):
                url = cls._decode_js_string(raw).strip()
                if url.startswith("//"):
                    url = "https:" + url
                if not url.startswith(("http://", "https://")):
                    continue
                if not re.search(r"\.(?:jpe?g|png|webp|avif|gif)(?:[?#]|$)", url, re.I):
                    continue
                if url not in values:
                    values.append(url)
            marker = tuple(values)
            if len(values) >= 2 and marker not in seen:
                seen.add(marker)
                sets.append((name, values))
        sets.sort(key=lambda item: (0 if item[0].casefold() == "thzq" else 1, item[0].casefold()))
        return sets

    def _page_url_sets(self, chapter):
        source_ref = chapter.get("source_ref") or {}
        manga_id = chapter.get("title_id") or source_ref.get("manga_id")
        raw_chapter_id = source_ref.get("chapter_id")
        if not raw_chapter_id:
            candidate = str(chapter.get("id") or "")
            if self._valid_chapter_id(candidate):
                raw_chapter_id = candidate
        if not manga_id or not raw_chapter_id or not self._valid_chapter_id(raw_chapter_id):
            raise SourceError(tr('mangakatana.mangakatana_invalid_chapter_reference'), source=self.name, kind="identity")
        path = "/manga/{}/{}".format(
            urllib.parse.quote(str(manga_id), safe="._-"),
            urllib.parse.quote(str(raw_chapter_id), safe="._-"),
        )
        page = self._get(path, timeout=10, context="reader", expected_any=["Server", "image"])
        sets = self._js_url_arrays(page)
        if not sets:
            raise SourceError(tr('mangakatana.mangakatana_reader_page_lists_were_not_found'), source=self.name,
                              kind="parser", transient=False, context="reader")
        return [values for _name, values in sets]

    def _page_urls(self, chapter):
        return self._page_url_sets(chapter)[0]

    def prepare_pages(self, chapter, chapter_dir):
        source_ref = chapter.get("source_ref") or {}
        manga_id = chapter.get("title_id") or source_ref.get("manga_id") or ""
        raw_chapter_id = source_ref.get("chapter_id") or chapter.get("id") or ""
        identity = "{}:{}:{}".format(self.key, manga_id, raw_chapter_id)
        failures = []
        for urls in self._page_url_sets(chapter):
            try:
                referer = self.base_url + "/manga/{}/{}".format(
                    urllib.parse.quote(str(manga_id), safe="._-"),
                    urllib.parse.quote(str(raw_chapter_id), safe="._-"),
                )
                return download_image_set(urls, chapter_dir, self.name, referer, timeout=24,
                                          workers=3, cache_identity=identity)
            except SourceError as exc:
                failures.append(str(exc))
        raise SourceError(tr('mangakatana.mangakatana_all_image_servers_failed').format(" | ".join(failures[-3:])),
                          source=self.name, kind="download", transient=True)

    def diagnostic(self):
        expected = "Vagabond"
        results = self.search(expected)
        exact = next((item for item in results if normalize_title(item.get("title")) == normalize_title(expected)), None)
        if exact is None:
            raise SourceError(tr('mangakatana.mangakatana_diagnostic_search_did_not_resolve_vagabond'), source=self.name,
                              kind="identity")
        details = self.details(exact["ref"], force=True)
        chapters = self.chapters_all(exact["ref"], force=True)["items"]
        if not chapters:
            raise SourceError(tr('mangakatana.mangakatana_vagabond_returned_no_chapters'), source=self.name, kind="parser")
        pages = self._page_urls(chapters[-1])
        raw = request_bytes(pages[0], self.name, timeout=12,
                            headers={"Referer": self.base_url + "/"}, retries=1)
        if not looks_like_image(raw):
            raise SourceError(tr('mangakatana.mangakatana_first_test_page_was_not_an_image'), source=self.name,
                              kind="invalid_response")
        return {
            "source": self.key,
            "ok": True,
            "detail": tr('mangakatana.e2e_ok_ch_reader_image_ok').format(details.get("title"), len(chapters)),
        }
