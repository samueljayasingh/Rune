"""Real data for the ops dashboard UI: agents, workers, crons, system health, logs."""

import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

import psutil
from croniter import croniter

from rune.observability import metrics as metrics_module

if TYPE_CHECKING:
    from rune.core.context import SharedContext

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
