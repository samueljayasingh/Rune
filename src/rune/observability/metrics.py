"""Token accounting for model routing: what got spent where, and what got saved.

"Saved" = tokens served by a non-Fireworks tier (local Gemma) that would
otherwise have gone to Fireworks. Cost-per-token isn't tracked here since
pricing varies by contract; this reports token counts, which is what the
Track 1 scoring is actually based on.
"""

from prometheus_client import Counter

CALLS_TOTAL = Counter(
    "rune_llm_calls_total",
    "LLM calls by role/tier/model/provider/outcome",
    ["role", "tier", "model", "provider", "outcome"],
)

PROMPT_TOKENS_TOTAL = Counter(
    "rune_llm_prompt_tokens_total",
    "Prompt tokens by role/tier/model/provider",
    ["role", "tier", "model", "provider"],
)

COMPLETION_TOKENS_TOTAL = Counter(
    "rune_llm_completion_tokens_total",
    "Completion tokens by role/tier/model/provider",
    ["role", "tier", "model", "provider"],
)

FIREWORKS_TOKENS_TOTAL = Counter(
    "rune_fireworks_tokens_total",
    "Tokens actually billed through Fireworks, by role/tier/model",
    ["role", "tier", "model", "token_type"],
)

LOCAL_TOKENS_SAVED_TOTAL = Counter(
    "rune_local_tokens_saved_total",
    "Tokens served locally instead of Fireworks (would've been billed otherwise)",
    ["role", "tier", "model"],
)


def record_call(
    role: str | None,
    tier: str,
    model: str,
    provider: str,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    outcome: str,
) -> None:
    """Record one completed (or failed) LLM call for the analytics dashboard."""
    role = role or "unset"
    CALLS_TOTAL.labels(role, tier, model, provider, outcome).inc()

    if outcome != "success":
        return

    prompt_tokens = prompt_tokens or 0
    completion_tokens = completion_tokens or 0

    PROMPT_TOKENS_TOTAL.labels(role, tier, model, provider).inc(prompt_tokens)
    COMPLETION_TOKENS_TOTAL.labels(role, tier, model, provider).inc(completion_tokens)

    if provider == "fireworks_ai":
        FIREWORKS_TOKENS_TOTAL.labels(role, tier, model, "prompt").inc(prompt_tokens)
        FIREWORKS_TOKENS_TOTAL.labels(role, tier, model, "completion").inc(
            completion_tokens
        )
    else:
        LOCAL_TOKENS_SAVED_TOTAL.labels(role, tier, model).inc(
            prompt_tokens + completion_tokens
        )
