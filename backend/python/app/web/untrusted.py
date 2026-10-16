"""Marks text that came from the web, so the model treats it as data and not as instructions."""

import re

_TAG = "untrusted_web_content"
_CLOSING_TAG = re.compile(rf"</\s*{_TAG}\s*>", re.IGNORECASE)
_UNSAFE_SOURCE_CHARS = re.compile(r"[^A-Za-z0-9._:/-]")

FENCE_CLOSE = f"</{_TAG}>"


def fence_open(source: str) -> str:
    return f'<{_TAG} source="{_UNSAFE_SOURCE_CHARS.sub("", source)[:200]}">'


def defuse(text: str) -> str:
    """``text`` with any closing fence tag broken, so a page can't end the block and speak
    outside it."""
    return _CLOSING_TAG.sub(f"</ {_TAG}_>", text)
