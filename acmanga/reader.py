"""Reader controller: one mpv process per reading session, with bounded prefetch."""
from .i18n import tr
import functools
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

from .errors import MangaError
from .state import save_progress, progress_for, clean_reader_view, manga_identity
from .settings import _normalized as normalize_settings
from .streaming import background_call, chapter_key, prepare_stream, prune_pages

CACHE_CHAPTER_LIMIT = 2
MANUAL_QUIT_CODE = 4


def cache_key(chapter):
    return "{}-{}".format(chapter.get("source") or "source", chapter.get("id") or "chapter")


def prune_cache(cache_dir, keep=None, limit=CACHE_CHAPTER_LIMIT):
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    keep = set(keep or [])
    dirs = [p for p in cache_dir.iterdir() if p.is_dir()]
    if len(dirs) <= limit:
        return
    removable = []
    for path in dirs:
        if path.name in keep:
            continue
        try:
            stamp = path.stat().st_mtime
        except OSError:
            stamp = 0
        removable.append((stamp, path))
    removable.sort(key=lambda item: item[0])
    current = len(dirs)
    while current > limit and removable:
        _stamp, path = removable.pop(0)
        shutil.rmtree(str(path), ignore_errors=True)
        current -= 1


def prepare_chapter(engine, chapter, cache_dir, progress_callback=None):
    source = engine.source(chapter["source"])
    key = cache_key(chapter)
    chapter_dir = Path(cache_dir) / key
    chapter_dir.mkdir(parents=True, exist_ok=True)
    if progress_callback:
        progress_callback(tr('reader.preparing_pages_from').format(source.name))
    pages = source.prepare_pages(chapter, chapter_dir)
    pages = [Path(p) for p in pages if Path(p).exists() and Path(p).stat().st_size > 0]
    if not pages:
        raise MangaError(tr('reader.the_chapter_contains_no_pages'))
    try:
        os.utime(str(chapter_dir), None)
    except OSError:
        pass
    prune_cache(cache_dir, keep={key})
    return pages


def _lua_reader_script():
    return Path(__file__).with_name("reader.lua").read_text(encoding="utf-8")


def write_mpv_files(work_dir, pages):
    work_dir = Path(work_dir)
    playlist = work_dir / "pages.m3u"
    input_conf = work_dir / "input.conf"
    script = work_dir / "acmanga_reader.lua"
    playlist.write_text("".join(str(Path(p).resolve()) + "\n" for p in pages), encoding="utf-8")
    input_conf.write_text("# manga-cli: input is owned by acmanga_reader.lua\n", encoding="utf-8")
    script.write_text(_lua_reader_script(), encoding="utf-8")
    return playlist, input_conf, script


@functools.lru_cache(maxsize=4)
def _mpv_supported_options(mpv_path):
    """Return option names reported by ``mpv --list-options``.

    AntiComadreja Manga was developed against a newer mpv than Debian 12's
    0.35.x.  A few window-management flags are optional, so discover them at
    runtime instead of making reader startup depend on a specific mpv build.
    """
    try:
        result = subprocess.run(
            [mpv_path, "--no-config", "--list-options"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return set()

    options = set()
    for raw in result.stdout.splitlines():
        line = raw.strip()
        if not line.startswith("--"):
            continue
        options.add(line.split(None, 1)[0][2:])
    return options



@functools.lru_cache(maxsize=4)
def _mpv_video_outputs(mpv_path):
    """Return video output drivers reported by ``mpv --vo=help``."""
    try:
        result = subprocess.run(
            [mpv_path, "--no-config", "--vo=help"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return set()

    outputs = set()
    for raw in result.stdout.splitlines():
        parts = raw.strip().split(None, 1)
        if parts and parts[0] and not parts[0].startswith("Available"):
            outputs.add(parts[0])
    return outputs


def _mpv_output_candidates(mpv_path):
    """Prefer the GPU renderer and retain X11 as a compatibility fallback."""
    outputs = _mpv_video_outputs(mpv_path)
    candidates = [name for name in ("gpu", "x11") if name in outputs]
    # Some test doubles or unusual builds may not report --vo=help correctly.
    # Let mpv try its normal GPU renderer first; ReaderSession will report failure.
    return candidates or ["gpu"]

def ipc_command(sock_path, command, timeout=0.4):
    if not os.path.exists(sock_path):
        return None
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(sock_path)
        client.sendall(json.dumps({"command": command}).encode("utf-8") + b"\n")
        # Signal EOF cleanly after the request. This avoids noisy "connection reset"
        # messages from mpv's IPC server when using short-lived command sockets.
        try:
            client.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        data = b""
        while not data.endswith(b"\n"):
            block = client.recv(4096)
            if not block:
                break
            data += block
        if not data:
            return None
        for line in data.decode("utf-8", "replace").splitlines():
            try:
                response = json.loads(line)
            except ValueError:
                continue
            if "error" in response:
                return response.get("data") if response.get("error") == "success" else None
        return None
    except (OSError, ValueError, TypeError):
        return None
    finally:
        try:
            client.close()
        except OSError:
            pass


def wait_for_ipc(sock_path, process, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline and process.poll() is None:
        if os.path.exists(sock_path):
            return True
        time.sleep(0.05)
    return os.path.exists(sock_path)



def mpv_command(mpv_path, playlist, input_conf, reader_script, ipc_path, video_output="gpu"):
    supported = _mpv_supported_options(mpv_path)
    # Normal Xfce window, maximized so the canvas occupies the whole work area.
    # F controls image fit; it does not resize the window.
    cmd = [mpv_path, "--no-config", "--vo={}".format(video_output), "--fs=no",
           "--border=yes",
           "--no-audio", "--keepaspect=yes", "--keepaspect-window=no",
           "--idle=yes", "--force-window=immediate", "--keep-open=yes",
           "--input-default-bindings=no", "--input-terminal=no",
           "--input-doubleclick-time=0", "--image-display-duration=inf",
           "--loop-playlist=no", "--osc=no", "--osd-level=1", "--osd-bar=no",
           "--osd-font-size=21", "--osd-border-size=2", "--osd-align-x=right",
           "--osd-align-y=top", "--osd-margin-x=18", "--osd-margin-y=16",
           "--cursor-autohide=1000", "--title=manga-cli", "--background=#000000",
           "--input-conf={}".format(input_conf), "--script={}".format(reader_script),
           "--input-ipc-server={}".format(ipc_path)]
    if 'window-maximized' in supported:
        cmd.append('--window-maximized=yes')
    else:
        # Compatibility fallback: still occupy the complete desktop area.
        cmd.append('--geometry=100%x100%+0+0')
    # Keep a stable window size when newer mpv builds expose this flag.
    if 'auto-window-resize' in supported:
        cmd.append('--auto-window-resize=no')
    if 'video-recenter' in supported:
        cmd.append('--video-recenter=no')
    return cmd


class ReaderSession:
    """Lazy context manager, shared by successive chapters, never by two users."""
    def __init__(self, cache_dir, preferences=None):
        self.cache_dir = Path(cache_dir)
        self.preferences = normalize_settings(preferences)
        self.manga_key = None
        self.process = None
        self.video_output = None
        self.temp = None
        self.log_handle = None
        self.closed = False
        self.prepared = {}
        self.active_key = None
        self.next_chapter = None
        self.positions = {}
        self.last_view = {'position': 0, 'zoom': 0, 'horizontal': 0,
                          'fit': self.preferences['reader_fit']}
        self._last_snapshot = {}
        self._last_stat = None
        self.started = time.monotonic()
        self.stats = {'first_page_seconds': None, 'page_load_seconds': [], 'chapters': 0}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def ensure(self):
        if self.process is not None:
            if self.process.poll() is not None:
                raise MangaError(tr('reader.the_reader_has_closed'))
            return
        mpv = shutil.which('mpv')
        if not mpv:
            raise MangaError(tr('reader.mpv_was_not_found_in_path'))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        log = self.cache_dir / 'reader.log'
        if log.exists():
            try:
                log.replace(log.with_suffix('.log.1'))
            except OSError:
                pass
        self.log_handle = log.open('w', encoding='utf-8')
        self.temp = tempfile.TemporaryDirectory(prefix='manga-cli-')
        playlist, conf, script = write_mpv_files(self.temp.name, [])
        self.ipc = str(Path(self.temp.name) / 'mpv.sock')
        self.control = Path(self.temp.name) / 'control.json'
        env = os.environ.copy()
        env['ACMANGA_READER_STATE'] = str(self.control)

        failures = []
        for video_output in _mpv_output_candidates(mpv):
            for path in (Path(self.ipc), self.control, Path(str(self.control) + '.tmp')):
                try:
                    path.unlink()
                except OSError:
                    pass
            self.log_handle.write(tr('reader.manga_cli_starting_reader_with_vo').format(video_output))
            self.log_handle.flush()
            self.process = subprocess.Popen(
                mpv_command(mpv, playlist, conf, script, self.ipc, video_output=video_output),
                stdin=subprocess.DEVNULL, stdout=self.log_handle, stderr=subprocess.STDOUT, env=env)
            if wait_for_ipc(self.ipc, self.process):
                deadline = time.monotonic() + 3
                while not self.control.exists() and self.process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.03)
                if self.control.exists() and self.process.poll() is None:
                    self.video_output = video_output
                    self.log_handle.write(tr('reader.manga_cli_reader_ready_with_vo').format(video_output))
                    self.log_handle.flush()
                    return
            failures.append(video_output)
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self.process.kill(); self.process.wait()
            self.process = None

        raise MangaError(tr('reader.mpv_did_not_start_with_check').format(', '.join(failures), log))

    def note(self, text):
        if not self.log_handle:
            return
        try:
            self.log_handle.write('[manga-cli] {}\n'.format(str(text)))
            self.log_handle.flush()
        except (OSError, ValueError):
            pass

    def snapshot(self):
        if not self.temp:
            return {}
        try:
            stat = self.control.stat()
            marker = (stat.st_mtime_ns, stat.st_size)
            if marker != self._last_stat:
                value = json.loads(self.control.read_text(encoding='utf-8'))
                if isinstance(value, dict):
                    self._last_snapshot, self._last_stat = value, marker
        except (OSError, ValueError):
            pass
        return self._last_snapshot

    def cancelled(self):
        return self.closed or bool(self.snapshot().get('quit')) or (self.process is not None and self.process.poll() is not None)

    def send(self, name, *args):
        return ipc_command(self.ipc, ['script-message-to', 'acmanga_reader', name] + list(args))

    def waiting(self, text):
        self.send('waiting', str(text)[:200])

    def prepare(self, engine, chapter, lookahead=False):
        key = chapter_key(chapter)
        if key not in self.prepared:
            future = background_call(lambda: prepare_stream(engine, chapter, self.cache_dir,
                prefetch=self.preferences.get('prefetch_pages', 3), workers=1 if lookahead else 2))
            self.prepared[key] = future
            def finished(job):
                try:
                    stream = job.result()
                    if self.closed or key not in self.prepared:
                        stream.close()
                    elif lookahead:
                        stream.request(1)
                        stream.focus(1)
                except BaseException:
                    pass
            future.add_done_callback(finished)
        return self.prepared[key]

    def activate(self, key):
        self.active_key = key
        for other in list(self.prepared):
            if other == key:
                continue
            future = self.prepared.pop(other)
            if future.done():
                try:
                    future.result().close()
                except BaseException:
                    pass
            else:
                future.cancel()
        prune_pages(self.cache_dir, keep={key}, limit_mib=self.preferences.get('cache_mib', 512))

    def prefetch_next(self, engine):
        if self.next_chapter and self.preferences.get('prefetch_next_chapter', True):
            self.prepare(engine, self.next_chapter, lookahead=True)

    def capture(self, snapshot):
        if snapshot.get('loaded') and snapshot.get('chapter_key') and snapshot.get('page'):
            view = clean_reader_view(snapshot.get('view'))
            self.last_view = dict(view)
            self.positions[(snapshot['chapter_key'], snapshot['page'])] = view
            if len(self.positions) > 512:
                self.positions.pop(next(iter(self.positions)))

    def begin_manga(self, state, manga):
        """Choose a mode once per manga, not once per page or chapter."""
        key = manga_identity(manga) or str(manga.get('title') or '')
        if self.manga_key == key:
            return
        self.manga_key = key
        mode = self.preferences['reader_fit']
        progress = progress_for(state, manga) or {}
        if self.preferences['remember_reader_mode']:
            old_mode = progress.get('reader_mode')
            if old_mode not in ('width', 'page'):
                saved = progress.get('view')
                old_mode = saved.get('fit') if isinstance(saved, dict) else None
            if old_mode in ('width', 'page'):
                mode = old_mode
        self.last_view = {'position': 0, 'horizontal': 0, 'zoom': 0, 'fit': mode}
        self.positions.clear()

    def show(self, path, chapter, page, total, saved=None, previous=False):
        mode = self.last_view['fit']
        view = dict(self.last_view)
        view['position'], view['horizontal'] = 0, 0
        # A remembered view from another mode must not zoom a full-page view.
        if isinstance(saved, dict) and saved.get('fit') == mode:
            view = clean_reader_view(saved)
        view['fit'] = mode
        if previous and mode == 'width':
            # Going back from the top continues naturally from the bottom of the
            # preceding image, even when it has never been visited this session.
            view['position'] = 1
        payload = {'path': str(Path(path).resolve()), 'page': page, 'total': total,
                   'chapter_key': chapter_key(chapter), 'label': tr('reader.ch').format(chapter.get('number') or '?'),
                   'view': view, 'scroll_step': self.preferences['scroll_step'], 'overlap': 0.12,
                   'auto_page_turn': self.preferences['auto_page_turn'],
                   'show_page_indicator': self.preferences['show_page_indicator']}
        self.send('show-page', json.dumps(payload, ensure_ascii=False))

    def close(self):
        if self.closed:
            return
        self.closed = True
        for future in list(self.prepared.values()):
            if future.done():
                try:
                    future.result().close()
                except BaseException:
                    pass
            else:
                future.cancel()
        self.prepared.clear()
        if self.process is not None and self.process.poll() is None:
            ipc_command(self.ipc, ['quit', MANUAL_QUIT_CODE])
            try:
                self.process.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self.process.kill(); self.process.wait()
        if self.log_handle:
            self.log_handle.close()
        if self.temp:
            self.temp.cleanup()
        if self.stats['chapters']:
            from .util import atomic_json_write
            try:
                atomic_json_write(self.cache_dir / 'timings.json', self.stats)
            except OSError:
                pass
        prune_pages(self.cache_dir, keep=(), limit_mib=self.preferences.get('cache_mib', 512))


def view(engine, state_path, state, manga, chapter, cache_dir, start_page=1,
         progress_callback=None, session=None):
    if session is None:
        with ReaderSession(cache_dir, getattr(engine, 'preferences', {})) as own:
            return view(engine, state_path, state, manga, chapter, cache_dir,
                        start_page, progress_callback, session=own)
    session.begin_manga(state, manga)
    session.ensure()
    key = chapter_key(chapter)
    session.waiting(tr('reader.preparing_chapter_esc_to_exit'))
    if progress_callback:
        progress_callback(tr('reader.preparing_pages_the_reader_will_open_the_current_page_first'))
    future = session.prepare(engine, chapter)
    while not future.done():
        if session.cancelled():
            return {'page':max(1,start_page),'total':0,'completed':False,'exit_code':4}
        time.sleep(0.05)
    stream = future.result()
    session.activate(key)
    total = stream.total
    desired = total if start_page == -1 else min(total, max(1, int(start_page or 1)))
    last_page = desired
    displayed = None
    loading = None
    failed = None
    seen_seq = int(session.snapshot().get('seq') or 0)
    first = True
    want_previous = start_page == -1
    last_saved = None
    last_save_time = 0.0
    request_time = time.monotonic()
    load_started = request_time
    saved = progress_for(state, manga) or {}
    initial_view = None
    if (str(saved.get('chapter_id')) == str(chapter.get('id')) and saved.get('source') == chapter.get('source')
            and saved.get('page') == desired):
        if session.preferences['save_reader_position']:
            initial_view = saved.get('view')
    if start_page == -1:
        initial_view = session.positions.get((key, desired), initial_view)
    stream.request(desired)
    session.stats['chapters'] += 1

    def persist(snap, force=False):
        nonlocal last_saved, last_save_time, last_page
        if snap.get('chapter_key') != key or not snap.get('loaded'):
            return
        page = int(snap.get('page') or 0)
        if not 1 <= page <= total:
            return
        session.capture(snap)
        clean = clean_reader_view(snap.get('view'))
        marker = (page, json.dumps(clean, sort_keys=True))
        now = time.monotonic()
        last_page = page
        if marker != last_saved and (force or page != (last_saved or (None,))[0] or now-last_save_time >= 0.75):
            save_progress(state_path, state, manga, chapter, page, total,
                          view_state=clean if session.preferences['save_reader_position'] else None,
                          reader_mode=clean['fit'] if session.preferences['remember_reader_mode'] else None)
            last_saved, last_save_time = marker, now

    try:
        while True:
            snap = session.snapshot()
            persist(snap)
            if session.cancelled():
                persist(snap, force=True)
                return {'page':last_page,'total':total,'completed':False,'exit_code':4}
            if snap.get('chapter_key') == key and snap.get('loaded'):
                if loading == snap.get('page'):
                    displayed = loading
                    loading = None
                    first = False
                    stream.focus(displayed)
                    delay = round(time.monotonic()-request_time, 4)
                    session.stats['page_load_seconds'].append(delay)
                    if len(session.stats['page_load_seconds']) > 1000:
                        session.stats['page_load_seconds'].pop(0)
                    if session.stats['first_page_seconds'] is None:
                        session.stats['first_page_seconds'] = round(time.monotonic()-session.started,4)
                if total-int(snap['page']) <= 3:
                    session.prefetch_next(engine)
            if snap.get('chapter_key') == key and int(snap.get('seq') or 0) > seen_seq:
                seen_seq = int(snap['seq'])
                act = snap.get('action')
                page = int(snap.get('page') or desired)
                persist(snap, force=True)
                if act in ('next','previous'):
                    pending = desired != displayed or loading is not None
                    if act == 'next':
                        if page == total:
                            return {'page':page,'total':total,'completed':True,'exit_code':0}
                        desired = page+1; want_previous=False
                    else:
                        if pending and displayed is not None:
                            desired=displayed
                        elif page == 1:
                            return {'page':1,'total':total,'completed':False,'previous':True,'exit_code':0}
                        else:
                            desired=page-1
                        want_previous=True
                    loading=None; failed=None; request_time=time.monotonic()
                    stream.request(desired)
                    if desired == displayed:
                        session.send('ready','')
                    else:
                        session.waiting(tr('reader.loading_page_esc_to_exit').format(desired,total))
                elif act == 'retry':
                    stream.discard(desired) if snap.get('error') else None
                    stream.request(desired,retry=True)
                    loading=None; failed=None; request_time=time.monotonic()
                    session.waiting(tr('reader.retrying_page').format(desired))
            if snap.get('chapter_key') == key and snap.get('error') and loading is not None:
                failed=desired; loading=None
                if first:
                    raise MangaError(tr('reader.mpv_could_not_display_the_first_page_check_reader_log'))
                session.send('ready',tr('reader.invalid_image_t_retry_right_click_back_esc_exit'))
            if desired != displayed and loading is None and failed != desired:
                path, error = stream.peek(desired)
                if error:
                    if first:
                        raise MangaError(str(error))
                    failed=desired
                    session.send('ready',tr('reader.download_failed_t_retry_right_click_back_esc_exit'))
                    if progress_callback:
                        progress_callback(tr('reader.page').format(desired,error))
                elif path:
                    chosen = initial_view if first else (session.positions.get((key,desired)) if want_previous else None)
                    session.show(path,chapter,desired,total,saved=chosen,previous=want_previous)
                    loading=desired
                    load_started = time.monotonic()
            if loading is not None and time.monotonic()-load_started > 12:
                raise MangaError(tr('reader.mpv_did_not_confirm_the_page_check').format(session.cache_dir/'reader.log'))
            time.sleep(0.05)
    except KeyboardInterrupt:
        persist(session.snapshot(), force=True)
        return {'page':last_page,'total':total,'completed':False,'exit_code':4}
