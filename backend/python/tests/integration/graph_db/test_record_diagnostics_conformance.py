"""The run rule's statement fragments equal the Python reference on a real store (ArangoDB and Neo4j here,
PostgreSQL in ``postgres/test_record_diagnostics_conformance_pg.py``).

5,000 stored records of every shape (absent, JSON null, number, float, string, bool in each field) x every kind of
write. Each case is compared record by record. The negative controls run the same comparison with one part of the
rule left out and must report mismatches, so a comparison that cannot see a defect fails here.

The seed is printed; set ``RD_SEED`` to repeat a run.
"""

from __future__ import annotations

import logging
import os
import random
from typing import TYPE_CHECKING, Any

import pytest

from tests.integration.graph_db.record_diagnostics_conformance_support import (
    CASES,
    CONTROL_STORE,
    CONTROLS,
    ArangoRunner,
    Neo4jRunner,
    generate,
    mismatches,
)
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(900)]
logger = logging.getLogger("record-diagnostics-conformance-it")
N = int(os.environ.get("RD_CONFORMANCE_N", "5000"))


STORES = ["neo4j", "arango"]


@pytest.fixture(params=STORES)
async def runner(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Any]:
    try:
        if request.param == "neo4j":
            provider = await connect_neo4j(logger, monkeypatch)
            made: Any = Neo4jRunner(provider)
        else:
            provider = await connect_arango(logger, "record_diagnostics_conformance_it")
            made = ArangoRunner(provider)
    except Exception as exc:
        backend_unavailable(request.param, exc)
    await made.start()
    try:
        yield made
    finally:
        await made.stop()
        await provider.disconnect()


def _seed() -> int:
    seed = int(os.environ.get("RD_SEED") or random.SystemRandom().randint(1, 10**9))
    print(f"record diagnostics conformance seed={seed} n={N}")
    return seed


async def test_fragment_equals_reference(runner: Any) -> None:  # noqa: ANN401
    stored = generate(random.Random(_seed()), N)
    report = []
    for case in CASES:
        await runner.load(stored)
        bad = mismatches(stored, await runner.apply(case), case)
        if bad:
            report.append((case[0].value, case[1], len(bad), bad[0]))
    assert report == [], f"{runner.name}: fragment and reference differ: {report}"


# each control is run only on the store whose dialect it mutates: the other store's job never selects it
@pytest.mark.parametrize(
    ("runner", "control"),
    [pytest.param(CONTROL_STORE[c], c, id=f"{CONTROL_STORE[c]}-{c}") for c in sorted(CONTROLS) if CONTROL_STORE[c] in STORES],
    indirect=["runner"],
)
async def test_a_rule_with_a_part_left_out_is_seen_to_differ(runner: Any, control: str) -> None:  # noqa: ANN401
    stored = generate(random.Random(_seed()), N)
    seen = 0
    for case in CASES:
        await runner.load(stored)
        try:
            seen += len(mismatches(stored, await runner.apply(case, CONTROLS[control]), case))
        except Exception:
            seen += 1  # a statement the store refuses is a difference too
    assert seen > 0, f"{control}: the comparison did not see the defect"


def test_the_generated_records_hold_floats_and_integers_in_the_timestamp_fields() -> None:
    """No store: the float shape of a stored time reaches the comparison, so a number test that drops the float branch is seen."""
    stored = generate(random.Random(7), 600)
    for field in ("firstQueuedAt", "queuedAtTimestamp", "failedAt", "lastIndexTimestamp", "restoredAtTimestamp"):
        kinds = {type(doc[field]) for doc in stored if field in doc and isinstance(doc[field], (int, float)) and not isinstance(doc[field], bool)}
        assert kinds == {int, float}, f"{field}: {kinds}"


def test_a_boolean_written_where_a_count_belongs_is_not_equal_to_it() -> None:
    from tests.integration.graph_db.record_diagnostics_conformance_support import _norm

    assert _norm({"indexingAttempts": 1}) != _norm({"indexingAttempts": True})
    assert _norm({"indexingAttempts": 1}) == _norm({"indexingAttempts": 1.0})
