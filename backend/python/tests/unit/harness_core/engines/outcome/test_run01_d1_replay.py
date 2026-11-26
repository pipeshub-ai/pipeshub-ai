"""RUN01-D1-REPLAY (WP95-FU-1): the outcome ledger is a projection of the journal (ADR-RUN-01 D1, amended 2026-11-10).

The engine kernel derives the ledger at a turn's end and at a cut from the journal's per-call events and the
workspace head; what it settles on is what ``ledger_from_events`` replays from the journal, for a run that finished
and for one whose worker was killed mid-turn, whose calls the next turn end still counts. Only the outcome package
builds facts, so no second record can grow beside the journal.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from harness_core.effects.types import ToolExecution
from harness_core.engines.durable import kernel as kernel_module
from harness_core.engines.outcome import ledger as ledger_module
from harness_core.engines.outcome.ledger import (
    OUTCOME_RECORDED,
    SourceGathered,
    ledger_from_events,
)
from harness_core.testing.engines.durable import (
    EngineWorld,
    ScriptedEngineAdapter,
    engine_profile,
    engine_run_spec,
)

if TYPE_CHECKING:
    from harness_core.effects.types import ToolContext
    from harness_core.engines.outcome.ledger import Fact
    from harness_core.kernel.core.messages import ToolCall

RUN = "run-run01-replay"
REQUEST = "Research how Onyx compares with PipesHub. Write the final report as one Markdown file named report.md in out/"
PYTHON_ROOT = Path(__file__).resolve().parents[5]
FACTS = ("ArtifactWritten", "SourceGathered", "NoteSaved", "StepCompleted", "LimitReached", "ChildEnded", "SaveRefused")


async def _fetch(call: ToolCall, ctx: ToolContext) -> ToolExecution:
    del ctx
    url = str(call.arguments.get("url"))
    return ToolExecution(status="ok", content=json.dumps({"ok": True, "url": url, "title": "Onyx", "blocks": []}))


def _lookups(count: int) -> list[str]:
    return [f'tool lookup {json.dumps({"url": f"https://docs.onyx.app/p{n}"})}' for n in range(count)]


@pytest.fixture
def settled(monkeypatch: pytest.MonkeyPatch) -> list[frozenset[Fact]]:
    """Each ledger the kernel settled a run on, as it held it in memory."""
    seen: list[frozenset[Fact]] = []
    journal_them = kernel_module._EngineAttemptRun._outcome_events  # pyright: ignore[reportPrivateUsage]

    def spying(self: object, outcome: object) -> object:
        seen.append(frozenset(outcome.ledger.facts))  # type: ignore[attr-defined]
        return journal_them(self, outcome)  # type: ignore[arg-type]

    monkeypatch.setattr(kernel_module._EngineAttemptRun, "_outcome_events", spying)  # pyright: ignore[reportPrivateUsage]
    return seen


def _world() -> EngineWorld:
    return EngineWorld(adapter=ScriptedEngineAdapter(), handlers={"lookup": _fetch}, wrap_up=None)


def _replayed(world: EngineWorld) -> frozenset[Fact]:
    return frozenset(ledger_from_events(world.events(RUN), run_id=RUN).facts)


async def test_run01_d1_replay_1_a_finished_run_settles_on_the_ledger_its_journal_replays(
    settled: list[frozenset[Fact]],
) -> None:
    world = _world()
    world.admit(RUN, "\n".join([REQUEST, *_lookups(4), "say done"]), spec=engine_run_spec(engine_profile(max_steps=40)))

    assert (await world.attempt(RUN)).status == "completed"

    (ledger,) = settled
    assert any(isinstance(f, SourceGathered) for f in ledger), "the run gathered sources"
    assert _replayed(world) == ledger


async def test_run01_d1_replay_2_a_run_killed_mid_turn_loses_no_fact(settled: list[frozenset[Fact]]) -> None:
    world = _world()
    world.admit(RUN, "\n".join([REQUEST, *_lookups(4), "say done"]), spec=engine_run_spec(engine_profile(max_steps=40)))

    assert await world.crash_at(RUN, "engine.kernel.before_turn_commit")
    assert not [e for e in world.events(RUN) if e.type == OUTCOME_RECORDED], "the dead worker recorded nothing"
    called = {e.data["invocationId"] for e in world.events(RUN) if e.type == "tool.outcome"}
    assert called, "its calls are in the journal"
    assert (await world.attempt(RUN)).status == "completed"

    (ledger,) = settled
    assert _replayed(world) == ledger
    urls = {f.url for f in ledger if isinstance(f, SourceGathered)}
    assert {f"https://docs.onyx.app/p{n}" for n in range(4)} <= urls


_NAMED = re.compile(r"\b(?:" + "|".join(FACTS) + r")\(")


def _constructs_a_fact(path: Path) -> bool:
    text = path.read_text(encoding="utf-8-sig")
    if not _NAMED.search(text):
        return False
    return any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FACTS
        for node in ast.walk(ast.parse(text))
    )


def test_run01_d1_replay_3_only_the_outcome_package_and_the_engine_kernel_build_facts() -> None:
    # The kernel derives facts from journal events and the workspace head; anything else that built them would be a
    # second record of what a run produced.
    allowed = {Path(ledger_module.__file__).parent.resolve()}
    kernel = Path(kernel_module.__file__).resolve()
    builders = sorted(
        str(path.relative_to(PYTHON_ROOT))
        for root in ("harness_core", "app")
        for path in (PYTHON_ROOT / root).rglob("*.py")
        if path.resolve().parent not in allowed
        and path.resolve() != kernel
        and _constructs_a_fact(path)
    )
    assert builders == []
