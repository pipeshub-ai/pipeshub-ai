"""Org-level "Custom Instructions" resolver for Chat Assistant modes.

Shared by `chat_modes.bridge` (`/chat/stream` Internal / Web Search) and the
Universal Agent Mode path (`agentIdPlaceholder` → `run_agent_loop_stream`).
Agent Builder agents (real agent IDs) must never call this — they use their
own `systemPrompt` / `instructions` instead.
"""

from __future__ import annotations

from typing import Any

from app.agents.chat_modes.policy import ChatModePolicy

# Maps `ChatModePolicy.system_prompt_key` to the AI models config field the
# workspace "Custom Instructions" settings page writes.
_CUSTOM_INSTRUCTIONS_CONFIG_KEY: dict[str, str] = {
    "internal_search": "customSystemPrompt",
    "web_search": "customSystemPromptWebSearch",
    "agent": "customSystemPromptAgent",
}


def resolve_custom_instructions(
    ai_models_config: dict[str, Any], policy: ChatModePolicy,
) -> str | None:
    """Selects the org-level "Custom Instructions" text matching the active
    chat mode. Returns `None` when unset/blank so the prompt builder omits
    the section instead of rendering an empty header."""
    key = _CUSTOM_INSTRUCTIONS_CONFIG_KEY.get(policy.system_prompt_key, "")
    if not key:
        return None
    prompt = (ai_models_config.get(key) or "").strip()
    return prompt or None
