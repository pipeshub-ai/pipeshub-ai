"""`app_is_current`: a connector's stamps may be used only while every condition holds, and anything missing or unreadable is not current."""

from __future__ import annotations

import pytest

from app.services.permissions.currency import StampCurrency, app_is_current
from app.services.permissions.scope_fields import (
    SCOPE_FIELDS_VERSION,
    set_resolved_generation,
)
from app.services.permissions.structure_generation import GENERATION_KEY, UNSAFE_KEY
from app.services.vector_db.scope_backfill import BACKFILL_KEYS as K
from app.services.vector_db.scope_backfill import DONE


@pytest.fixture(autouse=True)
def acl_generation():  # noqa: ANN201
    set_resolved_generation(3)
    yield
    set_resolved_generation(None)


def good(**over: object) -> dict:
    app = {K["state"]: DONE, K["version"]: SCOPE_FIELDS_VERSION, K["generation"]: 3, K["structure_generation"]: 5, GENERATION_KEY: 5}
    return {**app, **over}


def test_a_connector_whose_pass_finished_from_its_current_generation_is_current() -> None:
    assert app_is_current(good())
    assert app_is_current(good(**{GENERATION_KEY: None, K["structure_generation"]: 0}))          # a connector whose structure never changed
    assert app_is_current(good(**{GENERATION_KEY: 6.0, K["structure_generation"]: 6}))           # AQL increments in doubles
    assert app_is_current(good(**{UNSAFE_KEY: 1000, K["unsafe"]: 1000}))                          # the pass read this very mark
    assert app_is_current(good(**{UNSAFE_KEY: True, K["unsafe"]: True}))                          # the mark older code wrote
    assert app_is_current(good(**{UNSAFE_KEY: False, K["unsafe"]: None}))


@pytest.mark.parametrize(("name", "over"), [
    ("state running", {K["state"]: "running"}), ("state failed", {K["state"]: "failed"}), ("no state", {K["state"]: None}),
    ("older field version", {K["version"]: SCOPE_FIELDS_VERSION - 1}), ("newer field version", {K["version"]: SCOPE_FIELDS_VERSION + 1}),
    ("another ACL generation", {K["generation"]: 2}), ("no ACL generation", {K["generation"]: None}),
    ("the structure moved on", {GENERATION_KEY: 6}), ("the pass is from a later generation", {GENERATION_KEY: 4}),
    ("no recorded structure generation", {K["structure_generation"]: None}), ("recorded -1", {K["structure_generation"]: -1}),
    ("unsafe", {UNSAFE_KEY: True}), ("unsafe with a token", {UNSAFE_KEY: 123}),
    ("a mark written since the pass", {UNSAFE_KEY: 2000, K["unsafe"]: 1000}), ("a mark where the pass read none", {UNSAFE_KEY: 2000}),
    ("the mark was cleared after the pass read one", {K["unsafe"]: 1000}), ("a bool mark over a token", {UNSAFE_KEY: True, K["unsafe"]: 1000}),
    ("a bool mark over the token 1", {UNSAFE_KEY: True, K["unsafe"]: 1}), ("the token 1 over a bool mark", {UNSAFE_KEY: 1, K["unsafe"]: True}),
    ("a whole float mark over a bool mark", {UNSAFE_KEY: 1.0, K["unsafe"]: True}),
    ("a token over a bool mark", {UNSAFE_KEY: 1000, K["unsafe"]: True}), ("an unreadable mark", {UNSAFE_KEY: "x", K["unsafe"]: "x"}),
    ("an unreadable recorded mark", {UNSAFE_KEY: 5, K["unsafe"]: [5]}), ("a zero mark", {UNSAFE_KEY: 0, K["unsafe"]: 0}),
    ("both generations -1", {GENERATION_KEY: -1, K["structure_generation"]: -1}), ("a negative held generation", {GENERATION_KEY: -5, K["structure_generation"]: 5}),
    ("garbage generation", {GENERATION_KEY: "x"}), ("garbage recorded generation", {K["structure_generation"]: "x"}),
    ("fractional generation", {GENERATION_KEY: 5.5}), ("NaN generation", {GENERATION_KEY: float("nan")}), ("infinite", {GENERATION_KEY: float("inf")}),
    ("boolean recorded generation", {K["structure_generation"]: True}), ("garbage version", {K["version"]: "x"}),
], ids=lambda v: v if isinstance(v, str) else "")
def test_anything_else_is_not_current(name: str, over: dict) -> None:
    assert not app_is_current(good(**over)), name


def test_no_app_document_is_not_current() -> None:
    assert not app_is_current(None) and not app_is_current({})


def test_an_unavailable_acl_generation_trusts_nothing() -> None:
    set_resolved_generation(-2)
    assert not app_is_current(good(**{K["generation"]: -2}))


async def test_the_reader_skips_a_connector_it_cannot_read_and_never_raises() -> None:
    class Graph:
        async def get_document(self, key: str, collection: str, **kw: object) -> dict | None:
            if key == "broken":
                raise RuntimeError("graph down")
            return {"a": good(), "b": good(**{GENERATION_KEY: 9}), "c": None}.get(key)

    assert await StampCurrency(Graph()).current(["a", "b", "c", "broken", "unknown"]) == {"a"}


GARBAGE = ["x", "", [], [0], {}, {"a": 0}, float("nan"), float("inf"), True, False, 0.5, "0.5", "1e999", b"0",
           "1_0", " 5 ", "1e1", "5", "0", "-1", -1, -0.0 - 1.0]


@pytest.mark.parametrize("junk", GARBAGE, ids=repr)
@pytest.mark.parametrize("field", [GENERATION_KEY, K["structure_generation"], K["generation"], K["version"]])
def test_a_value_that_is_not_a_whole_number_is_never_read_as_zero(field: str, junk: object) -> None:
    """The stamp was written at generation 0 of a connector whose structure never changed, under ACL generation 0 (the default): a value
    that does not parse must not be taken for that 0 on either side of the comparison."""
    set_resolved_generation(0)
    zero = {K["state"]: DONE, K["version"]: SCOPE_FIELDS_VERSION, K["generation"]: 0, K["structure_generation"]: 0, GENERATION_KEY: 0}
    assert app_is_current(zero)
    zero[field] = junk
    assert not app_is_current(zero), (field, junk)


def test_only_an_absent_generation_means_the_structure_never_changed() -> None:
    from app.services.permissions.structure_generation import structure_generation_of

    assert structure_generation_of({}) == 0 and structure_generation_of(None) == 0 and structure_generation_of({GENERATION_KEY: None}) == 0
    assert structure_generation_of({GENERATION_KEY: 4}) == 4 and structure_generation_of({GENERATION_KEY: 4.0}) == 4
    for junk in GARBAGE:
        assert structure_generation_of({GENERATION_KEY: junk}) is None, junk


OPENED, CLOSED = "scopeProjectionOpened", "scopeProjectionClosed"


@pytest.mark.parametrize(("name", "over", "expected"), [
    ("nothing opened", {}, True), ("opened and closed", {OPENED: 3, CLOSED: 3}, True), ("never closed", {OPENED: 1}, False),
    ("one still open", {OPENED: 3, CLOSED: 2}, False), ("two still open", {OPENED: 2}, False), ("opened as a float, closed", {OPENED: 2.0, CLOSED: 2}, True),
    ("closed ahead of opened", {OPENED: 1, CLOSED: 2}, False), ("closed with nothing opened", {CLOSED: 1}, False),
    ("a counter that is text", {OPENED: "1", CLOSED: "1"}, False), ("a bool counter", {OPENED: True, CLOSED: True}, False),
    ("a negative counter", {OPENED: -1, CLOSED: -1}, False), ("a list counter", {OPENED: [1], CLOSED: [1]}, False),
], ids=lambda v: v if isinstance(v, str) else "")
def test_a_write_that_has_started_and_not_written_its_closing_step_keeps_the_connector_not_current(name: str, over: dict, expected: bool) -> None:
    assert app_is_current(good(**over)) is expected, name


def test_unclosed_writes_counts_what_is_open_and_reads_a_broken_counter_as_unreadable() -> None:
    from app.services.permissions.structure_generation import unclosed_writes

    assert unclosed_writes(None) == 0 and unclosed_writes({}) == 0 and unclosed_writes({OPENED: 5, CLOSED: 2}) == 3
    for junk in GARBAGE:
        assert unclosed_writes({OPENED: junk}) is None and unclosed_writes({OPENED: 5, CLOSED: junk}) is None, junk
