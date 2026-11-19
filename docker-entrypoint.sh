#!/bin/sh
# Runs as root to align the cleanarr user with PUID/PGID, then drops privileges.
set -e

PUID=${PUID:-1000}
PGID=${PGID:-1000}
# Keep the DB and poster cache owner-only; they hold API keys and the session secret.
umask 077

if [ "$(id -u)" = "0" ]; then
    if [ "$(id -g cleanarr)" != "$PGID" ]; then
        groupmod -o -g "$PGID" cleanarr
    fi
    if [ "$(id -u cleanarr)" != "$PUID" ]; then
        usermod -o -u "$PUID" cleanarr
    fi
    mkdir -p "${DATA_DIR:-/data}"
    chown -R cleanarr:cleanarr "${DATA_DIR:-/data}"
    chmod -R go-rwx "${DATA_DIR:-/data}" 2>/dev/null || true
    exec setpriv --reuid cleanarr --regid cleanarr --init-groups "$@"
fi

exec "$@"
