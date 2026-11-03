"""How many tool schemas one model request can carry."""

from __future__ import annotations

# OpenAI refuses more ("Invalid 'tools': array too long ... maximum length 128"), the strictest
# limit of the providers in use. Two large MCP servers loaded whole (Linear 68 + ClickUp 61) pass it.
MAX_TOOLS_PER_REQUEST = 128

__all__ = ["MAX_TOOLS_PER_REQUEST"]
