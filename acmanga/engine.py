from .i18n import tr
import copy
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from .errors import SourceError
from .settings import default_settings
from .util import normalize_title, relevance, titles_equivalent

DEFAULT_SOURCE_PRIORITY = ("mangakatana", "mangapill")
SEARCH_CACHE_TTL = 120


class MultiSourceEngine:
    def __init__(self, sources, preferences=None):
        self.sources = {source.key: source for source in sources}
        self.last_search_stats = {}
        self.last_error_details = {}
        self._search_cache = {}
        self._chapter_cache = {}
        self._source_report_cache = {}
        self.preferences = default_settings()
        self.update_preferences(preferences or {})

    def update_preferences(self, preferences):
        merged = default_settings()
        if isinstance(preferences, dict):
            for key in merged:
                if key in preferences:
                    merged[key] = preferences[key]
        priority = merged.get("source_priority") or list(DEFAULT_SOURCE_PRIORITY)
        if tuple(priority) not in (DEFAULT_SOURCE_PRIORITY, tuple(reversed(DEFAULT_SOURCE_PRIORITY))):
            priority = list(DEFAULT_SOURCE_PRIORITY)
        merged["source_priority"] = list(priority)
        mode = merged.get("source_mode") or "auto"
        if mode != "auto" and mode not in self.sources:
            merged["source_mode"] = "auto"
        self.preferences = merged
        self._search_cache.clear()
        self._chapter_cache.clear()
        self._source_report_cache.clear()

    def source(self, key):
        source = self.sources.get(key)
        if not source:
            raise SourceError(tr('engine.source_unavailable').format(key), source=key, kind="configuration")
        return source

    def _source_priority(self):
        return [key for key in (self.preferences.get("source_priority") or DEFAULT_SOURCE_PRIORITY) if key in self.sources]

    def _source_rank(self, key):
        try:
            return self._source_priority().index(key)
        except ValueError:
            return 99

    @staticmethod
    def _language_rank(language):
        return 0 if (language or "").lower() == "en" else 1

    def active_source_keys(self, all_sources=False):
        priority = self._source_priority()
        if all_sources:
            return priority
        mode = self.preferences.get("source_mode") or "auto"
        if mode != "auto":
            return [mode] if mode in self.sources else []
        return priority

    def _search_cache_key(self, query, all_sources):
        return (normalize_title(query), tuple(self.active_source_keys(all_sources=all_sources)))

    @staticmethod
    def _error_detail(exc):
        if isinstance(exc, SourceError):
            return exc.as_dict()
        return {
            "message": tr('engine.message').format(type(exc).__name__, exc),
            "source": None,
            "status": None,
            "kind": "internal",
            "transient": False,
            "context": None,
        }

    @staticmethod
    def _valid_search_item(item):
        if not isinstance(item, dict):
            return False
        return bool(item.get("source") and item.get("id") and normalize_title(item.get("title") or ""))

    def _search_sources(self, query, source_keys, progress_callback=None):
        flat = []
        errors = {}
        details = {}
        stats = {}
        per_source = {}
        sources = [self.sources[key] for key in source_keys if key in self.sources]
        with ThreadPoolExecutor(max_workers=max(1, len(sources))) as pool:
            jobs = [(source, pool.submit(source.search, query)) for source in sources]
            source_by_future = {future: source for source, future in jobs}
            for future in as_completed(source_by_future):
                source = source_by_future[future]
                try:
                    raw = future.result()
                    results = [dict(item) for item in (raw or []) if self._valid_search_item(item)]
                    per_source[source.key] = results
                    stats[source.key] = len(results)
                    flat.extend(results)
                except Exception as exc:
                    detail = self._error_detail(exc)
                    errors[source.key] = detail["message"]
                    details[source.key] = detail
                    stats[source.key] = 0
                    per_source[source.key] = []
                if progress_callback:
                    progress_callback(self._group_search_results(flat, query), dict(errors), dict(stats))
        return flat, errors, details, stats, per_source

    def _group_identity(self, variants, title):
        ordered = sorted(variants, key=lambda v: (self._source_rank(v.get("source")), str(v.get("id") or "")))
        if ordered:
            return "{}:{}".format(ordered[0].get("source"), ordered[0].get("id"))
        return "title:{}".format(normalize_title(title))

    @staticmethod
    def _item_names(item):
        names = []
        for value in [item.get("title")] + list(item.get("aliases") or []):
            value = str(value or "").strip()
            if value and value not in names:
                names.append(value)
        return names

    @classmethod
    def _item_relevance(cls, item, query):
        scores = [relevance(value, query) for value in cls._item_names(item)]
        return max(scores or [0])

    @classmethod
    def _items_name_equivalent(cls, left, right):
        return any(
            titles_equivalent(a, b)
            for a in cls._item_names(left)
            for b in cls._item_names(right)
        )

    def _make_group(self, items, query, disambiguator=""):
        variants = [dict(item) for item in items]
        variants.sort(key=lambda v: (self._source_rank(v.get("source")), self._language_rank(v.get("language")), str(v.get("id"))))
        best = max(variants, key=lambda item: (self._item_relevance(item, query), -self._source_rank(item.get("source"))))
        title = best.get("title") or "Untitled"
        identity = self._group_identity(variants, title)
        if disambiguator:
            identity = identity + "#" + disambiguator
        author = next((v.get("author") for v in variants if v.get("author")), "")
        aliases = []
        genres = []
        for variant in variants:
            # El título que usa la otra fuente también es un alias útil. Esto permite
            # conservar HIMA-TEN! / Himaten! sin alterar la identidad del manga.
            variant_title = str(variant.get("title") or "").strip()
            if variant_title and variant_title != title and variant_title not in aliases:
                aliases.append(variant_title)
            for alias in variant.get("aliases") or []:
                alias = str(alias or "").strip()
                if alias and alias != title and alias not in aliases:
                    aliases.append(alias)
            for genre in variant.get("genres") or []:
                if genre and genre not in genres:
                    genres.append(genre)
        return {
            "key": identity,
            "identity": identity,
            "title": title,
            "author": author,
            "aliases": aliases,
            "year": next((v.get("year") for v in variants if v.get("year")), ""),
            "status": next((v.get("status") for v in variants if v.get("status")), ""),
            "type": next((v.get("type") for v in variants if v.get("type")), "manga"),
            "genres": genres,
            "variants": variants,
        }

    @staticmethod
    def _metadata_conflict(a, b):
        year_a = str(a.get("year") or "").strip()
        year_b = str(b.get("year") or "").strip()
        if year_a and year_b and year_a != year_b:
            return True
        author_a = normalize_title(a.get("author") or "")
        author_b = normalize_title(b.get("author") or "")
        if author_a and author_b and author_a != author_b:
            # Los autores completos pueden tener orden distinto; comparar palabras significativas.
            words_a = {word for word in author_a.split() if len(word) > 2}
            words_b = {word for word in author_b.split() if len(word) > 2}
            if words_a and words_b and not words_a.intersection(words_b):
                return True
        return False

    def _bucket_has_metadata_conflict(self, items):
        for index, left in enumerate(items):
            for right in items[index + 1:]:
                if self._metadata_conflict(left, right):
                    return True
        return False

    def _group_search_results(self, flat, query):
        # Agrupación efímera y conservadora: título o alias equivalente. El número de
        # candidatos de MK+MP es pequeño, así que un recorrido cuadrático evita crear
        # claves fuzzy persistentes y permite casos como Kokou no Hito / The Climber.
        buckets = []
        for item in flat:
            if not self._item_names(item):
                continue
            matching = []
            for index, items in enumerate(buckets):
                if any(self._items_name_equivalent(item, existing) for existing in items):
                    matching.append(index)
            if not matching:
                buckets.append([item])
                continue
            first = matching[0]
            buckets[first].append(item)
            for index in reversed(matching[1:]):
                buckets[first].extend(buckets.pop(index))

        groups = []
        for items in buckets:
            counts = {}
            for item in items:
                source = item.get("source")
                counts[source] = counts.get(source, 0) + 1
            # No fusionar por título si una misma fuente ofrece dos identidades distintas.
            # Bibliomania 6082 y 8526 es el caso real de regresión.
            if any(count > 1 for count in counts.values()) or self._bucket_has_metadata_conflict(items):
                for index, item in enumerate(sorted(items, key=lambda x: (self._source_rank(x.get("source")), str(x.get("id"))))):
                    groups.append(self._make_group([item], query, disambiguator=str(index + 1)))
            else:
                groups.append(self._make_group(items, query))

        for group in groups:
            group["_score"] = max(self._item_relevance(v, query) for v in group.get("variants") or [])
        groups.sort(key=lambda g: (-g["_score"], g.get("title", "").casefold(), g.get("identity", "")))
        for group in groups:
            group.pop("_score", None)
        return groups[:20]

    def search(self, query, all_sources=False, force=False, progress_callback=None):
        query = " ".join(str(query or "").split())
        if not query:
            self.last_search_stats = {}
            self.last_error_details = {}
            return [], {}
        cache_key = self._search_cache_key(query, all_sources)
        cached = self._search_cache.get(cache_key)
        now = time.monotonic()
        if not force and cached and now - cached[0] < SEARCH_CACHE_TTL:
            self.last_search_stats = dict(cached[3])
            self.last_error_details = copy.deepcopy(cached[4])
            return copy.deepcopy(cached[1]), dict(cached[2])

        source_keys = self.active_source_keys(all_sources=all_sources)
        flat, errors, details, stats, _per_source = self._search_sources(query, source_keys, progress_callback=progress_callback)
        self.last_search_stats = stats
        self.last_error_details = details
        results = self._group_search_results(flat, query)
        self._search_cache[cache_key] = (now, copy.deepcopy(results), dict(errors), dict(stats), copy.deepcopy(details))
        if len(self._search_cache) > 24:
            oldest = min(self._search_cache, key=lambda key: self._search_cache[key][0])
            self._search_cache.pop(oldest, None)
        return results, errors

    @staticmethod
    def _variant_marker(variant):
        return (str(variant.get("source") or ""), str(variant.get("id") or ""))

    def _match_group(self, manga, results):
        old_markers = {self._variant_marker(v) for v in (manga.get("variants") or [])}
        by_marker = []
        for item in results:
            new_markers = {self._variant_marker(v) for v in (item.get("variants") or [])}
            if old_markers and old_markers.intersection(new_markers):
                by_marker.append(item)
        if len(by_marker) == 1:
            return by_marker[0]
        def names_of(value):
            names = []
            for name in [value.get("title")] + list(value.get("aliases") or []):
                if name and name not in names:
                    names.append(name)
            for variant in value.get("variants") or []:
                for name in [variant.get("title")] + list(variant.get("aliases") or []):
                    if name and name not in names:
                        names.append(name)
            return names

        wanted = names_of(manga)
        matching = []
        for item in results:
            candidate = names_of(item)
            if any(titles_equivalent(left, right) for left in wanted for right in candidate):
                matching.append(item)
        return matching[0] if len(matching) == 1 else None

    def resolve_manga(self, manga, force=False):
        merged = copy.deepcopy(manga)
        variants = [dict(v) for v in (manga.get("variants") or []) if v.get("source") in self.sources]
        if not variants:
            return merged, {}
        errors = {}
        resolved = []

        def work(variant):
            source = self.source(variant.get("source"))
            ref = variant.get("ref") or {"id": variant.get("id")}
            details = getattr(source, "details", None)
            if not callable(details):
                # Compatibilidad segura con adaptadores de prueba/antiguos: no inventar metadatos.
                return dict(variant)
            return details(ref, force=force)

        with ThreadPoolExecutor(max_workers=max(1, len(variants))) as pool:
            jobs = [(variant, pool.submit(work, variant)) for variant in variants]
            for original, future in jobs:
                try:
                    detail = dict(future.result() or {})
                    if str(detail.get("id") or "") != str(original.get("id") or ""):
                        raise SourceError(tr('engine.metadata_identity_mismatch').format(original.get("source")),
                                          source=original.get("source"), kind="identity")
                    combined = dict(original)
                    for key in ("title", "author", "aliases", "year", "status", "type", "genres", "language", "ref", "metadata_state"):
                        if detail.get(key) not in (None, "", []):
                            combined[key] = copy.deepcopy(detail.get(key))
                    resolved.append(combined)
                except Exception as exc:
                    errors[original.get("source")] = self._error_detail(exc)["message"]
                    resolved.append(original)

        resolved.sort(key=lambda v: (self._source_rank(v.get("source")), str(v.get("id"))))
        if resolved:
            preferred_variant = resolved[0]
            compatible = [preferred_variant]
            for candidate in resolved[1:]:
                if self._metadata_conflict(preferred_variant, candidate):
                    errors[candidate.get("source")] = tr('engine.metadata_identity_conflict_variant_kept_separate').format(candidate.get("source"))
                    continue
                compatible.append(candidate)
            resolved = compatible
        merged["variants"] = resolved
        preferred = resolved[0] if resolved else {}
        if preferred.get("title"):
            merged["title"] = preferred.get("title")
        for field in ("author", "year", "status", "type"):
            value = next((v.get(field) for v in resolved if v.get(field)), merged.get(field) or "")
            merged[field] = value
        aliases = []
        genres = []
        for alias in manga.get("aliases") or []:
            alias = str(alias or "").strip()
            if alias and alias != merged.get("title") and alias not in aliases:
                aliases.append(alias)
        for variant in resolved:
            variant_title = str(variant.get("title") or "").strip()
            if variant_title and variant_title != merged.get("title") and variant_title not in aliases:
                aliases.append(variant_title)
            for alias in variant.get("aliases") or []:
                alias = str(alias or "").strip()
                if alias and alias != merged.get("title") and alias not in aliases:
                    aliases.append(alias)
            for genre in variant.get("genres") or []:
                if genre and genre not in genres:
                    genres.append(genre)
        merged["aliases"] = aliases
        merged["genres"] = genres
        # La identidad de una ficha ya abierta/guardada no cambia al enriquecerla.
        merged["identity"] = manga.get("identity") or manga.get("key") or self._group_identity(resolved, merged.get("title"))
        merged["key"] = manga.get("key") or merged["identity"]
        return merged, errors

    @staticmethod
    def _chapter_number_value(value):
        text = str(value or "").strip().replace(",", ".")
        match = re.search(r"([0-9]+(?:\.[0-9]+)?)", text)
        if not match:
            return None
        try:
            return float(match.group(1))
        except ValueError:
            return None

    @classmethod
    def _chapter_key(cls, chapter):
        number = cls._chapter_number_value(chapter.get("number"))
        if number is not None:
            return "n:{:.4f}".format(number)
        text = normalize_title(chapter.get("number") or chapter.get("name") or chapter.get("id"))
        return "t:" + text

    @staticmethod
    def _group_key(chapter):
        groups = chapter.get("groups") or []
        return normalize_title(" ".join(str(x) for x in groups if x))

    def _chapter_preference(self, chapter):
        return (self._source_rank(chapter.get("source")), self._language_rank(chapter.get("language")))

    @staticmethod
    def _manga_identity(manga):
        identity = manga.get("identity") or manga.get("key") or normalize_title(manga.get("title") or "")
        variants = []
        for variant in manga.get("variants") or []:
            source = variant.get("source") or ""
            if source not in DEFAULT_SOURCE_PRIORITY:
                continue
            variants.append((source, str(variant.get("id") or "")))
        variants.sort()
        return (identity, tuple(variants))

    def _manga_cache_key(self, manga, all_sources=False):
        return (self._manga_identity(manga), tuple(self.active_source_keys(all_sources=all_sources)), tuple(self._source_priority()))

    def _active_variants(self, manga, all_sources=False):
        allowed = set(self.active_source_keys(all_sources=all_sources))
        variants = [v for v in (manga.get("variants") or []) if v.get("source") in allowed]
        variants.sort(key=lambda v: (self._source_rank(v.get("source")), self._language_rank(v.get("language"))))
        return variants

    @staticmethod
    def _call_chapters_all(source, ref, force=False):
        try:
            return source.chapters_all(ref, force=force)
        except TypeError as exc:
            # Compatibilidad con fuentes de prueba/terceras que aún no acepten force.
            if "force" not in str(exc):
                raise
            return source.chapters_all(ref)

    def _merge_chapter_number_bucket(self, candidates):
        by_source = {}
        for chapter in candidates:
            by_source.setdefault(chapter.get("source"), []).append(chapter)
        results = []
        # Caso inequívoco: como máximo un candidato por fuente -> fallback por número.
        if all(len(items) <= 1 for items in by_source.values()):
            ordered = sorted(candidates, key=self._chapter_preference)
            chosen = dict(ordered[0])
            if len(ordered) > 1:
                chosen["_alternates"] = [dict(item) for item in ordered[1:]]
            return [chosen]

        # Caso ambiguo (p. ej. MangaPill Group 2 + edición normal): sólo fusionar grupos
        # equivalentes; los demás capítulos permanecen visibles e independientes.
        by_group = {}
        for chapter in candidates:
            by_group.setdefault(self._group_key(chapter), []).append(chapter)
        for group_items in by_group.values():
            group_sources = {}
            for item in group_items:
                group_sources.setdefault(item.get("source"), []).append(item)
            if all(len(items) <= 1 for items in group_sources.values()):
                ordered = sorted(group_items, key=self._chapter_preference)
                chosen = dict(ordered[0])
                if len(ordered) > 1:
                    chosen["_alternates"] = [dict(item) for item in ordered[1:]]
                results.append(chosen)
            else:
                results.extend(dict(item) for item in group_items)
        return results

    def chapters(self, manga, force=False, all_sources=False):
        cache_key = self._manga_cache_key(manga, all_sources=all_sources)
        if not force and cache_key in self._chapter_cache:
            cached = self._chapter_cache[cache_key]
            return copy.deepcopy(cached[0]), dict(cached[1]), dict(cached[2])

        chapters = []
        errors = {}
        totals = {}
        variants = self._active_variants(manga, all_sources=all_sources)
        with ThreadPoolExecutor(max_workers=max(1, len(variants))) as pool:
            jobs = []
            for variant in variants:
                key = variant.get("source")
                source = self.source(key)
                ref = variant.get("ref") or {"id": variant.get("id")}
                jobs.append((key, pool.submit(self._call_chapters_all, source, ref, force)))
            for key, future in jobs:
                try:
                    batch = future.result()
                    source_items = batch.get("items") or []
                    chapters.extend(source_items)
                    totals[key] = len(source_items)
                except Exception as exc:
                    errors[key] = self._error_detail(exc)["message"]
                    totals[key] = 0

        numbered = {}
        for chapter in chapters:
            numbered.setdefault(self._chapter_key(chapter), []).append(chapter)
        result = []
        for candidates in numbered.values():
            result.extend(self._merge_chapter_number_bucket(candidates))
        result.sort(key=lambda ch: (
            self._chapter_number_value(ch.get("number")) is not None,
            self._chapter_number_value(ch.get("number")) if self._chapter_number_value(ch.get("number")) is not None else -1,
            self._group_key(ch),
            ch.get("name") or "",
        ), reverse=True)
        self._chapter_cache[cache_key] = (copy.deepcopy(result), dict(errors), dict(totals))
        return result, errors, totals

    def chapter_alternates(self, chapter):
        return [dict(item) for item in (chapter.get("_alternates") or [])]

    def _probe_missing_source(self, key, manga, force=False):
        source = self.source(key)
        title = manga.get("title") or ""
        entry = {"source": key, "present": False, "status": "unknown", "verified": False,
                 "chapters": 0, "languages": [], "error": "", "variant": None}
        try:
            results = source.search(title)
        except Exception as exc:
            entry["status"] = "error"
            entry["error"] = self._error_detail(exc)["message"]
            return entry
        wanted = {normalize_title(title)}
        wanted.update(normalize_title(alias) for alias in (manga.get("aliases") or []) if alias)
        matches = []
        for item in results or []:
            names = {normalize_title(item.get("title") or "")}
            names.update(normalize_title(alias) for alias in (item.get("aliases") or []) if alias)
            if wanted.intersection(names):
                matches.append(item)
        if len(matches) == 0:
            search_status = getattr(source, "last_search_status", "unknown")
            # Un resultado vacío sólo demuestra ausencia si el adaptador reconoció de forma
            # explícita una pantalla de "sin resultados". HTML cambiado, redirects que no
            # pudieron resolverse o candidatos no exactos se muestran como no comprobados.
            if search_status == "not_found":
                entry["status"] = "not_found"
                entry["verified"] = True
            elif results:
                entry["status"] = "ambiguous"
                entry["verified"] = False
                entry["error"] = tr('engine.search_returned_candidates_but_no_exact_identity').format(source.name)
            else:
                entry["status"] = "unknown"
                entry["verified"] = False
            return entry
        if len(matches) > 1:
            entry["status"] = "ambiguous"
            entry["verified"] = True
            entry["error"] = tr('engine.multiple_exact_identities').format(source.name)
            return entry
        variant = matches[0]
        entry["variant"] = dict(variant)
        entry["present"] = True
        entry["status"] = "found"
        entry["verified"] = True
        try:
            ref = variant.get("ref") or {"id": variant.get("id")}
            items = self._call_chapters_all(source, ref, force=force).get("items") or []
            entry["chapters"] = len(items)
            entry["languages"] = ["en"] if items else []
        except Exception as exc:
            entry["status"] = "error"
            entry["error"] = self._error_detail(exc)["message"]
        return entry

    def source_report(self, manga, force=False):
        identity = self._manga_identity(manga)
        if not force and identity in self._source_report_cache:
            return copy.deepcopy(self._source_report_cache[identity])
        by_source = {}
        for variant in manga.get("variants") or []:
            key = variant.get("source")
            if key in self.sources and key not in by_source:
                by_source[key] = variant

        def inspect(key):
            variant = by_source.get(key)
            if variant is None:
                return key, self._probe_missing_source(key, manga, force=force)
            entry = {"source": key, "present": True, "status": "found", "verified": True,
                     "chapters": 0, "languages": [], "error": "", "variant": dict(variant)}
            try:
                source = self.source(key)
                ref = variant.get("ref") or {"id": variant.get("id")}
                items = self._call_chapters_all(source, ref, force=force).get("items") or []
                entry["chapters"] = len(items)
                entry["languages"] = ["en"] if items else []
            except Exception as exc:
                entry["status"] = "error"
                entry["error"] = self._error_detail(exc)["message"]
            return key, entry

        report = {}
        keys = self._source_priority()
        with ThreadPoolExecutor(max_workers=max(1, len(keys))) as pool:
            futures = [pool.submit(inspect, key) for key in keys]
            for future in futures:
                key, entry = future.result()
                report[key] = entry
        self._source_report_cache[identity] = copy.deepcopy(report)
        return report

    def diagnostic_sources(self):
        result = {}
        keys = self._source_priority()

        def run(key):
            source = self.sources.get(key)
            if source is None:
                return key, {"source": key, "ok": False, "detail": tr('engine.source_module_missing')}
            try:
                data = source.diagnostic()
                if not isinstance(data, dict):
                    data = {"ok": True, "detail": str(data)}
                data.setdefault("source", key)
                data.setdefault("ok", True)
                data.setdefault("detail", "available")
            except Exception as exc:
                detail = self._error_detail(exc)
                data = {"source": key, "ok": False, "detail": detail["message"], "kind": detail["kind"]}
            return key, data

        with ThreadPoolExecutor(max_workers=max(1, len(keys))) as pool:
            futures = [pool.submit(run, key) for key in keys]
            for future in futures:
                key, data = future.result()
                result[key] = data
        return result

    def invalidate_chapters(self, manga=None):
        if manga is None:
            self._chapter_cache.clear()
            self._source_report_cache.clear()
            for source in self.sources.values():
                try:
                    source.invalidate()
                except Exception:
                    pass
            return
        target = self._manga_identity(manga)[0]
        for key in list(self._chapter_cache):
            if key[0][0] == target:
                self._chapter_cache.pop(key, None)
        for key in list(self._source_report_cache):
            if key[0] == target:
                self._source_report_cache.pop(key, None)
        for variant in manga.get("variants") or []:
            source = self.sources.get(variant.get("source"))
            if source is not None:
                try:
                    source.invalidate(variant.get("ref") or {"id": variant.get("id")})
                except Exception:
                    pass

    def next_chapter(self, chapters, current):
        if not chapters or not current:
            return None
        exact = None
        for index, item in enumerate(chapters):
            if item.get("source") == current.get("source") and str(item.get("id")) == str(current.get("id")):
                exact = index
                break
        if exact is None:
            current_key = self._chapter_key(current)
            current_group = self._group_key(current)
            for index, item in enumerate(chapters):
                if self._chapter_key(item) == current_key and self._group_key(item) == current_group:
                    exact = index
                    break
        if exact is None or exact <= 0:
            return None
        return chapters[exact - 1]

    def previous_chapter(self, chapters, current):
        return self.next_chapter(list(reversed(chapters)), current)

    def find_chapter(self, chapters, number):
        wanted = self._chapter_number_value(number)
        if wanted is None:
            return None, None
        best_index = None
        best_delta = None
        for index, chapter in enumerate(chapters):
            value = self._chapter_number_value(chapter.get("number"))
            if value is None:
                continue
            delta = abs(value - wanted)
            if best_delta is None or delta < best_delta:
                best_delta = delta
                best_index = index
                if delta == 0:
                    break
        if best_index is None:
            return None, None
        return best_index, chapters[best_index]

    def refresh_manga(self, manga):
        title = manga.get("title") or ""
        source_keys = self.active_source_keys(all_sources=True)
        flat, search_errors, details, stats, per_source = self._search_sources(title, source_keys)
        self.last_search_stats = stats
        self.last_error_details = details
        results = self._group_search_results(flat, title)
        fresh = self._match_group(manga, results)

        old_variants = {self._variant_marker(v): dict(v) for v in (manga.get("variants") or []) if v.get("source") in self.sources}
        updated = dict(old_variants)
        if fresh is not None:
            for variant in fresh.get("variants") or []:
                updated[self._variant_marker(variant)] = dict(variant)

        # Una búsqueda que no devuelve la variante NO basta para borrarla. Verificamos el ref
        # conocido; sólo un 404 explícito autoriza retirarlo. Timeouts/parser/challenge conservan.
        for marker, old in list(old_variants.items()):
            key = old.get("source")
            if key in search_errors:
                continue
            source = self.sources.get(key)
            if source is None:
                continue
            try:
                detail = source.details(old.get("ref") or {"id": old.get("id")}, force=True)
                combined = dict(old)
                for field in ("title", "author", "aliases", "year", "status", "type", "genres", "ref", "metadata_state"):
                    if detail.get(field) not in (None, "", []):
                        combined[field] = copy.deepcopy(detail.get(field))
                updated[marker] = combined
            except SourceError as exc:
                if exc.kind == "not_found" or exc.status == 404:
                    updated.pop(marker, None)
                else:
                    search_errors.setdefault(key, str(exc))
            except Exception as exc:
                search_errors.setdefault(key, self._error_detail(exc)["message"])

        merged = copy.deepcopy(manga)
        merged["variants"] = sorted(updated.values(), key=lambda v: (self._source_rank(v.get("source")), str(v.get("id"))))
        merged, resolve_errors = self.resolve_manga(merged, force=True)
        for key, message in resolve_errors.items():
            search_errors.setdefault(key, message)
        self.invalidate_chapters(merged)
        return merged, search_errors

    def chapter_from_progress(self, manga, progress):
        if not progress:
            return None
        source_key = progress.get("source")
        chapter_id = progress.get("chapter_id")
        if source_key in self.sources and chapter_id:
            return {
                "source": source_key,
                "id": str(chapter_id),
                "title_id": progress.get("title_id"),
                "number": progress.get("chapter_number") or "",
                "name": progress.get("chapter_name") or "",
                "language": "en",
                "groups": list(progress.get("groups") or []),
                "pages_hint": progress.get("total_pages") or 0,
                "source_ref": dict(progress.get("source_ref") or {}),
            }
        if progress.get("chapter_number"):
            return {
                "source": "",
                "id": "legacy-progress",
                "number": progress.get("chapter_number") or "",
                "name": progress.get("chapter_name") or "",
                "groups": list(progress.get("groups") or []),
            }
        return None
