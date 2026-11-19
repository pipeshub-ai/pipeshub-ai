"""Glob patterns compiled to regular expressions, for repo paths and for single folder names.

``glob_regex`` reads a gitattributes or exclude glob over a whole path.
``name_regex`` reads one segment of a folder pattern, to be matched against one
folder name with ``fullmatch``. Both turn ``*`` into the same atomic chain, so a
name with many ``*`` still matches in linear time.
"""

from __future__ import annotations

import re

__all__ = ["glob_regex", "name_regex"]

# `**/` segments still backtrack over the path depth, so a rule may hold a few.
_MAX_DOUBLE_STARS = 3
_NEVER = re.compile(r"(?!)")


def _star_chain(chunks: list[str]) -> str:
    """The regex text for ``chunks`` joined by ``*``, each ``*`` within one path segment.

    Every `*` but the last is atomic and lazy; the last is anchored by the
    caller (`$` or `fullmatch`). A chunk holding `**/` has no single earliest
    match, so it backtracks.
    """
    middle = "".join(f"[^/]*{c}" if "(?:.*/)?" in c else f"(?>[^/]*?{c})" for c in chunks[1:-1])
    tail = f"[^/]*{chunks[-1]}" if len(chunks) > 1 else ""
    return chunks[0] + middle + tail


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
    joined = _star_chain("".join(out).split("\0"))
    prefix = "" if anchored else "(?:.*/)?"
    return re.compile(f"^{prefix}{joined}$")


def name_regex(segment: str, *, escapes: bool) -> re.Pattern[str]:
    """One folder-name pattern -> regex for ``fullmatch`` against one name.

    ``*`` matches any run of characters and ``?`` exactly one; a run of ``*``
    is one ``*``. Every other character is literal: there are no classes. With
    ``escapes``, ``\\*`` and ``\\?`` are a literal ``*`` and ``?``, and any other
    backslash stays a backslash. Use ``fullmatch``: ``$`` would also match
    before a trailing newline, which an object key may end with.
    """
    # Chunks are collected directly, not split on a sentinel: a name may hold any character.
    chunks: list[list[str]] = [[]]
    i = 0
    while i < len(segment):
        char = segment[i]
        if escapes and char == "\\" and segment[i + 1:i + 2] in ("*", "?"):
            chunks[-1].append(re.escape(segment[i + 1]))
            i += 2
        elif char == "*":
            while i < len(segment) and segment[i] == "*":
                i += 1
            chunks.append([])
        elif char == "?":
            chunks[-1].append("[^/]")
            i += 1
        else:
            chunks[-1].append(re.escape(char))
            i += 1
    return re.compile(_star_chain(["".join(c) for c in chunks]))
