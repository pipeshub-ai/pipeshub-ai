"""H1 EG-4 (P9 FB-5): a dead opencode reaches the kernel as a typed engine death, anything else as before."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpcore
import pytest

from harness_core.engines.errors import EngineCrashed, EngineGone, EngineUnhealthy
from harness_core.testing.engines.contract import open_rig
from harness_engines.opencode.adapter import STARTUP_ATTEMPTS, OpencodeAdapter
from tests.unit.harness_engines.opencode.conftest import OpencodeHarness

if TYPE_CHECKING:
    from harness_core.testing.sandbox.fake import FakeProcessContext


class _NeverAnswers:
    async def request(self, method: str, path: str, body: object = None, *, timeout_s: float = 30.0) -> object:
        raise httpcore.ReadTimeout("listening, never serving")


async def _no_wait(_seconds: float) -> None:
    return None


async def test_eg4_an_opencode_that_never_answers_its_health_check_is_unhealthy() -> None:
    adapter = OpencodeAdapter(sleep=_no_wait)
    with pytest.raises(EngineUnhealthy) as gone:
        await adapter._wait_healthy(_NeverAnswers())  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
    assert isinstance(gone.value.__cause__, httpcore.ReadTimeout)
    assert gone.value.reason_code == "engine_unhealthy"
    assert STARTUP_ATTEMPTS > 1


@pytest.mark.parametrize(("code", "dead"), [(137, True), (-9, True), (1, False)])
async def test_eg4_an_export_killed_by_a_signal_is_a_crash_any_other_failure_is_not(code: int, dead: bool) -> None:
    harness = OpencodeHarness()
    rig = await open_rig(harness)

    async def exits(ctx: FakeProcessContext) -> int:
        return code

    try:
        rig.sandbox.install("sh", exits)
        with pytest.raises(Exception) as failed:
            await rig.session.capture_state()
        assert isinstance(failed.value, EngineGone) is dead
        if dead:
            assert isinstance(failed.value, EngineCrashed)
        else:
            assert type(failed.value) is RuntimeError
    finally:
        await rig.session.close()
