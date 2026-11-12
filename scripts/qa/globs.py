"""Path globs with `**` that, unlike fnmatch, never let `*` cross a `/`."""

from __future__ import annotations

import functools
import re
from collections.abc import Iterable


@functools.lru_cache(maxsize=4096)
def compile_glob(pattern: str) -> re.Pattern[str]:
    out = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    # A pattern ending in "/" matches everything under that directory.
    if pattern.endswith("/"):
        out.append(".*")
    return re.compile("^" + "".join(out) + "$")


def match(path: str, pattern: str) -> bool:
    return compile_glob(pattern).match(path) is not None


def match_any(path: str, patterns: Iterable[str]) -> bool:
    return any(match(path, p) for p in patterns)
