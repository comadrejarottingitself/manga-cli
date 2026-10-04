#!/bin/sh
set -eu

SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if [ -n "${SUDO_USER:-}" ] || [ "$(/usr/bin/id -u)" = 0 ]; then
    printf '%s\n' 'ERROR: run bash install.sh as your normal user, without sudo.' >&2
    exit 1
fi

# Informational/recovery options must never bootstrap system packages.
# Python is still required for these operations; invalid options fail closed.
if [ "$#" -gt 0 ]; then
    case "$1" in
        --recover|--check|--help|-h)
            exec python3 "$SOURCE_DIR/tools/install.py" "$@"
            ;;
        *)
            printf '%s\n' 'ERROR: unsupported installer argument. Use --help.' >&2
            exit 2
            ;;
    esac
fi

# Reject corrupt payloads before requesting system package changes when Python exists.
if command -v python3 >/dev/null 2>&1; then
    python3 "$SOURCE_DIR/tools/install.py" --check
fi

missing_packages=''
command -v python3 >/dev/null 2>&1 || missing_packages="$missing_packages python3"
command -v mpv >/dev/null 2>&1 || missing_packages="$missing_packages mpv"
[ -s /etc/ssl/certs/ca-certificates.crt ] || missing_packages="$missing_packages ca-certificates"

if [ -n "$missing_packages" ]; then
    if [ "${MANGA_CLI_SKIP_SYSTEM_DEPS:-0}" = '1' ]; then
        printf 'ERROR: missing system dependencies:%s\n' "$missing_packages" >&2
        exit 1
    fi

    if command -v apt-get >/dev/null 2>&1; then
        if ! command -v sudo >/dev/null 2>&1; then
            printf 'ERROR: missing system dependencies:%s\n' "$missing_packages" >&2
            printf '%s\n' 'Install them as an administrator, then run this installer again:' >&2
            printf '  apt-get update && apt-get install -y%s\n' "$missing_packages" >&2
            exit 1
        fi

        printf 'manga-cli needs:%s\n' "$missing_packages"
        printf '%s\n' 'Installing the missing Debian/Ubuntu packages with sudo...'
        sudo apt-get update
        # shellcheck disable=SC2086
        sudo apt-get install -y $missing_packages
    else
        printf 'ERROR: missing system dependencies:%s\n' "$missing_packages" >&2
        printf '%s\n' 'Automatic dependency installation is supported on apt-based systems.' >&2
        printf '%s\n' 'Install Python 3.11+ and mpv with your package manager, then run this installer again.' >&2
        exit 1
    fi
fi

exec python3 "$SOURCE_DIR/tools/install.py" "$@"
