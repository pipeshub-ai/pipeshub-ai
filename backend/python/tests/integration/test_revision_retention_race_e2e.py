# ruff: noqa
"""Real storage: a guarded sweep delete racing a re-index's write of the same revision,
many rounds with a fresh seed. Invariant: a write that returned a set has every file readable.
Uses the fixtures and environment of test_revision_retention_storage_e2e: a storage target, and the
graph through NEO4J_IT_URI / ARANGO_IT_URL."""
from __future__ import annotations

import asyncio
import os
import random
import time
from collections import Counter

import pytest

from tests.integration.test_revision_retention_storage_e2e import (  # noqa: F401
    STORED_RUN, TARGETS, _Lifecycle, graph, life, target, logger,
)

# Module marks are not inherited through the fixture import, so the skip is repeated.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
    pytest.mark.skipif(not TARGETS, reason="set RETENTION_IT_TARGETS or ARTIFACTS_IT_STORAGE_URL to a running storage service"),
]
SEED = int(os.environ.get("RETENTION_TEST_SEED", str(time.time_ns() % 2**31)))
ROUNDS = int(os.environ.get("RETENTION_RACE_ROUNDS", "40"))


async def test_guarded_delete_racing_a_rewrite_never_strands_a_written_set(life: _Lifecycle) -> None:
    rnd = random.Random(SEED)
    outcomes: Counter = Counter()
    refusals: Counter = Counter()
    for round_ in range(ROUNDS):
        vrid = life.vrid(f"r{round_}")
        await life.mapping(vrid)
        a = await life.record(f"a{round_}", vrid, None)
        rev = life.rev()
        await life.index(a, vrid, rev)
        listed = (await life.listed(vrid))[rev]
        stamp = max(listed.get("createdAt") or 0, listed.get("updatedAt") or 0)
        await asyncio.sleep(0.003)
        record = life.model(a, vrid, f"content of {rev}")
        delay_w, delay_d = rnd.random() * 0.03, rnd.random() * 0.03

        async def write():
            await asyncio.sleep(delay_w)
            try:
                return await life.writer.write_revision(record, life.org_id, vrid, rev, record.block_containers, stored_run=STORED_RUN)
            except Exception as exc:  # refused by a live claim: the sink logs and counts it failed
                refusals[type(exc).__name__ + ":" + str(exc)[-60:]] += 1
                return None

        async def delete():
            await asyncio.sleep(delay_d)
            return await life.target.store.delete_revision(
                life.org_id, listed["documentId"], if_unchanged_since=stamp, timeout_seconds=30)

        written, purged = await asyncio.gather(write(), delete())
        outcomes[(written is not None, purged)] += 1
        if written is not None:
            manifest = await life.read_manifest(written.manifest_ref.document_id)
            assert manifest["rev"] == rev, f"seed {SEED} round {round_}"
            await life.flip(record, written)
            await life.assert_pointer_resolves(vrid)
    logger.warning("race seed=%s outcomes (written, purged): %s", SEED, dict(outcomes))
    print(f"RACE seed={SEED} {life.target.type} outcomes={dict(outcomes)} refusals={dict(refusals)}")
