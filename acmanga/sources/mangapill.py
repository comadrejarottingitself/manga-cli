from ..i18n import tr
import html as html_module
import re
import time
import urllib.parse

from .base import MangaSource
from ..errors import SourceError
from ..htmlutil import anchors, images, text as html_text
from ..util import (
    build_url, download_image_set, looks_like_image, normalize_title, relevance,
    request_bytes, request_text_info, stable_digest, validate_html,
)


class MangaPillSource(MangaSource):
    key = "mangapill"
    name = "MangaPill"
    base_url = "https://mangapill.com"

    def __init__(self, timeout=8):
        self.timeout = timeout
        self._chapter_cache = {}
        self._details_cache = {}
        self.last_search_status = "unknown"

    @staticmethod
    def _manga_ref_from_href(href):
        try:
            path = urllib.parse.urlparse(href or "").path
        except ValueError:
            return None
        match = re.match(r"^/manga/([0-9]+)(?:/([^/?#]+))?/?$", path)
        if not match:
            return None
        first = urllib.parse.unquote(match.group(1))
        second = urllib.parse.unquote(match.group(2) or "")
        return first + ("/" + second if second else "")

    @staticmethod
    def _chapter_ref_from_href(href):
        try:
            path = urllib.parse.urlparse(href or "").path
        except ValueError:
            return None
        match = re.match(r"^/chapters/([^/?#]+)(?:/([^/?#]+))?/?$", path)
        if not match:
            return None
        first = urllib.parse.unquote(match.group(1))
        second = urllib.parse.unquote(match.group(2) or "")
        return first + ("/" + second if second else "")

    @staticmethod
    def _clean_title(value):
        value = " ".join(str(value or "").split()).strip()
        value = re.sub(r"\s+-\s+MangaPill\s*$", "", value, flags=re.I)
        value = re.sub(r"\s+Manga\s*$", "", value, flags=re.I)
        value = re.sub(r"^Read\s+", "", value, flags=re.I)
        value = re.sub(r"\s+Online\s*$", "", value, flags=re.I)
        return value.strip(" -")

    @classmethod
    def _display_title(cls, item, manga_ref, query=""):
        candidates = []
        # El texto visible/alt suele ser canónico; title puede contener "... Manga - MangaPill".
        for key in ("text", "alt", "title"):
            value = cls._clean_title(item.get(key) or "")
            if value:
                candidates.append(value)
        slug = manga_ref.split("/", 1)[1] if "/" in manga_ref else ""
        if slug:
            candidates.append(slug.replace("-", " ").strip().title())
        if not candidates:
            return ""
        if query:
            candidates.sort(key=lambda value: (-relevance(value, query), len(value), value.casefold()))
        return candidates[0]

    @staticmethod
    def _first_h1(page):
        match = re.search(r"<h1\b[^>]*>(.*?)</h1>", page or "", re.I | re.S)
        return html_text(match.group(1)) if match else ""

    @classmethod
    def _alternate_title(cls, page, title):
        """Extrae el subtítulo/alias corto que MangaPill coloca tras el H1.

        En páginas reales aparecen formas como ``18`` -> ``Eighteen`` y
        ``QP`` -> ``QP: Soul of Violence``. Se exige un bloque corto y claramente
        separado para no confundir el resumen argumental con un alias.
        """
        match = re.search(r"<h1\b[^>]*>.*?</h1>(.*?)(?=\bType\b|<[^>]+>\s*Type\s*<)", page or "", re.I | re.S)
        if not match:
            return ""
        block = match.group(1)
        # MangaPill coloca el alias como un bloque corto independiente. No aceptamos
        # párrafos ni un fallback de texto bruto: una descripción breve jamás debe
        # convertirse en alias por accidente.
        candidates = []
        pattern = r"<(div|h2|h3|span)\b[^>]*>(.*?)</\1>"
        for _tag, inner in re.findall(pattern, block, re.I | re.S):
            value = " ".join(html_text(inner).split()).strip(" -")
            if value:
                candidates.append(value)
        for value in candidates:
            if normalize_title(value) == normalize_title(title):
                continue
            words = value.split()
            if 1 <= len(words) <= 14 and len(value) <= 120 and not value.endswith((".", ";")):
                return value
        return ""

    def _get(self, path, params=None, timeout=None, retries=1, context="page", expected_any=None, with_url=False):
        url = build_url(self.base_url, path, params)
        page, final_url = request_text_info(url, self.name, timeout=timeout or self.timeout, retries=retries)
        validate_html(page, self.name, context=context, expected_any=expected_any, strict=False)
        return (page, final_url) if with_url else page

    @staticmethod
    def _search_is_explicitly_empty(page):
        low = html_text(page).casefold()
        return any(marker in low for marker in (
            "no results", "no result", "nothing found", "no manga found", "0 manga found",
        ))

    def _direct_search_result(self, page, final_url, query):
        ref = self._manga_ref_from_href(final_url)
        if not ref:
            return None
        title = self._clean_title(self._first_h1(page))
        if not title:
            title = self._display_title({}, ref, query=query)
        if relevance(title, query) <= 0:
            return None
        return {
            "source": self.key,
            "id": ref,
            "title": title,
            "author": "",
            "aliases": [],
            "year": "",
            "status": "",
            "type": "manga",
            "genres": [],
            "language": "en",
            "ref": {"id": ref},
            "metadata_state": "partial",
        }

    def search(self, query):
        query = " ".join(str(query or "").split())
        if not query:
            self.last_search_status = "not_found"
            return []
        self.last_search_status = "unknown"
        payload = self._get("/search", {"q": query}, timeout=7, retries=1, context="search",
                            expected_any=["/manga/", "Search"], with_url=True)
        page, final_url = payload if isinstance(payload, tuple) else (payload, "")

        direct = self._direct_search_result(page, final_url, query)
        if direct:
            self.last_search_status = "ok"
            return [direct]

        found = []
        seen = set()
        for item in anchors(page):
            ref = self._manga_ref_from_href(item.get("href"))
            if not ref or ref in seen:
                continue
            title = self._display_title(item, ref, query=query)
            if not title or title.casefold().startswith("chapter ") or relevance(title, query) <= 0:
                continue
            seen.add(ref)
            found.append({
                "source": self.key,
                "id": ref,
                "title": title,
                "author": "",
                "aliases": [],
                "year": "",
                "status": "",
                "type": "manga",
                "genres": [],
                "language": "en",
                "ref": {"id": ref},
                "metadata_state": "partial",
            })
        found.sort(key=lambda item: (-relevance(item.get("title"), query), item.get("title", "").casefold(), item.get("id")))
        if found:
            self.last_search_status = "ok"
            return found[:12]
        if self._search_is_explicitly_empty(page):
            self.last_search_status = "not_found"
            return []
        self.last_search_status = "unknown"
        raise SourceError(tr('mangapill.mangapill_search_response_could_not_be_resolved_safely'), source=self.name,
                          kind="parser", transient=False, context="search")

    def details(self, ref, force=False):
        manga_ref = ref.get("id") if isinstance(ref, dict) else ref
        manga_ref = str(manga_ref or "")
        if not manga_ref:
            raise SourceError(tr('mangapill.mangapill_invalid_manga_reference'), source=self.name, kind="identity")
        now = time.monotonic()
        cached = self._details_cache.get(manga_ref)
        if not force and cached and now - cached[0] < 300:
            return dict(cached[1])
        page = self._get("/manga/{}".format(urllib.parse.quote(manga_ref, safe="/-._")), timeout=9,
                         context="manga details", expected_any=["Type", "Status", "Chapter"])
        title = self._clean_title(self._first_h1(page))
        if not title:
            raise SourceError(tr('mangapill.mangapill_manga_title_missing_from_details'), source=self.name,
                              kind="parser", context="manga details")
        flat = html_text(page)
        year_match = re.search(r"\bYear\s+([0-9]{4})\b", flat, re.I)
        status_match = re.search(r"\bStatus\s+([A-Za-z][A-Za-z -]{1,30}?)(?=\s+Year\b|\s+Genres\b|\s+Chapters\b|$)", flat, re.I)
        type_match = re.search(r"\bType\s+([A-Za-z][A-Za-z -]{1,30}?)(?=\s+Status\b|\s+Year\b|$)", flat, re.I)
        genres = []
        genre_match = re.search(r"Genres(.*?)(?:<h[1-6]\b[^>]*>\s*Chapters|\bChapters\b)", page, re.I | re.S)
        if genre_match:
            for item in anchors(genre_match.group(1)):
                value = " ".join((item.get("text") or "").split())
                if value and value not in genres:
                    genres.append(value)
        alternate = self._alternate_title(page, title)
        item = {
            "source": self.key,
            "id": manga_ref,
            "title": title,
            "author": "",
            "aliases": [alternate] if alternate else [],
            "year": year_match.group(1) if year_match else "",
            "status": status_match.group(1).strip() if status_match else "",
            "type": type_match.group(1).strip() if type_match else "manga",
            "genres": genres,
            "language": "en",
            "ref": {"id": manga_ref},
            "metadata_state": "resolved",
        }
        self._details_cache[manga_ref] = (now, dict(item))
        return item

    @staticmethod
    def _chapter_number(label, ref):
        for value in (label or "", ref or ""):
            match = re.search(r"chapter[ .:_-]*([0-9]+(?:\.[0-9]+)?)", value, re.I)
            if match:
                return match.group(1)
        nums = re.findall(r"([0-9]+(?:\.[0-9]+)?)", label or "")
        return nums[-1] if nums else ""

    @staticmethod
    def _chapter_group(label):
        match = re.search(r"\b(Group\s+[A-Za-z0-9._-]+)\b(?=\s+Chapter\b)", str(label or ""), re.I)
        return " ".join(match.group(1).split()) if match else ""

    @staticmethod
    def _stable_chapter_id(chapter_ref):
        return "mp{}".format(stable_digest(chapter_ref, 22))

    def _chapter_item(self, manga_ref, chapter_ref, label=""):
        label = " ".join(str(label or "").split())
        if not label:
            slug = chapter_ref.split("/", 1)[-1]
            label = slug.replace("-", " ").strip().title()
        number = self._chapter_number(label, chapter_ref)
        if not number:
            return None
        group = self._chapter_group(label)
        return {
            "source": self.key,
            "id": self._stable_chapter_id(chapter_ref),
            "title_id": manga_ref,
            "number": number,
            "name": label,
            "language": "en",
            "groups": [group] if group else [],
            "pages_hint": 0,
            "source_ref": {"manga_id": manga_ref, "chapter_ref": chapter_ref},
        }

    def _all_chapters(self, manga_ref, force=False):
        now = time.monotonic()
        cached = self._chapter_cache.get(manga_ref)
        if not force and cached and now - cached[0] < 180:
            return [dict(item) for item in cached[1]]
        page = self._get("/manga/{}".format(urllib.parse.quote(manga_ref, safe="/-._")), timeout=9,
                         context="chapter list", expected_any=["Chapter", "/chapters/"])
        items = []
        seen = set()
        for anchor in anchors(page):
            chapter_ref = self._chapter_ref_from_href(anchor.get("href"))
            if not chapter_ref or chapter_ref in seen:
                continue
            label = " ".join((anchor.get("text") or anchor.get("title") or anchor.get("alt") or "").split())
            item = self._chapter_item(manga_ref, chapter_ref, label)
            if item:
                seen.add(chapter_ref)
                items.append(item)

        # Fallback de estructura: si el HTML conserva las rutas pero la envoltura de anchors
        # cambia, extraer los href sin inventar capítulos.
        if not items:
            cleaned = html_module.unescape(page or "").replace(r"\/", "/")
            for match in re.finditer(r"(?:https?://[^/\"']+)?/chapters/([^/?#\"']+)(?:/([^/?#\"']+))?", cleaned, re.I):
                chapter_ref = urllib.parse.unquote(match.group(1))
                if match.group(2):
                    chapter_ref += "/" + urllib.parse.unquote(match.group(2))
                if chapter_ref in seen:
                    continue
                item = self._chapter_item(manga_ref, chapter_ref, "")
                if item:
                    seen.add(chapter_ref)
                    items.append(item)

        if not items:
            raise SourceError(tr('mangapill.mangapill_no_valid_chapters_found_for_this_title'), source=self.name,
                              kind="parser", context="chapter list")
        if len(self._chapter_cache) >= 16:
            oldest = min(self._chapter_cache, key=lambda key: self._chapter_cache[key][0])
            self._chapter_cache.pop(oldest, None)
        self._chapter_cache[manga_ref] = (now, [dict(item) for item in items])
        return items

    def chapters(self, ref, offset=0, limit=100, force=False):
        manga_ref = ref.get("id") if isinstance(ref, dict) else ref
        if not manga_ref:
            raise SourceError(tr('mangapill.mangapill_invalid_manga_reference'), source=self.name, kind="identity")
        items = self._all_chapters(str(manga_ref), force=force)
        offset = max(0, int(offset or 0))
        limit = max(1, int(limit or 100))
        batch = items[offset:offset + limit]
        return {"items": batch, "total": len(items), "next_offset": offset + len(batch)}

    def chapters_all(self, ref, page_size=100, max_pages=100, force=False):
        manga_ref = ref.get("id") if isinstance(ref, dict) else ref
        if not manga_ref:
            raise SourceError(tr('mangapill.mangapill_invalid_manga_reference'), source=self.name, kind="identity")
        items = self._all_chapters(str(manga_ref), force=force)
        return {"items": [dict(item) for item in items], "total": len(items), "next_offset": len(items)}

    def invalidate(self, ref=None):
        if ref is None:
            self._chapter_cache.clear()
            self._details_cache.clear()
            return
        manga_ref = ref.get("id") if isinstance(ref, dict) else ref
        if manga_ref:
            self._chapter_cache.pop(str(manga_ref), None)
            self._details_cache.pop(str(manga_ref), None)

    @staticmethod
    def _absolute(url):
        url = (url or "").strip()
        if url.startswith("//"):
            return "https:" + url
        if url.startswith("/"):
            return "https://mangapill.com" + url
        return url

    @staticmethod
    def _reader_image(attrs):
        classes = (attrs.get("class") or "").split()
        raw = attrs.get("data-src") or attrs.get("data-original") or attrs.get("src") or ""
        if not raw:
            return False
        if "js-page" in classes:
            return True
        alt = " ".join((attrs.get("alt") or attrs.get("title") or "").split())
        # "Page N" es señal semántica suficiente; no atamos el parser a un CDN concreto.
        if re.search(r"\bPage\s+[0-9]+\b", alt, re.I):
            return True
        return False

    def _page_urls(self, chapter):
        source_ref = chapter.get("source_ref") or {}
        chapter_ref = source_ref.get("chapter_ref")
        if not chapter_ref:
            raise SourceError(tr('mangapill.mangapill_invalid_chapter_reference'), source=self.name, kind="identity")
        path = "/chapters/{}".format(urllib.parse.quote(str(chapter_ref), safe="/-._"))
        page = self._get(path, timeout=10, context="reader", expected_any=["page", "Image", "img"])
        values = []
        for attrs in images(page):
            if not self._reader_image(attrs):
                continue
            url = self._absolute(attrs.get("data-src") or attrs.get("data-original") or attrs.get("src"))
            if url.startswith(("http://", "https://")) and url not in values:
                values.append(url)
        if not values:
            raise SourceError(tr('mangapill.mangapill_reader_returned_zero_validated_image_urls'), source=self.name,
                              kind="parser", context="reader")
        return values

    def prepare_pages(self, chapter, chapter_dir):
        urls = self._page_urls(chapter)
        source_ref = chapter.get("source_ref") or {}
        chapter_ref = source_ref.get("chapter_ref") or ""
        manga_ref = source_ref.get("manga_id") or chapter.get("title_id") or ""
        referer = self.base_url + "/chapters/" + urllib.parse.quote(str(chapter_ref), safe="/-._")
        identity = "{}:{}:{}".format(self.key, manga_ref, chapter_ref)
        return download_image_set(urls, chapter_dir, self.name, referer, timeout=24, workers=3,
                                  cache_identity=identity)

    def diagnostic(self):
        expected = "Vagabond"
        results = self.search(expected)
        exact = next((item for item in results if normalize_title(item.get("title")) == normalize_title(expected)), None)
        if exact is None:
            raise SourceError(tr('mangapill.mangapill_diagnostic_search_did_not_resolve_vagabond'), source=self.name, kind="identity")
        details = self.details(exact["ref"], force=True)
        chapters = self.chapters_all(exact["ref"], force=True)["items"]
        if not chapters:
            raise SourceError(tr('mangapill.mangapill_vagabond_returned_no_chapters'), source=self.name, kind="parser")
        chapter = chapters[-1]
        pages = self._page_urls(chapter)
        chapter_ref = (chapter.get("source_ref") or {}).get("chapter_ref") or ""
        referer = self.base_url + "/chapters/" + urllib.parse.quote(str(chapter_ref), safe="/-._")
        raw = request_bytes(pages[0], self.name, timeout=12, headers={"Referer": referer}, retries=1)
        if not looks_like_image(raw):
            raise SourceError(tr('mangapill.mangapill_first_test_page_was_not_an_image'), source=self.name,
                              kind="invalid_response")
        return {
            "source": self.key,
            "ok": True,
            "detail": tr('mangapill.e2e_ok_ch_reader_image_ok').format(details.get("title"), len(chapters)),
        }
