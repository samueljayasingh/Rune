#!/usr/bin/env bash
# Boots Rune for daily use: brings up all services (Grafana, Prometheus, Rune)
# via Docker Compose. Ctrl+C will stop the Rune server logs; the stack keeps
# running in the background. Use `docker compose down` to stop everything.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

log()  { printf '\n\033[1;36m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$1"; }

# ---------- Pre-flight checks ----------
if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  warn "Docker (with the 'compose' plugin) is required to run Rune."
  warn "Install Docker from https://docs.docker.com/get-docker/ then re-run."
  exit 1
fi

if [ ! -f .env ]; then
  warn ".env not found — copying from .env.example. Edit it with your API keys before continuing."
  cp .env.example .env
fi

if [ ! -f default_workspace/config.user.yaml ]; then
  warn "No config.user.yaml found — copying from example. Edit it to set your port/model/keys."
  cp default_workspace/config.example.yaml default_workspace/config.user.yaml
fi

# ---------- Build & start ----------
log "Building Rune image (skipped if up-to-date)"
docker compose build rune

log "Starting all services (Rune + Prometheus + Grafana)"
docker compose up -d

RUNE_PORT="${RUNE_PORT:-8000}"
echo ""
echo "  Rune dashboard: http://localhost:${RUNE_PORT}"
echo "  Prometheus:     http://localhost:9090"
echo "  Grafana:        http://localhost:3000"
echo ""
echo "Tailing Rune logs... (Ctrl+C exits tail — services keep running)"
echo "Run 'docker compose down' to stop everything."
echo ""

docker compose logs -f rune
