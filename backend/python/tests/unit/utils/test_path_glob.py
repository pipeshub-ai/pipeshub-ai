"""``app.utils.path_glob``: the glob compiler moved out of gitattributes, and the folder-name matcher.

``glob_regex`` must compile every pattern to the same regex text as before the
move (gitattributes inspects ``.pattern``), and ``name_regex`` must match one
folder name as fnmatch would, with no classes, in linear time.
"""

from __future__ import annotations

import fnmatch
import itertools
import re
import time
import warnings
from typing import TYPE_CHECKING

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.modules.parsers.code_parser import gitattributes
from app.utils.path_glob import glob_regex, name_regex

if TYPE_CHECKING:
    from collections.abc import Callable

# Main's glob_regex as it was before the move (gitattributes.py at e29bf542f), frozen.
_MAIN_MAX_DOUBLE_STARS = 3
_MAIN_NEVER = re.compile(r"(?!)")


def _main_glob_regex(pattern: str) -> re.Pattern[str]:
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
    if stars > _MAIN_MAX_DOUBLE_STARS:
        return _MAIN_NEVER
    chunks = "".join(out).split("\0")
    # Every `*` but the last is atomic and lazy; the last is anchored by `$`.
    # A chunk holding `**/` has no single earliest match, so it backtracks.
    middle = "".join(f"[^/]*{c}" if "(?:.*/)?" in c else f"(?>[^/]*?{c})" for c in chunks[1:-1])
    tail = f"[^/]*{chunks[-1]}" if len(chunks) > 1 else ""
    joined = chunks[0] + middle + tail
    prefix = "" if anchored else "(?:.*/)?"
    return re.compile(f"^{prefix}{joined}$")


# Every character gitattributes globs treat specially, plus plain ones.
GLOB_ALPHABET = "*?[]!/\\.ab"


def test_gitattributes_still_exports_the_same_glob_regex() -> None:
    assert gitattributes.glob_regex is glob_regex
    assert "glob_regex" in gitattributes.__all__


def _compiled(compile_glob: Callable[[str], re.Pattern[str]], pattern: str) -> str:
    """The regex text, or the error: an empty class (``[]``) does not compile, before the move or after."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)  # `[[` reads as a possible nested set
            return compile_glob(pattern).pattern
    except re.error as e:
        return f"re.error: {e}"


def test_every_short_glob_compiles_to_mains_regex() -> None:
    for size in range(5):
        for chars in itertools.product(GLOB_ALPHABET, repeat=size):
            pattern = "".join(chars)
            assert _compiled(glob_regex, pattern) == _compiled(_main_glob_regex, pattern), pattern


@settings(max_examples=2000, deadline=None)
@given(st.text(alphabet=GLOB_ALPHABET, max_size=24))
def test_any_glob_compiles_to_mains_regex(pattern: str) -> None:
    assert _compiled(glob_regex, pattern) == _compiled(_main_glob_regex, pattern)


@pytest.mark.parametrize(
    ("segment", "escapes", "name", "matches"),
    [
        ("quotes", False, "quotes", True),
        ("quotes", False, "quotes\n", False),  # fullmatch: no match before a trailing newline
        ("quotes", False, "quotesx", False),
        ("quotes", False, "Quotes", False),  # case is the caller's normalisation
        ("q?", False, "q1", True),
        ("q?", False, "q12", False),
        ("q?", False, "q", False),
        ("q?", False, "q\n", True),
        ("rep*", False, "reports", True),
        ("rep*", False, "rep", True),
        ("rep*", False, "xreports", False),
        ("*", False, ".hidden", True),
        ("a*b", False, "axxb", True),
        ("a*b", False, "a*b", True),
        ("a*b", False, "ab", True),
        ("a*b", False, "axxbc", False),
        ("a**b", False, "axb", True),  # a run of `*` is one `*`
        ("*a*b*", False, "xaybz", True),
        ("*a*b*", False, "xbya", False),
        ("a.b", False, "axb", False),
        ("Project [2024]", False, "Project [2024]", True),
        ("Project [2024]", False, "Project 2", False),  # no classes
        ("Project [2024]", True, "Project [2024]", True),
        ("[!a]", False, "b", False),
        ("{a,b}", False, "a", False),
        ("a\\*b", True, "a*b", True),
        ("a\\*b", True, "axxb", False),
        ("a\\?", True, "a?", True),
        ("a\\?", True, "ab", False),
        ("a\\b", True, "a\\b", True),  # a backslash before anything else is a backslash
        ("a\\", True, "a\\", True),
        ("a\\*b", False, "a\\xxb", True),  # without escapes a backslash is a literal
        ("a\\*b", False, "a*b", False),
        ("caf\u00e9", False, "cafe\u0301", False),  # NFC is the caller's normalisation
        ("a\x00*", False, "a\x00b", True),
    ],
)
def test_name_regex(segment: str, escapes: bool, name: str, matches: bool) -> None:
    assert bool(name_regex(segment, escapes=escapes).fullmatch(name)) is matches


def _as_fnmatch(segment: str, *, escapes: bool) -> str:
    """The same segment for fnmatch, which knows classes: brackets and escapes become one-character classes."""
    out, i = [], 0
    while i < len(segment):
        if escapes and segment[i] == "\\" and segment[i + 1:i + 2] in ("*", "?"):
            out.append(f"[{segment[i + 1]}]")
            i += 2
            continue
        out.append("[[]" if segment[i] == "[" else segment[i])
        i += 1
    return "".join(out)


NAME_ALPHABET = "ab*?\\[]!.\n "


@settings(max_examples=3000, deadline=None)
@given(
    st.text(alphabet=NAME_ALPHABET, max_size=10),
    st.text(alphabet=NAME_ALPHABET.replace("*", "").replace("?", "") + "*?", max_size=12),
    st.booleans(),
)
def test_name_regex_matches_as_fnmatch_does(segment: str, name: str, escapes: bool) -> None:
    expected = fnmatch.fnmatchcase(name, _as_fnmatch(segment, escapes=escapes))
    assert bool(name_regex(segment, escapes=escapes).fullmatch(name)) is expected


def test_a_sixteen_star_name_fails_in_linear_time() -> None:
    # Backtracking over every split of 20,000 characters between 16 stars would never finish.
    regex = name_regex("*a" * 15 + "*b", escapes=False)
    started = time.perf_counter()
    assert regex.fullmatch("a" * 20_000) is None
    assert regex.fullmatch("a" * 20_000 + "b") is not None
    assert time.perf_counter() - started < 1.0
