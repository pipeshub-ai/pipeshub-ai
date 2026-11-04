"""Which client sent a request, read from its root request id.

The SDKs stamp ``sdk-<lang>-`` (``sdk-py-``, ``sdk-ts-``), the local MCP server
``mcp-``, and the Node gateway gives hosted ``/mcp`` requests ``mcp-`` too.
Everything else (the web app's ``<userId>-``, ``anon-``, ``sys-``) is
``other``. Mirrors ``src/libs/services/telemetry/client.ts``.
"""

import re

_SDK_LANGUAGES = frozenset({"py", "ts", "go"})
_SDK_PREFIX = re.compile(r"^sdk-([a-z]+)-")


def classify_request_id(request_id: str | None) -> tuple[str, str]:
    """``(client, sdk)``: client is sdk, mcp or other; sdk is the SDK language,
    ``other`` for an unknown one, or ``none``."""
    if not request_id:
        return "other", "none"
    if request_id.startswith("mcp-"):
        return "mcp", "none"
    match = _SDK_PREFIX.match(request_id)
    if match:
        lang = match.group(1)
        return "sdk", lang if lang in _SDK_LANGUAGES else "other"
    return "other", "none"
