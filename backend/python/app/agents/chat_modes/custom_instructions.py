"""Org-level "Custom Instructions" resolver for Chat Assistant modes.

Shared by `chat_modes.bridge` (`/chat/stream` Internal / Web Search) and the
Universal Agent Mode path (`agentIdPlaceholder` → `run_agent_loop_stream`).
Agent Builder agents (real agent IDs) skip this — they use their own
`systemPrompt` / `instructions` instead.
"""

from __future__ import annotations

from typing import Any

from app.agents.chat_modes.policy import ChatModePolicy

# Maps `ChatModePolicy.system_prompt_key` to the SystemPromptsConfig field the
# workspace "Custom Instructions" settings page writes.
_CUSTOM_INSTRUCTIONS_CONFIG_KEY: dict[str, str] = {
    "internal_search": "customSystemPrompt",
    "web_search": "customSystemPromptWebSearch",
    "agent": "customSystemPromptAgent",
}


def resolve_custom_instructions(
    system_prompts_config: dict[str, Any], policy: ChatModePolicy,
) -> str | None:
    """Selects the org-level "Custom Instructions" text matching the active
    chat mode. Returns `None` when unset/blank so the prompt builder omits
    the section instead of rendering an empty header."""
    if not isinstance(system_prompts_config, dict):
        return None
    key = _CUSTOM_INSTRUCTIONS_CONFIG_KEY.get(policy.system_prompt_key, "")
    if not key:
        return None
    value = system_prompts_config.get(key)
    if not isinstance(value, str):
        return None
    prompt = value.strip()
    return prompt or None
