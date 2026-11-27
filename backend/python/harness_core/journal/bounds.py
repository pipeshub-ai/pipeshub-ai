"""How large a journal write that carries tool output may be (WP-62).

Node refuses a content object over ``CONTENT_MAX_BYTES`` (``run-content.ts``) with 413 ``CONTENT_TOO_LARGE``, and a
refused write on a run's completion failed the run. An engine child that had fetched two 4.2 MB GitHub tree
listings was cut short; its result for its parent carried every web block it had read (8.7 MB once JSON-encoded),
the put was refused, and the run failed with its 25 workspace revisions unmerged. Tool output is untrusted in size, so
every write that carries it is bounded here, well under the store's limit, before it is made: a tool's result as
journaled, a child's result document, a run's final answer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from harness_core.contracts import canonical_json

if TYPE_CHECKING:
    from pydantic import JsonValue

# Node's limit on one content object (``CONTENT_MAX_BYTES`` in run-content.ts); C-5 does not generate it.
CONTENT_MAX_BYTES: Final = 8 * 1024 * 1024
# One tool result as the journal keeps it. The model never reads more than a slice of it (the context compiler caps
# results far lower); an engine reads the full result from the gateway's answer or its shelf file.
TOOL_RESULT_MAX_BYTES: Final = 2 * 1024 * 1024
# What a child hands its parent: its answer, the files it published and the sources its answer cites.
CHILD_RESULT_MAX_BYTES: Final = 2 * 1024 * 1024
# A run's final answer.
ANSWER_MAX_BYTES: Final = 1024 * 1024
# The child result keys a parent cannot do without; anything else is dropped first when the document is too large.
_CORE_CHILD_KEYS: Final = frozenset({"output", "success", "confidence", "needs_input", "truncatedBy", "stopped_early"})


def shortened_note(dropped: int) -> str:
    return f"\n\n[… {dropped:,} more characters were not kept: this result was larger than a run's record holds]"


def bounded_text(text: str, max_bytes: int) -> str:
    """``text`` if it fits in ``max_bytes`` of UTF-8 (JSON-escaped, as the journal stores it); else its head and a note
    saying how much was cut."""
    if len(canonical_json(text)) <= max_bytes:
        return text
    # JSON escaping can make a character up to six bytes (a control character): search for the longest head that fits.
    budget = max_bytes - 256
    low, high = 0, len(text)
    while high - low > 64:
        mid = (low + high) // 2
        if len(canonical_json(text[:mid])) <= budget:
            low = mid
        else:
            high = mid
    return text[:low] + shortened_note(len(text) - low)


def bounded_content(content: JsonValue, max_bytes: int = TOOL_RESULT_MAX_BYTES) -> JsonValue:
    """A tool result's content within ``max_bytes``: text is shortened with a note; anything else that is too large is
    kept as its JSON text, shortened."""
    if len(canonical_json(content)) <= max_bytes:
        return content
    if isinstance(content, str):
        return bounded_text(content, max_bytes)
    return bounded_text(canonical_json(content).decode("utf-8"), max_bytes)


def bounded_child_document(
    document: dict[str, JsonValue], max_bytes: int = CHILD_RESULT_MAX_BYTES
) -> tuple[dict[str, JsonValue], bool]:
    """``document`` within ``max_bytes``, and whether anything was dropped: first what is not its answer (the
    sources it cites, the files it lists, kept by its host only as extras), then the answer's tail."""
    if len(canonical_json(document)) <= max_bytes:
        return document, False
    core = {key: value for key, value in document.items() if key in _CORE_CHILD_KEYS}
    files = document.get("files")
    if files is not None and len(canonical_json({**core, "files": files})) <= max_bytes:
        core["files"] = files
    if len(canonical_json(core)) > max_bytes and isinstance(core.get("output"), str):
        rest = len(canonical_json({k: v for k, v in core.items() if k != "output"}))
        core["output"] = bounded_text(str(core["output"]), max(max_bytes - rest - 1024, 1024))
    return core, True


__all__ = [
    "ANSWER_MAX_BYTES",
    "CHILD_RESULT_MAX_BYTES",
    "CONTENT_MAX_BYTES",
    "TOOL_RESULT_MAX_BYTES",
    "bounded_child_document",
    "bounded_content",
    "bounded_text",
    "shortened_note",
]
