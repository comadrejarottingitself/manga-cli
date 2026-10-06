#!/data/data/com.termux/files/usr/bin/bash
set -eu
APP_DIR="$HOME/.local/lib/manga-cli-android-v083"
LAUNCHER="$PREFIX/bin/manga-termux"
MARKER="# manga-cli android managed launcher v0.8.3"

rm -rf "$APP_DIR"
if [ -f "$LAUNCHER" ] && grep -Fq "$MARKER" "$LAUNCHER"; then
    rm -f "$LAUNCHER"
fi
printf 'Removed manga-cli Android application files.\n'
printf 'Saved manga, history, settings and cache were left untouched.\n'
