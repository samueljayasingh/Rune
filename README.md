# Rune

Rune is an autonomous AI agent runtime: chat loop, tools, skills, persistent
memory, event-driven multi-channel delivery (CLI, WebSocket, Telegram,
Discord), cron-scheduled tasks, and multi-agent dispatch.

## Features

- **Tools & Skills** — extend agents with tools and `SKILL.md`-defined capabilities
- **Persistence & Compaction** — durable conversation history with automatic context compaction
- **Web access** — built-in web search and page reading
- **Event-driven core** — scales beyond a single CLI session
- **Multi-channel** — Telegram, Discord, and WebSocket delivery
- **Config hot-reload** — edit configuration without restarting
- **Cron & heartbeat** — scheduled and background work
- **Multi-agent routing & dispatch** — route tasks to specialized agents that can collaborate
- **Concurrency control** — safe under multiple simultaneous runs
- **Long-term memory** — persistent recall across sessions

## Getting Started

1. **Copy the example config:**
   ```bash
   cp default_workspace/config.example.yaml default_workspace/config.user.yaml
   ```

2. **Edit `config.user.yaml`** with your API keys:
   - See [LiteLLM providers](https://docs.litellm.ai/docs/providers) for the full list of supported providers
   - Check out [Provider Examples](PROVIDER_EXAMPLES.md) for examples

3. **Install and run:**
   ```bash
   pip install -e .
   rune
   ```

## Project Layout

```
src/rune/
├── channel/    # Telegram, Discord, WebSocket adapters
├── cli/        # CLI entry points (chat, server)
├── core/       # Agent loop, context, routing, events, history
├── provider/   # LLM, web search, web read providers
├── server/     # FastAPI app and background workers
├── tools/      # Built-in and dispatchable tools
└── utils/      # Config, logging helpers

default_workspace/  # Default agents, skills, and config template
```
