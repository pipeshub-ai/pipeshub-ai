"""C-24 (DUR1): on one spec, the durable loop sends the model what today's loop sends.

Every model call is captured whole at the transport (system prompt, each tool's description and schema, messages
with their tool calls) while a scripted model plays both runs, and the two runs' requests must be equal, timings and
sandbox and artifact ids aside. The replay lane's digest leaves the system prompt out, which is how the prompt-tier
and time differences EV4 measured went unseen. Compiler off: the flag's default (the compiler path is P4.8's).

The quick set is one case of each wave 1 suite (the reference scripts; ``run_code`` in the host sandbox on both
loops) and every harness case the durable path serves, in its own mode and in ``auto`` (DUR3); the whole wave 1 set is
``slow``. Since DUR3 the outputs are compared too (condition c): every tool step's status, arguments and result, the
world's effects, the final answer and status (``outputs_difference``).
"""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from tests.harness_replay import durable_driver, legacy_driver
from tests.harness_replay.wire import (
    CapturingTransport,
    WireRequest,
    drive_captured,
    first_difference,
    normalized,
    outputs_difference,
)
from tests.support.parity_cases.harness_cases import load_cases as harness_cases
from tests.support.parity_cases.wave1_cases import select as wave1_cases
from tests.support.parity_cases.wave1_durable import DurableLoopSystem
from tests.support.parity_cases.wave1_reference import script_for
from tests.support.parity_cases.wave1_systems import TodayLoopSystem, task_for

if TYPE_CHECKING:
    from app.agent_loop_lib.transport.base import LLMTransport
    from harness_core.testing import ModelScript
    from tests.support.parity_cases.wave1_cases import Wave1Case

# The one allowed difference on the wire, `plan_tool` (durable_parity.py): react and auto offer write_todos for the
# chat's plan view (UXR-B1); `first_difference` leaves it and its prompt line out of both sides.
# Scripted runs that write after reading, compared with today's loop or with recordings made before the flow
# guard (on by default since UX1), which would hold those writes for a person. The guard is tested on its own.
pytestmark = pytest.mark.usefixtures("flow_guard_off")

WAVE1_QUICK = ("da-02-top-region", "od-01-incident-memo", "rp-01-travel-policy")
# Every harness case the durable path serves (DUR3-A1). Deep, planExecute and Universal Agent Mode are not served by it
# the same way (`ALLOWED_DIFFERENCES` in durable_parity.py says how and why).
HARNESS = [c for c in harness_cases() if durable_driver.supports(c.route, c.chat_mode)]
# `auto` is the request's default mode (DUR3-A2): every agent case of the quick and react tiers, driven as `auto`.
AUTO = [c for c in HARNESS if c.route == "agent" and c.category in ("quick", "react")]


def _scripted(script: ModelScript, seen: list[WireRequest]) -> LLMTransport:
    from app.harness.testing_adapters import ScriptedModelTransport
    from harness_core.testing import ScriptedModel

    return CapturingTransport(ScriptedModelTransport(ScriptedModel(script)), seen)


async def _wave1(case: Wave1Case) -> None:
    script = script_for(case)
    legacy_seen: list[WireRequest] = []
    durable_seen: list[WireRequest] = []
    legacy = await TodayLoopSystem(transport_factory=lambda: _scripted(script, legacy_seen)).run(task_for(case, 0, None))
    durable = await DurableLoopSystem(
        transport_factory=lambda: _scripted(script, durable_seen), sandbox="host"
    ).run(task_for(case, 0, None))
    assert legacy.error is None and durable.error is None, (legacy.error, durable.error)
    assert first_difference(legacy_seen, durable_seen) is None, first_difference(
        legacy_seen, durable_seen
    )
    assert (durable.status, durable.tool_calls) == (legacy.status, legacy.tool_calls)
    assert durable.cited_sources == legacy.cited_sources
    assert normalized(durable.answer) == normalized(legacy.answer)
    assert [_file(f) for f in durable.files] == [_file(f) for f in legacy.files]


def _file(f) -> tuple[str, str, object]:
    """A file as the user gets it. An office file is a zip stamped with the time it was written, so its members'
    contents are compared, its timestamps not."""
    import io
    import zipfile

    if not zipfile.is_zipfile(io.BytesIO(f.content)):
        return f.name, f.mime_type, f.content
    with zipfile.ZipFile(io.BytesIO(f.content)) as z:
        stamped = ("docProps/core.xml",)
        return f.name, f.mime_type, sorted((n, z.read(n)) for n in z.namelist() if n not in stamped)


@pytest.mark.parametrize("case_id", WAVE1_QUICK)
async def test_c24_wave1_case_sends_the_same_requests(case_id: str) -> None:
    await _wave1(wave1_cases(ids=[case_id])[0])


@pytest.mark.slow
@pytest.mark.parametrize("case_id", [c.id for c in wave1_cases()])
async def test_c24_every_wave1_case_sends_the_same_requests(case_id: str) -> None:
    await _wave1(wave1_cases(ids=[case_id])[0])


def _equipped(spec, case):  # noqa: ANN202 - a DriveSpec and a ParityCase
    """``spec`` as a deployment runs the case: a case that needs a sandbox gets the host one on both routes, and
    the durable route pins ``agent-code`` (Node pins it when a sandbox is proven, P5.11)."""
    from tests.contracts.golden_support import CONVERSATION_ID, ORG_ID
    from tests.harness_replay.artifacts import ParityArtifactRegistry

    if "local_sandbox" in case.requires:
        spec.sandbox_profile = "agent-code"
        spec.artifacts = ParityArtifactRegistry(ORG_ID, CONVERSATION_ID)
    return spec


async def _both(case, chat_mode: str | None = None):  # noqa: ANN202
    from tests.support.sandbox_env import sandbox_backend

    assert case.script is not None
    runs = []
    for driver in (legacy_driver, durable_driver):
        spec = _equipped(case.drive_spec(), case)
        if chat_mode is not None:
            spec = replace(spec, chat_mode=chat_mode)
        with ExitStack() as stack:
            if "local_sandbox" in case.requires:
                stack.enter_context(sandbox_backend("host"))
            runs.append(await drive_captured(driver.drive, spec, case.script))
    return runs[0], runs[1]


def _assert_same(legacy, durable) -> None:
    assert legacy.requests, "the scripted model was never called"
    assert first_difference(legacy.requests, durable.requests) is None, first_difference(
        legacy.requests, durable.requests
    )
    assert outputs_difference(legacy.result, durable.result) is None, outputs_difference(
        legacy.result, durable.result
    )


@pytest.mark.parametrize("case", HARNESS, ids=[c.id for c in HARNESS])
async def test_c24_harness_case_sends_the_same_requests(case) -> None:
    legacy, durable = await _both(case)
    _assert_same(legacy, durable)


@pytest.mark.parametrize("case", AUTO, ids=[c.id for c in AUTO])
async def test_dur3_a2_auto_sends_the_same_requests(case) -> None:
    legacy, durable = await _both(case, "auto")
    _assert_same(legacy, durable)


@pytest.mark.parametrize("tier", ["quick", "react"])
async def test_dur3_a2_auto_classified_takes_the_same_tier_on_both(tier: str) -> None:
    """The intent call's tier decides the loop on both routes, through one ``select_loop_and_goal``."""
    from app.agents.agent_loop.intent import IntentRouteDecision

    (case,) = [c for c in AUTO if c.id == "quick-01-kb-lookup"]

    async def classified(*_: object, **__: object) -> IntentRouteDecision:
        return IntentRouteDecision(rewritten_query=case.task, route=tier)

    with patch("app.agents.agent_loop.router.parse_intent_and_route", classified):
        legacy, durable = await _both(case, "auto")
    _assert_same(legacy, durable)


async def test_dur3_a1_a_full_record_read_on_the_assistants_route_is_answered_the_same_on_both() -> None:
    """Found in DUR3's live dry run: the durable rig answered the assistant route's read gate with a mock, so a
    live model's full-record read came back a storage error there. Both rigs now answer it from one record-access
    fake; neither serves the assistant route's fixture records to a full read (``not available`` on both)."""
    from harness_core.testing import ModelScript
    from harness_core.testing.model_script import Reply, ToolCallSpec, Turn

    (case,) = [c for c in HARNESS if c.id == "chat-01-kb-answer"]
    script = ModelScript(
        id="chat-fetch",
        turns=[
            Turn(reply=Reply(tool_calls=[ToolCallSpec(
                id="f1", name="knowledgegraph__fetch_record", args={"record_ids": ["rec-kb-refund"]}
            )])),
            Turn(reply=Reply(text="Refunds are accepted within 30 days of purchase.")),
        ],
    )
    legacy = await drive_captured(legacy_driver.drive, case.drive_spec(), script)
    durable = await drive_captured(durable_driver.drive, case.drive_spec(), script)
    _assert_same(legacy, durable)
    (step,) = durable.result.recording.tool_steps()
    assert "not available" in (step.error or "") and "storage error" not in (step.error or "")


def test_dur3_a1_the_corpus_covers_every_category_the_durable_path_serves() -> None:
    categories = {c.category for c in HARNESS}
    assert {"quick", "react", "plain_chat", "approval", "injection", "robustness", "sandbox"} <= categories
    assert {"deep", "plan_execute"}.isdisjoint(categories)
    assert {c.id for c in HARNESS} >= {"quick-06-ask-user-question", "react-05-ask-user-question"}


def _lazy_kb_spec(threshold: int | None = None):  # noqa: ANN202 - a DriveSpec
    from dataclasses import replace

    from tests.harness_replay.world import TOOLSETS

    (case,) = [c for c in harness_cases() if c.id == "quick-01-kb-lookup"]
    assert case.script is not None
    spec = replace(case.drive_spec(), fake_toolsets=tuple(t for t in TOOLSETS if t != "knowledge"))
    if threshold is not None:
        spec = replace(spec, env={**spec.env, "PIPESHUB_LAZY_TOOLS_THRESHOLD": str(threshold)})
    return spec, case.script


def _lazy_rule(requests: list[WireRequest]) -> list[bool]:
    return ["`list_toolsets` FIRST" in r.system for r in requests]


async def _threshold_at_the_opening_grant() -> int:
    """The threshold the opening grant sits exactly at. The decision counts the person's tools (D-DISC-1: never the
    internal-tools cards, meta tools, artifact reader or plan tool, nor a deployment switch's tools) with the knowledge
    toolset's entity tools, which both loops drop after deciding when no entity store is wired (main #3308); it does
    not count the late tools granted after it."""
    from app.agents.agent_loop.factory import (
        _SWITCHED_TOOL_NAMES,  # pyright: ignore[reportPrivateUsage]
    )
    from app.agents.agent_loop.hooks.progressive_tools import ENTITY_TOOL_NAMES
    from app.agents.agent_loop.lazy_tools_wiring import (
        HARNESS_TOOL_NAMES,
        HARNESS_TOOLSET,
    )
    from app.harness.assembly.loop import LATE_TOOLS

    spec, script = _lazy_kb_spec(threshold=1000)
    eager = await drive_captured(legacy_driver.drive, spec, script)
    uncounted = HARNESS_TOOL_NAMES | _SWITCHED_TOOL_NAMES | set(LATE_TOOLS)
    persons = [
        n for n in eager.requests[0].tool_names if n not in uncounted and not n.startswith(f"{HARNESS_TOOLSET}__")
    ]
    return len(persons) + len(ENTITY_TOOL_NAMES - _SWITCHED_TOOL_NAMES)


async def test_c24_fb7_a_grant_that_crosses_the_lazy_threshold_mid_run_sends_the_same_requests() -> None:
    """P4.9 FB-7 (DS-1): a knowledge base plus five connectors, one over the threshold, opens lazy; the first
    knowledge result grants ``fetch_record``, and both loops keep the disclosure they opened with."""
    spec, script = _lazy_kb_spec(threshold=await _threshold_at_the_opening_grant() - 1)
    legacy = await drive_captured(legacy_driver.drive, spec, script)
    durable = await drive_captured(durable_driver.drive, spec, script)
    assert first_difference(legacy.requests, durable.requests) is None, first_difference(
        legacy.requests, durable.requests
    )
    assert _lazy_rule(durable.requests) == [True] * len(durable.requests)
    assert "knowledgegraph__fetch_record" in durable.requests[-1].tool_names


async def test_c24_ds2_a_late_grant_never_flips_disclosure_mid_run() -> None:
    """DS-2: the opening grant sits exactly at the threshold, so both loops open eager, and the
    ``fetch_record`` grant, made after the decision, takes it across. Today's loop decided when it built
    the agent and stays eager; the durable loop must not decide again on the grown grant. Since R4 (recs2)
    that grant is in the first request already, and the list is the same on every request."""
    from app.harness.assembly.loop import LATE_TOOLS

    spec, script = _lazy_kb_spec(threshold=await _threshold_at_the_opening_grant())
    legacy = await drive_captured(legacy_driver.drive, spec, script)
    durable = await drive_captured(durable_driver.drive, spec, script)
    assert _lazy_rule(legacy.requests) == [False] * len(legacy.requests)
    assert first_difference(legacy.requests, durable.requests) is None, first_difference(
        legacy.requests, durable.requests
    )
    assert set(LATE_TOOLS) <= set(durable.requests[0].tool_names)
    assert len({tuple(r.tool_names) for r in durable.requests}) == 1
