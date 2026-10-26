"""Marks text that came from the web, so the model treats it as data and not as instructions."""

import re
import unicodedata

_TAG = "untrusted_web_content"
_UNSAFE_SOURCE_CHARS = re.compile(r"[^A-Za-z0-9._:/-]")
# Characters that render as nothing; a page could hide them inside the tag name.
_INVISIBLE = re.compile("[­᠎​-‏‪-‮⁠-⁤﻿]")
_TAG_NAME = re.compile(_TAG, re.IGNORECASE)

FENCE_CLOSE = f"</{_TAG}>"


def fence_open(source: str) -> str:
    return f'<{_TAG} source="{_UNSAFE_SOURCE_CHARS.sub("", source)[:200]}">'


def defuse(text: str) -> str:
    """``text`` that can't end the fence: wherever the fence's tag name appears, however it is
    spelled (case, fullwidth letters, invisible characters, extra attributes), it is renamed, so no
    closing tag a model could read as the end of the block survives. Other text is unchanged."""
    folded = _INVISIBLE.sub("", unicodedata.normalize("NFKC", text))
    if not _TAG_NAME.search(folded):
        return text
    return _TAG_NAME.sub("untrusted-web-content", folded)
