# Rune AgentOS: Complete System Context for LLMs

**Purpose of this Document:** 
This document is specifically designed to be read by Large Language Models (LLMs) or AI coding assistants analyzing the Rune codebase. It provides the "why" behind the code—explaining architectural decisions, component interactions, setup nuances, and known pitfalls so that future AI tools can write idiomatic code for this system without breaking established patterns.

Every pitfall below was learned the hard way — through a live bug, a wrong first fix, or an explicit user correction — so treat each one as a constraint, not a suggestion.

---

## 1. System Overview & Philosophy

Rune is not a simple chatbot; it is an **AgentOS**—a persistent, event-driven runtime environment for autonomous AI agents. It is designed to run efficiently on "minimal devices" (like a local PC or small server) using a local-first philosophy.

**Core Philosophies:**
1. **Cost & Privacy Optimization (Multi-Model Routing):** The system defaults to a local model (Gemma 4 via Ollama) for routine tasks. It only escalates to expensive cloud models (Fireworks AI, DeepSeek, etc.) when the task requires complex reasoning or coding.
2. **Event-Driven & Crash Resilient:** Components do not call each other directly. They communicate asynchronously via an `EventBus`. If a component crashes, the message is safely stored, the worker restarts, and the system continues without data loss.
3. **Progressive Disclosure (Skills):** The LLM context window is precious. Agents do not load every tool upfront. Instead, they load "Skills" on demand.

---

## 2. Core Architectural Components

### A. The Event Bus (`src/rune/core/eventbus.py`)
- **What it is:** An async Pub/Sub system using `asyncio.Queue` combined with an atomic JSONL disk-backed persistence layer.
- **Reason:** Decoupling and Reliability. A Telegram message, a WebSocket message, and a scheduled Cron task all generate the exact same `InboundEvent`. The `AgentWorker` doesn't care where the message came from. Outbound events are synced to disk before delivery.
- **Event Types:**
  - `InboundEvent`: User or trigger input into the system.
  - `OutboundEvent`: Final agent response destined for the user.
  - `DispatchEvent`: An agent delegating a sub-task to another agent (e.g., Rune asking Ledger to save a memory).
  - `DispatchResultEvent`: The result of that sub-task returning to the delegating agent.

### B. The Worker System (`src/rune/server/`)
- **What it is:** Independent infinite-loop asyncio tasks managed by `Server`.
- **Reason:** Fault isolation. If the `ChannelWorker` (Telegram bot) crashes due to a network conflict, it shouldn't crash the `AgentWorker` (which might be in the middle of a long reasoning task). `Server` monitors workers and restarts them automatically (`Server._monitor_workers`).
- **Workers:**
  - `AgentWorker`: Listens for `InboundEvent` and `DispatchEvent`, manages concurrency (`asyncio.Semaphore` per agent), executes the LLM loop (`AgentSession.chat`), and publishes output events.
  - `DeliveryWorker`: Listens for `OutboundEvent` and routes it to the correct platform using the `EventSource` metadata.
  - `WebSocketWorker`: Manages the real-time UI dashboard connection, broadcasts events, and ingests UI messages.
  - `CronWorker`: Evaluates `croniter` schedules from `CRON.md` definitions and emits `InboundEvent`s autonomously.
  - `ChannelWorker`: Long-polling loops for Discord/Telegram.

### C. Context Guard (`src/rune/core/context_guard.py`)
- **What it is:** A proactive token manager that prevents context overflow.
- **Reason:** Infinite chats will inevitably hit context limits. 
- **Mechanics:** 
  1. If a tool output is massive (>10k chars), it hard-truncates it.
  2. If the total session tokens exceed a safety threshold, it pauses generation, invokes a fast LLM pass to summarize the history, "rolls" to a new `session_id`, and injects the summary as the new system prompt so memory isn't lost.

### D. Multi-Model Router (`src/rune/provider/llm/router.py` & `base.py`)
- **What it is:** An upfront intent classifier that dynamically selects the LLM provider.
- **Reason:** Prevents wasting $0.05 on a cloud reasoning model when the user just said "hello".
- **Mechanics:** 
  - Every incoming user request passes through a fast classifier prompt (`classifier_tier`).
  - The classifier assigns it a category: `daily`, `coding`, or `reasoning`.
  - `LLMProvider` dynamically instantiates the correct LiteLLM client for that tier.
  - **Fallback/Escalation:** If a chosen tier's call raises an exception (e.g., Ollama is down), `LLMProvider` catches it and retries with the tier's `escalate_to` (e.g. `daily` escalates to `reasoning`).
- **Critical: tool schemas are never sent to `gemma4:e2b-it-qat`:** the local model does not reliably support tool-calling — it hangs for minutes or hallucinates calls to tools that don't exist when given a tools list. This is NOT handled by `supports_tools` filtering a tier out of the candidate list (an earlier, wrong design). Instead, `ModelTierConfig.supports_tools` controls a separate `attach_tools` flag computed per-candidate in `router.py`'s `resolve_tiers()`: a tier can still be *chosen* by classification even if it can't do tools, it just never receives the `tools` param in the actual `acompletion()` call (see `base.py`'s `chat()`). The model then answers in plain text instead of hanging/hallucinating — a degraded-but-correct answer beats a broken one. **Do not "fix" this by excluding `daily` from tool-needing roles** — the classifier already routes anything needing a real tool/skill/memory-write/subagent-dispatch to `reasoning` (see the `CLASSIFY_PROMPT` examples in `router.py` — this list was hard-won through live bug reports, e.g. "ledger save this as a note" was originally misrouted to `daily` and just printed fake tool-call syntax instead of running it).

---

## 3. The Entity System (Agents, Skills, Crons)

Instead of hardcoding features in Python, Rune uses YAML-frontmatter Markdown files for definitions. This allows users to hot-add capabilities without rebooting.

### A. Agents (`workspace/agents/<id>/AGENT.md`)
- **Structure:** `AGENT.md` contains YAML metadata (LLM overrides, max concurrency) and Markdown body instructions. Optionally accompanied by `SOUL.md` (personality/tone).
- **Core Pattern:** The `ledger` agent manages memory autonomously, while the `rune` agent handles general chatting. They collaborate using the `subagent_dispatch` tool.
- **Subagent Flow:** `Rune -> Tool Call -> DispatchEvent -> EventBus -> AgentWorker -> Ledger -> Tool Call (Write File) -> DispatchResultEvent -> Rune`.

### B. Skills (`workspace/skills/<id>/SKILL.md`)
- **Structure:** Progressive disclosure. The YAML frontmatter contains a short `description` (~100 words). This is ALWAYS injected into the LLM context.
- **Execution:** When the LLM calls the `use_skill` tool, Rune dynamically loads the Markdown body of `SKILL.md` into the prompt, giving the LLM detailed, step-by-step instructions (e.g., API schemas) only when needed.

### C. Crons (`workspace/crons/<id>/CRON.md`)
- **Structure:** Contains `schedule` (cron string), `agent` (who executes it), and `one_off` flags. The markdown body is the prompt.
- **Execution:** When time matches, `CronWorker` creates an `InboundEvent` with the markdown body as the message content. The assigned agent wakes up, reads the prompt, and typically uses the `post_message` tool to deliver the result to the user asynchronously.

---

## 4. Persistent Memory Axes

State is isolated in `/workspace/memories`. The `ledger` agent manages this structure:
1. **`topics/`**: Timeless facts, user preferences, identity data. (e.g., `user_prefers_python.md`).
2. **`projects/`**: Contextual context for ongoing work (e.g., `agentos_migration.md`). Contains goals, decisions, constraints.
3. **`daily-notes/`**: Temporal logging (e.g., `2026-07-11.md`). Ledger summarizes daily events into these files so the agent remembers what happened "yesterday".

---

## 5. The Dockerized Infrastructure

Rune is deployed via a multi-container `docker-compose.yml` stack.

**Containers:**
1. `rune`: The Python FastAPI/Worker runtime.
2. `prometheus`: Scrapes token usage metrics from Rune's `/metrics` endpoint.
3. `grafana`: Displays the metrics visually (e.g., "Tokens Saved vs Cloud Cost").
4. `ollama`: Runs local models (Gemma 4) with hardware acceleration (e.g., ROCm for AMD GPUs) entirely within the Docker network.

### Critical Docker Nuances:
- **Ollama Networking:** Do NOT configure Rune to look for Ollama at `http://localhost:11434`. Inside the Rune container, `localhost` is the container itself. Because Ollama is running in a sibling Docker container, Rune must connect to it via `http://ollama:11434` (using Docker Compose's internal DNS).
- **Build Caching Trick (`Dockerfile`):** The `Dockerfile` intentionally creates a dummy `src/rune/__init__.py` and runs `pip install -e .` *before* copying the real `src/` directory. 
  - *Reason:* If `COPY src/` happened first, changing a single line of Python code would bust the Docker layer cache, forcing a slow rebuild every time `boot.sh` is run. By installing against a dummy directory first, the heavy dependency download is cached permanently.
- **Live Source Mounts:** For development, `src/` is volume-mounted into the `rune` container at runtime. The developer doesn't need to rebuild the container for Python code changes; they just restart it (`docker compose restart rune`).

---

## 6. End-to-End Setup Instructions

1. **Environment Preparation:**
   ```bash
   cp default_workspace/config.example.yaml default_workspace/config.user.yaml
   cp .env.example .env
   # Add FIREWORKS_API_KEY to .env
   ```

2. **Stop Conflicting Host Services:**
   Ensure no host-level Ollama service is running, as it will fight for port 11434 and GPU VRAM.
   ```bash
   sudo systemctl stop ollama
   sudo systemctl disable ollama
   ```

3. **Boot the Stack:**
   ```bash
   bash boot.sh
   ```

4. **Pull Local Models (One-time):**
   Because Ollama is containerized with a fresh isolated volume (`./ollama_data`), models must be pulled inside the container on the very first run:
   ```bash
   docker compose exec ollama ollama pull gemma4:e2b-it-qat
   ```

5. **Access:**
   - Web Dashboard / UI: `http://localhost:8000`
   - Grafana Metrics: `http://localhost:3000`
   - Prometheus: `http://localhost:9090`

### Note: `install.sh` vs `boot.sh` are two different deployment models
`install.sh` predates full dockerization — it sets up a **native** Python venv and a **native** Ollama install/pull on the host. `boot.sh` now runs the **entire stack in Docker**, including its own `ollama` container with a separate model volume (`./ollama_data`). These are not unified: running `install.sh` then `boot.sh` means you have Ollama installed twice (host + container), and the host copy is never used by the dockerized `rune` container (it talks to the `ollama` service via Docker DNS, not `localhost`). If asked to "fix the install flow," clarify with the user whether they want the native or the Docker path before touching either script — don't assume install.sh is dead code without asking.

---

## 7. Web API surface (`src/rune/server/app.py`, logic in `dashboard.py`)

Beyond `/ws` (chat) and `/metrics` (Prometheus scrape), the dashboard UI is backed by:

- **Chat/session management** — the CLI and the web dashboard are deliberately **separate conversations**, not shared: `rune chat` uses a fixed source `cli-user`, the web UI uses a fixed source `web-user` (see `static/index.html`'s `clientId` and `cli/chat.py`'s `SOURCE_ID`). Do not unify these without being asked — a past attempt to give them the same source was explicitly reverted by the user.
  - `GET /api/history` — messages for the web UI's current active session.
  - `POST /api/chat/new` — starts a fresh session for `web-user`; the old one stays on disk, just un-bound.
  - `GET /api/chat/sessions` — past sessions with ≥1 message (zero-message "New chat, never used" sessions are filtered out — otherwise repeated clicks clutter the list).
  - `POST /api/chat/resume` / `POST /api/chat/delete` — switch the active session / remove one (auto-starts a fresh session if the deleted one was active).
- **Live settings editor** — `GET /api/settings`, `POST /api/settings/env`, `POST /api/settings/tier` in `dashboard.py`. Both writes are **whitelisted** (`EDITABLE_ENV_VARS`, `EDITABLE_TIER_FIELDS`), never accept arbitrary keys. Env values are never echoed back raw — only a masked last-4-chars preview. Both take effect **immediately, no restart** (env var writes update `os.environ` directly; tier writes mutate the live `context.config.model_routing.tiers[...]` object, not just the YAML file).
- **`GET /api/dashboard`** — one call returning everything else the UI needs (workers, agents, skills, crons, memories, system health, logs, nodes, balancer).

### Note: `Config.set_runtime()` only writes the YAML file, not the in-memory object
The config-reload file watcher only watches `config.user.yaml`, not `config.runtime.yaml`. Anything that calls `set_runtime()` to persist a session binding or setting **must also mutate the corresponding attribute on the live `context.config` object in the same call**, or other concurrent requests will keep reading stale data until the next full reload. This bit us twice already (`routing.py`'s `get_or_create_session_id`, and the settings/session helpers in `dashboard.py`) — both are now fixed by mutating both places, but any *new* code that persists config must do the same.

---

## 8. CI (`.github/workflows/ci.yml`, config in `pyproject.toml`)

Pylint runs on every push/PR to `main`, gated on **errors/fatal only** (`disable = ["all"]`, `enable = ["E", "F"]` under `[tool.pylint."messages control"]`). This codebase has ~50 pre-existing style warnings (broad-except, missing file encodings, etc.) inherited from its tutorial origins — gating on those would make every push red for unrelated reasons. Two static-analysis false positives (pylint can't see dataclass `__dataclass_fields__` or pydantic's `model_fields` as real attributes) are suppressed with inline `# pylint: disable=...` comments at the exact lines, not a blanket rule disable. Run locally with `pylint src/rune` before assuming CI will pass.

---

## 9. Development Guidelines for Future LLMs

When modifying this codebase, you must adhere to these rules:

1. **Never block the event loop:** Do not use `time.sleep`, `requests`, or synchronous file I/O in the core `src/rune/server` or `core` logic. Always use `asyncio`, `httpx`, and `aiofiles`.
2. **Respect the EventBus Pattern:** Workers should never invoke each other's methods directly. If the `CronWorker` wants an agent to do something, it must publish an `InboundEvent`. 
3. **Telemetry & Observability:** Every LLM call must be routed through `LLMProvider.chat` in `provider/llm/base.py`. Do not call `litellm.acompletion` directly in random files, as this bypasses the model routing logic and breaks Prometheus token accounting (`rune_llm_calls_total`).
4. **Config Hot Reloading:** Changes to `config.user.yaml` are handled instantly by `ConfigReloader`. Do not read `config.user.yaml` from disk manually. Access the in-memory `context.config` object.
5. **UI Simplicity:** The dashboard (`src/rune/server/static/index.html`) is a single, zero-build HTML file using Vanilla JS/CSS. Do not introduce React, Webpack, or NPM dependencies. Maintain the minimal footprint.
