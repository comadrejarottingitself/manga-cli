"""Bounded, cancellable look-ahead downloads and a per-page disk cache.

Only the two existing adapters supply URLs. Files already downloaded successfully
survive a failure of a different page; .part files are never given to mpv.
"""
from .i18n import tr
import hashlib
import json
import os
import re
import threading
import time
from collections import deque
from concurrent.futures import Future
from pathlib import Path

from .errors import MangaError, SourceError
from .util import atomic_json_write, looks_like_image, extension_from_bytes, _stream_image

PLAN_TTL = 6 * 60 * 60
PAGE_NAME = re.compile(r'^[0-9]{6}\.(?:jpg|png|gif|webp|avif)$')
CHAPTER_NAME = re.compile(r'^ch-[a-f0-9]{32}$')


def background_call(fn):
    """One daemon worker: cancellation never keeps the TUI process alive."""
    future = Future()
    def run():
        if not future.set_running_or_notify_cancel():
            return
        try:
            future.set_result(fn())
        except BaseException as exc:
            future.set_exception(exc)
    threading.Thread(target=run, name='manga-cli-prepare', daemon=True).start()
    return future


def chapter_key(chapter):
    identity = {k: chapter.get(k) for k in ('source', 'id', 'title_id', 'source_ref')}
    raw = json.dumps(identity, ensure_ascii=True, sort_keys=True).encode('utf-8')
    return 'ch-' + hashlib.sha256(raw).hexdigest()[:32]


def make_plan(source, chapter):
    """Expose existing parsers without changing their extraction/identity rules."""
    ref = chapter.get('source_ref') or {}
    if source.key == 'mangakatana':
        from urllib.parse import quote
        ident = chapter.get('title_id') or ref.get('manga_id') or ''
        cid = ref.get('chapter_id') or chapter.get('id') or ''
        referer = source.base_url + '/manga/{}/{}'.format(quote(str(ident), safe='._-'), quote(str(cid), safe='._-'))
        sets = source._page_url_sets(chapter)
    elif source.key == 'mangapill':
        from urllib.parse import quote
        referer = source.base_url + '/chapters/' + quote(str(ref.get('chapter_ref') or ''), safe='/-._')
        sets = [source._page_urls(chapter)]
    else:
        raise MangaError(tr('streaming.no_progressive_page_adapter_is_available_for_this_source'))
    if not sets or not sets[0] or len(sets[0]) > 3000:
        raise MangaError(tr('streaming.page_list_is_empty_or_too_large'))
    count = len(sets[0])
    # Different-sized servers cannot safely be mixed page by page.
    sets = [[str(u) for u in urls] for urls in sets if len(urls) == count]
    if any(not u.startswith(('https://', 'http://')) for urls in sets for u in urls):
        raise MangaError(tr('streaming.the_source_returned_an_invalid_image_url'))
    return {'schema': 1, 'key': chapter_key(chapter), 'source_name': source.name,
            'referer': referer, 'url_sets': sets, 'created': time.time()}


def prepare_stream(engine, chapter, cache_dir, prefetch=3, workers=2, refresh=False):
    folder = Path(cache_dir) / 'pages' / chapter_key(chapter)
    folder.mkdir(parents=True, exist_ok=True)
    plan_path = folder / 'plan.json'
    plan = None
    try:
        raw = json.loads(plan_path.read_text(encoding='utf-8'))
        if (not refresh and isinstance(raw, dict) and raw.get('key') == chapter_key(chapter)
                and 0 <= time.time() - float(raw.get('created', 0)) < PLAN_TTL):
            plan = raw
    except (OSError, ValueError, TypeError):
        pass
    if plan is None:
        plan = make_plan(engine.source(chapter['source']), chapter)
        atomic_json_write(plan_path, plan)
    return PageStream(plan, folder, prefetch=prefetch, workers=workers)


class PageStream:
    def __init__(self, plan, folder, prefetch=3, workers=2):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        sets = plan.get('url_sets')
        if not isinstance(sets, list) or not sets or not isinstance(sets[0], list) or not sets[0]:
            raise MangaError(tr('streaming.invalid_page_cache_refresh_the_chapters'))
        self.total = len(sets[0])
        if self.total > 3000:
            raise MangaError(tr('streaming.too_many_pages_in_a_chapter'))
        self.urls = [urls for urls in sets if isinstance(urls, list) and len(urls) == self.total]
        if any(not isinstance(u, str) or not u.startswith(('http://','https://')) for urls in self.urls for u in urls):
            raise MangaError(tr('streaming.invalid_url_cache'))
        self.name = str(plan.get('source_name') or 'Fuente')
        self.referer = str(plan.get('referer') or '')
        self.fingerprint = hashlib.sha256(json.dumps(self.urls, separators=(',', ':')).encode()).hexdigest()
        self.manifest_path = self.folder / 'pages.json'
        self.manifest = {'schema': 1, 'fingerprint': self.fingerprint, 'files': {}}
        try:
            old = json.loads(self.manifest_path.read_text(encoding='utf-8'))
            if old.get('schema') == 1 and old.get('fingerprint') == self.fingerprint and isinstance(old.get('files'), dict):
                self.manifest = old
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        self.prefetch = max(0, min(10, int(prefetch)))
        self.cv = threading.Condition()
        self.closed = False
        self.ready = {}
        self.errors = {}
        self.active = set()
        self.requests = deque()
        self.anchor = None
        self.threads = []
        # Validation is cheap and limited to a file header and expected byte size.
        for key, entry in list(self.manifest['files'].items()):
            try:
                index = int(key)
            except (TypeError, ValueError):
                continue
            path = self._cached(index, entry)
            if path is not None:
                self.ready[index] = path
        for i in range(max(1, min(3, int(workers)))):
            worker = threading.Thread(target=self._worker, name='manga-cli-page-{}'.format(i), daemon=True)
            self.threads.append(worker)
            worker.start()

    def _cached(self, index, entry):
        if not 1 <= index <= self.total or not isinstance(entry, dict):
            return None
        name = str(entry.get('name') or '')
        if not PAGE_NAME.fullmatch(name) or not name.startswith('{:06d}.'.format(index)):
            return None
        path = self.folder / name
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size != entry.get('size') or path.stat().st_size < 32:
                return None
            with path.open('rb') as f:
                if not looks_like_image(f.read(32)):
                    return None
            return path
        except OSError:
            return None

    def request(self, index, retry=False):
        index = max(1, min(self.total, int(index)))
        with self.cv:
            if retry:
                self.errors.pop(index, None)
            if not self.closed and index not in self.ready and index not in self.active:
                try:
                    self.requests.remove(index)
                except ValueError:
                    pass
                self.requests.appendleft(index)
                self.cv.notify_all()
        return index

    def focus(self, index):
        with self.cv:
            self.anchor = max(1, min(self.total, int(index)))
            self.cv.notify_all()
        try:
            os.utime(str(self.folder), None)
        except OSError:
            pass

    def peek(self, index):
        with self.cv:
            return self.ready.get(index), self.errors.get(index)

    def wait(self, index, timeout=10):
        """Convenience for tests/diagnostics; the UI uses nonblocking peek()."""
        self.request(index)
        deadline = time.monotonic() + timeout
        with self.cv:
            while not self.closed:
                if index in self.ready:
                    return self.ready[index]
                if index in self.errors:
                    raise self.errors[index]
                left = deadline-time.monotonic()
                if left <= 0:
                    raise TimeoutError(tr('streaming.the_page_is_still_loading'))
                self.cv.wait(left)
        raise MangaError(tr('streaming.download_cancelled'))

    def _next_locked(self):
        while self.requests:
            index = self.requests.popleft()
            if index not in self.ready and index not in self.active and index not in self.errors:
                return index
        if self.anchor is not None:
            # Keep a symmetric window around the visible page. Two workers can
            # naturally take +1/-1 together, then +2/-2, so rapid backward and
            # forward navigation receive the same nearby-page priority. Explicit
            # request() calls above always win over background prefetch.
            for distance in range(1, self.prefetch + 1):
                for index in (self.anchor + distance, self.anchor - distance):
                    if (1 <= index <= self.total and index not in self.ready
                            and index not in self.active and index not in self.errors):
                        return index
        return None

    def _fetch(self, index):
        last_error = None
        temp = self.folder / '{:06d}.{}.part'.format(index, threading.get_ident())
        headers = {'Referer': self.referer, 'Accept': 'image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8,*/*;q=0.2'}
        try:
            for urls in self.urls:
                if self.closed:
                    return None
                try:
                    head, size = _stream_image(urls[index-1], self.name, temp, timeout=12, headers=headers, retries=1)
                    if self.closed:
                        return None
                    if size < 32 or not looks_like_image(head):
                        raise MangaError(tr('streaming.incomplete_image_response'))
                    path = self.folder / ('{:06d}'.format(index) + extension_from_bytes(head))
                    os.replace(str(temp), str(path))
                    return path, size
                except Exception as exc:
                    last_error = exc
            if isinstance(last_error, MangaError):
                raise last_error
            raise MangaError(tr('streaming.could_not_download_page').format(index, last_error))
        finally:
            try:
                temp.unlink()
            except OSError:
                pass

    def _worker(self):
        while True:
            with self.cv:
                index = self._next_locked()
                while index is None and not self.closed:
                    self.cv.wait()
                    index = self._next_locked()
                if self.closed:
                    return
                self.active.add(index)
            try:
                result = self._fetch(index)
                with self.cv:
                    if result is not None and not self.closed:
                        path, size = result
                        self.ready[index] = path
                        self.manifest['files'][str(index)] = {'name':path.name,'size':size}
                        atomic_json_write(self.manifest_path, self.manifest)
            except Exception as exc:
                with self.cv:
                    self.errors[index] = exc
            finally:
                with self.cv:
                    self.active.discard(index)
                    self.cv.notify_all()

    def discard(self, index):
        """Invalidate a cached image after an explicit reader decode failure."""
        with self.cv:
            path = self.ready.pop(index, None)
            self.errors.pop(index, None)
            self.manifest['files'].pop(str(index), None)
            if path is not None:
                try:
                    path.unlink()
                except OSError:
                    pass
            atomic_json_write(self.manifest_path, self.manifest)

    def close(self):
        with self.cv:
            self.closed = True
            self.requests.clear()
            self.cv.notify_all()
        # An in-flight network read is bounded by its timeout and cannot commit
        # a new cache manifest after close(). Never block the reader on it.
        for thread in self.threads:
            thread.join(0.05)


def prune_pages(cache_dir, keep=(), limit_mib=512):
    """Only evict our hashed chapter directories, never data/progress or symlinks."""
    root = Path(cache_dir) / 'pages'
    if not root.is_dir():
        return
    keep = set(keep)
    entries = []
    size = 0
    for folder in root.iterdir():
        if folder.is_symlink() or not folder.is_dir() or not CHAPTER_NAME.fullmatch(folder.name):
            continue
        try:
            total = sum(p.stat().st_size for p in folder.iterdir() if p.is_file() and not p.is_symlink())
            stamp = folder.stat().st_mtime
        except OSError:
            continue
        size += total
        entries.append((stamp, folder, total))
    import shutil
    for _, folder, total in sorted(entries):
        if size <= max(64, int(limit_mib)) * 1024 * 1024:
            break
        if folder.name in keep or any(folder.glob('*.part')):
            continue
        shutil.rmtree(str(folder))
        size -= total
