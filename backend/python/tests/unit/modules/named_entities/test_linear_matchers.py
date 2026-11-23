"""The named-entity normalizers' email, version and date-separator matchers.

Each replaced a regex that backtracks on input crafted for it (CodeQL
py/polynomial-redos). The regexes are kept here as the specification: every
replacement must accept, or split at, exactly what its regex did, on random
text weighted towards the characters each pattern turns on, and on the inputs
that made the regex slow, which must now finish in linear time.
"""

from __future__ import annotations

import re
import time

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import app.edition_config  # noqa: F401  (the normalizers import the edition seams)
from app.modules.named_entities.domain.kinds import EntityKind
from app.modules.named_entities.guardrails import name_rejection
from app.modules.named_entities.normalizers.contacts import (
    is_email_shaped,
    normalize_contact,
)
from app.modules.named_entities.normalizers.dates import (
    _AND_WORDS,
    _RANGE_DASHES,
    _RANGE_WORDS,
    _separators,
)
from app.modules.named_entities.normalizers.money import _is_version

# The regexes the matchers replaced, verbatim.
OLD_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
OLD_GUARDRAILS_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
OLD_VERSION = re.compile(r"v?\d+(?:\.\d+)+", re.IGNORECASE)
OLD_RANGE_SEPARATOR = re.compile(r"(?<!\s)(?:\s*[–—‒/-]\s*|\s+(?:to|through|thru|until|till)\s+)", re.IGNORECASE)
OLD_AND = re.compile(r"(?<!\s)\s+and\s+", re.IGNORECASE)

# Characters each pattern turns on, and their look-alikes: Unicode spaces and
# digits, case-folding letters ("İ", "ı", "K"), the dashes, a newline at the end.
_EMAILISH = st.sampled_from(list("ab.@ \t\n  x-_+1"))
_VERSIONISH = st.sampled_from(list("0123456789.vV٣० xa"))
_RANGEISH = st.one_of(
    st.sampled_from(list(" \t\n  –—‒/-x1")),
    st.sampled_from(["to", "TO", "through", "thru", "until", "UNTIL", "untİl", "tıll", "till", "and", "AND", "an", "tox"]),
)


def _text(alphabet: st.SearchStrategy[str], max_size: int = 40) -> st.SearchStrategy[str]:
    return st.lists(alphabet, max_size=max_size).map("".join)


def _spans(pattern: re.Pattern[str], text: str) -> list[tuple[int, int]]:
    return [m.span() for m in pattern.finditer(text)]


# ---- equivalence ------------------------------------------------------------------
@settings(max_examples=3000, deadline=None)
@given(st.one_of(_text(_EMAILISH), st.text(max_size=30)))
def test_the_email_matcher_accepts_what_the_regex_accepted(text: str) -> None:
    assert is_email_shaped(text) == bool(OLD_EMAIL.match(text)) == bool(OLD_GUARDRAILS_EMAIL.match(text)), repr(text)


@settings(max_examples=3000, deadline=None)
@given(st.one_of(_text(_VERSIONISH), st.text(max_size=30)))
def test_the_version_check_accepts_what_the_regex_accepted(text: str) -> None:
    assert _is_version(text) == bool(OLD_VERSION.fullmatch(text)), repr(text)


@settings(max_examples=3000, deadline=None)
@given(st.one_of(_text(_RANGEISH), st.text(max_size=30)))
def test_the_range_separators_are_where_the_regex_found_them(text: str) -> None:
    assert _separators(text, _RANGE_WORDS, _RANGE_DASHES) == _spans(OLD_RANGE_SEPARATOR, text), repr(text)


@settings(max_examples=3000, deadline=None)
@given(st.one_of(_text(_RANGEISH), st.text(max_size=30)))
def test_the_and_separators_are_where_the_regex_found_them(text: str) -> None:
    assert _separators(text, _AND_WORDS) == _spans(OLD_AND, text), repr(text)


@pytest.mark.parametrize("text", [
    "a@b.c", "a@b.c\n", "a@b.c\n\n", "a@.c", "a@b.", "a@bc", "@b.c", "a@b@c.d", "a b@c.d", "a@b.c ", "a@b..c",
    "jane.doe@example.co.uk", "a@b.c ", "",
])
def test_the_email_edges(text: str) -> None:
    assert is_email_shaped(text) == bool(OLD_EMAIL.match(text)), repr(text)


@pytest.mark.parametrize("text", [
    "1.2", "v1.2", "V10.0.3", "vv1.2", "Vv1.2", "vV1.2", "1", "1.", ".1", "1..2", "v", "v.1", "1.2v", "٣.٤", "1.2\n", "x1.2", "",
])
def test_the_version_edges(text: str) -> None:
    assert _is_version(text) == bool(OLD_VERSION.fullmatch(text)), repr(text)


@pytest.mark.parametrize("text", [
    "May 20–22, 2025", "Jan - Mar 2025", "2024-01-01 to 2024-02-01", "1 until 2", "1 UNTİL 2", "1 tıll 2",
    "1  through   2", "1 thru2", "1 tomorrow", "-1", " - 1", "a/b", "a /", "1 to", "1 to 2", "\n\n-\n",
])
def test_the_separator_edges(text: str) -> None:
    assert _separators(text, _RANGE_WORDS, _RANGE_DASHES) == _spans(OLD_RANGE_SEPARATOR, text), repr(text)
    assert _separators(text, _AND_WORDS) == _spans(OLD_AND, text), repr(text)


def test_the_callers_answer_as_before() -> None:
    assert normalize_contact(EntityKind.EMAIL, " Jane.Doe@Example.com ").canonical == "jane.doe@example.com"
    assert normalize_contact(EntityKind.EMAIL, "jane@localhost") is None
    assert name_rejection(EntityKind.ORGANIZATION, "jane.doe@example.com") == "identifier"
    assert name_rejection(EntityKind.ORGANIZATION, "Acme Robotics") is None


# ---- linear time --------------------------------------------------------------------
# The inputs CodeQL reported each regex slow on, at two lengths 16x apart. Linear work
# takes at most about 16x as long on the longer one; a backtracking matcher about 256x.
# The bound is on that growth, not on the wall clock, so a slow runner passes. Each
# length is timed in this thread's CPU time, best of several rounds, so time spent
# preempted on a busy runner does not count.
_SHORT, _LONG = 250, 4_000
_ROUNDS = 10
_MAX_GROWTH = 64
# A clock coarser than the short run reads it as 0; any backtracking matcher takes
# far longer than 64x this on the long input.
_FLOOR_NS = 10_000


@pytest.mark.parametrize(("run", "text_of"), [
    pytest.param(is_email_shaped, lambda n: "!@!." + "!." * n + " ", id="email"),
    pytest.param(_is_version, lambda n: "0" * n + "x", id="version"),
    pytest.param(lambda text: _separators(text, _RANGE_WORDS, _RANGE_DASHES), lambda n: "1" + " " * n + "x", id="range separators"),
    # One long run of spaces: a matcher that rescans a run from each of its spaces is quadratic in its length.
    pytest.param(lambda text: _separators(text, _AND_WORDS), lambda n: "a" + " " * n + "x", id="and separators"),
])
def test_the_matchers_take_linear_time_on_the_inputs_that_slowed_the_regexes(run, text_of) -> None:
    texts = (text_of(_SHORT), text_of(_LONG))
    best = [float("inf"), float("inf")]
    for _ in range(_ROUNDS):
        for i, text in enumerate(texts):
            started = time.thread_time_ns()
            run(text)
            best[i] = min(best[i], time.thread_time_ns() - started)
    short_ns, long_ns = best
    assert long_ns <= _MAX_GROWTH * max(short_ns, _FLOOR_NS), f"{_LONG // _SHORT}x the input took {long_ns / max(short_ns, 1):.0f}x the time ({short_ns} ns -> {long_ns} ns)"
