"""C-34 (QA7-20; POL-24, MEM-11, R2 F10): a default the product reads has one definition.

Four defaults were found defined two or three times: the children one run may be granted, the chat
turn's deadline, ``MAX_MEMORY_CHARS`` and the offering an install defaults to; a fifth, the events one
commit carries, was defined in Node alone while the worker had to respect it (QA22-F1). Each has one canonical
definition, CPD-3's release table (``contracts/harness/core/policy/release.json``), read everywhere
through the generated ``RELEASE_DEFAULTS``. Any other definition the scan finds fails, unless
``DUPLICATE_DEFAULTS`` lists it: CPD-3 deleted the copies QA-7 found, so the list is empty. Every listed copy
must carry the canonical value, and a listed copy that is gone fails too, so the list only shrinks.
``DOCUMENTED`` copies (the OpenAPI document, the flag matrix) must state the value and stay.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache

import pytest

from tests.cohesion.support import REPO_ROOT

NODE = "backend/nodejs/apps/src/modules/agent-harness"
OPENAPI = "backend/nodejs/apps/src/modules/api-docs/pipeshub-openapi.yaml"
RELEASE = "contracts/harness/core/policy/release.json"


@dataclass(frozen=True)
class Site:
    path: str
    pattern: str

    def values(self) -> list[str]:
        text = (REPO_ROOT / self.path).read_text(encoding="utf-8")
        return [_value(m.group(1)) for m in re.finditer(self.pattern, text)]


@dataclass(frozen=True)
class Default:
    name: str
    canonical: Site
    # A definition-shaped line in the scanned files whose path contains ``scope``; each hit must be a listed site.
    finder: str
    scope: str = ""


def _value(raw: str) -> str:
    expression = raw.replace("_", "").strip().strip("'\"")
    if re.fullmatch(r"[\d\s*]+", expression):
        product = 1
        for factor in expression.split("*"):
            product *= int(factor)
        return str(product)
    return expression


DEFAULTS = (
    Default(
        "children per run",
        Site(RELEASE, r'"maxPerRun": (\d+)'),
        r"MAX_CHILDREN_PER_RUN\s*(?::[^=\n]*)?=\s*\d",
    ),
    Default(
        "chat-turn deadline",
        Site(RELEASE, r'"deadlineMs": (\d+)'),
        r"(?i)deadline\w*\s*(?::[^=\n]*)?=\s*60 \* 60 \* 1000|\.default\(60 \* 60 \* 1000\)",
    ),
    Default(
        "MAX_MEMORY_CHARS",
        Site(RELEASE, r'"maxChars": (\d+)'),
        r"\bMAX_(?:MEMORY_)?CHARS\s*(?::[^=\n]*)?=\s*\d|content: z\.string\(\)\.min\(1\)\.max\(\d+\)|at most \d+ characters",
        scope="memor",
    ),
    Default(
        "offering",
        Site(RELEASE, r'"offering": "(\w+)"'),
        r"DEFAULT_OFFERING\s*(?::[^=\n]*)?=\s*['\"]",
    ),
    # PERF-A (QA22-F1): Node allowed 100 events a commit and the worker did not know the number.
    Default(
        "commit event limit",
        Site(RELEASE, r'"maxCommitEvents": (\d+)'),
        r"(?i)MAX_COMMIT_EVENTS\s*(?::[^=\n]*)?=\s*\d|(?:events|legacyEvents): z\.array\(\w+\)\.max\(\d",
    ),
)

DUPLICATE_DEFAULTS: dict[str, tuple[Site, ...]] = {}

# Hits of a finder that are not the default, with the reason.
NOT_COPIES: dict[str, dict[str, str]] = {
    "MAX_MEMORY_CHARS": {
        f"{NODE}/transport/memory-controller.ts": "the worker's raw proposal bound before trimming; memoryContent applies the limit",
    },
}

DOCUMENTED: dict[str, tuple[Site, ...]] = {
    "chat-turn deadline": (Site(OPENAPI, r"deadlineMs: \{ type: integer, minimum: 60000, maximum: 86400000, default: (\d+) \}"),),
    "MAX_MEMORY_CHARS": (Site(OPENAPI, r"content: \{ type: string, maxLength: (\d+) \}"),),
    "offering": (Site("contracts/harness/flags.json", r'"name": "AGENT_HARNESS_OFFERING",[^}]*?"default": "(\w+)"'),),
}

SCANNED = (
    ("backend/python/app", "*.py"),
    ("backend/python/harness_core", "*.py"),
    ("backend/python/harness_engines", "*.py"),
    ("backend/nodejs/apps/src", "*.ts"),
    ("frontend/app", "*.ts"),
    ("frontend/app", "*.tsx"),
    ("frontend/lib", "*.ts"),
)


@cache
def _sources() -> dict[str, str]:
    return {
        path.relative_to(REPO_ROOT).as_posix(): path.read_text(encoding="utf-8", errors="replace")
        for root, glob in SCANNED
        for path in (REPO_ROOT / root).rglob(glob)
        if "generated" not in path.parts and "node_modules" not in path.parts
    }


def _canonical(default: Default) -> str:
    values = default.canonical.values()
    assert len(values) == 1, f"{default.name}: the canonical definition is not found exactly once in {default.canonical.path}"
    return values[0]


@pytest.mark.parametrize("default", DEFAULTS, ids=lambda d: d.name)
def test_c34_a_default_is_defined_once_or_by_a_listed_copy(default: Default) -> None:
    listed = {default.canonical.path} | {s.path for s in DUPLICATE_DEFAULTS.get(default.name, ())}
    listed |= set(NOT_COPIES.get(default.name, {}))
    found = {path for path, text in _sources().items() if default.scope in path and re.search(default.finder, text)}
    assert found - listed == set(), f"{default.name}: a definition outside the canonical one and the listed copies"


@pytest.mark.parametrize("default", DEFAULTS, ids=lambda d: d.name)
def test_c34_every_copy_carries_the_canonical_value_and_still_exists(default: Default) -> None:
    canonical = _canonical(default)
    for site in (*DUPLICATE_DEFAULTS.get(default.name, ()), *DOCUMENTED.get(default.name, ())):
        values = site.values()
        assert values, f"{default.name}: {site.path} no longer defines it; drop it from the list"
        assert set(values) == {canonical}, f"{default.name}: {site.path} says {values}, the definition {canonical}"


def test_c34_the_lists_name_only_known_defaults() -> None:
    assert set(DUPLICATE_DEFAULTS) | set(DOCUMENTED) | set(NOT_COPIES) <= {d.name for d in DEFAULTS}
