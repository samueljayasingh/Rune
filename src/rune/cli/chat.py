"""Chat CLI command: a WebSocket client of a running `rune server`.

Talks to the same /ws endpoint the web dashboard's Chat tab uses, with the
same fixed source ("local"), so both interfaces share one ongoing session
instead of each keeping its own isolated conversation.
"""

import asyncio
import json

import typer
import websockets
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.text import Text

from rune.utils.config import Config

SOURCE_ID = "cli-user"  # distinct from the web UI's clientId: separate sessions


def _ws_url(config: Config) -> str:
    host = config.api.host
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    return f"ws://{host}:{config.api.port}/ws"


class ChatLoop:
    """Interactive chat session that proxies to a running server over WebSocket."""

    def __init__(self, config: Config, agent_id: str | None = None):
        self.config = config
        self.console = Console()
        self.agent_label = agent_id or config.default_agent

    def get_user_input(self) -> str:
        prompt_text = Text("You", style="cyan")
        return Prompt.ask(prompt_text, console=self.console).strip()

    def display_agent_response(self, content: str) -> None:
        prefix = Text(f"{self.agent_label}: ", style="green")
        self.console.print(prefix, end="")
        self.console.print(content)

    async def run(self) -> None:
        self.console.print(
            Panel(
                Text("Welcome to rune!", style="bold cyan"),
                title="Chat",
                border_style="cyan",
            )
        )
        self.console.print("Type '/help' for commands, 'quit' or 'exit' to end.\n")

        url = _ws_url(self.config)
        try:
            ws = await websockets.connect(url)
        except (OSError, ConnectionRefusedError):
            self.console.print(
                f"[red]Could not connect to {url}.[/red] "
                "Start the server first with [bold]rune server[/bold]."
            )
            return

        try:
            while True:
                user_input = await asyncio.to_thread(self.get_user_input)
                if user_input.lower() in ("quit", "exit", "q"):
                    self.console.print("\n[bold yellow]Goodbye![/bold yellow]")
                    break
                if not user_input:
                    continue

                await ws.send(json.dumps({"source": SOURCE_ID, "content": user_input}))

                try:
                    await self._await_response(ws)
                except asyncio.TimeoutError:
                    self.console.print("[red]Agent response timed out[/red]")
                    self.console.print()

        except (KeyboardInterrupt, EOFError):
            self.console.print("\n[bold yellow]Goodbye![/bold yellow]")
        finally:
            await ws.close()

    async def _await_response(self, ws) -> None:
        """Read broadcast events until the agent's reply (or an error) arrives."""
        deadline = asyncio.get_event_loop().time() + 120.0
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            data = json.loads(raw)

            if data.get("type") == "OutboundEvent":
                self.display_agent_response(data["content"])
                return
            if data.get("type") == "error":
                self.console.print(f"[red]{data.get('message', 'error')}[/red]")
                return
            # InboundEvent (our own echo) / DispatchEvent etc: ignore and keep waiting.


def chat_command(ctx: typer.Context, agent_id: str | None = None) -> None:
    """Start interactive chat session against a running `rune server`."""
    config = ctx.obj.get("config")
    chat_loop = ChatLoop(config, agent_id=agent_id)
    asyncio.run(chat_loop.run())
