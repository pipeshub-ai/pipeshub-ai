"""The run rule on PostgreSQL: the run rule's SQL fragment equals the Python reference (see the ArangoDB and Neo4j file
``../test_record_diagnostics_conformance.py`` for the method). The control leaves out the number test that a JSON
null needs (``doc->'f' IS NULL`` is false for a JSON null)."""
from __future__ import annotations

import os
import random
import uuid

import asyncpg
import pytest

from tests.integration.graph_db.record_diagnostics_conformance_support import (
    CASES,
    CONTROL_STORE,
    CONTROLS,
    Controls,
    PostgresRunner,
    generate,
    mismatches,
)

pytestmark = [pytest.mark.integration, pytest.mark.timeout(900)]
N = int(os.environ.get("RD_CONFORMANCE_N", "5000"))


@pytest.fixture
async def runner(pg_db):  # noqa: ANN201
    made = PostgresRunner(pg_db, uuid.uuid4().hex[:24])
    yield made
    await made.stop()


def _seed() -> int:
    seed = int(os.environ.get("RD_SEED") or random.SystemRandom().randint(1, 10**9))
    print(f"record diagnostics conformance seed={seed} n={N}")
    return seed


async def test_fragment_equals_reference(runner) -> None:
    stored = generate(random.Random(_seed()), N)
    report = []
    for case in CASES:
        await runner.load(stored)
        bad = mismatches(stored, await runner.apply(case), case)
        if bad:
            report.append((case[0].value, case[1], len(bad), bad[0]))
    assert report == [], f"postgres: fragment and reference differ: {report}"


async def _defects_seen(runner, controls) -> int:
    """Records the control's statement got wrong, plus the statements the server refused for the one defect these controls make: a
    number test left out, so a stored string reaches a ``::numeric`` cast (22P02). Any other database error (a syntax error in the
    control, a missing column) is a broken test, not a defect seen, and is raised."""
    stored = generate(random.Random(_seed()), N)
    seen = 0
    for case in CASES:
        await runner.load(stored)
        try:
            seen += len(mismatches(stored, await runner.apply(case, controls), case))
        except asyncpg.exceptions.InvalidTextRepresentationError:
            seen += 1
    return seen


@pytest.mark.parametrize("control", sorted(c for c, store in CONTROL_STORE.items() if store == "postgres"))
async def test_a_rule_with_a_part_left_out_is_seen_to_differ(runner, control: str) -> None:
    assert await _defects_seen(runner, CONTROLS[control]) > 0, f"{control}: the comparison did not see the defect"


async def test_a_control_with_malformed_sql_fails_the_test_instead_of_counting_as_seen(runner) -> None:
    broken = Controls(sql=lambda patch: patch + " )) not sql")
    with pytest.raises(asyncpg.exceptions.PostgresSyntaxError):
        await _defects_seen(runner, broken)
