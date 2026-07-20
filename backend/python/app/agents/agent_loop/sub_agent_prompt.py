"""Shared system-prompt construction for scoped PipesHub sub-agents.

One prompt builder for BOTH ways a scoped child agent comes to exist:

- dynamically, via `spawn_agent` on the deep-agent path
  (`loops/orchestrator.py`'s `domain_spec_factory`), and
- statically, via the domain-agent catalog (`domain_agents.py`), where
  each domain agent is a pre-wired `AgentTool`.

Extracted from `loops/orchestrator.py` so the two paths can never drift:
a child that can't resolve "my tickets" or "last 2 months" flails with
wrong parameters for all its turns regardless of how it was spawned, so
user identity and time context must always be present.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.utils.time_conversion import build_llm_time_context

if TYPE_CHECKING:
    from app.agents.agent_loop.context import AgentContext

SUB_AGENT_EXECUTION_RULES = """\
## Execution Rules

### Tool Usage
- **Parallelise independent calls**: issue ALL independent tool calls in a SINGLE turn.
- **Maximise page size**: use the largest supported `maxResults`/`limit`/`pageSize`.
- **Retry differently**: if a tool returns empty results, try a DIFFERENT query phrasing
  or broader filter — do not repeat the same call.

### Response Format
- **Present ALL data in FULL**: every item returned by tools MUST appear in your
  response. Include the COMPLETE content — full text, full descriptions, full body —
  not summaries or excerpts. The calling agent cannot see the tool results you
  received; your response text is the ONLY thing it gets.
- **Include ALL fields**: IDs, keys, URLs, names, dates, statuses, priorities.
- **Date formatting**: render dates in human-readable form using the time zone from
  the Time context below (e.g. "April 28, 2026 at 3:45 PM IST"). Never output raw
  epoch numbers or ISO strings.
- **Links are mandatory**: include `[Title](url)` for every item.
- **Use tables** for lists of items.
- **Never summarise document content**: if a tool returned the full text of a
  document, page, ticket, or record, reproduce that content in your answer — do
  not paraphrase, condense, or omit sections. Length is fine; loss of detail is not.

### Large or Structured Results → Artifacts
- If your findings include many items, a large document, or structured records
  (tickets, rows, JSON) — in ADDITION to your complete prose answer above, also
  call `task_complete(..., artifacts=[{"name": "...", "type": "json"|"text",
  "content": <the full, un-truncated data>}])`. A task that depends on yours
  receives your artifacts as files with the exact original data, instead of
  having to parse it back out of prose that may have been summarized in transit.
  This is additive — never omit the prose answer in favor of the artifact.

### Completion
Once you have enough information, reply with your COMPLETE answer in plain text
(no further tool calls). Your response is returned VERBATIM as the final result —
it is NOT post-processed or summarised — so include every detail the goal asks for."""


def build_sub_agent_prompt(
    domain: str,
    context: "AgentContext | None",
    *,
    extra_instructions: str | None = None,
) -> str:
    """System prompt for a child agent scoped to one domain's tools:
    role line + execution rules + optional domain-specific instructions +
    user identity + time context."""
    parts: list[str] = [
        f"You are a focused sub-agent for the '{domain}' domain. "
        "Complete the assigned task using ONLY the tools you were given.",
        SUB_AGENT_EXECUTION_RULES,
    ]
    if extra_instructions:
        parts.append(extra_instructions)

    if context is not None:
        user_block = build_user_context_block(context)
        if user_block:
            parts.append(user_block)
        time_block = build_llm_time_context(
            current_time=context.current_time,
            time_zone=context.timezone,
        )
        if time_block:
            parts.append(time_block)

    return "\n\n".join(parts)


def build_user_context_block(context: "AgentContext") -> str:
    """The child needs user identity to resolve 'my tickets', 'assigned to
    me' — mirrors the legacy `deep/sub_agent.py::_build_sub_agent_instructions`."""
    user_info = context.user_info or {}
    email = context.user_email or user_info.get("userEmail") or user_info.get("email") or ""
    name = (
        user_info.get("fullName")
        or user_info.get("name")
        or user_info.get("displayName")
        or f"{user_info.get('firstName', '')} {user_info.get('lastName', '')}".strip()
    )
    if not name and not email:
        return ""
    lines = ["## Current User"]
    if name:
        lines.append(f"- Name: {name}")
    if email:
        lines.append(f"- Email: {email}")
    lines.append('When the query says "my", "me", or "I", it refers to this user.')
    return "\n".join(lines)


__all__ = ["SUB_AGENT_EXECUTION_RULES", "build_sub_agent_prompt", "build_user_context_block"]
