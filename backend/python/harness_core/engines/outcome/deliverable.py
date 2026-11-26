"""The run's deliverable, resolved once (ADR-RUN-01 D1): a file the request names or asks for, or an answer in chat.

Admission pins it in the run spec (PipesHub: ``x.pipeshub.deliverable``; Node resolves it with the same rules, held
to ``contracts/harness/vectors/deliverable.json``). A run admitted before the pin resolves it once when it loads,
from its own request. Nothing reads the request for a file name after that.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.contracts.generated.run_spec import RunSpec

DeliverableKind = Literal["file", "answer"]
DELIVERABLE_KEY = "deliverable"
TEXT_TYPES: Mapping[str, str] = {".md": "text/markdown", ".markdown": "text/markdown", ".txt": "text/plain"}
PARTIAL_FALLBACK = "partial-result"
# A file name the request spells out: a word with a known document extension.
# ASCII: Node resolves with the same expressions, whose word characters are ASCII.
_FILE_NAME = re.compile(
    r"(?<![\w/.-])([A-Za-z0-9][\w.-]{0,100}\.(?:md|markdown|txt|csv|json|docx|xlsx|pdf|pptx|html))\b", re.ASCII
)
# "make a file", "save it as a markdown document", "write a report file": the request wants a file.
_ASKS_FOR_FILE = re.compile(
    r"\b(?:make|create|write|save|produce|generate|give|prepare|build|export|draft|put)\b[^.\n]{0,80}?"
    r"\b(?:file|document|doc|markdown|\.md|spreadsheet|csv|download(?:able)?)\b",
    re.IGNORECASE | re.ASCII,
)
_SLUG = re.compile(r"[^A-Za-z0-9]+")
# The longest name ``_FILE_NAME`` matches: one character, 100 more, the dot and the longest extension.
MAX_NAME_CHARS = 110


@dataclass(frozen=True, slots=True)
class Deliverable:
    """What the run owes the person: ``file`` (``name`` as the request spells it, ``None`` when it asks for a file
    without naming one) or ``answer`` (a reply in chat)."""

    kind: DeliverableKind
    name: str | None = None

    @property
    def is_file(self) -> bool:
        return self.kind == "file"

    def partial_name(self, fallback: str | None = None) -> str | None:
        """The text file a partial result of this deliverable is saved as: the named file when it is text, else
        ``<stem>-partial.md``, else ``<fallback>.md``; ``None`` for an answer."""
        if not self.is_file:
            return None
        if self.name is not None:
            stem, _, suffix = self.name.rpartition(".")
            return self.name if f".{suffix.lower()}" in TEXT_TYPES else f"{stem or self.name}-partial.md"
        slug = _SLUG.sub("-", (fallback or PARTIAL_FALLBACK).strip()).strip("-").lower()[:60]
        return f"{slug or PARTIAL_FALLBACK}.md"

    def owns(self, name_or_path: str, *, outputs_dir: str = "out") -> str | None:
        """The workspace path of this deliverable when ``name_or_path`` names it (``report.md``, ``out/report.md``,
        ``/workspace/out/report.md``); ``None`` otherwise. The seam a tool that saves files asks before it creates an
        artifact: the run's own deliverable is published by the harness, so a save of it is a new version of that
        artifact, not a second one (the engine saved the published report.md again)."""
        if not self.is_file or self.name is None:
            return None
        return f"{outputs_dir}/{self.name}" if name_or_path.rsplit("/", 1)[-1] == self.name else None

    def wire(self) -> dict[str, JsonValue]:
        return {"kind": self.kind, **({"name": self.name} if self.name is not None else {})}

    @classmethod
    def from_wire(cls, value: object) -> Deliverable | None:
        """The pinned value; ``None`` when ``value`` is not one (the run then resolves its own)."""
        if not isinstance(value, dict):
            return None
        data = cast("dict[str, object]", value)
        kind, name = data.get("kind"), data.get("name")
        if kind not in ("file", "answer"):
            return None
        if name is not None and (kind != "file" or not isinstance(name, str) or not name or len(name) > MAX_NAME_CHARS):
            return None
        return cls(kind, name if isinstance(name, str) else None)


ANSWER = Deliverable("answer")


def resolve_deliverable(request: str) -> Deliverable:
    """The deliverable a request asks for: the file it names, a file it asks for without naming, or an answer."""
    named = _FILE_NAME.search(request)
    if named is not None:
        return Deliverable("file", named.group(1).rsplit("/", 1)[-1])
    return Deliverable("file") if _ASKS_FOR_FILE.search(request) else ANSWER


def pinned_deliverable(spec: RunSpec, request: str) -> Deliverable:
    """The deliverable admission pinned in ``spec``; a run admitted before the pin resolves it from ``request``."""
    extension = (spec.extensions or {}).get("x.pipeshub")
    pinned = Deliverable.from_wire(extension.get(DELIVERABLE_KEY)) if isinstance(extension, dict) else None
    return pinned if pinned is not None else resolve_deliverable(request)


__all__ = [
    "ANSWER",
    "DELIVERABLE_KEY",
    "PARTIAL_FALLBACK",
    "TEXT_TYPES",
    "Deliverable",
    "DeliverableKind",
    "pinned_deliverable",
    "resolve_deliverable",
]
