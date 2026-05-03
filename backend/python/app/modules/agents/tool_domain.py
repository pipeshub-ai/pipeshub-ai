"""Single source of truth for runtime tool → (domain, action) routing.

Both `deep.tool_router.group_tools_by_domain` (sub-agent assignment +
orchestrator validation) and `capability_summary._get_all_tool_domains`
(orchestrator prompt advertisement) must agree on which domain a tool
belongs to. When they drift, the orchestrator's prompt advertises a
domain whose tasks the validator then rejects — which is what produced
the phantom "execute" / "fetch" domains from bare-named built-ins like
`execute_sql_query` and `fetch_full_record`.

Resolution rule: trust the canonical dotted name set during tool
registration (`_original_name`, with `name` as fallback). Never invent
a domain via heuristics on the underscore-separated sanitized name —
that is the bug class this module exists to prevent.
"""

from __future__ import annotations

from typing import Any

# Spelling normalizations applied after the dotted-name split.
DOMAIN_ALIASES: dict[str, str] = {
    "googledrive": "google_drive",
    "google_drive": "google_drive",
    "google-drive": "google_drive",
    "googlecalendar": "google_calendar",
    "google_calendar": "google_calendar",
    "onedrive": "onedrive",
    "one_drive": "onedrive",
}

_WEB_TOOL_NAMES = {"fetch_url", "web_search"}

def derive_tool_domain(tool: Any) -> tuple[str, str]:
    """Return ``(domain, action)`` for a runtime tool.

    Resolution order:
    1. ``tool._original_name`` if it contains a ``.`` (canonical form set
       by the tool registry / `tool_system.py`).
    2. ``tool.name`` if it contains a ``.``.
    3. Fall back to ``("utility", name)`` for any bare name.
    """
    name = getattr(tool, "name", "") or ""
    canonical = getattr(tool, "_original_name", "") or name

    if canonical in _WEB_TOOL_NAMES or name in _WEB_TOOL_NAMES:
        domain, action = "web", canonical
    elif "." in canonical:
        domain, action = canonical.split(".", 1)
    else:
        domain, action = "utility", canonical

    domain = domain.lower()
    return DOMAIN_ALIASES.get(domain, domain), action
