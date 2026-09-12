"""Safety rails for cross-file symbol resolution.

Every rule here resolves ambiguity toward emitting *nothing*. Dropping any of
them produces god nodes -- a single popular name absorbing hundreds of false
edges -- which degrades the graph far more than the missing edges cost.
"""
from __future__ import annotations

import posixpath

__all__ = [
    "CALLABLE_KINDS",
    "TYPE_KINDS",
    "is_test_path",
    "lang_family",
    "path_proximity_winner",
    "prefer_non_test",
]

CALLABLE_KINDS = frozenset({"function", "method", "constructor"})
TYPE_KINDS = frozenset({"class", "interface", "enum", "type_alias"})

# JS and TS share a module system and resolve against each other; Python does
# not. A Python `validate()` must never bind to a TypeScript `validate()`.
_FAMILY_BY_LANGUAGE = {
    "python": "py",
    "javascript": "js",
    "typescript": "js",
    "tsx": "js",
}

_FAMILY_BY_EXTENSION = {
    "py": "py", "pyi": "py",
    "js": "js", "jsx": "js", "mjs": "js", "cjs": "js",
    "ts": "js", "tsx": "js", "mts": "js", "cts": "js",
}

_TEST_SEGMENTS = frozenset({
    "test", "tests", "spec", "specs", "__tests__", "__mocks__",
    "testing", "e2e", "integration-tests", "integration_tests",
})


def lang_family(language: str | None, file_path: str | None = None) -> str | None:
    if language:
        family = _FAMILY_BY_LANGUAGE.get(language.lower())
        if family:
            return family
    if file_path:
        _, _, ext = file_path.rpartition(".")
        return _FAMILY_BY_EXTENSION.get(ext.lower())
    return None


def is_test_path(file_path: str | None) -> bool:
    """Segment-aware, never substring.

    A substring check classifies `contest/`, `latest/` and `protest.py` as
    tests.
    """
    if not file_path:
        return False
    segments = [s for s in file_path.replace("\\", "/").split("/") if s]
    if any(seg.lower() in _TEST_SEGMENTS for seg in segments[:-1]):
        return True
    name = segments[-1].lower() if segments else ""
    return (
        name.startswith("test_")
        or name.endswith(("_test.py", "_test.go", "_test.ts", "_test.js", "_spec.rb"))
        or ".test." in name
        or ".spec." in name
        or name == "conftest.py"
    )


def prefer_non_test(candidate_ids: list[str], files_by_id: dict[str, str],
                    call_site_file: str | None) -> list[str]:
    """Production code calling a helper means the production definition.

    Only applied when the call site is itself not a test -- inside a test file,
    a test helper is the right target.
    """
    if len(candidate_ids) <= 1 or is_test_path(call_site_file):
        return candidate_ids
    non_test = [cid for cid in candidate_ids if not is_test_path(files_by_id.get(cid))]
    return non_test or candidate_ids


def path_proximity_winner(candidate_ids: list[str], files_by_id: dict[str, str],
                          call_site_file: str | None) -> list[str]:
    """Same file, then same directory, then longest shared path prefix."""
    if len(candidate_ids) <= 1 or not call_site_file:
        return candidate_ids

    same_file = [cid for cid in candidate_ids if files_by_id.get(cid) == call_site_file]
    if len(same_file) == 1:
        return same_file
    if same_file:
        return same_file

    call_dir = posixpath.dirname(call_site_file)
    same_dir = [cid for cid in candidate_ids if posixpath.dirname(files_by_id.get(cid, "")) == call_dir]
    if len(same_dir) == 1:
        return same_dir

    call_parts = call_site_file.split("/")
    scored: dict[str, int] = {}
    for cid in candidate_ids:
        parts = (files_by_id.get(cid) or "").split("/")
        shared = 0
        for a, b in zip(call_parts, parts):
            if a != b:
                break
            shared += 1
        scored[cid] = shared

    best = max(scored.values(), default=0)
    winners = [cid for cid, score in scored.items() if score == best]
    return winners if len(winners) == 1 else candidate_ids
