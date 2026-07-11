"""Real data for the ops dashboard UI: agents, workers, crons, system health, logs."""

import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

import psutil
from croniter import croniter
from dotenv import find_dotenv, set_key

from rune.observability import metrics as metrics_module

if TYPE_CHECKING:
    from rune.core.context import SharedContext

# Only these env vars are editable from the Settings UI — a fixed whitelist,
# not an arbitrary-key write, since this writes to a real file on disk.
EDITABLE_ENV_VARS = [
    "FIREWORKS_API_KEY",
    "FIREWORKS_BASE_URL",
    "OPENAI_API_KEY",
    "FIRECRAWL_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "DISCORD_BOT_TOKEN",
]

# Tiers/roles are config-defined, not hardcoded — but writes must stay inside
# this set of fields so a settings write can't touch arbitrary config keys.
EDITABLE_TIER_FIELDS = ["provider", "model", "api_base", "supports_tools"]


def _mask(value: str) -> str:
    if len(value) <= 4:
        return "•" * len(value)
    return "•" * (len(value) - 4) + value[-4:]

LOG_LINE_RE = re.compile(
    r"^(?P<time>\S+ \S+) - (?P<source>\S+) - (?P<level>\S+) - (?P<message>.*)$"
)


def _fmt_uptime(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _fmt_countdown(seconds: float) -> str:
    if seconds < 0:
        return "now"
    return _fmt_uptime(seconds)


def _messages_for(context: "SharedContext", session_id: str) -> list[dict]:
    messages = context.history_store.get_messages(session_id)
    return [
        {"role": m.role, "content": m.content}
        for m in messages
        if m.role in ("user", "assistant") and m.content.strip()
    ]


def chat_history(context: "SharedContext", source: str) -> list[dict]:
    """Persisted messages for the given source's active session, for the
    chat UI to load on open. Read-only: does not create a session if none
    exists yet."""
    from rune.core.events import WebSocketEventSource

    source_str = str(WebSocketEventSource(user_id=source))
    binding = context.config.sources.get(source_str)
    if not binding:
        return []

    return _messages_for(context, binding.session_id)


def _rebind_source(context: "SharedContext", source_str: str, session_id: str) -> None:
    """Point source_str at session_id, both on disk and in the live config
    object other requests read from (set_runtime alone only writes to disk)."""
    from rune.utils.config import SourceSessionConfig

    context.config.set_runtime(
        f"sources.{source_str}", SourceSessionConfig(session_id=session_id)
    )
    context.config.sources[source_str] = SourceSessionConfig(session_id=session_id)


def new_chat_session(context: "SharedContext", source: str) -> str:
    """Start a fresh session for this source; the old one stays on disk,
    just no longer the one new messages append to."""
    from rune.core.agent import Agent
    from rune.core.events import WebSocketEventSource

    ws_source = WebSocketEventSource(user_id=source)
    agent_def = context.agent_loader.load(context.config.default_agent)

    session = Agent(agent_def, context).new_session(ws_source)
    _rebind_source(context, str(ws_source), session.state.session_id)
    return session.state.session_id


def list_chat_sessions(context: "SharedContext", source: str) -> list[dict]:
    """Past sessions for this source with at least one message, most recent
    first. Sessions with zero messages (e.g. "New chat" clicked but never
    used) are omitted — they're just clutter, not real history."""
    from rune.core.events import WebSocketEventSource

    source_str = str(WebSocketEventSource(user_id=source))
    return [
        {
            "id": s.id,
            "preview": s.title or "(untitled)",
            "updated_at": s.updated_at,
            "message_count": s.message_count,
        }
        for s in context.history_store.list_sessions()
        if s.source == source_str and s.message_count > 0
    ]


def delete_chat_session(context: "SharedContext", source: str, session_id: str) -> None:
    """Delete a past session's history. If it's the active session for this
    source, also start a fresh one so the source isn't left pointing at a
    session that no longer exists."""
    from rune.core.events import WebSocketEventSource

    source_str = str(WebSocketEventSource(user_id=source))
    context.history_store.delete_session(session_id)

    binding = context.config.sources.get(source_str)
    if binding and binding.session_id == session_id:
        new_chat_session(context, source)


def resume_chat_session(context: "SharedContext", source: str, session_id: str) -> list[dict]:
    """Make session_id the active one for this source and return its messages."""
    from rune.core.events import WebSocketEventSource

    _rebind_source(context, str(WebSocketEventSource(user_id=source)), session_id)
    return _messages_for(context, session_id)


def workers_snapshot(context: "SharedContext") -> list[dict]:
    now = time.time()
    out = []
    for worker in context.workers:
        if worker.has_crashed():
            status = "ERROR"
        elif worker.is_running():
            status = "RUNNING"
        else:
            status = "IDLE"
        uptime = _fmt_uptime(now - worker.started_at) if worker.started_at else "00:00:00"
        out.append(
            {
                "name": worker.__class__.__name__,
                "status": status,
                "uptime": uptime,
            }
        )
    return out


def agents_snapshot(context: "SharedContext") -> list[dict]:
    out = []
    for agent_def in context.agent_loader.discover_agents():
        out.append(
            {
                "id": agent_def.id,
                "name": agent_def.name,
                "description": agent_def.description,
                "model": agent_def.llm.litellm_model,
                "allow_skills": agent_def.allow_skills,
            }
        )
    return out


def skills_snapshot(context: "SharedContext") -> list[dict]:
    return [
        {"id": s.id, "name": s.name, "description": s.description}
        for s in context.skill_loader.discover_skills()
    ]


def crons_snapshot(context: "SharedContext") -> list[dict]:
    now = time.time()
    out = []
    for cron in context.cron_loader.discover_crons():
        try:
            next_run = croniter(cron.schedule, time.localtime(now)).get_next(float)
            in_seconds = next_run - now
            next_in = _fmt_countdown(in_seconds)
        except Exception:
            next_in = "n/a"
        out.append(
            {
                "id": cron.id,
                "name": cron.name,
                "description": cron.description,
                "agent": cron.agent,
                "schedule": cron.schedule,
                "next_in": next_in,
            }
        )
    return out


def memories_snapshot(context: "SharedContext") -> dict:
    memories_path = Path(context.config.memories_path)
    axes = {}
    for axis in ("topics", "projects", "daily-notes"):
        axis_path = memories_path / axis
        files = sorted(axis_path.glob("*.md")) if axis_path.exists() else []
        axes[axis] = {
            "count": len(files),
            "recent": [f.name for f in files[-5:]],
        }
    return axes


def system_health(latency_samples: list[float]) -> dict:
    vm = psutil.virtual_memory()
    avg_latency_ms = (
        round(sum(latency_samples) / len(latency_samples) * 1000, 1)
        if latency_samples
        else 0
    )
    return {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "memory_used_gb": round((vm.total - vm.available) / (1024**3), 1),
        "memory_total_gb": round(vm.total / (1024**3), 1),
        "api_latency_ms": avg_latency_ms,
        "api_latency_samples": [round(s * 1000, 1) for s in latency_samples[-10:]],
    }


def tail_logs(context: "SharedContext", n: int = 60) -> list[dict]:
    log_path = Path(context.config.logging_path) / "rune.log"
    if not log_path.exists():
        return []
    with open(log_path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        chunk = min(size, 200_000)
        f.seek(size - chunk)
        text = f.read().decode("utf-8", errors="replace")
    lines = [ln for ln in text.splitlines() if ln.strip()][-n:]

    parsed = []
    for line in lines:
        m = LOG_LINE_RE.match(line)
        if m:
            parsed.append(m.groupdict())
        else:
            parsed.append({"time": "", "source": "", "level": "", "message": line})
    return parsed


def nodes_snapshot(context: "SharedContext") -> dict:
    model_routing = context.config.model_routing
    tiers = {
        name: {
            "provider": tier.provider,
            "model": tier.model,
            "supports_tools": tier.supports_tools,
            "billed": tier.provider == "fireworks_ai",
        }
        for name, tier in model_routing.tiers.items()
    }
    return {"enabled": model_routing.enabled, "tiers": tiers}


def balancer_snapshot(context: "SharedContext") -> dict:
    model_routing = context.config.model_routing
    roles = {
        name: {
            "default": policy.default,
            "escalate_to": policy.escalate_to,
            "classify": policy.classify,
            "categories": policy.categories,
        }
        for name, policy in model_routing.roles.items()
    }
    return {"roles": roles, "live": metrics_module.summary()}


def settings_snapshot(context: "SharedContext") -> dict:
    """Editable settings: env secrets (masked) and model tier config."""
    env = [
        {"key": key, "set": bool(os.environ.get(key)), "masked": _mask(os.environ[key])}
        if os.environ.get(key)
        else {"key": key, "set": False, "masked": ""}
        for key in EDITABLE_ENV_VARS
    ]

    llm = context.config.llm
    tiers = {
        name: {
            "provider": tier.provider,
            "model": tier.model,
            "api_base": tier.api_base,
            "supports_tools": tier.supports_tools,
        }
        for name, tier in context.config.model_routing.tiers.items()
    }

    return {
        "env": env,
        "llm": {
            "provider": llm.provider,
            "model": llm.model,
            "api_base": llm.api_base,
            "temperature": llm.temperature,
            "max_tokens": llm.max_tokens,
        },
        "model_routing_enabled": context.config.model_routing.enabled,
        "tiers": tiers,
    }


def update_env_var(key: str, value: str) -> None:
    """Write one whitelisted env var to .env and make it live immediately
    (config_routing already reads Fireworks creds fresh via os.environ on
    every call, so this takes effect without a server restart)."""
    if key not in EDITABLE_ENV_VARS:
        raise ValueError(f"'{key}' is not an editable setting")

    env_path = find_dotenv(usecwd=True) or ".env"
    set_key(env_path, key, value)
    os.environ[key] = value


def update_model_tier(context: "SharedContext", tier_name: str, fields: dict) -> None:
    """Update one model tier's config, persisted and applied live."""
    tier = context.config.model_routing.tiers.get(tier_name)
    if tier is None:
        raise ValueError(f"Unknown tier: {tier_name}")

    for field, value in fields.items():
        if field not in EDITABLE_TIER_FIELDS:
            raise ValueError(f"'{field}' is not an editable tier field")
        setattr(tier, field, value)
        context.config.set_user(f"model_routing.tiers.{tier_name}.{field}", value)
