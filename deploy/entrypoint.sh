#!/bin/sh
# Starts as root only to make a freshly mounted /data writable, then drops to an
# unprivileged user for the app itself.
set -e
if [ "$(id -u)" = "0" ]; then
  chown -R blackline:blackline /data 2>/dev/null || true
  exec setpriv --reuid=blackline --regid=blackline --init-groups "$@"
fi
exec "$@"
