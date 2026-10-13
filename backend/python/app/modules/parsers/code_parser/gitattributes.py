"""``linguist-generated`` / ``linguist-vendored`` rules from a repo's ``.gitattributes``.

Repos mark checked-in bundles and vendored trees this way for GitHub's own
language stats, so honouring it catches what the path-based ignore list in
``file_role`` cannot guess (``public/app.js``, ``third-party-ish/`` names).

Pure functions -- the caller fetches the file.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["LinguistExclusions", "parse_linguist_exclusions"]

_ATTRS = ("linguist-generated", "linguist-vendored")


def _attr_state(token: str) -> tuple[str, bool] | None:
    """``(attr, excluded)`` for one attribute token, or None if it is another attribute."""
    if token.startswith(("-", "!")):
        name, value = token[1:], False
    elif "=" in token:
        name, raw = token.split("=", 1)
        value = raw.strip().lower() in ("true", "1", "yes", "set")
    else:
        name, value = token, True
    return (name, value) if name in _ATTRS else None


def _pattern_regex(pattern: str) -> re.Pattern[str]:
    """gitattributes glob -> regex over a repo-relative path.

    A pattern without a slash matches the basename at any depth; one with a
    slash is anchored at the repo root.
    """
    anchored = "/" in pattern.rstrip("/")
    pattern = pattern.lstrip("/")
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("/**", i) and i + 3 == len(pattern):
            out.append("/.*")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        elif pattern[i] == "[":
            end = pattern.find("]", i + 1)
            if end == -1:
                out.append(re.escape(pattern[i]))
                i += 1
            else:
                body = pattern[i + 1:end].replace("\\", "\\\\")
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append(f"[{body}]")
                i = end + 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    prefix = "" if anchored else "(?:.*/)?"
    return re.compile(f"^{prefix}{''.join(out)}$")


@dataclass(frozen=True)
class LinguistExclusions:
    rules: tuple[tuple[re.Pattern[str], tuple[tuple[str, bool], ...]], ...] = ()

    def excludes(self, file_path: str) -> bool:
        """True when the last matching rules leave the file generated or vendored.

        As in git, a later line overrides an earlier one for the same attribute.
        """
        state: dict[str, bool] = {}
        for regex, attrs in self.rules:
            if regex.match(file_path):
                state.update(attrs)
        return any(state.values())

    def __bool__(self) -> bool:
        return bool(self.rules)


def parse_linguist_exclusions(text: str) -> LinguistExclusions:
    rules = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        pattern, *tokens = line.split()
        # Quoted patterns and macros (`[attr]name`) are rare in linguist rules.
        if pattern.startswith(('"', "[attr]")):
            continue
        attrs = tuple(a for a in (_attr_state(t) for t in tokens) if a is not None)
        if attrs:
            rules.append((_pattern_regex(pattern), attrs))
    return LinguistExclusions(tuple(rules))
