from typing import Any, TYPE_CHECKING

from rune.core.agent_loader import AgentLoader
from rune.core.commands.registry import CommandRegistry
from rune.core.cron_loader import CronLoader
from rune.core.history import HistoryStore
from rune.core.prompt_builder import PromptBuilder
from rune.core.routing import RoutingTable
from rune.core.skill_loader import SkillLoader
from rune.core.eventbus import EventBus
from rune.channel.base import Channel
from rune.utils.config import Config

if TYPE_CHECKING:
    from rune.server.websocket_worker import WebSocketWorker


class SharedContext:
    """Global shared state for the application."""

    config: Config
    history_store: HistoryStore
    agent_loader: AgentLoader
    skill_loader: SkillLoader
    cron_loader: CronLoader
    command_registry: CommandRegistry
    routing_table: RoutingTable
    prompt_builder: PromptBuilder
    channels: list[Channel[Any]]
    eventbus: EventBus
    websocket_worker: "WebSocketWorker | None"
    workers: list  # set by Server after it builds its worker list; empty until then

    def __init__(
        self, config: Config, channels: list[Channel[Any]] | None = None
    ) -> None:
        self.config = config
        self.history_store = HistoryStore.from_config(config)
        self.agent_loader = AgentLoader.from_config(config)
        self.skill_loader = SkillLoader.from_config(config)
        self.cron_loader = CronLoader.from_config(config)
        self.command_registry = CommandRegistry.with_builtins()
        self.routing_table = RoutingTable(self)
        self.prompt_builder = PromptBuilder(self)

        if channels is not None:
            self.channels = channels
        else:
            self.channels = Channel.from_config(config)

        self.eventbus = EventBus(self)
        self.websocket_worker = None
        self.workers = []
