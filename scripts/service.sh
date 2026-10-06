#!/usr/bin/env bash
# ==============================================================================
#  Tadagram Worker Portal Linux - Service Management Utility
# ==============================================================================
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR"

cmd="${1:-status}"

case "$cmd" in
  start)
    echo "Starting Tadagram Worker Portal stack..."
    docker compose up -d
    ;;
  stop)
    echo "Stopping Tadagram Worker Portal stack..."
    docker compose down
    ;;
  restart)
    echo "Restarting Tadagram Worker Portal stack..."
    docker compose restart
    ;;
  status)
    echo "=== Tadagram Worker Portal Status ==="
    docker compose ps
    echo ""
    echo "=== Installed Workers in $DIR/data/workers ==="
    ls -la "$DIR/data/workers" 2>/dev/null || true
    ;;
  logs)
    target="${2:-}"
    if [ -n "$target" ]; then
      docker compose logs -f "$target"
    else
      docker compose logs -f
    fi
    ;;
  update)
    echo "Triggering immediate Worker update scan..."
    docker compose restart worker-updater
    docker compose logs -f worker-updater
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|status|logs [service]|update}"
    exit 1
    ;;
esac
