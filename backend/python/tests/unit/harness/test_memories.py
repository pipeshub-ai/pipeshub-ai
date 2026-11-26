"""P7.3 PY-3, PY-5, PY-6, PY-8 (PipesHub): Node's memory routes as a ``MemorySource`` against the real
Node routes, what a run journals about what it was given, the assembler's wiring, and engines."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

from app.harness.assembly.assembler import _context_host, run_memory
from app.harness.assembly.context import MemoryPinRef
from app.harness.assembly.context_compiler import build_context_compiler
from app.harness.contracts.generated.pipeshub.events.x_pipeshub_memory_recalled import (
    PipeshubMemoryRecalledData,
)
from app.harness.engines.durable import engine_memory
from app.harness.engines.presenter import PipesHubEngineRunPresenter
from app.harness.journal_node import NodeJournal
from app.harness.memories import (
    RECALLED_EVENT,
    NodeMemorySource,
    recalled_data,
    recalled_event_for,
    source_free,
)
from harness_core.contracts import dump_contract, validate_contract
from harness_core.engines.durable.ports import ProgressStamp
from harness_core.journal.types import AttemptToken
from harness_core.logs import HarnessLog
from harness_core.memories import (
    MEMORY_SLOT,
    InMemoryMemorySource,
    MemoryContextHost,
    MemoryRecallStep,
    RecalledMemory,
    render_memories,
)
from harness_core.testing.contracts.memories import MemorySourceContract, MemoryWorld
from harness_core.testing.run_spec import minimal_run_spec
from harness_engines.claude_code.launch import FIXED_ENV
from harness_engines.codex.launch import FEATURES_OFF
from tests.unit.harness.test_node_journal_contract import (
    LEASE_MS,
    _Credential,
    node_server,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from harness_core.memories import MemoryScope, MemorySource
    from tests.unit.harness.test_node_journal_contract import _NodeServer

__all__ = ["node_server"]

ORG = "org-contract"
SESSION = "ses-memory"
LOG = HarnessLog.named("tests.memories")
PREF = RecalledMemory("mem_pref", "user", "preference", "Prefers German.", {"complete": True, "sources": []})
FROM_RECORD = RecalledMemory(
    "mem_rec", "user", "fact", "The Q3 plan names Dana.", {"complete": True, "sources": [{"kind": "indexed", "recordId": "r1"}]}
)


class _NodeWorld:
    """Memories as Node stores them (accepted, the run's actor's, in this session), a run whose spec pins them."""

    def __init__(self, server: _NodeServer) -> None:
        self.control = server.control
        _ = self.control.post("/reset").raise_for_status()
        _ = self.control.post("/memory/policy", json={"mode": "user", "review": "auto"}).raise_for_status()
        self.journal = NodeJournal(base_url=server.base_url, credentials=_Credential(server.credential), lease_ms=LEASE_MS)
        self.runs = 0

    async def source(self, recalled: Sequence[RecalledMemory], write_scopes: Sequence[MemoryScope]) -> MemorySource:
        for i, memory in enumerate(recalled):
            _ = self.control.post(
                "/memory/seed",
                json={
                    "memoryId": memory.memory_id,
                    "scope": memory.scope,
                    "kind": memory.kind,
                    "content": memory.content,
                    "label": dict(memory.label),
                    "status": "accepted",
                    "agentKey": None,
                    "sessionId": SESSION,
                    "runId": None,
                    "origin": "model",
                    "createdAt": 1_000 - i,
                    "updatedAt": 1_000 - i,
                    "reviewedBy": None,
                    "reviewedAt": None,
                },
            ).raise_for_status()
        self.runs += 1
        run_id = f"run-memory-{self.runs}"
        spec = cast("dict[str, Any]", dump_contract(minimal_run_spec()))
        spec["extensions"] = {
            "x.pipeshub": {"memory": {"recalled": [m.memory_id for m in recalled], "writeScopes": list(write_scopes)}}
        }
        _ = self.control.post(
            "/admit", json={"runId": run_id, "sessionId": SESSION, "inputText": "hi", "spec": spec, "orgId": ORG}
        ).raise_for_status()
        claim = await self.journal.claim(run_id, worker_id="w1", org_id=ORG)
        assert claim is not None
        return NodeMemorySource(self.journal.attempt_route(AttemptToken(claim.run_id, claim.attempt_id, claim.fence)))

    async def forget(self, memory_id: str) -> None:
        _ = self.control.post("/memory/delete", json={"memoryId": memory_id}).raise_for_status()


class TestNodeMemorySource(MemorySourceContract):
    @pytest.fixture
    def memory_world(self, node_server: _NodeServer) -> MemoryWorld:
        return _NodeWorld(node_server)


async def test_py3_a_proposal_reaches_node_pending_with_the_run_s_actor_and_session(node_server: _NodeServer) -> None:
    from harness_core.memories import MemoryProposal

    world = _NodeWorld(node_server)
    source = await world.source([], ["user"])
    proposed = await source.propose(MemoryProposal("Likes tea.", "preference", "user"))
    stored = world.control.get("/memory/get", params={"memoryId": proposed.memory_id}).json()["memory"]
    assert stored["status"] == "pending" and stored["label"] is None
    assert stored["ownerId"] == "actor-contract" and stored["sessionId"] == SESSION
    assert stored["runId"] == "run-memory-1" and stored["content"] == "Likes tea."


async def test_py5_the_pre_run_journals_what_it_recalled_without_content() -> None:
    slot = await MemoryRecallStep(InMemoryMemorySource([PREF, FROM_RECORD]), log=LOG).run("hi")
    data = recalled_data({MEMORY_SLOT: slot})
    assert data is not None
    _ = validate_contract(PipeshubMemoryRecalledData, data)
    assert [e["memoryId"] for e in data["entries"]] == ["mem_pref", "mem_rec"]  # type: ignore[index, union-attr]
    assert "Prefers German." not in str(data)
    assert recalled_data({}) is None
    assert recalled_data({MEMORY_SLOT: {"text": "", "entries": []}}) is None


def _run(memory: MemoryPinRef | None) -> Any:  # noqa: ANN401 - only the field the helper reads
    return SimpleNamespace(memory=memory)


def test_fl1_no_pin_adds_no_step_shaper_or_tool() -> None:
    claim = cast("Any", SimpleNamespace(memories=InMemoryMemorySource()))
    assert run_memory(_run(None), claim, LOG) is None
    assert run_memory(_run(MemoryPinRef()), claim, LOG) is None


def test_py6_a_pin_wires_the_run_s_source_and_granted_scopes_only() -> None:
    source = InMemoryMemorySource()
    claim = cast("Any", SimpleNamespace(memories=source))
    wired = run_memory(_run(MemoryPinRef(recalled=("m1",), write_scopes=("session", "org"))), claim, LOG)
    assert wired is not None and wired.source is source and wired.recalled
    assert wired.write_scopes == ("session",)
    assert run_memory(_run(MemoryPinRef(recalled=("m1",))), cast("Any", SimpleNamespace(memories=None)), LOG) is None


async def test_py8_an_engine_is_given_only_memories_derived_from_no_record_as_instruction_text() -> None:
    claim = cast("Any", SimpleNamespace(memories=InMemoryMemorySource([PREF, FROM_RECORD])))
    run = cast("Any", _run(MemoryPinRef(recalled=("mem_pref", "mem_rec"))))
    rendered = await engine_memory(run, claim, LOG)
    assert [m.memory_id for m in rendered.shown] == ["mem_pref"]
    assert rendered.text == render_memories([PREF]).text
    assert source_free([PREF, FROM_RECORD]) == [PREF]
    nothing = await engine_memory(cast("Any", _run(None)), claim, LOG)
    assert nothing.text == ""
    down = InMemoryMemorySource([PREF])
    down.down = True
    assert (await engine_memory(run, cast("Any", SimpleNamespace(memories=down)), LOG)).text == ""


def test_py8_the_engine_s_first_turn_journals_what_it_was_given() -> None:
    rendered = render_memories([PREF])
    event = recalled_event_for(rendered.shown, rendered.chars, truncated=False)
    assert event is not None and event.type == RECALLED_EVENT and not event.critical
    presenter = PipesHubEngineRunPresenter(recalled=event)
    counter = iter(range(100))
    stamp = ProgressStamp(task_id="t", turn_id="turn-1", allocate=lambda: next(counter))
    started = presenter.started(stamp, "do it")
    assert [e.type for e in started][-1] == RECALLED_EVENT
    assert [e.type for e in PipesHubEngineRunPresenter().started(stamp, "do it")] != [RECALLED_EVENT]
    assert recalled_event_for((), 0, truncated=False) is None


def test_py8_engines_keep_their_own_memories_off() -> None:
    assert "memories" in FEATURES_OFF
    assert FIXED_ENV["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"
    # A CLAUDE.md in the workspace would be a memory the engine could rewrite.
    assert FIXED_ENV["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] == "1"


def test_cb3_with_the_compiler_flag_on_a_run_given_memories_compiles_through_the_memory_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HARNESS_CONTEXT_COMPILER", "true")
    shaper = cast("Any", SimpleNamespace(bands=None))
    model = cast("Any", SimpleNamespace(context_length=8_000, transport=None))
    source = InMemoryMemorySource()
    claim = cast("Any", SimpleNamespace(memories=source))
    recalled = run_memory(_run(MemoryPinRef(recalled=("m1",), write_scopes=("user",))), claim, LOG)
    compiled = build_context_compiler(model, "scripted-1", _context_host(shaper, recalled))
    assert compiled is not None and isinstance(compiled.host, MemoryContextHost)
    writes_only = run_memory(_run(MemoryPinRef(write_scopes=("user",))), claim, LOG)
    assert _context_host(shaper, writes_only) is shaper
    assert _context_host(shaper, None) is shaper
    monkeypatch.setenv("HARNESS_CONTEXT_COMPILER", "false")
    assert build_context_compiler(model, "scripted-1", _context_host(shaper, recalled)) is None
