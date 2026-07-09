"""Resolves which model tier(s) to try for a given call-site role.

Category routing: when a role's policy sets `classify: true`, a cheap
pre-call to `classifier_tier` (usually the local/free tier) labels the
request (e.g. daily / coding / reasoning) and that label picks the tier
for the real call, via `categories`. Falls back to `default` if
classification is off, errors, or returns something unrecognized.

Scoring override: when ALLOWED_MODELS / FIREWORKS_BASE_URL are set in the
environment (e.g. a graded harness), local/dev-only tiers are skipped and
Fireworks credentials from the environment win over config, so only
Fireworks-routed calls are ever made.
"""

import logging
import os

from litellm import acompletion
from litellm.types.completion import ChatCompletionMessageParam as Message

from rune.utils.config import ModelRolePolicy, ModelRoutingConfig

logger = logging.getLogger(__name__)

CLASSIFY_PROMPT = (
    "Classify the user's request below into exactly one category: {categories}.\n"
    "Reply with only the category word, nothing else.\n\n"
    "Request: {content}"
)


def _allowed_models() -> set[str] | None:
    raw = os.environ.get("ALLOWED_MODELS")
    return {m.strip() for m in raw.split(",") if m.strip()} if raw else None


def _last_user_content(messages: list[Message]) -> str | None:
    for message in reversed(messages):
        if message.get("role") == "user" and message.get("content"):
            return str(message["content"])
    return None


async def _classify(
    model_routing: ModelRoutingConfig, policy: ModelRolePolicy, content: str
) -> str | None:
    """Ask the classifier tier which category this request falls into."""
    tier = model_routing.tiers.get(policy.classifier_tier or policy.default)
    if not tier:
        return None

    prompt = CLASSIFY_PROMPT.format(
        categories=", ".join(policy.categories), content=content
    )
    extra: dict = {}
    if tier.provider == "ollama":
        # Ollama's thinking models otherwise restate the category list
        # verbatim in their chain-of-thought, polluting `.content` and
        # making substring matching pick the wrong word.
        extra["think"] = False

    try:
        response = await acompletion(
            model=tier.litellm_model,
            messages=[{"role": "user", "content": prompt}],
            api_key=tier.api_key,
            api_base=tier.api_base,
            temperature=0,
            max_tokens=20,
            **extra,
        )
        label = (response.choices[0].message.content or "").strip().lower()
    except Exception as e:
        logger.warning("classification call failed (%s), using default tier", e)
        return None

    first_word = label.split()[0].strip(".,!:;\"'") if label else ""
    if first_word in policy.categories:
        return first_word
    for category in policy.categories:
        if category in label:
            return category
    return None


async def resolve_tiers(
    model_routing: ModelRoutingConfig | None,
    role: str | None,
    needs_tools: bool,
    messages: list[Message] | None = None,
) -> list[dict]:
    """Return ordered candidate tiers (chosen, then escalation) for this call.

    Empty list means: caller should fall back to its own fixed single model.
    """
    if not model_routing or not model_routing.enabled or not role:
        return []

    policy = model_routing.roles.get(role)
    if not policy:
        return []

    chosen_tier_name = policy.default
    if policy.classify and policy.categories and messages:
        content = _last_user_content(messages)
        if content:
            category = await _classify(model_routing, policy, content)
            if category:
                chosen_tier_name = policy.categories[category]

    scoring_mode = bool(os.environ.get("ALLOWED_MODELS"))
    allowed = _allowed_models()
    fw_base = os.environ.get("FIREWORKS_BASE_URL")
    fw_key = os.environ.get("FIREWORKS_API_KEY")

    candidates = []
    for tier_name in (chosen_tier_name, policy.escalate_to):
        if not tier_name:
            continue
        tier = model_routing.tiers.get(tier_name)
        if not tier:
            continue
        if needs_tools and not tier.supports_tools:
            continue
        if scoring_mode and not tier.scored:
            continue
        litellm_model = tier.litellm_model
        if allowed and litellm_model not in allowed and tier.model not in allowed:
            continue

        is_fireworks = tier.provider == "fireworks_ai"
        candidates.append(
            {
                "model": litellm_model,
                "api_key": (fw_key if (fw_key and is_fireworks) else tier.api_key),
                "api_base": (fw_base if (fw_base and is_fireworks) else tier.api_base),
                "tier_name": tier_name,
                "provider": tier.provider,
            }
        )

    return candidates
