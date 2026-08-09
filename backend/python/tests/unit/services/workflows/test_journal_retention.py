"""A suspended run must not outlive its journal.

`ctx.request_approval` stops the workflow and writes nothing more, so the
journal's retention clock keeps running from the last completed step. If the
answer arrives after it expires, replay finds an empty journal and re-executes
every step the first attempt already ran. `touch` restarts that clock at
suspension and reports the deadline the rest of the system enforces.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fakeredis import aioredis as fake_aioredis

from app.services.workflows.adapters.redis import keys as k
from app.services.workflows.adapters.redis.journal import RedisExecutionJournal
from app.services.workflows.domain.models import JournalEntry, ResultRef, StepOutcome

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def redis_client():  # noqa: ANN201 - fixture yields a fakeredis client
    client = fake_aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


def _entry(run_id: str, step_key: str) -> JournalEntry:
    return JournalEntry(
        run_id=run_id,
        seq=0,
        step_key=step_key,
        entry_kind="tool",
        idempotency_key=step_key,
        outcome=StepOutcome.SUCCEEDED,
        result_ref=ResultRef(inline={"ok": True}),
    )


class TestTouch:
    async def test_extends_every_key_belonging_to_the_run(self, redis_client) -> None:
        journal = RedisExecutionJournal(redis_client, ttl_seconds=100)
        await journal.append(_entry("run-1", "step-a"))
        await journal.append(_entry("run-1", "step-b"))
        # Simulate the run sitting suspended for most of its window.
        for key in (
            k.journal_index_key("run-1"),
            k.journal_seq_key("run-1"),
            k.journal_entry_key("run-1", "step-a"),
            k.journal_entry_key("run-1", "step-b"),
        ):
            await redis_client.expire(key, 5)

        deadline = await journal.touch("run-1")

        assert deadline is not None
        assert datetime.fromisoformat(deadline) > datetime.now(timezone.utc)
        for key in (
            k.journal_index_key("run-1"),
            k.journal_seq_key("run-1"),
            k.journal_entry_key("run-1", "step-a"),
            k.journal_entry_key("run-1", "step-b"),
        ):
            assert await redis_client.ttl(key) > 5

    async def test_reports_nothing_when_the_journal_is_already_gone(self, redis_client) -> None:
        """The caller cannot resume a run whose journal expired, and this
        None is how it finds out -- there is no deadline left to promise."""
        journal = RedisExecutionJournal(redis_client, ttl_seconds=100)
        assert await journal.touch("run-never-ran") is None

    async def test_leaves_another_run_alone(self, redis_client) -> None:
        journal = RedisExecutionJournal(redis_client, ttl_seconds=100)
        await journal.append(_entry("run-1", "step-a"))
        await journal.append(_entry("run-2", "step-a"))
        await redis_client.expire(k.journal_entry_key("run-2", "step-a"), 5)

        await journal.touch("run-1")

        assert await redis_client.ttl(k.journal_entry_key("run-2", "step-a")) <= 5
