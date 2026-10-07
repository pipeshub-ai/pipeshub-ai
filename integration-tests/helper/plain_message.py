"""Does a message a user will read explain itself?

The product's rule for anything a user sees — a failed file's reason, an
upload error — is plain language that says what went wrong in the user's
terms and what to do next. This checks the parts a test can check: no leaked
internals, a mention of the thing that broke, and a next step.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

_LEAKS = (
    (re.compile(r"\b[A-Z][A-Za-z]*(?:Error|Exception)\b"), "names an exception class"),
    (re.compile(r"Traceback|File \"/"), "includes a traceback"),
    (re.compile(r"\[Errno \d+\]|\bE(?:NOENT|NOTDIR|NOSPC|ACCES|ROFS|CONNREFUSED)\b"), "includes an OS error code"),
    (re.compile(r"(?:^|[\s'\"(])/(?:root|app|data|srv|tmp|usr)/"), "shows an internal file path"),
    (re.compile(r"\bstatus code \d{3}\b|\bHTTP \d{3}\b"), "shows an HTTP status code"),
    (re.compile(r"[{}]|\b[0-9a-f]{24}\b"), "shows raw data or an internal id"),
)

NEXT_STEPS = (
    "try again", "retry", "re-index", "reindex", "check", "contact", "ask",
    "add ", "set up", "configure", "wait", "upload it again", "free up",
)


def plain_language_problems(message: str | None, *, about: Sequence[str]) -> list[str]:
    """Why ``message`` falls short, or an empty list when it reads well.

    ``about`` holds words, any one of which names the thing that went wrong in
    terms a user knows (for an AI outage: "AI", "model").
    """
    text = (message or "").strip()
    if not text:
        return ["is empty"]
    problems = [why for pattern, why in _LEAKS if pattern.search(text)]
    lowered = text.lower()
    if not any(re.search(rf"\b{re.escape(word.lower())}\b", lowered) for word in about):
        problems.append(f"never says what failed (expected a mention of {' / '.join(about)})")
    if not any(step in lowered for step in NEXT_STEPS):
        problems.append("gives no next step")
    return problems
