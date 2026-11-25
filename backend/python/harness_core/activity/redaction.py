"""Command output redacted through the shared token vector before it leaves the worker (UX5).

``shared_redactor`` is the one hook every activity output goes through: QA-11's ``redact_tokens`` over the single
generated token vector, the same masking the approval card and ``interaction.explained`` use.

Output arrives in arbitrary chunks, so ``StreamRedactor`` holds back what could still be the start of a secret: the
partial last line (tokens never span a newline), and a private-key block until its END line.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from harness_core.security.token_patterns import SECRET_MASK, redact_tokens

if TYPE_CHECKING:
    from collections.abc import Callable

    Redactor = Callable[[str], str]

_KEY_BEGIN = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_KEY_END = re.compile(r"-----END [A-Z ]*PRIVATE KEY-----")
_SPACE = re.compile(r"\s")
_BEARER_END = re.compile(r"(?i)bearer\s*$")


def shared_redactor() -> Redactor:
    return redact_tokens


def _open_key_at(text: str) -> int | None:
    """Where a private-key block without its END line starts, if one does."""
    begins = list(_KEY_BEGIN.finditer(text))
    if not begins:
        return None
    last = begins[-1]
    return None if _KEY_END.search(text, last.end()) else last.start()


class StreamRedactor:
    """One output stream's redaction: ``feed`` returns what is safe to show now, ``flush`` the rest at the end."""

    def __init__(self, redact: Redactor, *, hold_chars: int, key_hold_chars: int) -> None:
        self._redact = redact
        self._hold = hold_chars
        self._key_hold = key_hold_chars
        self._pending = ""
        # Inside a block already masked for being too long: dropped through its END line.
        self._masking = False

    def feed(self, text: str) -> str:
        self._pending += text
        if self._masking and not self._skip_masked():
            return ""
        cut = self._safe_cut()
        out, self._pending = self._pending[:cut], self._pending[cut:]
        key_at = _open_key_at(self._pending)
        if key_at is not None and len(self._pending) - key_at > self._key_hold:
            # Fail closed: a block that never ends is shown as one mask, never in part.
            out += self._pending[:key_at] + SECRET_MASK
            self._pending = ""
            self._masking = True
        return self._redact(out) if out else ""

    def _skip_masked(self) -> bool:
        end = _KEY_END.search(self._pending)
        if end is None:
            # Keep a marker's worth, in case END is split across writes.
            self._pending = self._pending[-64:]
            return False
        self._pending = self._pending[end.end() :]
        self._masking = False
        return True

    def flush(self) -> str:
        rest, self._pending = self._pending, ""
        if self._masking:
            self._masking = False
            return ""
        key_at = _open_key_at(rest)
        if key_at is not None:
            rest = rest[:key_at] + SECRET_MASK
        return self._redact(rest) if rest else ""

    def _safe_cut(self) -> int:
        cut = self._line_cut()
        # A block whose END line is still pending is never shown in part.
        key_at = _open_key_at(self._pending[:cut])
        return cut if key_at is None else key_at

    def _line_cut(self) -> int:
        pending = self._pending
        key_at = _open_key_at(pending)
        limit = len(pending) if key_at is None else key_at
        newline = pending.rfind("\n", 0, limit)
        if newline >= 0:
            return newline + 1
        if key_at is not None or len(pending) <= self._hold:
            return 0
        # A line longer than the hold: cut at whitespace before the held tail; no token holds whitespace.
        boundary = len(pending) - self._hold
        # A cut right after "Bearer" would part the redact-only shape "Bearer <token>" from its token.
        spaces = [m.end() for m in _SPACE.finditer(pending, 0, boundary) if not _BEARER_END.search(pending, 0, m.start())]
        if spaces:
            return spaces[-1]
        # One unbroken word: held until it ends, up to four holds, so a long token is not split.
        return boundary if len(pending) > 4 * self._hold else 0


__all__ = ["SECRET_MASK", "StreamRedactor", "shared_redactor"]
