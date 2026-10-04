import json
import time
from pathlib import Path

from .util import atomic_json_write, normalize_title

SCHEMA = 6
ACTIVE_SOURCES = {"mangakatana", "mangapill"}
SOURCE_ORDER = ("mangakatana", "mangapill")



def default_availability():
    return {
        "status": "never",
        "known_total": 0,
        "latest_number": "",
        "source_totals": {},
        "known_keys": [],
        "new_keys": [],
        "checked_at": 0,
        "attempted_at": 0,
        "error_sources": [],
    }


def _clean_availability(value):
    base = default_availability()
    if not isinstance(value, dict):
        return base
    status = str(value.get("status") or "never")
    if status not in ("never", "ok", "partial", "error"):
        status = "never"
    base["status"] = status
    for field in ("known_total", "checked_at", "attempted_at"):
        try:
            base[field] = max(0, int(value.get(field) or 0))
        except (TypeError, ValueError):
            base[field] = 0
    base["latest_number"] = str(value.get("latest_number") or "")
    totals = value.get("source_totals") if isinstance(value.get("source_totals"), dict) else {}
    for source in SOURCE_ORDER:
        if source not in totals:
            continue
        try:
            base["source_totals"][source] = max(0, int(totals.get(source) or 0))
        except (TypeError, ValueError):
            pass
    for field in ("known_keys", "new_keys"):
        raw = value.get(field) if isinstance(value.get(field), (list, tuple)) else []
        seen = set()
        cleaned = []
        for item in raw:
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            cleaned.append(text)
        base[field] = cleaned
    errors = value.get("error_sources") if isinstance(value.get("error_sources"), (list, tuple)) else []
    base["error_sources"] = [source for source in SOURCE_ORDER if source in errors]
    if base["known_total"] < len(base["known_keys"]):
        base["known_total"] = len(base["known_keys"])
    base["new_keys"] = [key for key in base["new_keys"] if key in set(base["known_keys"])]
    return base


def default_state():
    return {"schema": SCHEMA, "saved": {}, "progress": {}, "last_read": None}


def _variant_marker(variant):
    if not isinstance(variant, dict):
        return None
    source = str(variant.get("source") or "")
    ident = variant.get("id") or (variant.get("ref") or {}).get("id")
    if source not in ACTIVE_SOURCES or not ident:
        return None
    return source, str(ident)


def _identity_from_variants(variants, title):
    valid = []
    for variant in variants or []:
        marker = _variant_marker(variant)
        if marker:
            valid.append(marker)
    valid.sort(key=lambda marker: (SOURCE_ORDER.index(marker[0]) if marker[0] in SOURCE_ORDER else 99, marker[1]))
    if valid:
        return "{}:{}".format(valid[0][0], valid[0][1])
    return "legacy:{}".format(normalize_title(title) or "saved")


def manga_identity(manga):
    if isinstance(manga, str):
        return manga
    if not isinstance(manga, dict):
        return ""
    identity = str(manga.get("identity") or manga.get("key") or "")
    if identity and (identity.startswith("mangakatana:") or identity.startswith("mangapill:") or identity.startswith("legacy:")):
        return identity
    return _identity_from_variants(manga.get("variants") or [], manga.get("title") or "")


def _clean_variant(variant):
    if not isinstance(variant, dict):
        return None
    source = str(variant.get("source") or "")
    if source not in ACTIVE_SOURCES:
        return None
    ref = variant.get("ref") if isinstance(variant.get("ref"), dict) else {}
    ident = variant.get("id") or ref.get("id") or ref.get("slug")
    if not ident:
        return None
    clean = {
        "source": source,
        "id": str(ident),
        "title": str(variant.get("title") or ""),
        "author": str(variant.get("author") or ""),
        "aliases": [str(x) for x in (variant.get("aliases") or []) if str(x or "").strip()],
        "year": str(variant.get("year") or ""),
        "status": str(variant.get("status") or ""),
        "type": str(variant.get("type") or "manga"),
        "genres": [str(x) for x in (variant.get("genres") or []) if str(x or "").strip()],
        "language": "en",
        "ref": dict(ref),
    }
    clean["ref"].setdefault("id", str(ident))
    return clean


def _clean_saved(old_key, rec):
    if not isinstance(rec, dict):
        return None
    title = str(rec.get("title") or rec.get("manga_title") or "Untitled")
    variants = []
    seen = set()
    for variant in rec.get("variants") or []:
        clean = _clean_variant(variant)
        if not clean:
            continue
        marker = _variant_marker(clean)
        if marker in seen:
            continue
        seen.add(marker)
        variants.append(clean)
    identity = str(rec.get("identity") or "")
    valid_identity = identity.startswith("mangakatana:") or identity.startswith("mangapill:") or identity.startswith("legacy:")
    if not valid_identity:
        identity = _identity_from_variants(variants, title)
    raw_aliases = rec.get("aliases") if isinstance(rec.get("aliases"), (list, tuple)) else []
    aliases = [str(x) for x in raw_aliases if str(x or "").strip()]
    try:
        saved_at = int(rec.get("saved_at") or time.time())
    except (TypeError, ValueError):
        saved_at = int(time.time())
    raw_genres = rec.get("genres") if isinstance(rec.get("genres"), (list, tuple)) else []
    return identity, {
        "key": identity,
        "identity": identity,
        "title": title,
        "author": str(rec.get("author") or ""),
        "aliases": aliases,
        "year": str(rec.get("year") or ""),
        "status": str(rec.get("status") or ""),
        "type": str(rec.get("type") or "manga"),
        "genres": [str(x) for x in raw_genres if str(x or "").strip()],
        "variants": variants,
        "saved_at": saved_at,
        "availability": _clean_availability(rec.get("availability")),
    }



def _clean_progress_manga(value, fallback=None):
    """Snapshot minimo para reabrir una lectura desde Historial.

    No crea una segunda coleccion: vive dentro del propio registro de progreso.
    """
    fallback = fallback if isinstance(fallback, dict) else {}
    raw = value if isinstance(value, dict) else {}
    title = str(raw.get("title") or fallback.get("title") or fallback.get("manga_title") or "Untitled")
    variants = []
    seen = set()
    for variant in raw.get("variants") or []:
        clean = _clean_variant(variant)
        if not clean:
            continue
        marker = _variant_marker(clean)
        if marker in seen:
            continue
        seen.add(marker)
        variants.append(clean)

    # Registros 0.6.1 no incluian snapshot. Reconstruye una referencia minima
    # desde la fuente/titulo-id del progreso para que sigan siendo reabribles.
    if not variants:
        source = str(fallback.get("source") or "")
        source_ref = fallback.get("source_ref") if isinstance(fallback.get("source_ref"), dict) else {}
        ident = fallback.get("title_id") or source_ref.get("manga_id") or source_ref.get("title_id")
        if source in ACTIVE_SOURCES and ident:
            clean = _clean_variant({
                "source": source,
                "id": str(ident),
                "title": title,
                "language": "en",
                "ref": {"id": str(ident)},
            })
            if clean:
                variants.append(clean)

    raw_aliases = raw.get("aliases") if isinstance(raw.get("aliases"), (list, tuple)) else []
    raw_genres = raw.get("genres") if isinstance(raw.get("genres"), (list, tuple)) else []
    identity = str(raw.get("identity") or raw.get("key") or "")
    if not (identity.startswith("mangakatana:") or identity.startswith("mangapill:") or identity.startswith("legacy:")):
        identity = _identity_from_variants(variants, title)
    return {
        "key": identity,
        "identity": identity,
        "title": title,
        "author": str(raw.get("author") or ""),
        "aliases": [str(x) for x in raw_aliases if str(x or "").strip()],
        "year": str(raw.get("year") or ""),
        "status": str(raw.get("status") or ""),
        "type": str(raw.get("type") or "manga"),
        "genres": [str(x) for x in raw_genres if str(x or "").strip()],
        "variants": variants,
    }


def _progress_manga_snapshot(manga):
    raw = manga if isinstance(manga, dict) else {}
    return _clean_progress_manga(raw, raw)

def clean_reader_view(value):
    """Optional schema-6 extension; old progress remains fully readable."""
    import math
    value = value if isinstance(value, dict) else {}
    clean = {}
    for key, default, low, high in [('position',0,0,1),('horizontal',0,-1,1),('zoom',0,-2,3)]:
        try:
            number = float(value.get(key, default))
            if not math.isfinite(number):
                number = default
        except (TypeError, ValueError):
            number = default
        clean[key] = round(max(low, min(high, number)), 6)
    clean['fit'] = 'page' if value.get('fit') == 'page' else 'width'
    return clean


def _clean_progress(rec):
    if not isinstance(rec, dict):
        return None
    try:
        page = max(1, int(rec.get("page") or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        total = max(0, int(rec.get("total_pages") or 0))
    except (TypeError, ValueError):
        total = 0
    try:
        updated = int(rec.get("updated") or time.time())
    except (TypeError, ValueError):
        updated = int(time.time())
    source = str(rec.get("source") or "")
    source_active = source in ACTIVE_SOURCES
    clean = {
        "title": str(rec.get("title") or rec.get("manga_title") or "Untitled"),
        "source": source if source_active else "",
        "chapter_id": rec.get("chapter_id") if source_active else None,
        "title_id": rec.get("title_id") if source_active else None,
        "chapter_number": str(rec.get("chapter_number") or rec.get("chapter") or ""),
        "chapter_name": str(rec.get("chapter_name") or rec.get("chapter_title") or ""),
        "language": "en",
        "groups": [str(x) for x in (rec.get("groups") if isinstance(rec.get("groups"), (list, tuple)) else []) if str(x or "").strip()],
        "source_ref": dict(rec.get("source_ref") or {}) if source_active and isinstance(rec.get("source_ref"), dict) else {},
        "page": page,
        "total_pages": total,
        "updated": updated,
    }
    if isinstance(rec.get("view"), dict):
        clean["view"] = clean_reader_view(rec["view"])
    if rec.get("reader_mode") in ("width", "page"):
        clean["reader_mode"] = rec["reader_mode"]
    clean["manga"] = _clean_progress_manga(rec.get("manga"), clean)
    return clean


def _sanitize(data):
    state = default_state()
    if not isinstance(data, dict):
        return state

    key_map = {}
    saved_input = data.get("saved") if isinstance(data.get("saved"), dict) else {}
    progress_input = data.get("progress") if isinstance(data.get("progress"), dict) else {}
    for old_key, rec in saved_input.items():
        result = _clean_saved(old_key, rec)
        if not result:
            continue
        key, clean = result
        # Si dos legados colisionan, conserva ambos con sufijo estable en vez de fusionarlos.
        candidate = key
        index = 2
        while candidate in state["saved"]:
            candidate = "{}#{}".format(key, index)
            index += 1
        clean["key"] = candidate
        clean["identity"] = candidate
        key_map[str(old_key)] = candidate
        state["saved"][candidate] = clean

    progress_key_map = {}
    for old_key, rec in progress_input.items():
        clean = _clean_progress(rec)
        if not clean:
            continue
        title = clean.get("title") or "Untitled"
        old_text = str(old_key)
        if old_text in key_map:
            key = key_map[old_text]
        elif old_text.startswith("mangakatana:") or old_text.startswith("mangapill:") or old_text.startswith("legacy:"):
            # 0.6.1 ya guardaba progreso de lecturas no guardadas con identidad estable.
            # No lo rebajes a legacy al migrar: Historial necesita poder reabrirlo.
            key = old_text
        else:
            key = "legacy:{}".format(normalize_title(title) or old_text)
        progress_key_map[old_text] = key
        if isinstance(clean.get("manga"), dict):
            clean["manga"]["key"] = key
            # Mantiene la identidad de proveedor reconstruida si existe; para legados
            # puros usa la misma clave del progreso.
            ident = str(clean["manga"].get("identity") or "")
            if not (ident.startswith("mangakatana:") or ident.startswith("mangapill:")):
                clean["manga"]["identity"] = key
        state["progress"][key] = clean
        if key not in state["saved"] and data.get("schema") == 1:
            state["saved"][key] = {
                "key": key, "identity": key, "title": title, "author": "", "aliases": [],
                "year": "", "status": "", "type": "manga", "genres": [], "variants": [],
                "saved_at": int(time.time()), "availability": default_availability(),
            }

    last_read = data.get("last_read")
    if last_read and str(last_read) in key_map:
        last_read = key_map[str(last_read)]
    elif last_read and str(last_read) in progress_key_map:
        last_read = progress_key_map[str(last_read)]
    if last_read and str(last_read) in state["progress"]:
        state["last_read"] = str(last_read)
    elif data.get("last_manga_id"):
        old = str(data.get("last_manga_id"))
        mapped = key_map.get(old)
        if mapped in state["progress"]:
            state["last_read"] = mapped
    return state


def load_state(path):
    path = Path(path)
    if not path.exists():
        return default_state()
    try:
        with open(str(path), "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError, TypeError):
        return default_state()
    return _sanitize(data)


def save_state(path, state):
    clean = _sanitize(state)
    state.clear()
    state.update(clean)
    atomic_json_write(path, state)


def _markers_from_manga(manga):
    markers = set()
    if isinstance(manga, dict):
        for variant in manga.get("variants") or []:
            marker = _variant_marker(variant)
            if marker:
                markers.add(marker)
    return markers


def _find_key(mapping, manga):
    if isinstance(manga, str):
        return manga if manga in mapping else None
    if not isinstance(manga, dict):
        return None
    for candidate in (manga.get("key"), manga.get("identity")):
        if candidate and str(candidate) in mapping:
            return str(candidate)
    wanted_markers = _markers_from_manga(manga)
    if wanted_markers:
        marker_matches = []
        for key, rec in mapping.items():
            if wanted_markers.intersection(_markers_from_manga(rec)):
                marker_matches.append(key)
        if len(marker_matches) == 1:
            return marker_matches[0]
    wanted_names = {normalize_title(manga.get("title") or "")}
    wanted_names.update(normalize_title(x) for x in (manga.get("aliases") or []) if x)
    wanted_names.discard("")
    title_matches = []
    explicit = str(manga.get("identity") or manga.get("key") or "")
    explicit_provider_identity = explicit.startswith("mangakatana:") or explicit.startswith("mangapill:")
    for key, rec in mapping.items():
        # Una identidad de proveedor nueva no puede apropiarse por título de otra identidad
        # ya estable. Sí puede reclamar un registro legacy durante la migración.
        if explicit_provider_identity and not str(key).startswith("legacy:"):
            continue
        names = {normalize_title(rec.get("title") or "")}
        names.update(normalize_title(x) for x in (rec.get("aliases") or []) if x)
        if wanted_names.intersection(names):
            title_matches.append(key)
    return title_matches[0] if len(title_matches) == 1 else None


def state_key_for(state, manga):
    return _find_key(state.get("saved") or {}, manga) or _find_key(state.get("progress") or {}, manga) or manga_identity(manga)


def _move_key(state, old_key, new_key):
    if not old_key or not new_key or old_key == new_key:
        return new_key or old_key
    saved = state.setdefault("saved", {})
    progress = state.setdefault("progress", {})
    if old_key in saved:
        if new_key not in saved:
            saved[new_key] = saved[old_key]
            saved[new_key]["key"] = new_key
            saved[new_key]["identity"] = new_key
        saved.pop(old_key, None)
    if old_key in progress:
        old_progress = progress.get(old_key) or {}
        new_progress = progress.get(new_key) or {}
        try:
            old_updated = int(old_progress.get("updated") or 0)
        except (TypeError, ValueError):
            old_updated = 0
        try:
            new_updated = int(new_progress.get("updated") or 0)
        except (TypeError, ValueError):
            new_updated = 0
        if new_key not in progress or old_updated > new_updated:
            progress[new_key] = old_progress
        progress.pop(old_key, None)
    if state.get("last_read") == old_key:
        state["last_read"] = new_key
    return new_key


def save_manga(path, state, manga):
    variants = []
    seen = set()
    for variant in manga.get("variants") or []:
        clean = _clean_variant(variant)
        if clean:
            marker = _variant_marker(clean)
            if marker not in seen:
                seen.add(marker)
                variants.append(clean)
    old_key = _find_key(state.get("saved") or {}, manga)
    desired = manga_identity(dict(manga, variants=variants))
    if old_key and not str(old_key).startswith("legacy:"):
        key = old_key
    else:
        key = desired
        if old_key and old_key != key:
            _move_key(state, old_key, key)
    old = (state.get("saved") or {}).get(key) or {}
    item = {
        "key": key,
        "identity": desired,
        "title": manga.get("title") or "Untitled",
        "author": manga.get("author") or "",
        "aliases": [str(x) for x in (manga.get("aliases") or []) if str(x or "").strip()],
        "year": str(manga.get("year") or ""),
        "status": str(manga.get("status") or ""),
        "type": str(manga.get("type") or "manga"),
        "genres": [str(x) for x in (manga.get("genres") or []) if str(x or "").strip()],
        "variants": variants,
        "saved_at": int(old.get("saved_at") or time.time()),
        "availability": _clean_availability(old.get("availability")),
    }
    state.setdefault("saved", {})[key] = item
    save_state(path, state)
    return state.get("saved", {}).get(key, item)


def remove_saved(path, state, manga):
    key = _find_key(state.get("saved") or {}, manga)
    if key:
        state.setdefault("saved", {}).pop(key, None)
    save_state(path, state)


def is_saved(state, manga):
    return _find_key(state.get("saved") or {}, manga) is not None


def save_progress(path, state, manga, chapter, page, total_pages, view_state=None, reader_mode=None):
    key = state_key_for(state, manga)
    source = chapter.get("source") or ""
    # Last mode is independent of vertical-position saving. When remembering is
    # disabled, retain the previous preference rather than overwrite it.
    previous = (state.get("progress") or {}).get(key) or {}
    previous_mode = previous.get("reader_mode")
    if previous_mode not in ("width", "page"):
        previous_mode = (previous.get("view") or {}).get("fit")
    state.setdefault("progress", {})[key] = {
        "title": manga.get("title") or "Untitled",
        "source": source if source in ACTIVE_SOURCES else "",
        "chapter_id": chapter.get("id") if source in ACTIVE_SOURCES else None,
        "title_id": chapter.get("title_id") if source in ACTIVE_SOURCES else None,
        "chapter_number": chapter.get("number") or "",
        "chapter_name": chapter.get("name") or "",
        "language": "en",
        "groups": list(chapter.get("groups") or []),
        "source_ref": dict(chapter.get("source_ref") or {}) if source in ACTIVE_SOURCES else {},
        "page": max(1, int(page or 1)),
        "total_pages": max(0, int(total_pages or 0)),
        "updated": int(time.time()),
        "manga": _progress_manga_snapshot(dict(manga, key=key, identity=key)),
    }
    if view_state is not None:
        state["progress"][key]["view"] = clean_reader_view(view_state)
    mode = reader_mode if reader_mode in ("width", "page") else previous_mode
    if mode in ("width", "page"):
        state["progress"][key]["reader_mode"] = mode
    state["last_read"] = key
    save_state(path, state)


def progress_for(state, manga):
    key = _find_key(state.get("progress") or {}, manga)
    return (state.get("progress") or {}).get(key) if key else None

def availability_for(state, manga):
    key = _find_key(state.get("saved") or {}, manga)
    if not key:
        return default_availability()
    rec = (state.get("saved") or {}).get(key) or {}
    return _clean_availability(rec.get("availability"))


def update_availability(path, state, manga, chapter_keys, latest_number, source_totals, errors=None, attempted_at=None):
    key = _find_key(state.get("saved") or {}, manga)
    if not key:
        return None
    saved = state.setdefault("saved", {})
    rec = saved.get(key) or {}
    old = _clean_availability(rec.get("availability"))
    now = int(attempted_at or time.time())
    errors = {str(k): str(v) for k, v in (errors or {}).items() if str(k) in ACTIVE_SOURCES}
    current = []
    seen = set()
    for item in chapter_keys or []:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            current.append(text)
    totals = {}
    for source in SOURCE_ORDER:
        if source not in (source_totals or {}):
            continue
        try:
            totals[source] = max(0, int((source_totals or {}).get(source) or 0))
        except (TypeError, ValueError):
            pass

    if errors and not current:
        new = dict(old)
        new["status"] = "error"
        new["attempted_at"] = now
        new["error_sources"] = [source for source in SOURCE_ORDER if source in errors]
    elif errors:
        # Una comprobacion parcial nunca reduce ni reemplaza el ultimo snapshot completo.
        # Así un timeout de una fuente no fabrica desapariciones ni falsas novedades.
        new = dict(old)
        new["status"] = "partial"
        new["attempted_at"] = now
        new["error_sources"] = [source for source in SOURCE_ORDER if source in errors]
        if not old.get("known_keys"):
            new["known_keys"] = list(current)
            new["known_total"] = len(current)
            new["latest_number"] = str(latest_number or "")
            new["source_totals"] = totals
    else:
        previous = set(old.get("known_keys") or [])
        current_set = set(current)
        unseen = set(old.get("new_keys") or [])
        if old.get("checked_at") and previous:
            unseen.update(current_set.difference(previous))
        # Si es la primera comprobacion completa, se establece baseline sin marcar todo como nuevo.
        unseen.intersection_update(current_set)
        new = {
            "status": "ok",
            "known_total": len(current),
            "latest_number": str(latest_number or ""),
            "source_totals": totals,
            "known_keys": list(current),
            "new_keys": [item for item in current if item in unseen],
            "checked_at": now,
            "attempted_at": now,
            "error_sources": [],
        }
    rec["availability"] = _clean_availability(new)
    saved[key] = rec
    save_state(path, state)
    return (state.get("saved") or {}).get(key, {}).get("availability")


def mark_updates_seen(path, state, manga):
    key = _find_key(state.get("saved") or {}, manga)
    if not key:
        return False
    rec = (state.get("saved") or {}).get(key) or {}
    availability = _clean_availability(rec.get("availability"))
    if not availability.get("new_keys"):
        return False
    availability["new_keys"] = []
    rec["availability"] = availability
    state["saved"][key] = rec
    save_state(path, state)
    return True

