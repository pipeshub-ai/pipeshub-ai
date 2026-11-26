"""How the session folder groups a run's tools by toolset: the schema files (``tools/``) and the stubs (``sdk/``)."""

from __future__ import annotations

from typing import Final

# A tool whose name has no ``<toolset>__`` prefix is one of PipesHub's own (search_tools, a knowledge tool).
OWN_TOOLSET: Final = "pipeshub"


def toolset_of(name: str) -> tuple[str, str]:
    """``(toolset, tool)`` as the folder files a tool: ``jira__search_issues`` is ``jira``/``search_issues``."""
    toolset, sep, tool = name.partition("__")
    return (toolset, tool) if sep and toolset and tool else (OWN_TOOLSET, name)


__all__ = ["OWN_TOOLSET", "toolset_of"]
