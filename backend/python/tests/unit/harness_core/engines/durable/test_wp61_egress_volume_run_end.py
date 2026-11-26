"""WP-61 (C6.12): an engine turn the Model Gateway refused for the run's egress cap ends the run
``failed`` with ``egress_volume_exceeded``, the code the chat words in the reader's language; it is not retried, not
quarantined and not cut short as a budget stop.

R11D-EVK-1 the run fails once, with the code as its ``errorType``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.engines.events import EngineError, EngineEventMeta, TurnCompleted
from harness_core.gateway.errors import EgressVolumeExceeded
from harness_core.testing.engines.durable import EngineWorld, ScriptedEngineAdapter

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from harness_core.engines.events import EngineEvent
    from harness_core.engines.types import EngineTask, FollowUp

RUN = "run-egress-capped"
CODE = EgressVolumeExceeded.reason_code


class _Capped(ScriptedEngineAdapter):
    """The engine runs its tools, then the gateway refuses its next model call for the run's egress cap."""

    async def open(self, sandbox, launch, restore, *, correlation=None):  # type: ignore[no-untyped-def] # noqa: ANN202
        return _Refused(await super().open(sandbox, launch, restore, correlation=correlation))


class _Refused:
    def __init__(self, inner: object) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    async def run_turn(self, task: EngineTask | FollowUp) -> AsyncIterator[EngineEvent]:
        async for event in self._inner.run_turn(task):  # type: ignore[attr-defined]
            if isinstance(event, TurnCompleted):
                yield EngineError(EngineEventMeta(task.turn_id, event.meta.seq), CODE, "refused")
                yield TurnCompleted(EngineEventMeta(task.turn_id, event.meta.seq + 1), "failed", final_text="")
                return
            yield event


async def test_r11d_evk_1_a_turn_refused_for_the_egress_cap_fails_the_run_with_its_code() -> None:
    world = EngineWorld(adapter=_Capped())
    world.admit(RUN, 'tool lookup {"q": "x"}\nsay done')
    disposition = await world.attempt(RUN)
    assert (disposition.status, disposition.reason) == ("failed", CODE), disposition
    [failed] = [e for e in world.events(RUN) if e.type == "run.failed"]
    assert failed.data.get("errorType") == CODE
    assert "run.quarantined" not in world.types(RUN)
    assert "run.completed" not in world.types(RUN)
