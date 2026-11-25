"""CTL-6: a coder agent's turn runs on the loop; ``code_task`` hands a task to an engine child.

The loop offers ``code_task`` only when the run's spec pins an engine authority (``x.pipeshub.codeTask``), then
discloses no ``run_code`` (one way to run code), says when to hand off, allows one handoff per turn, and a turn that
does not hand off never creates a sandbox. The real ``PipesHubRuntimeAssembler`` and ``RunDriver`` over the
in-memory journal, as ``test_durable_run_code`` drives them.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import ExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from harness_core.contracts import validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.driver import RunDriver
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable.delegation import Delegated, DelegationView, Refuse
from harness_core.testing import ManualClock, ModelScript, Reply, ScriptedModel, Turn
from harness_core.testing.durable import ScriptedModelPort
from tests.harness_replay.artifacts import ParityArtifactRegistry
from tests.support.sandbox_env import sandbox_backend, sandbox_env

from . import world as w

if TYPE_CHECKING:
    from collections.abc import Iterator


ROOT = Path(__file__).resolve().parents[6]
PROFILE = json.loads(
    (ROOT / "contracts/harness/vectors/engine-profiles.json").read_text()
)["release"][0]
CODE_TASK = "code_task"


@pytest.fixture(autouse=True)
def _deployment() -> Iterator[None]:
    with ExitStack() as stack:
        stack.enter_context(w.deployment())
        stack.enter_context(
            patch(
                "app.services.artifact_registry.ArtifactRegistryService",
                new=ParityArtifactRegistry(w.ORG, "ses-1"),
            )
        )
        stack.enter_context(
            patch.dict(
                os.environ, {**sandbox_env("host"), "SANDBOX_ALLOW_NETWORK": "false"}
            )
        )
        stack.enter_context(sandbox_backend("host"))
        yield


def _spec(*, code_task: bool = True) -> RunSpec:
    spec = w.pinned_spec(tool_ids=[], sandbox="agent-code")
    if not code_task:
        return spec
    raw = spec.model_dump(by_alias=True, exclude_none=True)
    raw["extensions"]["x.pipeshub"]["codeTask"] = {"profile": PROFILE}
    raw["budgets"]["maxChildren"] = 1
    return validate_contract(RunSpec, raw)


def _port(*replies: Reply) -> ScriptedModelPort:
    return ScriptedModelPort(
        ScriptedModel(ModelScript(id="t", turns=[Turn(reply=r) for r in replies]))
    )


async def _build(spec: RunSpec) -> Any:  # noqa: ANN401
    assembler = PipesHubRuntimeAssembler(
        w.Services(), model_factory=w.model_factory(_port(Reply(text="ok")))
    )
    return await assembler.build(w.claim(spec))  # type: ignore[arg-type]


def _delegated(invocation: str, goal: str, turn: int = 1) -> Delegated:
    call = ToolCall(id=invocation, name=CODE_TASK, arguments={"goal": goal})
    return Delegated(invocation_id=invocation, call=call, turn=turn, state="pending")  # type: ignore[arg-type]


def test_c6_the_run_context_reads_the_pinned_engine_authority() -> None:
    from app.harness.assembly.context import RunContext

    assert RunContext.from_claim(w.claim(_spec())).code_task is True
    assert RunContext.from_claim(w.claim(_spec(code_task=False))).code_task is False


async def test_c6_2_code_task_hands_the_goal_to_an_engine_child() -> None:
    runtime = await _build(_spec())
    delegation = runtime.delegation
    assert delegation is not None
    schema = next(s for s in delegation.schemas() if s.name == CODE_TASK)
    assert "file" in schema.description and "directly" in schema.description
    first = _delegated("t1.0", "Write the travel policy to travel_policy.md")
    decided = delegation.decide(first, DelegationView(batch=(first,)))
    assert decided.executor is not None and decided.executor.kind == "engine"  # type: ignore[union-attr]
    # ADR-RUN-01 D5 (WP93-SPEC): the person's message as they wrote it, then the model's goal as its notes.
    assert decided.goal == (  # type: ignore[union-attr]
        "find the bug\n\nNotes from the chat agent that handed this task to you:\n"
        "Write the travel policy to travel_policy.md"
    )
    await runtime.close()


async def test_c6_one_handoff_per_turn() -> None:
    runtime = await _build(_spec())
    first, second = _delegated("t1.0", "write a.md"), _delegated("t1.1", "write b.md")
    assert isinstance(
        runtime.delegation.decide(second, DelegationView(batch=(first, second))), Refuse
    )
    later = _delegated("t2.0", "write c.md", turn=2)
    assert isinstance(
        runtime.delegation.decide(
            later, DelegationView(batch=(later,), earlier=(first,))
        ),
        Refuse,
    )
    await runtime.close()


async def test_c6_5_no_engine_authority_no_code_task() -> None:
    runtime = await _build(_spec(code_task=False))
    names = (
        [s.name for s in runtime.delegation.schemas()]
        if runtime.delegation is not None
        else []
    )
    assert CODE_TASK not in names
    await runtime.close()


async def _drive(spec: RunSpec, *replies: Reply) -> tuple[Any, ScriptedModelPort]:
    clock = ManualClock()
    journal = w.pipeshub_journal(clock=clock, lease_ms=30_000)
    journal.admit(
        "run-1",
        org_id=w.ORG,
        session_id="ses-1",
        spec=spec,
        input_text="what is our travel policy?",
    )
    port = _port(*replies)
    driver = RunDriver(
        journal=journal,
        assembler=w.ClaimAssembler(
            PipesHubRuntimeAssembler(w.Services(), model_factory=w.model_factory(port))
        ),
        clock=clock,
    )
    return await driver.run_claimed(await w.claim_next(journal)), port


async def test_c6_1_a_turn_that_does_not_hand_off_creates_no_sandbox(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.agent_loop_lib.sandbox.manager import SandboxManager

    caplog.set_level(logging.INFO)
    created: list[object] = []
    original = SandboxManager.get_or_create

    async def counted(self: SandboxManager, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        created.append(args)
        return await original(self, *args, **kwargs)

    with patch.object(SandboxManager, "get_or_create", counted):
        done, port = await _drive(_spec(), Reply(text="Book flights 14 days ahead."))
    assert done.status == "completed"
    assert created == []
    offered = [t.name for t in port.calls[0].tools or []]
    assert CODE_TASK in offered
    # One way to run code: the engine. The grant keeps run_code; the loop is not shown it.
    assert "run_code" not in offered
    assert any(
        "tools.disclosure" in r.getMessage() and "run_code" in r.getMessage()
        for r in caplog.records
    )
    # When to hand off, said once in the system prompt.
    assert CODE_TASK in (port.calls[0].system or "")


async def test_c6_5_without_the_authority_run_code_stays_as_today() -> None:
    _done, port = await _drive(_spec(code_task=False), Reply(text="ok"))
    offered = [t.name for t in port.calls[0].tools or []]
    assert "run_code" in offered and CODE_TASK not in offered
    assert CODE_TASK not in (port.calls[0].system or "")


def test_c6_the_worker_advertises_the_code_task_tool() -> None:
    from app.harness.worker.capabilities import advertised

    # The loop part runs on any worker of this build; whether engines run anywhere, Node decides.
    for engines in (True, False):
        assert "code_task_tool" in [
            f.root for f in advertised(engines=engines).features
        ]


def test_c6_the_engine_names_its_files_by_name_not_their_out_path() -> None:
    from app.harness.engines.instructions import ENGINE_TASK_INSTRUCTIONS

    assert "name the files you wrote under out/" not in ENGINE_TASK_INSTRUCTIONS
    assert "never its out/ path" in ENGINE_TASK_INSTRUCTIONS
    # Deliverables are still written under out/: that is what gets published.
    assert "Write every deliverable file under out/" in ENGINE_TASK_INSTRUCTIONS


# --- the child's files reach the parent's answer ---------------------------------------------------------


def _presented(lifecycle: str, version: int = 1) -> Any:  # noqa: ANN401
    from app.harness.artifacts.events import presented_event
    from harness_core.artifacts.types import ArtifactCard

    card = ArtifactCard(
        artifact_id="art-1",
        name="travel_policy.md",
        mime_type="text/markdown",
        size_bytes=276,
        kind="DOCUMENT",
        version=version,
        lifecycle=lifecycle,
        storage_ref="doc-1",
    )
    return presented_event(card)


def test_c6_2_the_engine_childs_result_names_its_final_files() -> None:
    from app.harness.engines.child_result import with_published_files

    events = [_presented("draft"), _presented("final")]
    document = with_published_files(
        {"output": "Done.", "success": True}, [e.data for e in events]
    )
    assert document["files"] == [
        {
            "artifactId": "art-1",
            "fileName": "travel_policy.md",
            "mimeType": "text/markdown",
            "sizeBytes": 276,
            "artifactType": "DOCUMENT",
            "version": 1,
            "documentId": "doc-1",
        },
    ]
    assert (
        with_published_files(
            {"output": "x", "success": True}, [_presented("discarded").data]
        )["files"]
        == []
    )


async def test_c6_2_the_parent_presents_the_childs_files_as_its_own_cards() -> None:
    runtime = await _build(_spec())
    call = ToolCall(id="c1", name=CODE_TASK, arguments={"goal": "write it"})
    document = {
        "output": "I wrote out/travel_policy.md.",
        "success": True,
        "files": [
            {
                "artifactId": "art-1",
                "fileName": "travel_policy.md",
                "mimeType": "text/markdown",
                "sizeBytes": 276,
                "artifactType": "DOCUMENT",
                "version": 1,
                "documentId": "doc-1",
            }
        ],
    }
    outcome = await runtime.delegation.outcome(call, document)
    assert outcome.error is False
    presented = [e for e in outcome.events if e.type == "x.pipeshub.artifact.presented"]
    # Presentation, as the presenter's own cards are: a fold that does not know it must not refuse the run.
    assert all(e.critical is False for e in presented)
    assert [
        (e.data["fileName"], e.data["recordId"], e.data["card"], e.data["lifecycle"])
        for e in presented
    ] == [("travel_policy.md", "art-1", True, "final")]
    # What the loop reads: the engine's words and the files by name, never an out/ path.
    shown = json.dumps(outcome.content)
    assert "travel_policy.md" in shown and "out/" not in shown
    await runtime.close()


# --- files attached to the turn (inputArtifacts) ------------------------------------------------------------------


class _Registry:
    """The registry calls the access check makes; ``denied``: artifact ids the actor may not read."""

    def __init__(self, denied: frozenset[str] = frozenset()) -> None:
        from types import SimpleNamespace

        from app.services.artifact_registry.access import AccessDeniedError

        async def authorize_read(actor: Any, artifact_id: str) -> dict[str, Any]:  # noqa: ANN401
            if artifact_id in denied:
                raise AccessDeniedError(artifact_id)
            return {}

        self.access = SimpleNamespace(authorize_read=authorize_read)
        self.read: list[str] = []

    async def resolve(self, *, actor: Any, ref: str) -> Any:  # noqa: ANN401
        from types import SimpleNamespace

        from app.models.entities import LifecycleStatus

        return SimpleNamespace(
            artifact_id=ref, name="q3.csv", logical_name="q3.csv", mime_type="text/csv", size_bytes=2048,
            version=2, conversation_id="ses-1", lifecycle_status=LifecycleStatus.PUBLISHED,
        )

    async def get_content(self, **kwargs: Any) -> bytes:  # noqa: ANN401
        self.read.append(str(kwargs))
        return b"never read for a listing"


async def test_input_artifacts_the_loop_is_told_which_files_came_with_the_turn_never_their_bytes() -> None:
    from app.harness.engines.inputs import input_listing
    from app.services.artifact_registry.models import Actor
    from harness_core.journal.types import InputArtifactRef

    registry = _Registry(denied=frozenset({"art-secret"}))
    listing = await input_listing(
        registry,  # type: ignore[arg-type]
        Actor(org_id=w.ORG, user_id=w.ACTOR),
        (InputArtifactRef("art-q3", 2), InputArtifactRef("art-secret")),
        "ses-1",
    )
    assert "q3.csv (text/csv, 2048 bytes; artifact id art-q3, version 2)" in listing
    assert "art-secret: not available to you" in listing
    assert registry.read == []


# --- a refused hand-off the person can act on ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "notice"),
    [
        ("ACCOUNT_MISSING", "account_missing"),
        ("ORG_POLICY_REFUSED", "model_access"),
        ("ACCOUNT_NOT_OWNED", "model_access"),
        ("ENGINE_PROFILE_WIDENING", "engine_profile"),
        ("ENGINE_UNAVAILABLE", "engine_unavailable"),
        (None, None),
    ],
)
def test_c6_a_refused_hand_off_tells_the_person_what_to_do(code: str | None, notice: str | None) -> None:
    from app.harness.assembly.code_task import CodeTaskDelegation, CombinedDelegation
    from harness_core.journal.children import ChildRefusal, ChildRefused

    call = ToolCall(id="c1", name=CODE_TASK, arguments={"goal": "write it"})
    refusal = (
        ChildRefused(ChildRefusal.ACCESS_REFUSED, "no", code=code)
        if code is not None
        else ChildRefused(ChildRefusal.LIMIT_REACHED, "no")
    )
    for delegation in (CodeTaskDelegation(), CombinedDelegation(CodeTaskDelegation())):
        events = delegation.refused(call, refusal)
        if notice is None:
            assert events == ()
        else:
            assert [(e.type, e.data) for e in events] == [("x.pipeshub.run.notice", {"code": notice})]


def test_wp111_a_hand_off_refused_because_the_engine_was_turned_off_says_the_engine_is_off() -> None:
    """WP-111: an admin turned the engine off after the turn was admitted; the chat says so and what to do."""
    from app.harness.assembly.code_task import CodeTaskDelegation, CombinedDelegation
    from harness_core.journal.children import ChildRefusal, ChildRefused

    call = ToolCall(id="c1", name=CODE_TASK, arguments={"goal": "write it"})
    refusal = ChildRefused(ChildRefusal.ACCESS_REFUSED, "no", code="ENGINE_OFF")
    for delegation in (CodeTaskDelegation(), CombinedDelegation(CodeTaskDelegation())):
        events = delegation.refused(call, refusal)
        assert [(e.type, e.data) for e in events] == [
            ("x.pipeshub.run.notice", {"code": "ran_without_code_tools", "item": "engine_off"})
        ]
