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
# The inputs CodeQL reported each regex slow on, at a length the regexes could take
# seconds over; each matcher must finish well inside a budget that only linear work meets.
_BUDGET_SECONDS = 0.5
_N = 200_000


@pytest.mark.parametrize(("name", "run"), [
    ("email", lambda: is_email_shaped("!@!." + "!." * _N + " ")),
    ("version", lambda: _is_version("0" * _N + "x")),
    ("range separators", lambda: _separators("1" + " " * _N + "x", _RANGE_WORDS, _RANGE_DASHES)),
    ("and separators", lambda: _separators(("a" + " " * 50) * (_N // 50), _AND_WORDS)),
])
def test_the_matchers_take_linear_time_on_the_inputs_that_slowed_the_regexes(name: str, run) -> None:
    started = time.perf_counter()
    run()
    assert time.perf_counter() - started < _BUDGET_SECONDS, name
