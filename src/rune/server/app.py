"""FastAPI application with WebSocket support."""

import time
from collections import deque
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request, Response, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from rune.core.context import SharedContext
from rune.observability import metrics as metrics_module
from rune.server import dashboard

STATIC_DIR = Path(__file__).parent / "static"


def create_app(context: SharedContext) -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Rune WebSocket Server",
        description="WebSocket server for real-time agent communication",
        version="0.1.0",
    )
    app.state.context = context
    latency_samples: deque[float] = deque(maxlen=50)

    # Enable CORS for web clients
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def track_latency(request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        latency_samples.append(time.perf_counter() - start)
        return response

    @app.get("/")
    async def index() -> FileResponse:
        """Ops dashboard UI."""
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/metrics")
    async def metrics() -> Response:
        """Prometheus scrape endpoint for token routing analytics."""
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/metrics/summary")
    async def metrics_summary() -> dict:
        """Plain-JSON metrics snapshot consumed by the dashboard's meter."""
        return metrics_module.summary()

    @app.get("/api/history")
    async def api_history() -> dict:
        """Persisted messages for the web UI's own session (distinct from CLI)."""
        return {"messages": dashboard.chat_history(context, source="web-user")}

    @app.post("/api/chat/new")
    async def api_chat_new() -> dict:
        """Start a fresh conversation; the old one stays browsable in /api/chat/sessions."""
        session_id = dashboard.new_chat_session(context, source="web-user")
        return {"session_id": session_id}

    @app.get("/api/chat/sessions")
    async def api_chat_sessions() -> dict:
        """Past web-chat sessions, most recent first, for a history sidebar."""
        return {"sessions": dashboard.list_chat_sessions(context, source="web-user")}

    @app.post("/api/chat/resume")
    async def api_chat_resume(payload: dict = Body(...)) -> dict:
        """Make a past session the active one again and return its messages."""
        session_id = payload["session_id"]
        messages = dashboard.resume_chat_session(context, "web-user", session_id)
        return {"messages": messages}

    @app.post("/api/chat/delete")
    async def api_chat_delete(payload: dict = Body(...)) -> dict:
        """Delete a past session. If it was active, starts a fresh one."""
        dashboard.delete_chat_session(context, "web-user", payload["session_id"])
        return {"ok": True}

    @app.get("/api/settings")
    async def api_settings() -> dict:
        """Editable settings: env secrets (masked) and model tiers."""
        return dashboard.settings_snapshot(context)

    @app.post("/api/settings/env")
    async def api_settings_env(payload: dict = Body(...)) -> dict:
        """Set one whitelisted env var (API key/token). Takes effect immediately."""
        try:
            dashboard.update_env_var(payload["key"], payload["value"])
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"ok": True}

    @app.post("/api/settings/tier")
    async def api_settings_tier(payload: dict = Body(...)) -> dict:
        """Update one model tier's provider/model/api_base/supports_tools."""
        try:
            dashboard.update_model_tier(context, payload["tier"], payload["fields"])
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"ok": True}

    @app.get("/api/dashboard")
    async def api_dashboard() -> dict:
        """Everything the ops dashboard UI needs, in one call."""
        return {
            "workers": dashboard.workers_snapshot(context),
            "agents": dashboard.agents_snapshot(context),
            "skills": dashboard.skills_snapshot(context),
            "crons": dashboard.crons_snapshot(context),
            "memories": dashboard.memories_snapshot(context),
            "system": dashboard.system_health(list(latency_samples)),
            "logs": dashboard.tail_logs(context),
            "nodes": dashboard.nodes_snapshot(context),
            "balancer": dashboard.balancer_snapshot(context),
        }

    # WebSocket endpoint
    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        """WebSocket endpoint for real-time event streaming and chat."""
        await websocket.accept()

        # Check if WebSocket worker is available
        if context.websocket_worker is None:
            await websocket.close(code=1013, reason="WebSocket not available")
            return

        # Hand off to worker
        await context.websocket_worker.handle_connection(websocket)

    return app
