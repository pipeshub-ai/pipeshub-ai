"""``linguist-generated`` / ``linguist-vendored`` rules from a repo's ``.gitattributes``.

Repos mark checked-in bundles and vendored trees this way for GitHub's own
language stats, so honouring it catches what the path-based ignore list in
``file_role`` cannot guess (``public/app.js``, ``third-party-ish/`` names).

Pure functions -- the caller fetches the file.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["LinguistExclusions", "glob_regex", "parse_linguist_exclusions"]

_ATTRS = ("linguist-generated", "linguist-vendored")
# `**/` segments still backtrack over the path depth, so a rule may hold a few.
_MAX_DOUBLE_STARS = 3
_NEVER = re.compile(r"(?!)")


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


def glob_regex(pattern: str) -> re.Pattern[str]:
    """gitattributes glob -> regex over a repo-relative path.

    A pattern without a slash matches the basename at any depth; one with a
    slash is anchored at the repo root.
    """
    anchored = "/" in pattern.rstrip("/")
    pattern = pattern.lstrip("/")
    out: list[str] = []
    stars = 0
    i = 0
    while i < len(pattern):
        whole = i == 0 or pattern[i - 1] == "/"
        if whole and pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            stars += 1
            i += 3
        elif pattern.startswith("/**", i) and i + 3 == len(pattern):
            out.append("/.*")
            i += 3
        elif whole and pattern.startswith("**", i) and i + 2 == len(pattern):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            # As in git, any other run of `*` is one `*`. Each `*` takes the
            # earliest match of what follows it up to the next `*` (atomic, as
            # fnmatch does), which is enough within one segment and keeps the
            # match linear instead of backtracking over every split.
            while i < len(pattern) and pattern[i] == "*":
                i += 1
            out.append("\0")
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
                    body = "^/" + body[1:]  # as in git, a class never matches `/`
                out.append(f"[{body}]")
                i = end + 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    if stars > _MAX_DOUBLE_STARS:
        return _NEVER
    chunks = "".join(out).split("\0")
    # Every `*` but the last is atomic and lazy; the last is anchored by `$`.
    # A chunk holding `**/` has no single earliest match, so it backtracks.
    middle = "".join(f"[^/]*{c}" if "(?:.*/)?" in c else f"(?>[^/]*?{c})" for c in chunks[1:-1])
    tail = f"[^/]*{chunks[-1]}" if len(chunks) > 1 else ""
    joined = chunks[0] + middle + tail
    prefix = "" if anchored else "(?:.*/)?"
    return re.compile(f"^{prefix}{joined}$")


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

    def forces_in(self, file_path: str) -> bool:
        """True when the matching rules explicitly mark the file not generated and
        not vendored (`-linguist-generated`): the repo says it is source, which
        overrides the path heuristics (P-7)."""
        state: dict[str, bool] = {}
        for regex, attrs in self.rules:
            if regex.match(file_path):
                state.update(attrs)
        return bool(state) and not any(state.values())

    def may_force_in_under(self, folder_path: str) -> bool:
        """Whether some file under ``folder_path`` could be forced in, so the
        folder must be walked even if the path heuristics would prune it."""
        folder = folder_path.strip("/")
        inside = f"^{re.escape(folder + '/')}"
        probe = f"{folder}/__probe__"
        for regex, attrs in self.rules:
            if any(v for _a, v in attrs):
                continue
            # Unanchored (`*.js`, `**/x`), rooted at or below this folder, or
            # matching something directly in it.
            if (regex.pattern.startswith("^(?:.*/)?") or regex.pattern.startswith(inside)
                    or regex.match(probe)):
                return True
        return False

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
            rules.append((glob_regex(pattern), attrs))
    return LinguistExclusions(tuple(rules))
