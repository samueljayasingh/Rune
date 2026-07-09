"""FastAPI application with WebSocket support."""

from pathlib import Path

from fastapi import FastAPI, Response, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from rune.core.context import SharedContext
from rune.observability import metrics as metrics_module

STATIC_DIR = Path(__file__).parent / "static"


def create_app(context: SharedContext) -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Rune WebSocket Server",
        description="WebSocket server for real-time agent communication",
        version="0.1.0",
    )
    app.state.context = context

    # Enable CORS for web clients
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )


    @app.get("/")
    async def index() -> FileResponse:
        """Demo UI: chat pane + live token-routing meter."""
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/metrics")
    async def metrics() -> Response:
        """Prometheus scrape endpoint for token routing analytics."""
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/metrics/summary")
    async def metrics_summary() -> dict:
        """Plain-JSON metrics snapshot consumed by the demo UI's meter."""
        return metrics_module.summary()

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
