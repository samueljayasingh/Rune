"""FastAPI application with WebSocket support."""

import time
from collections import deque
from pathlib import Path

from fastapi import FastAPI, Request, Response, WebSocket
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
