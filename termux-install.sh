#!/data/data/com.termux/files/usr/bin/bash
set -eu

# Experimental Android installer for manga-cli v0.8.3.
# It never runs the Debian installer and never touches manga-cli user data.

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
APP_DIR="$HOME/.local/lib/manga-cli-android-v083"
LAUNCHER="$PREFIX/bin/manga-termux"
MARKER="# manga-cli android managed launcher v0.8.3"
PID="$$"

fail() {
    printf 'manga-cli Android: %s\n' "$*" >&2
    exit 1
}

case "${PREFIX:-}" in
    /data/data/com.termux/*) ;;
    *) fail "this installer is only for native Termux" ;;
esac

[ -f "$ROOT/VERSION" ] || fail "VERSION not found"
[ "$(tr -d '\r\n' < "$ROOT/VERSION")" = "0.8.3" ] || fail "this profile must be applied to manga-cli 0.8.3"
[ -f "$ROOT/acmanga/reader_android.lua" ] || fail "reader_android.lua not found; apply the Android branch patch first"

for command in python mpv termux-x11 pgrep; do
    command -v "$command" >/dev/null 2>&1 || fail "$command is missing"
done

python - <<'PY' || fail "Python 3.8 or newer is required"
import sys
raise SystemExit(0 if sys.version_info >= (3, 8) else 1)
PY

mkdir -p "$(dirname "$APP_DIR")" "$PREFIX/bin"

# Refuse unknown launchers before replacing any application code.
OLD_LAUNCHER_KIND="none"
if [ -e "$LAUNCHER" ]; then
    if grep -Fq "$MARKER" "$LAUNCHER" 2>/dev/null; then
        OLD_LAUNCHER_KIND="managed"
    elif grep -Fq '[manga-termux]' "$LAUNCHER" 2>/dev/null && grep -Fq 'termux-x11' "$LAUNCHER" 2>/dev/null; then
        OLD_LAUNCHER_KIND="prototype"
    else
        fail "$LAUNCHER already exists and is not managed by this Android profile"
    fi
fi

APP_STAGE="${APP_DIR}.new.${PID}"
APP_OLD="${APP_DIR}.old.${PID}"
LAUNCHER_STAGE="${LAUNCHER}.new.${PID}"
LAUNCHER_OLD="${LAUNCHER}.old.${PID}"
rm -rf "$APP_STAGE" "$APP_OLD"
rm -f "$LAUNCHER_STAGE" "$LAUNCHER_OLD"

cleanup_stage() {
    rm -rf "$APP_STAGE" 2>/dev/null || true
    rm -f "$LAUNCHER_STAGE" 2>/dev/null || true
}
trap cleanup_stage EXIT HUP INT TERM

mkdir -p "$APP_STAGE"
cp "$ROOT/manga.py" "$ROOT/VERSION" "$APP_STAGE/"
cp -R "$ROOT/acmanga" "$APP_STAGE/acmanga"
find "$APP_STAGE" -type d -name '__pycache__' -prune -exec rm -rf {} +
find "$APP_STAGE" -type f -name '*.pyc' -delete

cat > "$LAUNCHER_STAGE" <<'LAUNCHER_EOF'
#!/data/data/com.termux/files/usr/bin/bash
# manga-cli android managed launcher v0.8.3
set -eu
DISPLAY_NUM=":1"
APP_DIR="$HOME/.local/lib/manga-cli-android-v083"

if [ ! -f "$APP_DIR/manga.py" ]; then
    echo "[manga-termux] ERROR: Android manga-cli files are not installed." >&2
    exit 1
fi

if ! pgrep -f "termux-x11 $DISPLAY_NUM" >/dev/null 2>&1; then
    termux-x11 "$DISPLAY_NUM" >/dev/null 2>&1 &
    sleep 1
fi
export DISPLAY="$DISPLAY_NUM"
cd "$APP_DIR"
exec python manga.py "$@"
LAUNCHER_EOF
chmod 0755 "$LAUNCHER_STAGE"

# Prepare rollback copies only after every staged file is ready.
if [ -d "$APP_DIR" ]; then
    mv "$APP_DIR" "$APP_OLD"
fi
if [ -e "$LAUNCHER" ]; then
    cp "$LAUNCHER" "$LAUNCHER_OLD"
    if [ "$OLD_LAUNCHER_KIND" = "prototype" ]; then
        cp "$LAUNCHER" "${LAUNCHER}.pre-android-v083"
    fi
fi

rollback() {
    rm -rf "$APP_DIR" 2>/dev/null || true
    if [ -d "$APP_OLD" ]; then mv "$APP_OLD" "$APP_DIR"; fi
    rm -f "$LAUNCHER" 2>/dev/null || true
    if [ -f "$LAUNCHER_OLD" ]; then mv "$LAUNCHER_OLD" "$LAUNCHER"; fi
}

if ! mv "$APP_STAGE" "$APP_DIR"; then
    rollback
    fail "could not install application files"
fi
if ! mv "$LAUNCHER_STAGE" "$LAUNCHER"; then
    rollback
    fail "could not install launcher"
fi

rm -rf "$APP_OLD"
rm -f "$LAUNCHER_OLD"
trap - EXIT HUP INT TERM
cleanup_stage

printf 'Installed manga-cli 0.8.3 Android profile.\n'
printf 'Run: manga-termux\n'
printf 'User state/cache were not modified.\n'
