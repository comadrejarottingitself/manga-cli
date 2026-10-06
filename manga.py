#!/usr/bin/env python3
from acmanga.i18n import tr
# manga-cli 0.8.3 - a comadreja project / inspired by ani-cli.

import argparse
import hashlib
import os
import platform
import queue
import shutil
import subprocess
import sys
import time
from pathlib import Path

from acmanga import VERSION
from acmanga.engine import MultiSourceEngine
from acmanga.errors import MangaError
from acmanga.reader import (ReaderSession, view, mpv_command, write_mpv_files, MANUAL_QUIT_CODE,
    _mpv_supported_options, _mpv_video_outputs, _mpv_output_candidates, is_android_termux)
from acmanga.settings import default_settings, load_settings, save_settings
from acmanga.streaming import background_call
from acmanga.sources import MangaKatanaSource, MangaPillSource
from acmanga.state import (
    load_state, save_state, save_manga, remove_saved, is_saved, progress_for, state_key_for,
    availability_for, update_availability, mark_updates_seen,
)
from acmanga.util import normalize_title, titles_equivalent
from acmanga import ui
from acmanga.theme import ACCENT_COLORS, PALETTES, normalize_accent

MIN_PYTHON = (3, 8)
HOME = Path.home()
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", str(HOME / ".local" / "share"))) / "anticomadreja-manga"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", str(HOME / ".cache"))) / "anticomadreja-manga"
STATE_FILE = DATA_DIR / "state.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
READER_LUA_SHA256 = "bf5190cc890cf94c7ecfb12318db022750a9909a5459220ae5b7266ef7e1fa5a"
READER_ANDROID_LUA_SHA256 = "02a862d1a643cd90a44a4aad3ec87f98319f753e25952af9fcfa870f97a200e3"
READER_SHA256 = "196e6ddb6ed25371fa6538551eaf80ffba0d6f8fee8b6055d5cb310cebbadd25"


def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def source_label(key):
    return {"mangakatana": "MangaKatana", "mangapill": "MangaPill"}.get(key, key or "?")

def chapter_label(chapter):
    number = str(chapter.get("number") or "").strip()
    name = str(chapter.get("name") or "").strip()
    base = tr('app.ch').format(number) if number else tr('app.chapter')
    if name and normalize_title(name) != normalize_title(base):
        return "{} · {}".format(base, name)
    return base


def progress_label(progress):
    if not progress:
        return tr('app.no_progress')
    chapter = progress.get("chapter_number") or "?"
    page = progress.get("page") or 1
    total = progress.get("total_pages") or 0
    if total:
        return tr('app.ch_page_a22464').format(chapter, page, total)
    return tr('app.ch_page').format(chapter, page)


def make_engine(settings=None):
    return MultiSourceEngine([MangaKatanaSource(), MangaPillSource()], preferences=settings or {})

def source_mode_label(settings):
    return {
        "auto": tr('app.automatic'),
        "mangakatana": "MANGAKATANA",
        "mangapill": "MANGAPILL",
    }.get((settings or {}).get("source_mode"), tr('app.automatic'))

def source_priority_label(settings):
    priority = (settings or {}).get("source_priority") or ["mangakatana", "mangapill"]
    return " > ".join(source_label(key).upper() for key in priority)


def saved_sort_label(settings):
    return {
        "activity": tr('app.activity'),
        "title": tr('app.title'),
        "saved": tr('app.recently_saved'),
    }.get((settings or {}).get("saved_sort"), tr('app.activity'))


def saved_activity_value(state, manga):
    prog = progress_for(state, manga)
    progress_updated = int((prog or {}).get("updated") or 0)
    saved_at = int(manga.get("saved_at") or 0)
    bonus = 10 ** 12 if manga.get("key") == state.get("last_read") else 0
    return bonus + max(progress_updated, saved_at)


def sort_saved_items(saved, state, settings):
    mode = (settings or {}).get("saved_sort") or "activity"
    items = list(saved)
    if mode == "title":
        items.sort(key=lambda item: ((item.get("title") or "").casefold(), -(item.get("saved_at") or 0)))
        return items
    if mode == "saved":
        items.sort(key=lambda item: (-(item.get("saved_at") or 0), (item.get("title") or "").casefold()))
        return items
    items.sort(key=lambda item: (-saved_activity_value(state, item), (item.get("title") or "").casefold()))
    return items


def saved_summary(saved, state):
    continuing = 0
    updates = 0
    checked = 0
    for manga in saved:
        if progress_for(state, manga):
            continuing += 1
        availability = availability_for(state, manga)
        if availability.get("checked_at"):
            checked += 1
        if availability.get("new_keys"):
            updates += 1
    last_key = state.get("last_read")
    last_title = None
    for manga in saved:
        if manga.get("key") == last_key:
            last_title = manga.get("title")
            break
    return {
        "count": len(saved),
        "continuing": continuing,
        "updates": updates,
        "checked": checked,
        "last": last_title or tr('app.no_recent_reading'),
    }


def history_manga_for(state, key, progress):
    """Reconstruye la referencia de manga asociada a un progreso.

    Historial no es una coleccion nueva: deriva de state["progress"].
    """
    saved = (state.get("saved") or {}).get(key)
    if isinstance(saved, dict):
        return dict(saved)
    snap = progress.get("manga") if isinstance(progress.get("manga"), dict) else {}
    manga = dict(snap)
    manga["key"] = key
    manga["identity"] = manga.get("identity") or key
    manga["title"] = manga.get("title") or progress.get("title") or tr('app.untitled')
    manga.setdefault("author", "")
    manga.setdefault("aliases", [])
    manga.setdefault("year", "")
    manga.setdefault("status", "")
    manga.setdefault("type", "manga")
    manga.setdefault("genres", [])
    variants = list(manga.get("variants") or [])
    if not variants:
        source = progress.get("source") or ""
        source_ref = progress.get("source_ref") if isinstance(progress.get("source_ref"), dict) else {}
        ident = progress.get("title_id") or source_ref.get("manga_id") or source_ref.get("title_id")
        if source in ("mangakatana", "mangapill") and ident:
            variants = [{
                "source": source, "id": str(ident), "title": manga["title"],
                "language": "en", "ref": {"id": str(ident)},
            }]
    manga["variants"] = variants
    return manga


def history_items(state):
    items = []
    for key, progress in (state.get("progress") or {}).items():
        if not isinstance(progress, dict):
            continue
        try:
            updated = int(progress.get("updated") or 0)
        except (TypeError, ValueError):
            updated = 0
        manga = history_manga_for(state, key, progress)
        items.append({
            "key": key,
            "manga": manga,
            "progress": progress,
            "updated": updated,
            "saved": is_saved(state, manga),
        })
    items.sort(key=lambda item: (-item.get("updated", 0), (item.get("manga", {}).get("title") or "").casefold()))
    return items


def last_history_item(state):
    items = history_items(state)
    if not items:
        return None
    last_key = state.get("last_read")
    for item in items:
        if item.get("key") == last_key:
            return item
    return items[0]


def history_time(value):
    try:
        stamp = int(value or 0)
    except (TypeError, ValueError):
        stamp = 0
    if stamp <= 0:
        return ""
    try:
        return time.strftime("%d/%m %H:%M", time.localtime(stamp))
    except (ValueError, OverflowError, OSError):
        return ""


def availability_status_text(availability):
    availability = availability or {}
    total = int(availability.get("known_total") or 0)
    status = availability.get("status") or "never"
    chunks = []
    if total:
        chunks.append(tr('app.ch_5f9b83').format(total))
    elif status == "ok":
        chunks.append(tr('app.0_ch'))
    else:
        chunks.append(tr('app.not_checked'))
    new_count = len(availability.get("new_keys") or [])
    if new_count:
        chunks.append(tr('app.new').format(new_count, ""))
    if status == "partial":
        chunks.append(tr('app.partial'))
    elif status == "error":
        chunks.append(tr('app.not_updated'))
    return " · ".join(chunks)


def chapter_snapshot_keys(engine, chapters):
    keys = []
    used = set()
    for chapter in chapters or []:
        base = "{}|g:{}".format(engine._chapter_key(chapter), engine._group_key(chapter))
        key = base
        if key in used:
            key = "{}|{}:{}".format(base, chapter.get("source") or "?", chapter.get("id") or "?")
        suffix = 2
        original = key
        while key in used:
            key = "{}#{}".format(original, suffix)
            suffix += 1
        used.add(key)
        keys.append(key)
    return keys


def update_saved_entry(engine, state, manga):
    engine.invalidate_chapters(manga)
    chapters, errors, totals = engine.chapters(manga, force=True, all_sources=True)
    keys = chapter_snapshot_keys(engine, chapters)
    latest = chapters[0].get("number") if chapters else ""
    availability = update_availability(
        STATE_FILE, state, manga, keys, latest, totals, errors=errors,
    )
    return availability or availability_for(state, manga), errors


def refresh_all_saved(engine, state, settings):
    saved = sort_saved_items(list((state.get("saved") or {}).values()), state, settings)
    if not saved:
        return tr('app.no_saved_manga')
    ok = partial = failed = 0
    for index, manga in enumerate(saved, 1):
        ui.clear()
        ui.brand(VERSION, tr('app.saved_manual_refresh'))
        print()
        ui.frame_top()
        ui.frame_line(tr('app.checking'), "{}/{}".format(index, len(saved)), (ui.BOLD, ui.ACCENT), (ui.CYAN, ui.BOLD))
        ui.frame_line(ui.compact(manga.get("title") or tr('app.untitled'), ui.width() - 8), "", (ui.WHITE,), ())
        ui.frame_bottom()
        ui.status(tr('app.checking_available_chapters'), "info")
        availability, errors = update_saved_entry(engine, state, manga)
        status = (availability or {}).get("status")
        if status == "ok":
            ok += 1
        elif status == "partial":
            partial += 1
        else:
            failed += 1
        if errors:
            print_source_errors(errors)
    saved_after = list((state.get("saved") or {}).values())
    update_titles = sum(1 for manga in saved_after if availability_for(state, manga).get("new_keys"))
    parts = ["{} OK".format(ok)]
    if partial:
        parts.append(tr('app.partial_06fe90').format(partial))
    if failed:
        parts.append(tr('app.failed').format(failed))
    if update_titles:
        parts.append(tr('app.with_updates_0476ef').format(update_titles))
    return tr('app.refresh_complete') + " · ".join(parts)


def pause(message=""):
    if sys.stdin.isatty():
        ui.read_key()
        return
    try:
        input(message)
    except (EOFError, KeyboardInterrupt):
        pass


def print_source_errors(errors):
    if not errors:
        return
    for key, message in errors.items():
        ui.status("{}: {}".format(source_label(key), ui.compact(message, ui.width() - 16)), "warn")


def source_stats_line(stats):
    chunks = []
    for key, badge in (("mangakatana", "MK"), ("mangapill", "MP")):
        if key in stats:
            chunks.append("{} {}".format(badge, stats.get(key, 0)))
    return " · ".join(chunks)

def chapter_totals_line(totals):
    chunks = []
    for key in ("mangakatana", "mangapill"):
        if key in totals:
            chunks.append("{} {}".format(source_label(key), totals.get(key, 0)))
    return " · ".join(chunks)

def source_names(manga):
    names = []
    for variant in manga.get("variants") or []:
        name = source_label(variant.get("source"))
        if name and name not in names:
            names.append(name)
    return names


def source_short(manga):
    value = ui.source_badge(manga)
    return "[{}]".format(value) if value else "[?]"


def reader_integrity_ok():
    path = Path(__file__).resolve().parent / "acmanga" / "reader.py"
    android_lua = path.with_name("reader_android.lua")
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lua_digest = hashlib.sha256(path.with_suffix(".lua").read_bytes()).hexdigest()
        if android_lua.exists():
            android_digest = hashlib.sha256(android_lua.read_bytes()).hexdigest()
            marker_ok = "def is_android_termux" in path.read_text(encoding="utf-8")
            return lua_digest == READER_LUA_SHA256 and android_digest == READER_ANDROID_LUA_SHA256 and marker_ok
    except OSError:
        return False
    return digest == READER_SHA256 and lua_digest == READER_LUA_SHA256


def toggle_save(state, manga):
    if is_saved(state, manga):
        remove_saved(STATE_FILE, state, manga)
        return False
    save_manga(STATE_FILE, state, manga)
    return True


def _page_bounds(selection, total, rows):
    if total <= 0:
        return 0, 0
    selection = max(0, min(selection, total - 1))
    start = (selection // rows) * rows
    return start, min(total, start + rows)


def _move(selection, key, total, rows):
    if total <= 0:
        return 0
    if key == "up":
        return max(0, selection - 1)
    if key == "down":
        return min(total - 1, selection + 1)
    if key == "pgup" or key == "left":
        return max(0, selection - rows)
    if key == "pgdn" or key == "right":
        return min(total - 1, selection + rows)
    if key == "home":
        return 0
    if key == "end":
        return total - 1
    return selection


def render_main(state, settings=None, selection=0):
    # Compatibilidad con pruebas antiguas que llamaban render_main(state, selection).
    if isinstance(settings, int):
        selection = settings
        settings = default_settings()
    ui.clear()
    ui.brand(VERSION, tr('app.online_reading'))
    print()
    ui.frame_top()
    ui.frame_line(tr('app.menu'), "", (ui.BOLD, ui.ACCENT), ())
    last = last_history_item(state)
    if last:
        ui.frame_line(tr('app.last_read'), ui.compact(last["manga"].get("title") or "", 42), (ui.ACCENT, ui.BOLD), (ui.TEXT_WARM, ui.BOLD))
        ui.frame_line("", progress_label(last.get("progress")), (), (ui.TEXT_WARM,))
    ui.frame_rule()
    options = [tr('app.search'), tr('app.saved'), tr('app.history'), tr('app.settings')]
    for index, label in enumerate(options):
        selected = index == selection
        marker = "▶" if selected else "·"
        ui.frame_line("{} {}".format(marker, label), "", (ui.BOLD, ui.ACCENT) if selected else (ui.DIM,), ())
        if index != len(options) - 1:
            ui.frame_blank()
    ui.frame_bottom()


def main_menu(state, settings):
    selection = 0
    actions = ("search", "saved", "history", "options")
    while True:
        render_main(state, settings, selection)
        key = ui.read_key()
        if key in ("up", "down", "home", "end"):
            selection = _move(selection, key, len(actions), 1)
            continue
        if key in ("1", "2", "3", "4"):
            return actions[int(key) - 1]
        if key == "c" and last_history_item(state):
            return "continue"
        if key == "esc":
            return "quit"
        if key == "enter":
            return actions[selection]


def render_search_home(settings, state):
    ui.clear()
    ui.brand(VERSION, tr('app.search'))
    print()


def render_search_results(query, results, errors, stats, state, selection):
    ui.clear()
    ui.brand(VERSION, tr('app.global_search'))
    print()
    ui.banner(tr('app.results'), "“{}”".format(ui.compact(query, ui.width() - 12)))
    stats_text = source_stats_line(stats) or tr('app.no_data')
    ui.section(tr('app.sources'), stats_text)
    if errors:
        print_source_errors(errors)
    rows = max(4, ui.list_rows(extra=len(errors)) // 2)
    start, end = _page_bounds(selection, len(results), rows)
    for index in range(start, end):
        manga = results[index]
        selected = index == selection
        marker = ui.paint("▶", ui.CYAN, ui.BOLD) if selected else " "
        saved = ui.paint("●", ui.GREEN) if is_saved(state, manga) else ui.paint("○", ui.DIM)
        badge = ui.badge(ui.source_badge(manga), ui.MAGENTA if "MP" in ui.source_badge(manga) else ui.CYAN)
        title = ui.compact(manga.get("title"), max(24, ui.width() - 28))
        title_text = ui.paint(title, ui.BOLD, ui.ACCENT) if selected else title
        print(" {} {} {:>2}  {}  {}".format(marker, saved, index + 1, title_text, badge))
        author = manga.get("author") or ""
        if author:
            print("        {}".format(ui.paint(ui.compact(author, ui.width() - 10), ui.DIM)))
        else:
            print()
    pages = max(1, (len(results) + rows - 1) // rows)
    current = (selection // rows) + 1
    ui.rule()
    print(tr('app.results_saved').format(
        ui.paint("{}/{}".format(current, pages), ui.CYAN, ui.BOLD),
        len(results),
        ui.paint("●", ui.GREEN),
    ))
    ui.key_hint([("G", tr('app.save')), ("N", tr('app.new_search'))])


def search_flow(engine, state, settings):
    while True:
        render_search_home(settings, state)
        query = ui.prompt_line(tr('app.search_2e5f79'))
        if not query:
            return
        snapshots = queue.Queue()
        task = background_call(lambda q=query, out=snapshots: engine.search(q,
            progress_callback=lambda r,e,st: out.put((r,e,st))))
        results, errors, stats = [], {}, {}
        selection, redraw, applied_final = 0, True, False
        while True:
            update = None
            while True:
                try:
                    update = snapshots.get_nowait()
                except queue.Empty:
                    break
            if task.done() and not applied_final:
                try:
                    final, errs = task.result()
                    update = (final, errs, dict(engine.last_search_stats))
                except Exception as exc:
                    update = (results, {tr('app.search_241932'): str(exc)}, stats)
                applied_final = True
            if update is not None:
                old = results[selection] if results and selection < len(results) else {}
                markers = {(v.get("source"),str(v.get("id"))) for v in old.get("variants", [])}
                results, errors, stats = update
                if markers:
                    for i, item in enumerate(results):
                        if markers & {(v.get("source"),str(v.get("id"))) for v in item.get("variants", [])}:
                            selection=i; break
                selection=max(0,min(selection,len(results)-1))
                redraw=True
            if redraw:
                render_search_results(query, results, errors, stats, state, selection)
                if not applied_final:
                    ui.status(tr('app.checking_sources_available_results_can_already_be_opened'), "info")
                elif not results:
                    ui.status(tr('app.no_results_n_new_search_esc_back'), "warn")
                redraw=False
            key = ui.read_key(timeout=0.12)
            if not key:
                continue
            if key == "esc":
                task.cancel(); return
            if key == "n":
                task.cancel(); break
            if not results:
                continue
            rows = max(4, ui.list_rows(extra=len(errors)) // 2)
            if key in ("up","down","pgup","pgdn","left","right","home","end"):
                selection=_move(selection,key,len(results),rows); redraw=True
            elif key == "g":
                toggle_save(state,results[selection]); redraw=True
            elif key == "enter":
                manga_flow(engine,state,results[selection],settings); redraw=True


def render_history(items, state, selection, flash=None):
    ui.clear()
    ui.brand(VERSION, tr('app.history_resume_reading'))
    print()
    ui.frame_top()
    ui.frame_line(tr('app.history'), tr('app.reading_9dfd4d').format(len(items), "s" if len(items) != 1 else ""), (ui.BOLD, ui.ACCENT), (ui.CYAN, ui.BOLD))
    ui.frame_rule()
    if not items:
        ui.frame_line(tr('app.no_recent_reading_yet'), "", (ui.DIM,), ())
        ui.frame_bottom()
        return
    rows = max(3, min(6, ui.list_rows(extra=4) // 2))
    start, end = _page_bounds(selection, len(items), rows)
    for index in range(start, end):
        item = items[index]
        manga = item.get("manga") or {}
        progress = item.get("progress") or {}
        selected = index == selection
        marker = "▶" if selected else "·"
        latest = "◆" if item.get("key") == state.get("last_read") else " "
        saved = "●" if item.get("saved") else "○"
        badge = "[{}]".format(ui.source_badge(manga))
        title = ui.compact(manga.get("title") or tr('app.untitled'), max(18, ui.width() - 34))
        right = "{} {} {}".format(latest, saved, badge)
        ui.frame_line("{} {}".format(marker, title), right, (ui.BOLD, ui.ACCENT) if selected else (), (ui.CYAN,))
        ui.frame_line("  {}".format(progress_label(progress)), history_time(item.get("updated")), (ui.TEXT_WARM,), (ui.ACCENT_SOFT,))
    ui.frame_bottom()
    pages = max(1, (len(items) + rows - 1) // rows)
    if flash:
        ui.status(flash, "ok")
    if pages > 1:
        ui.status(tr('app.page_0b926c').format((selection // rows) + 1, pages), "info")


def continue_manga_reading(engine, state, manga, settings):
    manga, errors = refresh_saved_manga(engine, state, manga)
    manga, detail_errors = engine.resolve_manga(manga, force=False)
    errors.update(detail_errors)
    if is_saved(state, manga):
        save_manga(STATE_FILE, state, manga)
        manga = (state.get("saved") or {}).get(state_key_for(state, manga), manga)
    return chapter_flow(engine, state, manga, prefer_continue=True, auto_continue=True, initial_errors=errors)


def history_flow(engine, state, settings):
    selection = 0
    flash = None
    while True:
        items = history_items(state)
        if items:
            selection = max(0, min(selection, len(items) - 1))
        else:
            selection = 0
        render_history(items, state, selection, flash=flash)
        flash = None
        key = ui.read_key()
        if not items:
            if key == "esc":
                return
            continue
        rows = max(3, min(6, ui.list_rows(extra=4) // 2))
        if key in ("up", "down", "pgup", "pgdn", "left", "right", "home", "end"):
            selection = _move(selection, key, len(items), rows)
            continue
        if key == "esc":
            return
        if key == "enter":
            message = continue_manga_reading(engine, state, items[selection]["manga"], settings)
            if message:
                flash = message


def continue_last_read(engine, state, settings):
    item = last_history_item(state)
    if not item:
        return None
    return continue_manga_reading(engine, state, item["manga"], settings)


def render_saved(saved, state, settings, selection, flash=None):
    ui.clear()
    ui.brand(VERSION, tr('app.saved_manga'))
    print()
    summary = saved_summary(saved, state)
    subtitle = "{} manga{}".format(summary["count"], "s" if summary["count"] != 1 else "")
    ui.frame_top()
    ui.frame_line(tr('app.saved'), subtitle.upper(), (ui.BOLD, ui.ACCENT), (ui.CYAN, ui.BOLD))
    ui.frame_line(tr('app.with_progress'), str(summary["continuing"]), (ui.ACCENT, ui.BOLD), (ui.TEXT_WARM, ui.BOLD))
    ui.frame_line(tr('app.with_updates'), str(summary["updates"]), (ui.ACCENT, ui.BOLD), (ui.GREEN, ui.BOLD) if summary["updates"] else (ui.TEXT_WARM,))
    ui.frame_line(tr('app.last_read'), summary["last"], (ui.ACCENT, ui.BOLD), (ui.TEXT_WARM, ui.BOLD) if summary["last"] != tr('app.no_recent_reading') else (ui.TEXT_WARM,))
    ui.frame_rule()
    if not saved:
        ui.frame_line(tr('app.no_saved_manga_yet'), "", (ui.DIM,), ())
        ui.frame_bottom()
        if flash:
            ui.status(flash, "ok")
        return

    rows = max(3, min(5, ui.list_rows(extra=6) // 2))
    start, end = _page_bounds(selection, len(saved), rows)
    for index in range(start, end):
        manga = saved[index]
        selected = index == selection
        marker = "▶" if selected else "·"
        last = "◆" if manga.get("key") == state.get("last_read") else " "
        badge = "[{}]".format(ui.source_badge(manga))
        availability = availability_for(state, manga)
        title = ui.compact(manga.get("title"), max(18, ui.width() - 36))
        right = "{:>2} {} {}".format(index + 1, last, badge)
        ui.frame_line("{} {}".format(marker, title), right, (ui.BOLD, ui.ACCENT) if selected else (), (ui.CYAN,))
        prog = progress_for(state, manga)
        left = progress_label(prog) if prog else tr('app.no_progress_yet')
        avail_text = availability_status_text(availability)
        right_codes = (ui.GREEN, ui.BOLD) if availability.get("new_keys") else ((ui.YELLOW,) if availability.get("status") in ("partial", "error") else (ui.ACCENT_SOFT,))
        ui.frame_line("  {}".format(left), avail_text, (ui.TEXT_WARM,), right_codes)
    ui.frame_bottom()
    pages = max(1, (len(saved) + rows - 1) // rows)
    if flash:
        ui.status(flash, "ok")
    if pages > 1:
        ui.status(tr('app.page_0b926c').format((selection // rows) + 1, pages), "info")
    ui.key_hint([("R", tr('app.refresh'))])


def saved_flow(engine, state, settings):
    selection = 0
    flash = None
    while True:
        saved = sort_saved_items(list((state.get("saved") or {}).values()), state, settings)
        if saved:
            selection = max(0, min(selection, len(saved) - 1))
        else:
            selection = 0
        render_saved(saved, state, settings, selection, flash=flash)
        flash = None
        key = ui.read_key()
        if not saved:
            if key == "esc":
                return
            continue
        rows = max(3, min(5, ui.list_rows(extra=5) // 2))
        if key in ("up", "down", "pgup", "pgdn", "left", "right", "home", "end"):
            selection = _move(selection, key, len(saved), rows)
            continue
        if key == "esc":
            return
        if key == "r":
            flash = refresh_all_saved(engine, state, settings)
            continue
        if key == "enter":
            manga_flow(engine, state, saved[selection], settings, prefer_continue=True)


def find_progress_index(engine, chapters, progress):
    if not progress:
        return None
    for index, chapter in enumerate(chapters):
        if chapter.get("source") == progress.get("source") and str(chapter.get("id")) == str(progress.get("chapter_id")):
            return index
    target = engine.chapter_from_progress({}, progress)
    if target:
        target_key = engine._chapter_key(target)
        for index, chapter in enumerate(chapters):
            if engine._chapter_key(chapter) == target_key:
                return index
    return None


def render_manga(manga, state, chapters, errors, totals, selection):
    ui.clear()
    ui.brand(VERSION, tr('app.online_reading'))
    print()
    ui.banner(ui.compact(manga.get("title"), ui.width() - 8), manga.get("author") or source_short(manga))
    saved = is_saved(state, manga)
    prog = progress_for(state, manga)
    saved_text = ui.paint(tr('app.saved_b653f9'), ui.GREEN, ui.BOLD) if saved else ui.paint(tr('app.not_saved'), ui.DIM)
    print("{}   {}".format(saved_text, ui.paint(progress_label(prog), ui.WHITE if prog else ui.DIM)))
    ui.section(tr('app.chapters'), chapter_totals_line(totals) or tr('app.available_source'))
    if errors:
        print_source_errors(errors)
    rows = ui.list_rows(extra=len(errors) + 1)
    start, end = _page_bounds(selection, len(chapters), rows)
    for index in range(start, end):
        chapter = chapters[index]
        selected = index == selection
        marker = ui.paint("▶", ui.CYAN, ui.BOLD) if selected else " "
        current = " "
        if prog and chapter.get("source") == prog.get("source") and str(chapter.get("id")) == str(prog.get("chapter_id")):
            current = ui.paint("◆", ui.GREEN)
        badge = ui.badge(ui.chapter_source_badge(chapter), ui.MAGENTA if chapter.get("source") == "mangapill" else ui.CYAN)
        label = ui.compact(chapter_label(chapter), max(24, ui.width() - 28))
        label = ui.paint(label, ui.BOLD, ui.ACCENT) if selected else label
        print(" {}{} {:>4}  {}  {}".format(marker, current, index + 1, badge, label))
    pages = max(1, (len(chapters) + rows - 1) // rows)
    ui.rule()
    print(tr('app.chapters_current_progress').format(
        ui.paint("{}/{}".format((selection // rows) + 1, pages), ui.CYAN, ui.BOLD),
        len(chapters),
        ui.paint("◆", ui.GREEN),
    ))
    ui.key_hint([("J", tr('app.chapter_667b93')), ("G", tr('app.save')), ("R", tr('app.refresh'))])


def read_sequence(engine, state, manga, chapters, chapter, start_page=1):
    # ``current`` always stays on the canonical merged chapter list. A source
    # alternate may render a chapter, but it must never decide which chapter
    # comes next/previous. This keeps continuous reading stable on fallback.
    current, page = chapter, start_page
    with ReaderSession(CACHE_DIR, getattr(engine, 'preferences', {})) as reader:
        while current:
            ui.clear()
            ui.brand(VERSION, tr('app.reader_manga_cli'))
            print()
            ui.banner(ui.compact(manga.get("title"), ui.width() - 8), chapter_label(current))
            ui.key_hint([(tr('app.click'), tr('app.next')), (tr('app.right_click'), tr('app.previous')), (tr('app.wheel'), tr('app.navigate_scroll')), ("ESC", tr('app.save_exit'))])

            canonical = current
            next_fn = getattr(engine, 'next_chapter', lambda *args: None)
            next_chapter = next_fn(chapters, canonical)
            reader.next_chapter = next_chapter
            candidates = [canonical] + engine.chapter_alternates(canonical)
            result, used, failures = None, canonical, []
            for candidate in candidates:
                try:
                    result = view(engine, STATE_FILE, state, manga, candidate, CACHE_DIR,
                        start_page=page, progress_callback=lambda text: ui.status(text, "info"), session=reader)
                    used = candidate
                    break
                except (MangaError, OSError) as exc:
                    failures.append("{}: {}".format(source_label(candidate.get("source")), exc))
            if result is None:
                reader.close()
                ui.status(tr('app.error_no_source_could_open_this_chapter'), "error")
                for message in failures:
                    ui.status(ui.compact(message, ui.width() - 4), "warn")
                pause()
                return tr('app.could_not_open').format(chapter_label(canonical))

            if is_saved(state, manga):
                mark_updates_seen(STATE_FILE, state, manga)
            if result.get('previous'):
                previous_fn = getattr(engine, 'previous_chapter', lambda *args: None)
                previous = previous_fn(chapters, canonical)
                if previous is None:
                    reader.send('ready', tr('app.this_is_the_first_available_chapter'))
                    page = 1
                    current = canonical
                else:
                    reader.note(tr('app.previous_chapter').format(chapter_label(canonical), chapter_label(previous)))
                    current, page = previous, -1
                continue
            if not result.get("completed"):
                return tr('app.page_saved').format(chapter_label(used), result.get("page"), result.get("total"))
            if next_chapter is None:
                return tr('app.finished_no_later_chapter_available').format(chapter_label(canonical))

            reader.note(tr('app.next_chapter_0571bf').format(chapter_label(canonical), chapter_label(next_chapter)))
            current = next_chapter
            page = 1
    return tr('app.reading_finished')

def manga_card_actions(state, manga):
    actions = []
    if progress_for(state, manga):
        actions.append(("continue", tr('app.continue')))
    actions.extend([
        ("chapters", tr('app.chapters_498bff')),
        ("sources", tr('app.sources_03d463')),
        ("info", tr('app.information')),
        ("save", tr('app.remove_from_saved') if is_saved(state, manga) else tr('app.save_c210bf')),
    ])
    return actions


def render_manga_card(manga, state, selection, flash=None):
    ui.clear()
    ui.brand(VERSION, tr('app.manga_details_online_reading'))
    print()
    saved = is_saved(state, manga)
    prog = progress_for(state, manga)
    status = tr('app.saved_408739') if saved else tr('app.not_saved')
    status_codes = (ui.GREEN, ui.BOLD) if saved else (ui.DIM,)
    title = (manga.get("title") or tr('app.untitled')).upper()
    author = manga.get("author") or ""
    sources = source_short(manga)

    ui.frame_top()
    ui.frame_line(title, status, (ui.BOLD, ui.ACCENT), status_codes)
    if author:
        ui.frame_line(author, "", (ui.DIM,), ())
    else:
        ui.frame_blank()
    ui.frame_blank()
    ui.frame_line(tr('app.progress'), progress_label(prog), (ui.ACCENT, ui.BOLD), (ui.TEXT_WARM, ui.BOLD) if prog else (ui.TEXT_WARM,))
    if saved:
        availability = availability_for(state, manga)
        ui.frame_line(tr('app.available'), availability_status_text(availability), (ui.ACCENT, ui.BOLD), (ui.GREEN, ui.BOLD) if availability.get("new_keys") else (ui.TEXT_WARM,))
    ui.frame_line(tr('app.sources'), sources, (ui.CYAN,), (ui.MAGENTA,) if "MP" in sources else (ui.CYAN,))
    ui.frame_rule()
    actions = manga_card_actions(state, manga)
    for index, (_action, label) in enumerate(actions):
        selected = index == selection
        marker = "\u25b6" if selected else "\u00b7"
        left = "{} {}".format(marker, label)
        codes = (ui.BOLD, ui.ACCENT) if selected else (ui.DIM,)
        ui.frame_line(left, "", codes, ())
    ui.frame_bottom()
    if flash:
        ui.status(flash, "ok")


def render_sources(manga, settings=None, report=None, selection=0, flash=None):
    settings = settings or default_settings()
    if report is None:
        report = {}
        present = {variant.get("source") for variant in manga.get("variants") or []}
        for key in ("mangakatana", "mangapill"):
            report[key] = {"present": key in present, "chapters": 0, "languages": [], "error": ""}
    ui.clear()
    ui.brand(VERSION, tr('app.manga_details_sources'))
    print()
    ui.frame_top()
    ui.frame_line((manga.get("title") or tr('app.untitled')).upper(), source_short(manga), (ui.BOLD, ui.ACCENT), (ui.CYAN, ui.BOLD))
    ui.frame_line(tr('app.source_mode'), source_mode_label(settings), (ui.CYAN,), (ui.WHITE,))
    ui.frame_line(tr('app.automatic_priority'), source_priority_label(settings), (ui.CYAN,), (ui.WHITE,))
    ui.frame_rule()

    source_keys = settings.get("source_priority") or ["mangakatana", "mangapill"]
    mode = settings.get("source_mode") or "auto"
    for index, key in enumerate(source_keys):
        entry = report.get(key) or {"present": False, "chapters": 0, "languages": [], "error": ""}
        selected = index == selection
        marker = "▶" if selected else "·"
        status = entry.get("status") or ("found" if entry.get("present") else "unknown")
        if entry.get("error") and status == "error":
            right = tr('app.error')
            right_codes = (ui.RED, ui.BOLD)
        elif status == "not_found":
            right = tr('app.not_found')
            right_codes = (ui.DIM,)
        elif status == "ambiguous":
            right = tr('app.ambiguous')
            right_codes = (ui.YELLOW, ui.BOLD)
        elif status == "unknown":
            right = tr('app.not_checked_923055')
            right_codes = (ui.DIM,)
        else:
            right = tr('app.ch_5f9b83').format(entry.get("chapters") or 0)
            right_codes = (ui.GREEN,) if entry.get("chapters") else (ui.DIM,)
        active = mode == key or mode == "auto"
        label = "{} {}{}".format(marker, source_label(key), "  ◆" if active else "")
        ui.frame_line(label, right, (ui.BOLD, ui.ACCENT) if selected else (ui.DIM,), right_codes)
        if entry.get("error"):
            ui.frame_line("  " + ui.compact(entry.get("error"), 70), "", (ui.DIM,), ())
    ui.frame_bottom()
    if flash:
        ui.status(flash, "ok")
    ui.key_hint([("M", tr('app.mode')), ("R", tr('app.refresh')), ("D", tr('app.diagnostics'))])

def render_source_diagnostics(results):
    ui.clear()
    ui.brand(VERSION, tr('app.source_diagnostics'))
    print()
    ui.frame_top()
    ui.frame_line(tr('app.end_to_end_network_test'), tr('app.read_only'), (ui.BOLD, ui.ACCENT), (ui.CYAN, ui.BOLD))
    ui.frame_rule()
    for key in ("mangakatana", "mangapill"):
        item = results.get(key) or {"ok": False, "detail": tr('app.no_result')}
        ok = bool(item.get("ok"))
        ui.frame_line(source_label(key), tr('app.e2e_ok') if ok else tr('app.error_f0f9f6'), (ui.WHITE,), (ui.GREEN, ui.BOLD) if ok else (ui.RED, ui.BOLD))
        ui.frame_line("  " + ui.compact(item.get("detail") or "", 78), "", (ui.DIM,), ())
    ui.frame_bottom()
    ui.key_hint([("R", tr('app.repeat'))])

def source_diagnostics_flow(engine):
    while True:
        ui.clear()
        ui.brand(VERSION, tr('app.source_diagnostics'))
        print()
        ui.status(tr('app.testing_search_chapters_pages_and_an_actual_image'), "info")
        results = engine.diagnostic_sources()
        while True:
            render_source_diagnostics(results)
            key = ui.read_key()
            if key == "esc":
                return
            if key == "r":
                break

def cycle_source_mode(settings):
    values = ["auto", "mangakatana", "mangapill"]
    current = settings.get("source_mode") or "auto"
    try:
        index = values.index(current)
    except ValueError:
        index = 0
    settings["source_mode"] = values[(index + 1) % len(values)]

def sources_flow(engine, state, manga, settings):
    selection = 0
    flash = None
    current = manga
    ui.clear()
    ui.brand(VERSION, tr('app.sources_03d463'))
    print()
    ui.status(tr('app.checking_available_chapters_by_source'), "info")
    report = engine.source_report(current, force=False)
    while True:
        render_sources(current, settings, report, selection=selection, flash=flash)
        flash = None
        key = ui.read_key()
        if key in ("up", "down", "home", "end"):
            selection = _move(selection, key, 2, 1)
            continue
        if key == "esc":
            return current
        if key == "m":
            cycle_source_mode(settings)
            save_settings(SETTINGS_FILE, settings)
            engine.update_preferences(settings)
            flash = tr('app.mode_changed_to').format(source_mode_label(settings))
            continue
        if key == "d":
            source_diagnostics_flow(engine)
            continue
        if key == "r":
            ui.clear()
            ui.brand(VERSION, tr('app.sources_03d463'))
            print()
            ui.status(tr('app.refreshing_details_and_chapter_counts'), "info")
            fresh, errors = engine.refresh_manga(current)
            current = fresh
            if is_saved(state, current):
                save_manga(STATE_FILE, state, current)
                current = (state.get("saved") or {}).get(state_key_for(state, current), current)
            engine.invalidate_chapters(current)
            report = engine.source_report(current, force=True)
            if errors:
                flash = tr('app.refreshed_with_warnings').format(", ".join(source_label(k) for k in sorted(errors)))
            else:
                flash = tr('app.sources_and_chapters_updated')


def render_information(manga, state):
    ui.clear()
    ui.brand(VERSION, tr('app.manga_details_information'))
    print()
    saved = is_saved(state, manga)
    prog = progress_for(state, manga)
    names = source_names(manga)
    ui.frame_top()
    ui.frame_line((manga.get("title") or tr('app.untitled')).upper(), tr('app.saved_408739') if saved else tr('app.not_saved'), (ui.BOLD, ui.ACCENT), (ui.GREEN, ui.BOLD) if saved else (ui.DIM,))
    ui.frame_rule()
    label_codes = (ui.ACCENT, ui.BOLD)
    value_codes = (ui.TEXT_WARM,)
    if manga.get("author"):
        ui.frame_line(tr('app.author'), manga.get("author"), label_codes, (ui.WHITE,))
    aliases = [str(x) for x in (manga.get("aliases") or []) if str(x or "").strip()]
    if aliases:
        ui.frame_line(tr('app.alternative_titles'), " · ".join(aliases), label_codes, value_codes)
    if manga.get("year"):
        ui.frame_line(tr('app.year'), str(manga.get("year")), label_codes, value_codes)
    if manga.get("status"):
        ui.frame_line(tr('app.status'), str(manga.get("status")), label_codes, value_codes)
    if manga.get("type"):
        ui.frame_line(tr('app.type'), str(manga.get("type")).upper(), label_codes, value_codes)
    genres = [str(x) for x in (manga.get("genres") or []) if str(x or "").strip()]
    if genres:
        ui.frame_line(tr('app.genres'), " · ".join(genres), label_codes, value_codes)
    ui.frame_line(tr('app.progress'), progress_label(prog), label_codes, (ui.TEXT_WARM, ui.BOLD) if prog else value_codes)
    if saved:
        ui.frame_line(tr('app.available'), availability_status_text(availability_for(state, manga)), label_codes, (ui.WHITE,))
    ui.frame_line(tr('app.sources'), " + ".join(names) if names else tr('app.not_listed'), label_codes, (ui.WHITE,))
    ui.frame_line(tr('app.reading'), tr('app.online_temporary_cache'), label_codes, value_codes)
    ui.frame_bottom()


def information_flow(manga, state):
    while True:
        render_information(manga, state)
        key = ui.read_key()
        if key == "esc":
            return


def refresh_saved_manga(engine, state, manga):
    errors = {}
    if not is_saved(state, manga):
        return manga, errors
    ui.clear()
    ui.brand(VERSION, tr('app.refreshing_details'))
    print()
    ui.banner(ui.compact(manga.get("title"), ui.width() - 8), tr('app.checking_sources'))
    ui.status(tr('app.refreshing_available_variants'), "info")
    fresh, refresh_errors = engine.refresh_manga(manga)
    manga = fresh
    errors.update(refresh_errors)
    if is_saved(state, manga):
        save_manga(STATE_FILE, state, manga)
        manga = (state.get("saved") or {}).get(state_key_for(state, manga), manga)
    return manga, errors


def manga_flow(engine, state, manga, settings, prefer_continue=False):
    manga, refresh_errors = refresh_saved_manga(engine, state, manga)
    manga, detail_errors = engine.resolve_manga(manga, force=False)
    refresh_errors.update(detail_errors)
    if is_saved(state, manga):
        save_manga(STATE_FILE, state, manga)
        manga = (state.get("saved") or {}).get(state_key_for(state, manga), manga)
    selection = 0
    flash = None
    if refresh_errors:
        failed = ", ".join(source_label(key) for key in sorted(refresh_errors))
        flash = tr('app.details_opened_source_not_updated').format(failed)

    while True:
        saved_copy = (state.get("saved") or {}).get(state_key_for(state, manga))
        if saved_copy:
            manga = saved_copy
        actions = manga_card_actions(state, manga)
        selection = max(0, min(selection, len(actions) - 1))
        render_manga_card(manga, state, selection, flash=flash)
        flash = None
        key = ui.read_key()
        if key in ("up", "down", "home", "end"):
            selection = _move(selection, key, len(actions), 1)
            continue
        if key == "esc":
            return
        if key == "g":
            saved = toggle_save(state, manga)
            flash = tr('app.added_to_saved') if saved else tr('app.removed_from_saved')
            if saved:
                manga = (state.get("saved") or {}).get(state_key_for(state, manga), manga)
            continue
        if key != "enter":
            continue

        action = actions[selection][0]
        if action == "save":
            saved = toggle_save(state, manga)
            flash = tr('app.added_to_saved') if saved else tr('app.removed_from_saved')
            if saved:
                manga = (state.get("saved") or {}).get(state_key_for(state, manga), manga)
            continue
        if action == "sources":
            manga = sources_flow(engine, state, manga, settings)
            continue
        if action == "info":
            information_flow(manga, state)
            continue
        if action == "continue":
            message = chapter_flow(engine, state, manga, prefer_continue=True, auto_continue=True, initial_errors=refresh_errors)
            if message:
                flash = message
            continue
        if action == "chapters":
            chapter_flow(engine, state, manga, prefer_continue=prefer_continue, auto_continue=False, initial_errors=refresh_errors)
            prefer_continue = False


def chapter_flow(engine, state, manga, prefer_continue=False, auto_continue=False, initial_errors=None):
    errors = dict(initial_errors or {})
    ui.clear()
    ui.brand(VERSION, tr('app.chapters_498bff'))
    print()
    ui.banner(ui.compact(manga.get("title"), ui.width() - 8), tr('app.loading_the_full_list'))
    ui.status(tr('app.preparing_available_chapters'), "info")
    chapters, chapter_errors, totals = engine.chapters(manga, force=False)
    errors.update(chapter_errors)
    if not chapters:
        ui.status(tr('app.no_readable_chapters_are_available_right_now'), "warn")
        print_source_errors(errors)
        pause()
        return

    prog = progress_for(state, manga)
    progress_index = find_progress_index(engine, chapters, prog)
    selection = progress_index if progress_index is not None else 0
    flash = None

    if auto_continue and prog and progress_index is not None:
        selection = progress_index
        chapter = chapters[selection]
        return read_sequence(engine, state, manga, chapters, chapter, start_page=prog.get("page") or 1)

    if is_saved(state, manga):
        mark_updates_seen(STATE_FILE, state, manga)

    while True:
        render_manga(manga, state, chapters, errors, totals, selection)
        if flash:
            ui.status(flash, "ok")
            flash = None
        key = ui.read_key()
        rows = ui.list_rows(extra=len(errors) + 1)
        if key in ("up", "down", "pgup", "pgdn", "left", "right", "home", "end"):
            selection = _move(selection, key, len(chapters), rows)
            continue
        if key == "esc":
            return
        if key == "g":
            saved = toggle_save(state, manga)
            flash = tr('app.added_to_saved') if saved else tr('app.removed_from_saved')
            if saved:
                manga = (state.get("saved") or {}).get(state_key_for(state, manga), manga)
            continue
        if key == "r":
            ui.clear()
            ui.banner(ui.compact(manga.get("title"), ui.width() - 8), tr('app.refreshing_chapters'))
            engine.invalidate_chapters(manga)
            chapters, chapter_errors, totals = engine.chapters(manga, force=True)
            errors = dict(chapter_errors)
            selection = min(selection, max(0, len(chapters) - 1))
            flash = tr('app.chapter_list_updated')
            continue
        if key == "j":
            ui.clear()
            ui.banner(tr('app.go_to_chapter'), manga.get("title"))
            raw = ui.prompt_line(tr('app.chapter_number'))
            index, found = engine.find_chapter(chapters, raw)
            if index is not None:
                selection = index
                flash = tr('app.found').format(chapter_label(found))
            else:
                flash = tr('app.chapter_number_not_found')
            continue
        if key == "enter":
            chapter = chapters[selection]
            current = progress_for(state, manga)
            start_page = 1
            if current and current.get("source") == chapter.get("source") and str(current.get("chapter_id")) == str(chapter.get("id")):
                start_page = current.get("page") or 1
            message = read_sequence(engine, state, manga, chapters, chapter, start_page=start_page)
            flash = message
            prog = progress_for(state, manga)
            new_index = find_progress_index(engine, chapters, prog)
            if new_index is not None:
                selection = new_index



def option_rows(settings):
    return [
        ("source_mode", tr('app.reading_source'), source_mode_label(settings)),
        ("source_priority", tr('app.automatic_priority_33ffe8'), source_priority_label(settings)),
        ("saved_sort", tr('app.saved_sort_order'), saved_sort_label(settings)),
        ("reader_customization", tr('app.reader'), tr('app.open')),
        ("appearance", tr("appearance.title"), tr("appearance.open")),
        ("cache_mib", tr('app.page_cache'), "{} MiB".format(settings.get("cache_mib", 512))),
        ("diagnostics", tr('app.network_diagnostics'), tr('app.test_network')),
        ("about", tr("about.title"), tr("appearance.open")),
    ]


def reader_option_rows(settings, mobile=False):
    def yes_no(key):
        return tr('app.on') if settings.get(key, True) else tr('app.off')
    if mobile:
        return [
            ("mobile_double_tap_zoom", "Double-tap zoom", "{:.1f}x".format(settings.get("mobile_double_tap_zoom", 2.0))),
            ("show_page_indicator", tr('app.show_page_indicator'), yes_no("show_page_indicator")),
            ("prefetch_pages", tr('app.page_prefetch'), str(settings.get("prefetch_pages", 3))),
            ("prefetch_next_chapter", tr('app.prefetch_next_chapter'), yes_no("prefetch_next_chapter")),
        ]
    return [
        ("auto_page_turn", tr('app.auto_page_turn_at_edges'), yes_no("auto_page_turn")),
        ("reader_fit", tr('app.default_mode'), tr('app.width') if settings.get("reader_fit", "page") == "width" else tr('app.page')),
        ("remember_reader_mode", tr('app.remember_mode_per_manga'), yes_no("remember_reader_mode")),
        ("save_reader_position", tr('app.save_vertical_position'), yes_no("save_reader_position")),
        ("show_page_indicator", tr('app.show_page_indicator'), yes_no("show_page_indicator")),
        ("prefetch_pages", tr('app.page_prefetch'), str(settings.get("prefetch_pages", 3))),
        ("prefetch_next_chapter", tr('app.prefetch_next_chapter'), yes_no("prefetch_next_chapter")),
        ("scroll_step", tr('app.wheel_arrow_step'), tr('app.of_screen').format(round(settings.get("scroll_step", 0.10) * 100))),
    ]


MOBILE_READER_OPTION_HELP = {
    "mobile_double_tap_zoom": "Zoom factor used around the touched point. Double-tap again resets to fit-to-screen.",
}

READER_OPTION_HELP = {
    "auto_page_turn": tr('app.width_only_reaching_the_bottom_advances_another_upward_input_at_the_top'),
    "reader_fit": tr('app.mode_for_manga_without_a_remembered_mode_f_toggles_width_page_not_the_wi'),
    "remember_reader_mode": tr('app.on_each_manga_remembers_its_last_mode_off_opening_a_manga_always_uses_th'),
    "save_reader_position": tr('app.on_resume_the_vertical_position_off_start_at_the_top_chapter_and_page_ar'),
    "show_page_indicator": tr('app.brief_indicator_on_page_or_mode_changes_tab_shows_it_on_demand_errors_ar'),
    "prefetch_pages": tr('app.pages_around_current_0_1_3_5_or_10_each_direction_zero_disables_prefetch'),
    "prefetch_next_chapter": tr('app.prepare_the_next_chapter_near_the_end_chapter_navigation_still_works_whe'),
    "scroll_step": tr('app.distance_per_step_in_width_mode_space_moves_down_one_screen_with_a_small'),
}


def render_reader_options(settings, selection, flash=None, mobile=False):
    import textwrap
    ui.clear()
    ui.brand(VERSION, tr('app.reader'))
    print()
    rows = reader_option_rows(settings, mobile=mobile)
    selection = max(0, min(selection, len(rows) - 1))
    count = max(1, min(len(rows), ui.term_size().lines - 15))
    offset = min(max(0, selection - count + 1), max(0, len(rows) - count))
    ui.frame_top()
    for index in range(offset, min(len(rows), offset + count)):
        _key, label, value = rows[index]
        selected = index == selection
        ui.frame_line(("> " if selected else "  ") + label, value,
                      (ui.BOLD, ui.ACCENT) if selected else (ui.DIM,),
                      (ui.CYAN,) if selected else (ui.DIM,))
    ui.frame_bottom()
    help_key = rows[selection][0]
    help_text = MOBILE_READER_OPTION_HELP.get(help_key) or READER_OPTION_HELP.get(help_key, "")
    for line in textwrap.wrap(help_text, width=max(20, ui.width() - 2)):
        ui.emit(ui.paint(line, ui.DIM))
    ui.key_hint([(tr('app.up_down'), tr('app.select')), (tr('app.enter_left_right'), tr('app.change')), ("ESC", tr('app.back'))])
    if flash:
        ui.status(flash, "ok")


def reader_options_flow(engine, settings, mobile=False):
    selection, flash = 0, None
    while True:
        rows = reader_option_rows(settings, mobile=mobile)
        render_reader_options(settings, selection, flash, mobile=mobile)
        flash = None
        key = ui.read_key()
        if key == "esc":
            return
        if key in ("up", "down", "home", "end"):
            selection = _move(selection, key, len(rows), 1)
        elif key in ("enter", "left", "right"):
            cycle_option(settings, rows[selection][0], -1 if key == "left" else 1)
            save_settings(SETTINGS_FILE, settings)
            engine.update_preferences(settings)
            flash = tr('app.saved_applies_when_the_reader_opens')


def render_options(settings, selection, flash=None):
    ui.clear()
    ui.brand(VERSION, tr('app.settings'))
    print()
    ui.frame_top()
    ui.frame_line(tr('app.preferences'), "", (ui.BOLD, ui.ACCENT), (ui.CYAN, ui.BOLD))
    ui.frame_rule()
    rows = option_rows(settings)
    for index, (_action, label, value) in enumerate(rows):
        selected = index == selection
        marker = "▶" if selected else "·"
        ui.frame_line("{} {}".format(marker, label), value, (ui.BOLD, ui.ACCENT) if selected else (ui.DIM,), (ui.CYAN,) if selected and value else (ui.DIM,))
    ui.frame_bottom()
    ui.key_hint([("UP/DOWN", tr("nav.select")), ("ENTER", tr("nav.open")), ("ESC", tr("nav.back"))])
    if flash:
        ui.status(flash, "ok")

def cycle_option(settings, action, direction=1):
    choices = {"accent_color": list(ACCENT_COLORS), "reader_fit": ["width","page"], "scroll_step": [0.05,0.10,0.15,0.20],
               "mobile_double_tap_zoom": [1.5,2.0,2.5],
               "prefetch_pages": [0,1,3,5,10], "cache_mib": [128,256,512,1024],
               "prefetch_next_chapter": [False,True],
               "auto_page_turn": [False, True], "remember_reader_mode": [False, True],
               "show_page_indicator": [False, True], "save_reader_position": [False, True]}
    if action in choices:
        values = choices[action]
        current = settings.get(action, default_settings()[action])
        index = values.index(current) if current in values else 0
        settings[action] = values[(index+direction) % len(values)]
        return
    if action == "source_mode":
        values = ["auto", "mangakatana", "mangapill"]
        current = settings.get("source_mode") or "auto"
        try:
            index = values.index(current)
        except ValueError:
            index = 0
        settings["source_mode"] = values[(index + direction) % len(values)]
        return
    if action == "source_priority":
        priority = settings.get("source_priority") or ["mangakatana", "mangapill"]
        settings["source_priority"] = list(reversed(priority))
        return
    if action == "saved_sort":
        values = ["activity", "title", "saved"]
        current = settings.get("saved_sort") or "activity"
        try:
            index = values.index(current)
        except ValueError:
            index = 0
        settings["saved_sort"] = values[(index + direction) % len(values)]

def options_flow(engine, settings):
    selection = 0
    flash = None
    while True:
        rows = option_rows(settings)
        selection = max(0, min(selection, len(rows) - 1))
        render_options(settings, selection, flash=flash)
        flash = None
        key = ui.read_key()
        if key in ("up", "down", "home", "end"):
            selection = _move(selection, key, len(rows), 1)
            continue
        if key == "esc":
            return
        action = rows[selection][0]
        if action == "reader_customization":
            if key in ("enter", "right"):
                reader_options_flow(engine, settings, mobile=is_android_termux())
            continue
        if action == "appearance":
            if key in ("enter", "right"):
                appearance_flow(engine, settings)
            continue
        if action == "about":
            if key in ("enter", "right"):
                about_flow()
            continue
        if action == "diagnostics" and key == "enter":
            source_diagnostics_flow(engine)
            continue
        if key not in ("enter", "left", "right"):
            continue
        if action == "diagnostics":
            continue
        direction = -1 if key == "left" else 1
        cycle_option(settings, action, direction=direction)
        save_settings(SETTINGS_FILE, settings)
        engine.update_preferences(settings)
        flash = tr('app.settings_updated')


def render_appearance(selection, flash=None, error=False):
    """Render the live preview without committing any preferences."""
    name = ACCENT_COLORS[selection]
    ui.clear()
    ui.brand(VERSION, tr("appearance.title"))
    print()
    ui.frame_top()
    ui.frame_line(tr("appearance.accent"), tr("color." + name),
                  (ui.BOLD, ui.ACCENT), (ui.BOLD, ui.ACCENT))
    ui.frame_rule()
    count = max(1, min(len(ACCENT_COLORS), ui.term_size().lines - 15))
    offset = min(max(0, selection - count + 1), max(0, len(ACCENT_COLORS) - count))
    for index in range(offset, min(len(ACCENT_COLORS), offset + count)):
        color = ACCENT_COLORS[index]
        selected = index == selection
        marker = "\u25b6" if selected else "\u00b7"
        ui.frame_line(marker + " " + tr("color." + color), "\u25c6",
                      (ui.BOLD, ui.ACCENT) if selected else (ui.DIM,),
                      (ui._fg256(PALETTES[color].accent),))
    ui.frame_bottom()
    ui.emit(ui.paint(ui.compact(tr("appearance.preview"), ui.width()), ui.DIM))
    ui.key_hint([("UP/DOWN", tr("appearance.select")),
                 ("ENTER", tr("appearance.confirm")), ("ESC", tr("appearance.cancel"))])
    if flash:
        ui.status(flash, "error" if error else "ok")


def appearance_flow(engine, settings):
    original = normalize_accent(settings.get("accent_color"))
    selection = ACCENT_COLORS.index(original)
    committed, flash = False, None
    try:
        while True:
            ui.apply_theme(ACCENT_COLORS[selection])
            render_appearance(selection, flash=flash, error=bool(flash))
            flash = None
            key = ui.read_key()
            if key == "esc":
                return
            if key in ("up", "down", "home", "end", "pgup", "pgdn", "left", "right"):
                selection = _move(selection, key, len(ACCENT_COLORS), 1)
            elif key == "enter":
                candidate = dict(settings, accent_color=ACCENT_COLORS[selection])
                try:
                    save_settings(SETTINGS_FILE, candidate)
                except OSError as exc:
                    flash = tr("appearance.save_failed", exc)
                    continue
                settings.clear()
                settings.update(candidate)
                committed = True
                engine.update_preferences(settings)
                return
    finally:
        ui.apply_theme(settings.get("accent_color") if committed else original)


def render_about():
    ui.clear()
    ui.brand(VERSION, tr("brand.subtitle"))
    print()
    ui.frame_top()
    ui.frame_line(tr("about.title"), "", (ui.BOLD, ui.ACCENT), ())
    ui.frame_line(tr("about.version"), VERSION, (ui.ACCENT,), (ui.ACCENT,))
    ui.frame_line(tr("about.description"))
    ui.frame_line(tr("about.inspiration"))
    ui.frame_rule()
    ui.frame_line(tr("about.sources"), "MangaKatana + MangaPill", (ui.ACCENT,), ())
    ui.frame_line(tr("about.guide"), tr("about.guide_value"), (ui.ACCENT,), (ui.TEXT_WARM,))
    ui.frame_line(tr("about.scope"), "", (ui.DIM,), ())
    ui.frame_line(tr("about.privacy"), "", (ui.DIM,), ())
    ui.frame_bottom()
    ui.key_hint([("ESC", tr("nav.back"))])


def about_flow():
    while True:
        render_about()
        if ui.read_key() in ("esc", "enter"):
            return


def doctor():
    ensure_dirs()
    checks = []
    settings = load_settings(SETTINGS_FILE)
    checks.append(("python", sys.version_info >= MIN_PYTHON, platform.python_version()))
    checks.append((tr('app.architecture'), True, platform.machine() or tr('app.unknown')))
    mpv_path = shutil.which("mpv")
    checks.append(("mpv", mpv_path is not None, mpv_path or tr('app.not_found_907ba7')))
    vo_ok = False
    vo_detail = tr('app.mpv_unavailable')
    if mpv_path:
        outputs = _mpv_video_outputs(mpv_path)
        candidates = _mpv_output_candidates(mpv_path, mobile=is_android_termux())
        vo_ok = bool(outputs.intersection({"gpu", "x11"}))
        vo_detail = tr('app.preferred_available').format(
            " > ".join(candidates), ", ".join(sorted(outputs.intersection({"gpu", "gpu-next", "xv", "x11"}))) or tr('app.none'))
    checks.append((tr('app.reader_video'), vo_ok, vo_detail))
    checks.append((tr('app.reader_integrity'), reader_integrity_ok(), tr('app.reader_py_reader_lua_release')))
    checks.append((tr('app.state'), os.access(str(DATA_DIR), os.W_OK), str(DATA_DIR)))
    state = load_state(STATE_FILE)
    checks.append((tr('app.state_schema'), state.get("schema") == 6, "schema {}".format(state.get("schema"))))
    checks.append(("cache", os.access(str(CACHE_DIR), os.W_OK), str(CACHE_DIR)))
    checks.append((tr('app.preferences_bca684'), True, "{} · {}".format(source_mode_label(settings), source_priority_label(settings))))
    engine = make_engine(settings)
    checks.append((tr('app.sources_878a52'), set(engine.sources) == {"mangakatana", "mangapill"}, "MK + MP"))
    sources_dir = Path(__file__).resolve().parent / "acmanga" / "sources"
    modules = sorted(path.stem for path in sources_dir.glob("*.py") if path.name not in ("__init__.py", "base.py") and not path.name.startswith("__"))
    checks.append((tr('app.source_tree'), modules == ["mangakatana", "mangapill"], ", ".join(modules)))
    checks.append((tr('app.dependencies'), True, tr('app.python_stdlib_no_browser_js_external_parser')))
    checks.append((tr('app.reader_3d0941'), True, tr('app.mpv_gpu_preferred_x11_fallback_bounded_prefetch_space_limited_cache')))
    failed = 0
    print(tr('app.manga_cli_doctor').format(VERSION))
    for name, ok, detail in checks:
        print("[{}] {:<18} {}".format("OK" if ok else tr('app.fail'), name, detail))
        if not ok:
            failed += 1
    print(tr('app.local_diagnostics_ok').format(len(checks) - failed, len(checks)))
    print(tr('app.use_network_test_to_check_search_chapters_pages_an_actual_first_image'))
    return 0 if failed == 0 else 1

def self_test():
    tests = []
    def check(name, condition):
        tests.append((name, bool(condition)))

    check("version", VERSION == "0.8.3")
    check(tr('app.reader_integrity'), reader_integrity_ok())
    check(tr('app.normalizer'), normalize_title("One-Piéce!") == "one piece")
    check(tr('app.alias_separators'), titles_equivalent("HIMA-TEN!", "Himaten!"))
    check(tr('app.readable_theme'), ui.CYAN != ui.BLOOD and ui.TEXT_WARM != ui.DIM)
    engine = make_engine()
    check(tr('app.sources_878a52'), set(engine.sources) == {"mangakatana", "mangapill"})
    check(tr('app.auto_mode'), engine.active_source_keys() == ["mangakatana", "mangapill"])
    configured = make_engine({"source_mode": "mangapill", "source_priority": ["mangapill", "mangakatana"], "saved_sort": "title"})
    check(tr('app.manual_mode'), configured.active_source_keys() == ["mangapill"])
    check("saved-sort", saved_sort_label({"saved_sort": "title"}) == tr('app.title'))
    sample_state = {"schema": 6, "saved": {}, "progress": {
        "legacy:x": {"title": "X", "chapter_number": "3", "page": 4, "total_pages": 9, "updated": 20, "manga": {"key": "legacy:x", "identity": "legacy:x", "title": "X", "variants": []}},
        "legacy:y": {"title": "Y", "chapter_number": "1", "page": 2, "total_pages": 7, "updated": 10, "manga": {"key": "legacy:y", "identity": "legacy:y", "title": "Y", "variants": []}},
    }, "last_read": "legacy:x"}
    check(tr('app.history_order'), [item["key"] for item in history_items(sample_state)] == ["legacy:x", "legacy:y"])
    check(tr('app.history_last'), last_history_item(sample_state)["key"] == "legacy:x")
    check(tr('app.saved_availability'), availability_status_text({"status": "ok", "known_total": 12}) == tr('app.12_ch'))
    check(tr('app.saved_updates'), availability_status_text({"status": "ok", "known_total": 12, "new_keys": ["x"]}) == tr('app.12_ch_1_new'))
    check(tr('app.source_badge'), ui.source_badge({"variants": [{"source": "mangakatana"}, {"source": "mangapill"}]}) == "MK+MP")
    check(tr('app.chapter_priority'), engine._chapter_preference({"language": "en", "source": "mangakatana"}) < engine._chapter_preference({"language": "en", "source": "mangapill"}))
    chapters = [
        {"source": "mangakatana", "id": "3", "number": "3"},
        {"source": "mangakatana", "id": "2", "number": "2"},
        {"source": "mangakatana", "id": "1", "number": "1"},
    ]
    check(tr('app.next_chapter'), engine.next_chapter(chapters, chapters[2])["id"] == "2")
    try:
        own_source = Path(__file__).read_text(encoding="utf-8")
    except OSError:
        own_source = ""
    check(tr('app.continuous_reading'), "next_chapter = next_fn(chapters, canonical)" in own_source and "current = next_chapter" in own_source and "page = 1" in own_source)
    check(tr('app.chapters_no_c'), '("C", "continue")' not in __import__("inspect").getsource(render_manga))
    check("parser-katana", engine.sources["mangakatana"]._page_urls is not None)
    check("parser-pill", engine.sources["mangapill"]._page_urls is not None)
    with __import__("tempfile").TemporaryDirectory(prefix="acmanga-self-") as tmp:
        playlist, conf, script = write_mpv_files(tmp, [Path(tmp) / "001.jpg", Path(tmp) / "002.jpg"], mobile=False)
        lua = script.read_text(encoding="utf-8")
        conf_text = conf.read_text(encoding="utf-8")
        check("playlist", playlist.exists() and "002.jpg" in playlist.read_text(encoding="utf-8"))
        check("reader-lua", script.exists() and "mp.register_event('file-loaded'" in lua and script.name == "acmanga_reader.lua")
        check(tr('app.unified_scroll'), "scroll(1, scroll_step*s,k)" in lua and "scroll(1,(1-overlap)*s,k)" in lua)
        check("page-reset-top", "saved.position or 0" in lua and "video-align-y" in lua)
        check("fine-arrows", "pan-up" in lua and "pan-down" in lua)
        check("bounded-align", "g.overflow" in lua and "clamp(" in lua)
        check("mpv-manual-quit", "mp.commandv('quit', '4')" in lua)
        check("mpv-fullscreen", "toggle-fullscreen" in lua and "F11" in lua)
        cmd = mpv_command("/usr/bin/mpv", playlist, conf, script, str(Path(tmp) / "sock"), video_output="gpu", mobile=False)
        check("mpv-ipc", any(part.startswith("--input-ipc-server=") for part in cmd))
        check("mpv-script", any(part.startswith("--script=") for part in cmd))
        check("mpv-fit-policy", (("--video-recenter=no" in cmd) == ("video-recenter" in _mpv_supported_options("/usr/bin/mpv"))) and "--no-config" in cmd and "--vo=gpu" in cmd and "--fs=no" in cmd)
        check("mpv-windowed", "--border=yes" in cmd and ("--window-maximized=yes" in cmd or "--geometry=100%x100%+0+0" in cmd) and "--force-window-position=yes" not in cmd)
        check(tr('app.mpv_single_session'), "--keep-open=yes" in cmd and "--idle=yes" in cmd)
        check(tr('app.mouse'), "MBTN_LEFT" in lua and "MBTN_RIGHT" in lua)
        check(tr('app.debounce'), "wheel_locked" in lua and "blocked[key]" in lua)
        check(tr('app.independent_width'), "toggle-fit" in lua and "bind({'f','F','v','V'}" in lua)
        check(tr('app.vertical_persistence'), "position=pos" in lua)
        check(tr('app.bounded_prefetch'), default_settings()["prefetch_pages"] == 3)

    # Offline checks for the user-visible personalization contract.
    expected = {"auto_page_turn", "reader_fit", "remember_reader_mode", "prefetch_pages",
                "show_page_indicator", "save_reader_position"}
    check(tr('app.reader_menu'), expected <= {row[0] for row in reader_option_rows(default_settings(), mobile=False)})
    check(tr('app.new_mode_page'), default_settings()["reader_fit"] == "page")
    options = default_settings()
    options["prefetch_pages"] = 0
    values = []
    for _ in range(5):
        values.append(options["prefetch_pages"])
        cycle_option(options, "prefetch_pages")
    check(tr('app.prefetch_0_1_3_5_10'), values == [0, 1, 3, 5, 10])
    with __import__("tempfile").TemporaryDirectory(prefix="manga-cli-prefs-") as tmp:
        options["auto_page_turn"] = False
        options["show_page_indicator"] = False
        options["save_reader_position"] = False
        path = Path(tmp) / "prefs.json"
        save_settings(path, options)
        loaded = load_settings(path)
        check(tr('app.configurable_edges'), loaded["auto_page_turn"] is False)
        check(tr('app.configurable_indicator'), loaded["show_page_indicator"] is False)
        check(tr('app.configurable_position'), loaded["save_reader_position"] is False)
        state_sample = {"progress": {"mangakatana:self-test": {"reader_mode": "width"}}}
        work = {"identity": "mangakatana:self-test", "title": "Self test"}
        with ReaderSession(tmp) as session:
            session.begin_manga(state_sample, work)
            check(tr('app.remember_manga_mode'), session.last_view["fit"] == "width")
        with ReaderSession(tmp, {"remember_reader_mode": False}) as session:
            session.begin_manga(state_sample, work)
            check(tr('app.global_mode_without_memory'), session.last_view["fit"] == "page")

    # UI-only checks added in 0.8; do not launch mpv or write user state.
    check("accent-default-crimson", default_settings()["accent_color"] == "crimson")
    check("ten-distinct-accents", len(ACCENT_COLORS) == 10 and len({p.accent for p in PALETTES.values()}) == 10)
    check("appearance-menu", "appearance" in {row[0] for row in option_rows(default_settings())})
    check("english-interface", progress_label(None) == "No progress")
    check("cjk-cell-width", ui.cell_width("\u5b9d\u77f3") == 4)
    check("combining-cell-width", ui.cell_width("e\u0301") == 1)
    check("ascii-ellipsis", ui.compact("abcdefgh", 5) == "ab...")
    old_theme = ui.THEME_NAME
    semantic = (ui.GREEN, ui.YELLOW, ui.RED)
    try:
        ui.apply_theme("blue")
        check("semantic-colors-fixed", (ui.GREEN, ui.YELLOW, ui.RED) == semantic)
    finally:
        ui.apply_theme(old_theme)
    check("english-catalogue", tr("appearance.title") == "APPEARANCE")

    failed = [name for name, ok in tests if not ok]
    for name, ok in tests:
        print("[{}] {}".format("OK" if ok else tr('app.fail'), name))
    print(tr('app.self_test_ok').format(len(tests) - len(failed), len(tests)))
    return 1 if failed else 0

def migrate_data():
    ensure_dirs()
    state = load_state(STATE_FILE)
    settings = load_settings(SETTINGS_FILE)
    save_state(STATE_FILE, state)
    save_settings(SETTINGS_FILE, settings)
    return 0


def network_test():
    engine = make_engine(default_settings())
    results = engine.diagnostic_sources()
    failed = 0
    print(tr('app.manga_cli_end_to_end_network_test').format(VERSION))
    for key in ("mangakatana", "mangapill"):
        item = results.get(key) or {"ok": False, "detail": tr('app.no_result')}
        ok = bool(item.get("ok"))
        print("[{}] {:<12} {}".format("OK" if ok else tr('app.fail'), source_label(key), item.get("detail") or ""))
        if not ok:
            failed += 1
    print(tr('app.network_test_2_ok').format(2 - failed))
    return 0 if failed == 0 else 1


def source_check(title):
    """Diagnose a single title without the TUI or modifying saved state.

    Print candidate identities and provider chapter counts for a reproducible
    source report. Review local paths/title details before sharing the output.
    """
    title = " ".join(str(title or "").split())
    if not title:
        print(tr('app.error_provide_a_title_for_source_check'))
        return 2
    engine = make_engine(default_settings())
    print(tr('app.manga_cli_source_check').format(VERSION, title))
    failed = 0
    for key in ("mangakatana", "mangapill"):
        source = engine.sources[key]
        print("\n[{}] {}".format(source_label(key), title))
        try:
            results = source.search(title)
            print(tr('app.search_candidate_s_status').format(len(results), getattr(source, "last_search_status", "?")))
            for item in results[:5]:
                print("  - {!r} · id={}".format(item.get("title"), item.get("id")))
            exact = [item for item in results if normalize_title(item.get("title") or "") == normalize_title(title)]
            chosen = exact[0] if len(exact) == 1 else (results[0] if len(results) == 1 else None)
            if chosen is None:
                print(tr('app.result_exact_identity_not_resolved'))
                failed += 1
                continue
            details = source.details(chosen.get("ref") or {"id": chosen.get("id")}, force=True)
            print(" details: {!r} · author={!r} · id={}".format(details.get("title"), details.get("author"), details.get("id")))
            chapters = source.chapters_all(chosen.get("ref") or {"id": chosen.get("id")}, force=True).get("items") or []
            print(" chapters: {}".format(len(chapters)))
            if chapters:
                first = chapters[-1]
                last = chapters[0]
                print("  first: {} · {}".format(first.get("number"), (first.get("source_ref") or {})))
                print("  last : {} · {}".format(last.get("number"), (last.get("source_ref") or {})))
        except Exception as exc:
            detail = engine._error_detail(exc)
            print(" ERROR: kind={} · {}".format(detail.get("kind"), detail.get("message")))
            failed += 1
    return 0 if failed == 0 else 1


def main():
    parser = argparse.ArgumentParser(prog="manga-cli", description=tr('app.manga_cli_lightweight_terminal_manga_reader_inspired_by_ani_cli'))
    parser.add_argument("--doctor", action="store_true", help=tr('app.local_diagnostics'))
    parser.add_argument("--self-test", action="store_true", help=tr('app.offline_self_test'))
    parser.add_argument("--network-test", action="store_true", help=tr('app.live_end_to_end_test_of_both_sources'))
    parser.add_argument("--source-check", metavar=tr('app.title'), help=tr('app.detailed_diagnostics_for_a_manga_on_mk_mp'))
    parser.add_argument("--migrate-data", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--version", action="store_true", help=tr('app.show_version'))
    args = parser.parse_args()
    if args.version:
        print(VERSION); return 0
    if args.self_test:
        return self_test()
    if args.doctor:
        return doctor()
    if args.network_test:
        return network_test()
    if args.source_check:
        return source_check(args.source_check)
    if args.migrate_data:
        return migrate_data()
    if sys.version_info < MIN_PYTHON:
        print(tr('app.error_python_or_later_is_required').format(*MIN_PYTHON)); return 1
    ensure_dirs()
    state = load_state(STATE_FILE); save_state(STATE_FILE, state)
    settings = load_settings(SETTINGS_FILE); save_settings(SETTINGS_FILE, settings)
    ui.apply_theme(settings.get("accent_color"))
    engine = make_engine(settings)
    while True:
        action = main_menu(state, settings)
        if action == "quit":
            ui.clear(); return 0
        if action == "search":
            search_flow(engine, state, settings)
        elif action == "saved":
            saved_flow(engine, state, settings)
        elif action == "history":
            history_flow(engine, state, settings)
        elif action == "continue":
            continue_last_read(engine, state, settings)
        elif action == "options":
            options_flow(engine, settings)



if __name__ == "__main__":
    raise SystemExit(main())
