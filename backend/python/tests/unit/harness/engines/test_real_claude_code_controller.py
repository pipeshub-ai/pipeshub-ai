"""RX-CC8 (E9): ``EngineController`` with Claude Code on its real binary, end to end.

E8's controller lane (``test_real_codex_controller.Lane``) on the release registry's Claude Code adapter, with
E3's real model gateway as the deployment's model route (the proxy's static upstream, as in production): the
controller picks the Anthropic Messages API for Claude Code from its capabilities (E3c), and the gateway's real
``OpenAITransport`` calls the scripted model as the org's provider.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING

import pytest

from harness_core.access.types import RunBudget
from harness_core.engines.events import turn_id_for
from harness_core.engines.types import FollowUp
from harness_core.skills.types import SkillPin, SkillRef
from harness_engines.pipeshub import sdk as sdk_module
from tests.unit.harness.engines.lane_releases import lane_release
from tests.unit.harness.engines.ph_e2e import (
    INPUTS_PROMPT,
    INPUTS_SEED,
    REPOSITORY,
    SDK_PROMPT,
    SESSION_PH_PROMPT,
    assert_c312_commands_worked,
    assert_input_staged_on_first_read,
    assert_ph_called_the_tool,
    assert_session_ph_found_the_repository_tool,
    assert_stubs_called_the_tool,
    run_c312_turn,
    with_input_staging,
    with_tool_folder,
)
from tests.unit.harness.engines.ph_e2e import PROMPT as PH_PROMPT
from tests.unit.harness.engines.real_stack import (
    ModelGatewayLane,
    _free_port,  # pyright: ignore[reportPrivateUsage]
    engine_broker,
    lane_available,
    start_stack,
    stop_stack,
)
from tests.unit.harness.engines.test_real_codex_controller import SKILL_MD, TENANT, Lane
from tests.unit.harness.sandbox_broker.conftest import cleanup, cleanup_networks

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator, Mapping

    from app.harness.sandbox.docker_broker import DockerSandboxBroker
    from harness_core.engines.types import EngineVersion
    from tests.unit.harness.engines.real_stack import RealStack

pytestmark = [pytest.mark.docker, pytest.mark.timeout(900, method="thread")]

IMAGE: str = lane_release("claude_code").image
_E3_PORT: dict[str, int] = {}


def _pinned() -> EngineVersion:
    return lane_release("claude_code").version


@pytest.fixture(scope="module")
def real_stack() -> Iterator[RealStack]:
    """The conftest's stack, with E3's gateway as the proxy's static model upstream."""
    available, reason = lane_available(IMAGE)
    if not available:
        pytest.skip(f"{reason} (deployment/sandbox-images/claude-code/build.sh --binary PATH)")

    def upstreams(stack: RealStack) -> Mapping[str, str]:
        _E3_PORT["port"] = _free_port(stack.host_ip)
        return {"EGRESS_UPSTREAMS": json.dumps({"model-gateway": f"http://{stack.host_ip}:{_E3_PORT['port']}"})}

    stack = start_stack(upstreams=upstreams)
    yield stack
    stop_stack(stack)


@pytest.fixture
async def model_gateway(stack: RealStack) -> AsyncIterator[ModelGatewayLane]:
    async with ModelGatewayLane(stack, _E3_PORT["port"]).serving() as lane:
        yield lane


@pytest.fixture
async def claude_broker(stack: RealStack) -> AsyncIterator[DockerSandboxBroker]:
    namespace = f"io.pipeshub.e9ctl.{uuid.uuid4().hex[:8]}"
    prefix = "pxh-e9c-" + namespace.rsplit(".", 1)[1]
    created, provider = await engine_broker(stack, namespace, prefix, image=IMAGE)
    yield created
    await provider.run_blocking(cleanup, provider, namespace, prefix)
    await provider.run_blocking(cleanup_networks, provider, namespace, stack.proxy)
    provider.close()


def _lane(stack: RealStack, broker: DockerSandboxBroker, models: ModelGatewayLane, **extra: object) -> Lane:
    return Lane(stack, broker, models, version=_pinned(), image=IMAGE, agent="E9", **extra)  # type: ignore[arg-type]


async def test_rxcc8_a_claude_code_turn_end_to_end_then_the_next_turn_restores_it(
    stack: RealStack, claude_broker: DockerSandboxBroker, model_gateway: ModelGatewayLane
) -> None:
    lane = _lane(stack, claude_broker, model_gateway)
    await lane.seed({"data/in.csv": b"a,b\n1,2\n"})
    lane.skills.publish(TENANT, "report", {"SKILL.md": SKILL_MD})
    bundle = await lane.builder.build(lane.scope, SkillRef("report"))
    run = lane.run(1, skills=(SkillPin("report", bundle.digest),))
    prompt = (
        "RXCC8-MARKER\nshell mkdir -p out && cp data/in.csv out/report.csv && cat /skills/report/SKILL.md > notes.md\n"
        'tool search {"q": "churn"}\nsay done'
    )
    first = await lane.controller.run_turn(run, lane.task(0, prompt))
    assert (first.reason, first.final_text.strip()) == ("completed", "done")
    head = await lane.head()
    assert head["out/report.csv"] == b"a,b\n1,2\n"
    assert head["notes.md"] == SKILL_MD
    assert [(p.path, p.lifecycle) for p in first.published] == [("report.csv", "draft")]
    history = await lane.publications.history(run.binding)
    assert [c.lifecycle for c in history.cards] == ["draft", "final"]
    assert "before_effect" in [s.reason for s in lane.sink.saves]
    assert first.checkpoint is not None and lane.sink.checkpoints[-1].blob == first.checkpoint
    # The controller gave Claude Code the Messages API; every model call went through E3c, none refused.
    assert {(r.method, r.path) for r in model_gateway.requests} == {("POST", "/v1/messages")}
    assert model_gateway.refused == []
    # Claude Code's own config never reaches the session's workspace.
    assert not any(path.startswith(".claude") for path in head)
    second = await lane.controller.run_turn(
        lane.run(2, restore=first.checkpoint), FollowUp(turn_id_for(lane.run_id, 1), "say again")
    )
    assert (second.reason, second.restored, second.restore_issue) == ("completed", "state", None)
    last = lane.gateways[1].model_calls[-1]
    assert any("RXCC8-MARKER" in m.content for m in last.messages if m.role == "user")


async def test_rxcc8b_the_model_gateways_budget_refusal_ends_the_turn_as_a_limit(
    stack: RealStack, claude_broker: DockerSandboxBroker, model_gateway: ModelGatewayLane
) -> None:
    lane = _lane(stack, claude_broker, model_gateway, budget=RunBudget(max_cost_usd=None, max_tokens=1))
    outcome = await lane.controller.run_turn(lane.run(1), lane.task(0, 'tool search {"q": 1}\nsay never'))
    assert (outcome.reason, outcome.limit) == ("limit", "budget")


async def test_ccpy_claude_code_runs_the_script_it_wrote_in_its_own_shell_and_the_output_is_the_deliverable(
    stack: RealStack, claude_broker: DockerSandboxBroker, model_gateway: ModelGatewayLane
) -> None:
    """CCPY (EMPTYOUT for Claude Code) on the real binary and the release run image: the output file exists empty
    when a gateway call publishes it as a draft, then ``python3`` in Claude Code's own Bash tool fills it; the
    final artifact is the program's output."""
    lane = _lane(stack, claude_broker, model_gateway)
    program = "print(*[n for n in range(2, 30) if all(n % d for d in range(2, n))], sep=chr(10))"
    prompt = (
        f"shell mkdir -p out && printf '%s\\n' '{program}' > primes.py && : > out/first_ten_primes_output.txt\n"
        'tool search {"q": "primes"}\n'
        "shell python3 primes.py > out/first_ten_primes_output.txt\n"
        "say done"
    )
    run = lane.run(1)
    outcome = await lane.controller.run_turn(run, lane.task(0, prompt))
    assert outcome.reason == "completed"
    primes = b"2\n3\n5\n7\n11\n13\n17\n19\n23\n29\n"
    assert (await lane.head())["out/first_ten_primes_output.txt"] == primes
    [artifact] = lane.artifacts.listing(run.binding)
    assert (artifact.name, artifact.lifecycle) == ("first_ten_primes_output.txt", "final")
    assert await lane.artifacts.content(run.binding, artifact.artifact_id, artifact.version) == primes


async def test_ph_e2e_claude_code_runs_ph_from_its_shell_through_the_real_gateway(
    stack: RealStack, claude_broker: DockerSandboxBroker, model_gateway: ModelGatewayLane
) -> None:
    """R3 (``ph_e2e``): ``ph call`` in Claude Code's Bash tool, through the egress proxy, to the run's tool."""
    lane = _lane(stack, claude_broker, model_gateway)
    with_tool_folder(lane.controller)
    outcome = await lane.controller.run_turn(lane.run(1), lane.task(0, PH_PROMPT))
    assert_ph_called_the_tool(outcome, await lane.head(), lane.gateways[1])


async def test_ws14_sdk_e2e_claude_code_calls_the_tool_through_the_python_and_typescript_stubs(
    stack: RealStack, claude_broker: DockerSandboxBroker, model_gateway: ModelGatewayLane
) -> None:
    """WS-14 (C3.15, C3.16): from Claude Code's Bash tool, the session folder's stubs answer what ``ph tools call``
    answers, through the egress proxy and the real gateway."""
    lane = _lane(stack, claude_broker, model_gateway)
    with_tool_folder(lane.controller)
    outcome = await lane.controller.run_turn(lane.run(1), lane.task(0, SDK_PROMPT))
    assert_stubs_called_the_tool(outcome, await lane.head(), lane.gateways[1])


async def test_ph_e2e_c312_claude_code_runs_ph_skill_publish_and_ask_through_the_real_gateway(
    stack: RealStack, claude_broker: DockerSandboxBroker, model_gateway: ModelGatewayLane
) -> None:
    """C3.12 (``ph_e2e``): ``ph skill``, ``ph publish`` and ``ph ask`` from Claude Code's shell, through the egress proxy and
    the real tool gateway, as their tool forms are."""
    lane = _lane(stack, claude_broker, model_gateway)
    run, outcome, head = await run_c312_turn(lane, TENANT, SKILL_MD, lane.task)
    await assert_c312_commands_worked(lane, run, outcome, head, SKILL_MD)


async def test_j26_c5_01_claude_code_places_a_large_input_on_its_first_read(
    stack: RealStack, claude_broker: DockerSandboxBroker, model_gateway: ModelGatewayLane
) -> None:
    """J-26 (C5.01): Claude Code starts without the large input and places it from its shell with the session's
    ``ph inputs get``, through the egress proxy and the real gateway, to the controller."""
    lane = _lane(stack, claude_broker, model_gateway)
    with_tool_folder(lane.controller)
    with_input_staging(lane.controller)
    await lane.seed(INPUTS_SEED)
    outcome = await lane.controller.run_turn(lane.run(1), lane.task(0, INPUTS_PROMPT))
    assert_input_staged_on_first_read(outcome, await lane.head())


async def test_ws14_c5_01_claude_code_stubs_rendered_on_first_import_answer_as_ph(
    stack: RealStack, claude_broker: DockerSandboxBroker, model_gateway: ModelGatewayLane,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WS-14 + C5.01: a grant past the eager threshold gets stubs rendered on first import in Claude Code's image."""
    monkeypatch.setattr(sdk_module, "SDK_EAGER_MAX_TOOLS", 0)
    lane = _lane(stack, claude_broker, model_gateway)
    with_tool_folder(lane.controller)
    outcome = await lane.controller.run_turn(lane.run(1), lane.task(0, SDK_PROMPT))
    assert_stubs_called_the_tool(outcome, await lane.head(), lane.gateways[1])


async def test_wp70_sessionph_claude_code_finds_the_repository_tool_with_the_session_ph(
    stack: RealStack, claude_broker: DockerSandboxBroker, model_gateway: ModelGatewayLane, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WP70-SESSIONPH (Claude Code's Bash tool): the session folder's ph comes first."""
    from tests.unit.harness.engines import test_real_codex_controller as codex

    monkeypatch.setattr(codex, "GRANT", (*codex.GRANT, REPOSITORY))
    lane = _lane(stack, claude_broker, model_gateway)
    with_tool_folder(lane.controller)
    outcome = await lane.controller.run_turn(lane.run(1), lane.task(0, SESSION_PH_PROMPT))
    assert_session_ph_found_the_repository_tool(outcome, await lane.head())


async def test_fixskills_claude_code_sees_the_builtin_pdf_pack_a_default_chat_run_pins(
    stack: RealStack, claude_broker: DockerSandboxBroker, model_gateway: ModelGatewayLane
) -> None:
    """FIX-SKILLS on the real binary: a default chat run's offer (the builtin packs, pinned by digest as Node pins
    them) is what the engine mounts, so the ``pdf`` pack is in Claude Code's skills folder and Claude Code lists it
    to the model with its description. The model is the lane's scripted one."""
    from types import SimpleNamespace

    from app.agents.agent_loop.skills.bundle_source import GraphSkillSource
    from app.agents.agent_loop.skills.manager_factory import (
        reset_builtin_reconcile_state,
    )
    from app.agents.run_definition import (
        ASSISTANT_AGENT_KEY,
        AgentRunDefinition,
        pin_skill_content,
        with_offered_skills,
    )
    from app.harness.engines.durable import _pins  # pyright: ignore[reportPrivateUsage]
    from harness_engines.claude_code.launch import SKILLS_LINK
    from tests.unit.agents.adapter.test_skills_graph_store import FakeGraphProvider

    graph = FakeGraphProvider()
    reset_builtin_reconcile_state()
    offered = await with_offered_skills(
        AgentRunDefinition(agentKey=ASSISTANT_AGENT_KEY, definitionVersion=1), graph, TENANT, ("me",), enabled=True
    )
    pinned = await pin_skill_content(offered, GraphSkillSource(graph), TENANT)  # type: ignore[arg-type]
    spec = {"x.pipeshub": {"skills": [{"name": s.name, "bundleDigest": s.bundle_digest} for s in pinned.skills]}}
    pins = _pins(SimpleNamespace(spec=SimpleNamespace(extensions=spec)))  # type: ignore[arg-type]
    assert "pdf" in {pin.name for pin in pins}
    lane = _lane(stack, claude_broker, model_gateway, skills=GraphSkillSource(graph))  # type: ignore[arg-type]

    outcome = await lane.controller.run_turn(
        lane.run(1, skills=pins), lane.task(0, f"shell ls {SKILLS_LINK}/pdf > notes.md\nsay done")
    )

    assert (outcome.reason, outcome.final_text.strip()) == ("completed", "done")
    assert b"SKILL.md" in (await lane.head())["notes.md"]
    listed = json.dumps([r.body for r in model_gateway.requests if r.method == "POST"])
    assert "Use this skill whenever the user asks for a PDF" in listed, "Claude Code lists the pack to the model"
    pdf = next(s for s in pinned.skills if s.name == "pdf")
    assert pdf.bundle_digest in {pin.bundle_digest for pin in pins}
