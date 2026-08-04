"""Tool-name resolution: normalize separator differences between the
dotted legacy catalog form and the double-underscore form the agent-loop
registry uses, so prompt code never has to guess separators.

The agent-loop registry grants tools as ``app__tool`` (double-underscore).
Legacy ChatState, SourceCatalog headers, and some tests use ``app.tool``
(dotted).  Call sites that detect capabilities by name (``ToolSurfaces``,
``factory.py``, prompt text builders) must not hard-code either form —
they should resolve through ``granted()`` so the printed name is always
the one the model can actually call.
"""
from __future__ import annotations

from typing import Iterable


def _split(name: str) -> tuple[str, str]:
    """Return ``(app, tool)`` from any separator form.

    ``"retrieval__search_internal_knowledge"``  → ``("retrieval", "search_internal_knowledge")``
    ``"retrieval.search_internal_knowledge"``   → ``("retrieval", "search_internal_knowledge")``
    ``"run_code"``                              → ``("", "run_code")``
    """
    if "__" in name:
        app, _, tool = name.partition("__")
        return app, tool
    if "." in name:
        app, _, tool = name.partition(".")
        return app, tool
    return "", name


def granted(logical: str, tool_names: Iterable[str]) -> str | None:
    """Return the exact name as granted in *tool_names*, or ``None``.

    Accepts dotted (``retrieval.x``), double-underscore (``retrieval__x``),
    or bare (``x``) spellings in *logical*.  A bare *logical* matches any
    granted name whose tool-suffix equals *logical*, so
    ``granted("web_search", names)`` returns ``"dynamic__web_search"`` when
    that is what is granted — the caller never needs to guess the app prefix.

    Priority:
    1. Exact match (handles bare-equals-bare and already-canonical names).
    2. Separator-normalized match (dotted logical → double-underscore granted,
       or vice-versa, when app prefixes agree).
    3. Bare-suffix match (no app prefix in logical → any granted name with
       the same tool suffix).
    """
    names_list = list(tool_names)
    if logical in names_list:
        return logical

    l_app, l_tool = _split(logical)
    for name in names_list:
        g_app, g_tool = _split(name)
        if g_tool != l_tool:
            continue
        if l_app and g_app:
            if l_app == g_app:
                return name
        elif not l_app:
            # bare logical: matches any granted name with this tool suffix
            return name
    return None


def granted_any(
    logicals: Iterable[str],
    tool_names: Iterable[str],
) -> tuple[str, ...]:
    """Return granted spellings for every logical name found in *tool_names*.

    Order matches *logicals*; unmatched logicals are omitted.  Deduplicates
    by granted name so the same granted name is never returned twice even if
    two logicals happen to resolve to the same entry.
    """
    names_list = list(tool_names)
    result: list[str] = []
    seen: set[str] = set()
    for logical in logicals:
        match = granted(logical, names_list)
        if match is not None and match not in seen:
            result.append(match)
            seen.add(match)
    return tuple(result)
