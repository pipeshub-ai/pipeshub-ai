"""P4.8 information parity: every request the context compiler sends carries what the turn
shaper's request carried for the same journal, differing only in placement for caching
(``tests/contracts/request_parity.py`` says what counts as an element and as placement).

Each corpus runs twice through the real assembler and kernel, the flag off and on, with
the same scripted model, and the two runs' requests are compared call by call."""

from __future__ import annotations

from contextlib import ExitStack
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from harness_core.kernel.core.messages import (
    AssistantMessage,
    ImagePart,
    ImageSource,
    TextPart,
    ToolMessage,
    ToolMessageMeta,
    UserMessage,
)
from harness_core.kernel.core.messages import ToolCall as CoreToolCall
from tests.contracts.golden_support import Scenario
from tests.contracts.request_parity import Request, compare_runs, missing
from tests.contracts.test_durable_goldens import (
    AGENT_SCENARIOS,
    CHAT_SCENARIOS,
    HISTORY_WITH_ATTACHMENT,
    LEGACY_HISTORY,
    STEERED_SCENARIOS,
    run_durably,
)
from tests.unit.harness.assembly import world as w

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator

    from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

# Scripted runs that write after reading, compared with today's loop or with recordings made before the flow
# guard (on by default since UX1), which would hold those writes for a person. The guard is tested on its own.
pytestmark = pytest.mark.usefixtures("flow_guard_off")

FLAG = "HARNESS_CONTEXT_COMPILER"
MODEL_SCENARIOS = [s for s in AGENT_SCENARIOS if not s.llm_unavailable]
# The built-in assistant's durable modes (`/chat/stream`) run the same kernel and host.
CHAT_MODEL_SCENARIOS = [s for s in CHAT_SCENARIOS if not s.llm_unavailable]


async def _arms(
    monkeypatch: pytest.MonkeyPatch, run: Callable[[list[ScriptedTransport]], Awaitable[Any]]
) -> tuple[list[Request], list[Request]]:
    out: list[list[Request]] = []
    for value in ("false", "true"):
        monkeypatch.setenv(FLAG, value)
        transports: list[ScriptedTransport] = []
        _ = await run(transports)
        out.append([Request.of(c) for c in transports[0].calls])
    return out[0], out[1]


def _assert_parity(shaped: list[Request], compiled: list[Request]) -> None:
    assert shaped, "the run calls the model"
    problems = compare_runs(shaped, compiled)
    assert not problems, "\n".join(problems)


# ---- PI-1: the extractor ----

_PNG = ImagePart(source=ImageSource(type="base64", media_type="image/png", data="iVBORw0KGgo="))
_RULE_OFF = "- **Loop control**: each tool result ends with `[loop: step N/MAX, stale_rounds=K]`. Keep going."
_RULE_ON = _RULE_OFF
_TIME = "## Time context\n\n**Current time**: 2026-10-06T00:00:00Z"
_USER = "## Current User Information\n- Name: Ada"
_MEMORY = "## What you remember about this user\n- prefers tables"
_SKILLS = "## Skills\n- pdf: read PDFs"


def _pair() -> tuple[Request, Request]:
    """A shaped request and the compiled request that carries the same information."""
    call = CoreToolCall(id="c1", name="docs__export", arguments={"q": "q3"})
    result = ToolMessage(
        content=[TextPart(text="rows: 3"), _PNG],
        tool_call_id="c1",
        artifact_meta=ToolMessageMeta(artifact_id="art-q3", summary="q3 rows"),
    )
    shaped = Request(
        system=f"You are an agent.\n\n## Operating Rules\n{_RULE_OFF}\n\n{_USER}\n\n{_SKILLS}\n\n{_TIME}",
        messages=(
            UserMessage(content=_MEMORY, injected=True),
            UserMessage(content="Export the Q3 report"),
            AssistantMessage(content=[TextPart(text="Exporting.")], tool_calls=[call]),
            result.model_copy(update={"step_footer": "\n\n[loop: step 1/15, stale_rounds=0]"}),
            UserMessage(content="Add Q4 too.", injected=True),
        ),
    )
    compiled = Request(
        system=f"You are an agent.\n\n## Operating Rules\n{_RULE_ON}\n\n{_USER}\n\n{_SKILLS}",
        messages=(
            UserMessage(content="Export the Q3 report"),
            AssistantMessage(content=[TextPart(text="Exporting.")], tool_calls=[call]),
            result.model_copy(update={"step_footer": "\n\n[loop: step 1/15, stale_rounds=0]"}),
            UserMessage(content="Add Q4 too.", injected=True),
            UserMessage(content=f"{_MEMORY}\n\n{_TIME}", injected=True),
        ),
    )
    return shaped, compiled


def _without(request: Request, drop: str) -> Request:
    """``request`` with every occurrence of ``drop`` taken out of its text."""

    def text(content: Any) -> Any:  # noqa: ANN401
        if isinstance(content, str):
            return content.replace(drop, "")
        return [p.model_copy(update={"text": p.text.replace(drop, "")}) if isinstance(p, TextPart) else p for p in content]

    messages = tuple(m.model_copy(update={"content": text(m.content)}) for m in request.messages)
    return Request((request.system or "").replace(drop, ""), messages, request.tools)


def test_pi1_the_compiled_twin_carries_everything() -> None:
    shaped, compiled = _pair()
    report = missing(shaped, compiled)
    assert report.ok, str(report)


@pytest.mark.parametrize(
    ("drop", "kind"),
    [
        ("Export the Q3 report", "user"),
        ("**Current time**", "section"),
        ("rows: 3", "tool_result"),
        ("Current User Information", "section"),
        ("prefers tables", "user"),
        ("pdf: read PDFs", "section"),
        ("Add Q4 too.", "user"),
        ("Keep going.", "section"),
        ("Exporting.", "assistant"),
    ],
)
def test_pi1_each_element_dropped_from_the_compiled_twin_is_reported(drop: str, kind: str) -> None:
    shaped, compiled = _pair()
    report = missing(shaped, _without(compiled, drop))
    assert kind in {e.kind for e in report.missing}, str(report)


def test_pi1_images_artifacts_calls_and_order_are_checked() -> None:
    shaped, compiled = _pair()
    no_image = Request(
        compiled.system,
        tuple(
            m.model_copy(update={"content": [p for p in m.content if not isinstance(p, ImagePart)]})
            if isinstance(m, ToolMessage)
            else m
            for m in compiled.messages
        ),
    )
    assert {e.kind for e in missing(shaped, no_image).missing} >= {"image"}
    no_artifact = Request(
        compiled.system,
        tuple(m.model_copy(update={"artifact_meta": None}) if isinstance(m, ToolMessage) else m for m in compiled.messages),
    )
    assert "artifact" in {e.kind for e in missing(shaped, no_artifact).missing}
    other_call = Request(
        compiled.system,
        tuple(
            m.model_copy(update={"tool_calls": [CoreToolCall(id="c1", name="docs__export", arguments={"q": "q4"})]})
            if isinstance(m, AssistantMessage)
            else m
            for m in compiled.messages
        ),
    )
    assert "tool_call" in {e.kind for e in missing(shaped, other_call).missing}
    swapped = list(compiled.messages)
    swapped[0], swapped[3] = swapped[3], swapped[0]
    assert missing(shaped, Request(compiled.system, tuple(swapped))).out_of_order


def test_pi1_a_wrong_step_is_reported() -> None:
    shaped, compiled = _pair()
    later = tuple(
        m.model_copy(update={"step_footer": "\n\n[loop: step 2/15, stale_rounds=0]"}) if isinstance(m, ToolMessage) else m
        for m in compiled.messages
    )
    report = missing(shaped, Request(compiled.system, later))
    assert [e.kind for e in report.missing] == ["step"]


# ---- PI-2..PI-4: react, quick, imported history, steering ----


@pytest.mark.parametrize(
    "scenario", MODEL_SCENARIOS + CHAT_MODEL_SCENARIOS, ids=[s.name for s in MODEL_SCENARIOS + CHAT_MODEL_SCENARIOS]
)
async def test_pi2_react_quick_and_chat_requests_carry_what_the_shaper_sent(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    shaped, compiled = await _arms(monkeypatch, lambda t: run_durably(scenario, transports=t))
    _assert_parity(shaped, compiled)


@pytest.mark.parametrize("scenario", MODEL_SCENARIOS, ids=[s.name for s in MODEL_SCENARIOS])
async def test_pi3_with_an_imported_history_and_an_attachment_on_an_earlier_turn(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    history = HISTORY_WITH_ATTACHMENT + LEGACY_HISTORY
    shaped, compiled = await _arms(monkeypatch, lambda t: run_durably(scenario, transports=t, history=history))
    _assert_parity(shaped, compiled)


@pytest.mark.parametrize(("scenario", "steer"), STEERED_SCENARIOS, ids=[s.name for s, _ in STEERED_SCENARIOS])
async def test_pi4_steering_messages_reach_both_requests(
    scenario: Scenario,
    steer: Any,  # noqa: ANN401
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shaped, compiled = await _arms(monkeypatch, lambda t: run_durably(scenario, transports=t, steer=steer))
    assert any(steer.text in str(m.content) for r in compiled for m in r.messages)
    _assert_parity(shaped, compiled)


# ---- PI-5: deep mode, the orchestrator and every child ----


async def test_pi5_deep_every_agent_s_requests_carry_what_the_shaper_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.contracts.deep_scenarios import DEEP_SCENARIOS
    from tests.contracts.test_durable_deep_goldens import run_deep_durably

    for deep in DEEP_SCENARIOS:
        monkeypatch.setenv(FLAG, "false")
        off = await run_deep_durably(deep)
        monkeypatch.setenv(FLAG, "true")
        on = await run_deep_durably(deep)
        assert sorted(on.script.agents) == sorted(off.script.agents)
        for key, agent in off.script.agents.items():
            shaped = [Request.of(c) for c in agent.calls]
            compiled = [Request.of(c) for c in on.script.agents[key].calls]
            problems = compare_runs(shaped, compiled)
            assert not problems, f"{deep.name}/{key}:\n" + "\n".join(problems)


# ---- PI-6: images (attachment, tool results with and without multipart support) ----


@pytest.mark.parametrize("multipart", [True, False], ids=["tool_result_images", "user_message_images"])
async def test_pi6_tool_images_reach_both_requests(multipart: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.contracts import test_durable_images as images

    catalog = images.golden_support._GoldenToolsetRegistry.get_all_toolsets

    def with_viz(self: object) -> dict[str, dict[str, Any]]:
        return {
            **catalog(self),  # type: ignore[arg-type]
            "goldenviz": {"class": images.GoldenViz, "isInternal": True, "provider": "platform", "essential": False, "description": "Charts"},
        }

    monkeypatch.setattr(images.golden_support._GoldenToolsetRegistry, "get_all_toolsets", with_viz)
    with ExitStack() as stack:
        # Only the transport decides now: main #4535 removed the factory's image re-injection hook (L0.1).
        for target in ("app.agents.agent_loop.langchain_transport._supports_multipart_tool_result",):
            stack.enter_context(patch(target, new=lambda _llm: multipart))
        shaped, compiled = await _arms(monkeypatch, lambda t: run_durably(images.SCENARIO, transports=t))
    assert any(isinstance(p, ImagePart) for m in compiled[-1].messages if isinstance(m.content, list) for p in m.content)
    _assert_parity(shaped, compiled)


async def test_pi6_attachment_images_reach_both_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.contracts.test_durable_images import IMAGE

    async def attached(env: Any, *_: Any) -> str:  # noqa: ANN401
        env.context.attachment_image_blocks = [dict(IMAGE)]
        return ""

    scenario = next(s for s in MODEL_SCENARIOS if s.name == "agent_quick_tool_call")
    with patch("app.harness.assembly.assembler._attachments", new=attached):
        shaped, compiled = await _arms(monkeypatch, lambda t: run_durably(scenario, transports=t))
    assert all(any(isinstance(p, ImagePart) for p in m.content) for m in compiled[-1].messages[:1] if isinstance(m.content, list))
    _assert_parity(shaped, compiled)


# ---- PI-7: recalled memory ----


async def test_pi7_recalled_memory_reaches_both_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.harness.assembly.assembler import _RunMemory
    from harness_core.memories.memory import InMemoryMemorySource
    from harness_core.memories.types import RecalledMemory

    def recalled(run: Any, claim: Any, log: Any) -> _RunMemory:  # noqa: ANN401
        memory = RecalledMemory("mem_001", "user", "preference", "Answer in a table.")
        return _RunMemory(InMemoryMemorySource([memory]), True, ())

    scenario = next(s for s in MODEL_SCENARIOS if s.name == "agent_quick_tool_call")
    with patch("app.harness.assembly.assembler.run_memory", new=recalled):
        shaped, compiled = await _arms(monkeypatch, lambda t: run_durably(scenario, transports=t))
    assert all("Answer in a table." in str([m.content for m in r.messages]) for r in compiled[1:])
    _assert_parity(shaped, compiled)


# ---- PI-8: the deadline warning; PI-9: the step and stale rounds ----


def _two_lookups(t: ScriptedTransport) -> None:
    t.add_tool_call(ToolCall(id="call-1", name="goldendocs__lookup_ticket", arguments={"ticket_id": "T-1"}))
    t.add_tool_call(ToolCall(id="call-2", name="goldendocs__lookup_ticket", arguments={"ticket_id": "T-42"}))
    t.add_text("T-42 is resolved; Priya owns it.")


@pytest.fixture
def four_turns(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from harness_core.contracts import dump_contract, validate_contract
    from harness_core.contracts.generated.run_spec import RunSpec

    pinned = w.pinned_spec

    def short(**kwargs: Any) -> RunSpec:  # noqa: ANN401
        raw = dump_contract(pinned(**kwargs))
        raw["budgets"]["maxTurns"] = 4
        return validate_contract(RunSpec, raw)

    monkeypatch.setattr(w, "pinned_spec", short)
    yield


@pytest.mark.usefixtures("four_turns")
async def test_pi8_the_deadline_warning_reaches_both_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    scenario = Scenario("agent_near_deadline", "agent", "quick", "What is the status of T-42?", _two_lookups)
    shaped, compiled = await _arms(monkeypatch, lambda t: run_durably(scenario, transports=t))
    # Both carry the note on the newest result's footer (SYNC2-F2).
    assert "[loop: last tool round:" in shaped[-1].messages[-1].step_footer
    assert "[loop: last tool round:" in compiled[-1].messages[-1].step_footer
    _assert_parity(shaped, compiled)


class GoldenEmpty:
    """A read that finds nothing: an ok result with no content (a stale round)."""

    @tool(
        path="/tools/goldenempty/find_notes",
        short_description="Find notes",
        description="Find meeting notes.",
        parameters=[ToolParameter(name="topic", type=ParameterType.STRING, description="Topic", required=True)],
        tags=[Tag(key="effect", value="read")],
    )
    async def find_notes(self, topic: str) -> str:
        return ""


def _empty_then_found(t: ScriptedTransport) -> None:
    t.add_tool_call(ToolCall(id="call-e1", name="goldenempty__find_notes", arguments={"topic": "q3"}))
    t.add_tool_call(ToolCall(id="call-e2", name="goldenempty__find_notes", arguments={"topic": "q3 plan"}))
    t.add_tool_call(ToolCall(id="call-t", name="goldendocs__lookup_ticket", arguments={"ticket_id": "T-42"}))
    t.add_text("No notes; T-42 is resolved.")


async def test_pi9_step_and_stale_rounds_are_the_shaper_s_numbers(monkeypatch: pytest.MonkeyPatch) -> None:
    scenario = Scenario(
        "agent_stale_rounds",
        "agent",
        "quick",
        "Find the Q3 notes",
        _empty_then_found,
        toolsets={"goldenempty": {"class": GoldenEmpty, "isInternal": True, "provider": "platform", "essential": False, "description": "Notes"}},
    )
    shaped, compiled = await _arms(monkeypatch, lambda t: run_durably(scenario, transports=t))
    footers = [m.step_footer for r in compiled for m in r.messages if isinstance(m, ToolMessage)]
    assert any("[loop: step 2/15, stale_rounds=2]" in f for f in footers), footers
    _assert_parity(shaped, compiled)


class GoldenScrape:
    """A page scrape far over L1's per-result cap."""

    @tool(
        path="/tools/goldenscrape/scrape_page",
        short_description="Scrape a page",
        description="Fetch a whole page as text.",
        parameters=[ToolParameter(name="url", type=ParameterType.STRING, description="URL", required=True)],
        tags=[Tag(key="effect", value="read")],
    )
    async def scrape_page(self, url: str) -> str:
        return f"[page {url}] " + "lorem ipsum dolor " * 6_000 + "Cite this page as ref9."


def _scrape(t: ScriptedTransport) -> None:
    t.add_tool_call(ToolCall(id="call-scrape", name="goldenscrape__scrape_page", arguments={"url": "https://x.test"}))
    t.add_text("The page says lorem ipsum.")


async def test_pi11_a_result_over_the_cap_is_cut_on_both_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    scenario = Scenario(
        "agent_huge_result",
        "agent",
        "quick",
        "What does x.test say?",
        _scrape,
        toolsets={"goldenscrape": {"class": GoldenScrape, "isInternal": True, "provider": "platform", "essential": False, "description": "Web"}},
    )
    shaped, compiled = await _arms(monkeypatch, lambda t: run_durably(scenario, transports=t))
    result = next(m for m in compiled[-1].messages if isinstance(m, ToolMessage))
    assert len(result.text) <= 64_000 and result.text.endswith("Cite this page as ref9.")
    _assert_parity(shaped, compiled)


# ---- PI-10: the harness parity cases and the replay corpus ----


def _parity_cases() -> list[Any]:
    from tests.harness_replay.durable_driver import supports
    from tests.support.parity_cases.harness_cases import load_cases

    return [
        c
        for c in load_cases()
        if c.category in {"react", "quick"} and supports(c.route, c.chat_mode) and not c.requires
    ]


async def _drive_captured(
    make_spec: Callable[[], Any], monkeypatch: pytest.MonkeyPatch
) -> tuple[list[Request], list[Request]]:
    from app.harness.testing_adapters import ScriptedModelTransport
    from harness_core.testing import ScriptedModel
    from tests.harness_replay import durable_driver
    from tests.harness_replay.durable_driver import drive
    from tests.harness_replay.wire import CapturingTransport, WireRequest

    # Both arms admitted at the same second, so the prompts show the same time.
    monkeypatch.setattr(durable_driver.time, "time", lambda: 1_790_000_000.0)
    out: list[list[Request]] = []
    for value in ("false", "true"):
        monkeypatch.setenv(FLAG, value)
        spec = make_spec()
        seen: list[WireRequest] = []
        spec.transport = CapturingTransport(
            ScriptedModelTransport(ScriptedModel(spec.script), model_name=spec.model_name), seen
        )
        _ = await drive(spec)
        out.append([Request.from_wire(r) for r in seen])
    return out[0], out[1]


@pytest.mark.parametrize("case", _parity_cases(), ids=lambda c: c.id)
async def test_pi10_harness_parity_cases_carry_what_the_shaper_sent(case: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN401
    shaped, compiled = await _drive_captured(case.drive_spec, monkeypatch)
    _assert_parity(shaped, compiled)


def _corpus() -> list[Any]:
    from harness_core.testing.replay import Recording
    from tests.harness_replay import durable_driver
    from tests.harness_replay.record import CORPUS_DIR

    return [
        p
        for p in sorted(CORPUS_DIR.glob("*.json"))
        if durable_driver.supports(Recording.load(p).inputs.route, Recording.load(p).inputs.chat_mode)
    ]


@pytest.mark.parametrize("path", _corpus(), ids=lambda p: p.stem)
async def test_pi10_the_replay_corpus_carries_what_the_shaper_sent(path: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN401
    from harness_core.testing.replay import FrozenTools, Recording, recording_to_script
    from tests.harness_replay.legacy_driver import DriveSpec
    from tests.harness_replay.world import WorldFixtures

    recording = Recording.load(path)

    def spec() -> DriveSpec:
        return DriveSpec(
            scenario=recording.manifest.scenario,
            query=recording.inputs.query,
            route=recording.inputs.route,
            chat_mode=recording.inputs.chat_mode,
            script=recording_to_script(recording),
            fixtures=WorldFixtures.model_validate(recording.inputs.fixtures),
            frozen=FrozenTools(recording),
            model_name=recording.manifest.model,
        )

    shaped, compiled = await _drive_captured(spec, monkeypatch)
    _assert_parity(shaped, compiled)


# ---- P4.9 RP-1: the wave 1 reports suite (each case's reference script, the durable loop's spec) ----


def _reports() -> list[str]:
    from tests.support.parity_cases.wave1_cases import select

    return [c.id for c in select("reports", None)]


@pytest.mark.parametrize("case_id", _reports())
async def test_rp1_wave1_reports_cases_carry_what_the_shaper_sent(case_id: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.support.parity_cases.wave1_cases import select
    from tests.support.parity_cases.wave1_reference import script_for
    from tests.support.parity_cases.wave1_systems import (  # pyright: ignore[reportPrivateUsage]
        _registry,
        _spec,
        task_for,
    )

    (case,) = select(None, [case_id])

    def spec() -> Any:  # noqa: ANN401
        task = task_for(case, 0, script_for(case))
        drive = _spec(task, _registry(task), chat_mode="quick", model="scripted-model", env={})
        drive.script = task.script
        return drive

    shaped, compiled = await _drive_captured(spec, monkeypatch)
    _assert_parity(shaped, compiled)
