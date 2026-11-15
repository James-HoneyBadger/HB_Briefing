#!/bin/sh
# Container entry point. Every mode shares the same state directory, so a scan run through
# `docker compose run` and the web app see the same database.
set -e

# Overridable so the script can be exercised outside a container (the tests do that).
APP_DIR="${HB_APP_DIR:-/app}"
PYTHON="${HB_PYTHON:-python3}"

usage() {
    cat <<'USAGE'
HB Briefing container

  web                 serve the browser app on $PORT (default 8765)  [default]
  daemon              scan on the interval configured in settings
  scan                one scan, then exit
  cli ARGS...         any other hbnscan.py arguments, e.g. "cli --export /data/digest.md"
  dossier|action|entity|sources ARGS...   the workspace commands
  tui                 terminal interface (needs docker run -it)
  shell               a shell in the container

State lives in $HB_BRIEFING_HOME (default /data).
USAGE
}

mode="${1:-web}"
[ "$#" -gt 0 ] && shift

case "$mode" in
    web)
        port="${PORT:-8765}"
        host="${HOST:-0.0.0.0}"
        exec "$PYTHON" "$APP_DIR/hbnweb.py" --host "$host" --port "$port" \
            ${HB_WEB_TOKEN:+--token "$HB_WEB_TOKEN"} --no-browser "$@"
        ;;
    daemon) exec "$PYTHON" "$APP_DIR/hbnscan.py" --daemon "$@" ;;
    scan)   exec "$PYTHON" "$APP_DIR/hbnscan.py" "$@" ;;
    cli)    exec "$PYTHON" "$APP_DIR/hbnscan.py" "$@" ;;
    dossier|action|entity|sources)
        exec "$PYTHON" "$APP_DIR/hbnscan.py" "$mode" "$@" ;;
    tui)    exec "$PYTHON" "$APP_DIR/hbntui.py" "$@" ;;
    shell)  exec /bin/sh ;;
    -h|--help|help) usage ;;
    *)      echo "unknown mode: $mode" >&2; usage >&2; exit 2 ;;
esac
