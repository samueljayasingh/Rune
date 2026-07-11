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
    "Judge by what the user is actually asking you to DO, not by words that merely "
    "remind you of a category (e.g. \"hello world\" is a greeting, not a coding "
    "request, even though it's a famous programming example). If a message mixes "
    "a greeting with a real task, classify by the task. If truly ambiguous or just "
    "a bare command/word, default to daily.\n\n"
    "- daily: greetings, small talk, one-step everyday facts/conversions, creative "
    "writing, vague/ambiguous one-word input — but NEVER anything needing "
    "current/live/external information or an actual tool/agent/skill invocation "
    "(see reasoning below), even if it sounds like simple small talk\n"
    "- coding: write, debug, explain, or review code; error messages/stack traces; "
    "questions about algorithms or code behavior\n"
    "- reasoning: multi-step math, logic, planning, or financial calculations; "
    "ALSO anything requiring current/live/real-world data a model can't know from "
    "training alone — weather, news, sports scores, stock/crypto prices, "
    "who-won/what-happened-today, current facts about specific people or events, "
    "or anything else that needs a web lookup to answer correctly; ALSO any request "
    "that needs a real tool, skill, or agent to actually run — saving/recalling a "
    "memory, dispatching to another agent (e.g. ledger), running a skill, searching "
    "or reading the web, scheduling a cron — even if the wording sounds like a "
    "simple one-line instruction\n\n"
    "Examples:\n"
    "Request: hello world -> daily\n"
    "Request: what's 15% of 340 -> daily\n"
    "Request: convert 5 miles to km -> daily\n"
    "Request: write me a poem about the sea -> daily\n"
    "Request: hey, can you write me a quicksort in Python -> coding\n"
    "Request: TypeError: undefined is not a function, what does this mean -> coding\n"
    "Request: what's the time complexity of this sort? -> coding\n"
    "Request: if I invest $500/month at 7% for 30 years, how much will I have -> reasoning\n"
    "Request: two trains leave stations 300 miles apart, when do they meet -> reasoning\n"
    "Request: what's the weather in Texas -> reasoning\n"
    "Request: who won the match yesterday -> reasoning\n"
    "Request: what's the current price of Bitcoin -> reasoning\n"
    "Request: ledger save it as a daily note -> reasoning\n"
    "Request: remember that I prefer TypeScript -> reasoning\n"
    "Request: search the web for the latest news on this -> reasoning\n\n"
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

    try:
        response = await acompletion(
            model=tier.litellm_model,
            messages=[{"role": "user", "content": prompt}],
            api_key=tier.api_key,
            api_base=tier.api_base,
            temperature=0,
            # Reasoning-tuned classifier models (e.g. gpt-oss) spend part of
            # this budget on internal reasoning_content before the final
            # answer; too low and content comes back empty.
            max_tokens=150,
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
                # Whether THIS tier should actually receive tool schemas, not
                # whether the caller offered them: a tier that can't reliably
                # do tool-calling still gets tried (via classification), just
                # without tools attached — a plain answer beats a hang or a
                # hallucinated tool call.
                "attach_tools": needs_tools and tier.supports_tools,
            }
        )

    return candidates
