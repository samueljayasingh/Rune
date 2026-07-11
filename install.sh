#!/usr/bin/env bash
# Clean-install script for Rune. Idempotent: safe to re-run.
# Sets up: Python venv + deps, .env, Ollama + the local Gemma model,
# and the Prometheus/Grafana observability stack (via Docker).

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

GEMMA_MODEL="gemma4:e2b-it-qat"

log()  { printf '\n\033[1;36m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$1"; }

# ---------- 1. Python environment ----------
log "Setting up Python environment"
if command -v uv >/dev/null 2>&1; then
  if [ -f .venv/bin/activate ]; then
    echo ".venv already exists and is valid, reusing it."
  else
    rm -rf .venv
    uv venv .venv
  fi
  # shellcheck source=/dev/null
  source .venv/bin/activate
  uv pip install -e ".[dev]"
else
  warn "uv not found, falling back to python -m venv + pip"
  if [ -f .venv/bin/activate ]; then
    echo ".venv already exists and is valid, reusing it."
  else
    rm -rf .venv
    if ! python3 -m venv .venv 2>/dev/null; then
      warn "python3-venv missing, attempting to install it..."
      if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update && sudo apt-get install -y python3-venv python3.12-venv || true
        python3 -m venv .venv
      else
        warn "Could not install python3-venv automatically. Please install it manually."
        exit 1
      fi
    fi
  fi
  # shellcheck source=/dev/null
  source .venv/bin/activate
  pip install -e ".[dev]"
fi

# ---------- 2. .env ----------
log "Setting up .env"
if [ ! -f .env ]; then
  cp .env.example .env
  warn ".env created from .env.example — fill in your real API keys before running the server."
else
  echo ".env already exists, leaving it as-is."
fi

# ---------- 3. Ollama + local Gemma model ----------
log "Setting up Ollama"
if ! command -v ollama >/dev/null 2>&1; then
  echo "Ollama not found, installing (Linux/macOS only)..."
  if [[ "$OSTYPE" == "linux-gnu"* || "$OSTYPE" == "darwin"* ]]; then
    curl -fsSL https://ollama.com/install.sh | sh
  else
    warn "Unsupported OS for automatic Ollama install ($OSTYPE)."
    warn "Install Ollama manually from https://ollama.com/download, then re-run this script."
  fi
else
  echo "Ollama already installed ($(ollama --version 2>&1 | head -n1))."
fi

if command -v ollama >/dev/null 2>&1; then
  if ! curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
    echo "Ollama daemon not running — start it with 'ollama serve' (or your OS service) before continuing."
  fi

  log "Pulling local model: $GEMMA_MODEL"
  if ollama list 2>/dev/null | grep -q "$GEMMA_MODEL"; then
    echo "$GEMMA_MODEL already pulled."
  else
    ollama pull "$GEMMA_MODEL" || warn "Could not pull $GEMMA_MODEL — is 'ollama serve' running?"
  fi
fi

# ---------- 4. Grafana / Prometheus ----------
log "Setting up observability stack (Prometheus + Grafana)"
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  docker compose up -d
  echo "Prometheus: http://localhost:9090"
  echo "Grafana:    http://localhost:3000  (anonymous admin access)"
else
  warn "Docker (with the 'compose' plugin) not found — skipping Prometheus/Grafana."
  warn "Install Docker, then run: docker compose up -d"
fi

# ---------- Done ----------
log "Install complete"
cat <<'EOF'

Next steps:
  1. Edit .env with your real API keys (Fireworks, Firecrawl, Telegram/Discord tokens).
  2. Start the server:
       source .venv/bin/activate
       cd src && rune server
  3. Open the dashboard: http://localhost:8000

EOF
