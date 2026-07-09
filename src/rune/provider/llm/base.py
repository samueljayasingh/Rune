"""Base LLM provider abstraction."""

import logging
from dataclasses import dataclass
from typing import Any, Optional, cast

from litellm import acompletion, Choices, TYPE_CHECKING
from litellm.types.completion import ChatCompletionMessageParam as Message
from litellm.types.utils import OpenAIChatCompletionFinishReason

from rune.observability.metrics import record_call
from rune.provider.llm.router import resolve_tiers

if TYPE_CHECKING:
    from rune.utils.config import LLMConfig, ModelRoutingConfig

StopReason = OpenAIChatCompletionFinishReason

logger = logging.getLogger(__name__)


@dataclass
class LLMToolCall:
    """A tool/function call from the LLM."""

    id: str
    name: str
    arguments: str  # JSON string


class LLMProvider:
    """LLM provider using litellm for multi-provider support."""

    def __init__(
        self,
        model: str,
        api_key: str,
        api_base: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        provider: str = "unknown",
        model_routing: Optional["ModelRoutingConfig"] = None,
        **kwargs: Any,
    ):
        """Initialize LLM provider."""
        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.provider = provider
        self.model_routing = model_routing
        self._settings = kwargs

    @classmethod
    def from_config(
        cls, config: "LLMConfig", model_routing: Optional["ModelRoutingConfig"] = None
    ) -> "LLMProvider":
        """Create provider from LLMConfig."""
        return cls(
            model=config.litellm_model,
            api_key=config.api_key,
            api_base=config.api_base,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            provider=config.provider,
            model_routing=model_routing,
        )

    async def chat(
        self,
        messages: list[Message],
        tools: Optional[list[dict[str, Any]]] = None,
        role: Optional[str] = None,
        **kwargs: Any,
    ) -> tuple[str, list[LLMToolCall], StopReason]:
        """Send a chat request to the LLM, routing across model tiers by role.

        Returns:
            Tuple of (content, tool_calls, stop_reason)
        """
        candidates = await resolve_tiers(
            self.model_routing, role, needs_tools=bool(tools), messages=messages
        )
        if not candidates:
            candidates = [
                {
                    "model": self.model,
                    "api_key": self.api_key,
                    "api_base": self.api_base,
                    "tier_name": "single",
                    "provider": self.provider,
                }
            ]

        last_error: Exception | None = None
        for tier in candidates:
            request_kwargs: dict[str, Any] = {
                "model": tier["model"],
                "messages": messages,
                "api_key": tier["api_key"],
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
            }
            if tier.get("api_base"):
                request_kwargs["api_base"] = tier["api_base"]
            if tools:
                request_kwargs["tools"] = tools
            request_kwargs.update(kwargs)

            try:
                response = await acompletion(**request_kwargs)
            except Exception as e:
                last_error = e
                record_call(
                    role, tier["tier_name"], tier["model"], tier["provider"],
                    None, None, outcome="error",
                )
                logger.warning(
                    "model tier %s failed for role=%s (%s), trying next tier",
                    tier["model"],
                    role,
                    e,
                )
                continue

            choice = cast(Choices, response.choices[0])
            message = choice.message
            stop_reason = choice.finish_reason
            usage = getattr(response, "usage", None)
            prompt_tokens = getattr(usage, "prompt_tokens", None)
            completion_tokens = getattr(usage, "completion_tokens", None)
            record_call(
                role, tier["tier_name"], tier["model"], tier["provider"],
                prompt_tokens, completion_tokens, outcome="success",
            )
            logger.info(
                "role=%s model=%s prompt_tokens=%s completion_tokens=%s",
                role,
                tier["model"],
                prompt_tokens,
                completion_tokens,
            )

            return (
                message.content or "",
                [
                    LLMToolCall(
                        id=tc["id"],
                        name=tc["function"]["name"],
                        arguments=tc["function"]["arguments"],
                    )
                    for tc in (message.tool_calls or [])
                ],
                stop_reason,
            )

        assert last_error is not None
        raise last_error
