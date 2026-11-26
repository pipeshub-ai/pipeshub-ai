"""Both effect logs pass the shared ``EffectLog`` contract (EL-1..EL-7, ED-1..ED-4)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.effects import (
    EffectCall,
    JournalEffectLog,
    RequestEffectLog,
    ToolEffect,
    ToolExecution,
    args_digest,
)
from harness_core.effects.results import put_ref
from harness_core.journal import InMemoryContentStore, InMemoryJournal
from harness_core.journal.types import CommitRequest, NewEvent, command_id
from harness_core.kernel.core.messages import AssistantMessage, ToolCall
from harness_core.kernel.durable.transcript import encode_assistant
from harness_core.kernel.fold import RunState, fold_run, invocation_id
from harness_core.testing import ManualClock
from harness_core.testing.contracts.effect_log import EffectLogContract, write_effect
from harness_core.testing.run_spec import minimal_run_spec

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.journal.types import Claim

RUN = "run-1"
SESSION = "ses-1"


class MemoryHarness:
    def __init__(self) -> None:
        self._log = RequestEffectLog()
        self._n = 0

    @property
    def log(self) -> RequestEffectLog:
        return self._log

    async def new_call(
        self, effect: ToolEffect, args: Mapping[str, JsonValue]
    ) -> EffectCall:
        self._n += 1
        call = ToolCall(id=f"call-{self._n}", name="tool", arguments=dict(args))
        return EffectCall(RUN, f"inv-{self._n}", call, effect, args_digest(args))


class JournalHarness:
    """A claimed run on the in-memory journal; each call is a new model turn that asked for it."""

    def __init__(self) -> None:
        self.journal = InMemoryJournal(clock=ManualClock(), lease_ms=30_000)
        self.content = InMemoryContentStore()
        self.state = RunState(run_id=RUN)
        self._claim: Claim | None = None
        self._commits = 0
        self._turn = 0
        self._log = JournalEffectLog(self._commit, self.content, lambda: self.state)

    @property
    def log(self) -> JournalEffectLog:
        return self._log

    async def start(self) -> None:
        self.journal.admit(
            RUN,
            org_id="org-1",
            session_id=SESSION,
            spec=minimal_run_spec(),
            input_text="hi",
        )
        self._claim = await self.journal.claim(RUN, worker_id="w")
        await self._refold()

    async def new_call(
        self, effect: ToolEffect, args: Mapping[str, JsonValue]
    ) -> EffectCall:
        self._turn += 1
        call = ToolCall(id=f"call-{self._turn}", name="tool", arguments=dict(args))
        message = AssistantMessage(content=[], tool_calls=[call])
        response: dict[str, JsonValue] = {
            "messageRef": await put_ref(self.content, encode_assistant(message)),
            "toolCalls": [
                {
                    "callId": call.id,
                    "modelName": "tool",
                    "argsDigest": args_digest(args),
                }
            ],
            "usage": {
                "inputTokens": 1,
                "outputTokens": 1,
                "cacheReadTokens": 0,
                "cacheWriteTokens": 0,
            },
            "provider": "test",
            "transport": "test",
        }
        request: dict[str, JsonValue] = {
            "contextDigest": args_digest({"turn": self._turn}),
            "route": {"provider": "test", "model": "test"},
            "modelAttempt": 1,
        }
        await self._commit(
            "model",
            [
                NewEvent("turn.started", {"turn": self._turn}),
                NewEvent("model.request", request),
                NewEvent("model.response", response),
            ],
        )
        inv = invocation_id(self._turn, 0)
        assert inv in self.state.invocations
        return EffectCall(RUN, inv, call, effect, args_digest(args))

    async def _commit(self, kind: str, events: list[NewEvent]) -> object:
        assert self._claim is not None
        self._commits += 1
        receipt = await self.journal.commit(
            CommitRequest(
                command_id(self._claim.attempt_id, kind, self._commits),
                self._claim.token,
                tuple(events),
            )
        )
        await self._refold()
        return receipt

    async def _refold(self) -> None:
        self.state = fold_run(await self.journal.read(SESSION), run_id=RUN)


class TestRequestEffectLog(EffectLogContract):
    @pytest.fixture
    def harness(self) -> MemoryHarness:
        return MemoryHarness()


class TestJournalEffectLog(EffectLogContract):
    @pytest.fixture
    async def harness(self) -> JournalHarness:
        harness = JournalHarness()
        await harness.start()
        return harness

    async def test_commits_use_the_runs_command_ids(
        self, harness: JournalHarness
    ) -> None:
        call = await harness.new_call(write_effect(), {"title": "a"})
        await harness.log.intent(call)
        await harness.log.outcome(call, ToolExecution(status="ok", content="J-1"))
        assert harness.journal.commits(RUN)[-2:] == [
            ("run-1.a1:intent:2", ["tool.intent"]),
            ("run-1.a1:outcome:3", ["tool.outcome"]),
        ]
