"""FastAPI application with WebSocket support."""

from fastapi import FastAPI, Response, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from rune.core.context import SharedContext


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


    @app.get("/metrics")
    async def metrics() -> Response:
        """Prometheus scrape endpoint for token routing analytics."""
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

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
