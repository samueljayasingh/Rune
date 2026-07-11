#!/usr/bin/env bash
# Boots Rune for daily use: brings up Grafana/Prometheus in the background,
# then runs the Rune server in the foreground (Ctrl+C to stop the server;
# Grafana/Prometheus keep running independently). Assumes install.sh has
# already been run once.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

log()  { printf '\n\033[1;36m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$1"; }

# ---------- venv ----------
if [ ! -d .venv ]; then
  warn ".venv not found — run ./install.sh first."
  exit 1
fi
# shellcheck source=/dev/null
source .venv/bin/activate

# ---------- Grafana / Prometheus ----------
log "Starting Prometheus + Grafana"
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  docker compose up -d
  echo "Prometheus: http://localhost:9090"
  echo "Grafana:    http://localhost:3000"
else
  warn "Docker not found — skipping Prometheus/Grafana. Install Docker to enable them."
fi

# ---------- Ollama (needed for the local model tier) ----------
if command -v ollama >/dev/null 2>&1; then
  if ! curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
    warn "Ollama daemon isn't running — starting it in the background."
    nohup ollama serve >/tmp/rune-ollama.log 2>&1 &
    sleep 1
  fi
else
  warn "Ollama not found — the local model tier won't work until it's installed (see install.sh)."
fi

# ---------- Rune server ----------
PORT="$(python3 -c "
import yaml
d = yaml.safe_load(open('default_workspace/config.user.yaml')) or {}
print((d.get('api') or {}).get('port', 8000))
" 2>/dev/null || echo 8000)"

if lsof -i ":$PORT" >/dev/null 2>&1; then
  warn "Something is already listening on port $PORT — is 'rune server' already running?"
  exit 1
fi

log "Starting Rune server (Ctrl+C to stop)"
cd src
exec rune --workspace ../default_workspace server
