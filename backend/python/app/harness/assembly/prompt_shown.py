"""What a default-chat turn's system prompt showed the model, as the journal keeps it (R43MCP-FU-5).

The loop builds its system prompt per turn, outside the journal's ``contextDigest`` (which covers the messages only),
and Opik is optional, so a deployment could not show what its model was told, nor confirm that
an attached server was named. Each turn's request records, in the run's slots, the digest and size of the prompt's
stable block (the volatile block holds the time, so it would change every turn) and the "Finding Information" source
lines as the model read them: each source's label (a server's name, never its tools or what it holds) and the tools
the line tells the model to use. Nothing here is beyond what the person sees of their own agent.
"""

from __future__ import annotations

import re
from hashlib import sha256
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic import JsonValue

PROMPT_SHOWN_SLOT = "prompt.shown"
_SECTION = "## Finding Information"
_ITEM = re.compile(r"^\d+\. (.+)$", re.MULTILINE)
_TOOL = re.compile(r"`([A-Za-z0-9_.\-]+)`")
_MAX_SOURCES = 20
_MAX_LABEL = 300


def source_lines(system: str) -> list[dict[str, JsonValue]]:
    """The numbered source list under "Finding Information": each line's label (up to its " — ") and the tools it
    names. Empty when the prompt has no such section."""
    if _SECTION not in system:
        return []
    section = system.split(_SECTION, 1)[1].split("\n## ", 1)[0]
    sources: list[dict[str, JsonValue]] = []
    for item in _ITEM.findall(section)[:_MAX_SOURCES]:
        label, _, rest = item.partition(" — ")
        via: list[JsonValue] = list(dict.fromkeys(_TOOL.findall(rest)))
        sources.append({"label": label[:_MAX_LABEL], "via": via})
    return sources


def prompt_shown(system: str, blocks: list[str] | None) -> dict[str, JsonValue]:
    """The slot value for a request whose system prompt is ``system`` (``blocks``: its stable and volatile parts)."""
    stable = blocks[0] if blocks else system
    return {
        "stableDigest": "sha256:" + sha256(stable.encode("utf-8")).hexdigest(),
        "stableChars": len(stable),
        "sources": list(source_lines(system)),
    }


__all__ = ["PROMPT_SHOWN_SLOT", "prompt_shown", "source_lines"]
