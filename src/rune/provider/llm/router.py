"""Resolves which model tier(s) to try for a given call-site role.

Scoring override: when ALLOWED_MODELS / FIREWORKS_BASE_URL are set in the
environment (the hackathon grading harness), local/dev-only tiers are
skipped and Fireworks credentials from the environment win over config,
so only Fireworks-routed calls are ever made.
"""

import os

from rune.utils.config import ModelRoutingConfig


def _allowed_models() -> set[str] | None:
    raw = os.environ.get("ALLOWED_MODELS")
    return {m.strip() for m in raw.split(",") if m.strip()} if raw else None


def resolve_tiers(
    model_routing: ModelRoutingConfig | None, role: str | None, needs_tools: bool
) -> list[dict]:
    """Return ordered candidate tiers (default, then escalation) for this call.

    Empty list means: caller should fall back to its own fixed single model.
    """
    if not model_routing or not model_routing.enabled or not role:
        return []

    policy = model_routing.roles.get(role)
    if not policy:
        return []

    scoring_mode = bool(os.environ.get("ALLOWED_MODELS"))
    allowed = _allowed_models()
    fw_base = os.environ.get("FIREWORKS_BASE_URL")
    fw_key = os.environ.get("FIREWORKS_API_KEY")

    candidates = []
    for tier_name in (policy.default, policy.escalate_to):
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
