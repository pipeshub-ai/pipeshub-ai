"""UX5 CO and BP: ``BoundedCommandActivity`` over the in-memory bus (the contract suite)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.activity import BoundedCommandActivity
from harness_core.journal.memory import InMemoryLiveBus
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.command_activity import (
    CommandActivityContract,
    spec,
)

if TYPE_CHECKING:
    from harness_core.activity.ports import CommandActivity


class TestInMemoryCommandActivity(CommandActivityContract):
    @pytest.fixture
    def bus(self) -> InMemoryLiveBus:
        return InMemoryLiveBus()


async def test_without_a_bus_the_record_is_kept_and_nothing_is_published() -> None:
    activity: CommandActivity = BoundedCommandActivity(None, run_id="r", attempt_id="a", fence=0, clock=ManualClock())
    recorder = activity.begin(spec())
    recorder.write("stdout", "hello\n")
    record = await recorder.finish(exit_code=0, status="completed")
    assert record.stdout.tail == "hello\n"
    await activity.aclose()
