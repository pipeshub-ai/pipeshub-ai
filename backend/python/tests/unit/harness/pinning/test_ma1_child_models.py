"""MA1 QA37-I2, I5, I7, K5: a child whose role sets a model runs on that model, on the worker.

Node decides a child's model at its spawn: the child's spec names the role's model (``model.policy[0]``,
``x.pipeshub.model``) and, for a pinned run, its pin's ``llm`` is that model's configured entry. The worker
resolves nothing: a pinned attempt builds its pin's ``llm``, an attempt without a pin the entry behind its spec's
``modelKey``. The provider client factory is the faked outermost I/O (as in QA-1's model-pin cases): one scripted
chat model per provider and model, recording which a run built and with which configured entry.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast, override

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

from app.control_plane.runtime.pinning import assemble
from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from app.harness.assembly.context import RunContext
from app.harness.contracts.generated.pipeshub.event_catalog import (
    EVENT_MODELS,
    EVENT_WRITERS,
)
from harness_core.contracts import canonical_digest, dump_contract, validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.driver import RunDriver
from harness_core.journal.children import ChildRequest
from harness_core.testing import ManualClock
from tests.unit.harness.assembly import world as w

from .support import (
    CREATE,
    DEPLOYMENT_ASKS_FOR_WRITES,
    JIRA_CREATE,
    JIRA_SEARCH,
    LEASE_MS,
    ControlPlane,
    LoopRig,
    child_pin,
    llm,
    pinned,
    run_policies,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from pydantic import JsonValue

    from harness_core.journal.child_model import ChildSpecModel
    from harness_core.journal.types import JournalEvent

A = ("openai", "gpt-x")
B = ("anthropic", "claude-b")
C = ("anthropic", "claude-c")
A_ENTRY = llm("model-1", *A)
B_ENTRY = llm("model-b", *B)
RESEARCHER: dict[str, JsonValue] = {"role": "researcher", "toolNames": []}
PARENT = "run-1"
SESSION = "ses-1"


class ScriptedChatModel:
    """A provider's chat model as LangChain exposes it: each call answers the next scripted message, streamed whole."""

    def __init__(self, *replies: AIMessage) -> None:
        self._replies = list(replies)
        self.calls = 0

    def bind_tools(self, tools: list[object]) -> ScriptedChatModel:
        return self

    def with_structured_output(self, schema: object, include_raw: bool = False) -> ScriptedChatModel:
        return self

    async def ainvoke(self, messages: list[object], config: object = None) -> AIMessage:
        reply = self._replies[min(self.calls, len(self._replies) - 1)]
        self.calls += 1
        return reply

    async def astream(self, messages: list[object], config: object = None) -> AsyncIterator[AIMessageChunk]:
        message = await self.ainvoke(messages, config)
        yield AIMessageChunk(
            content=message.content,
            tool_call_chunks=[
                {"name": c["name"], "args": json.dumps(c["args"]), "id": c["id"], "index": i}
                for i, c in enumerate(message.tool_calls)
            ],
        )


class Providers:
    """The deployment's providers: each (provider, model) is one chat model that asks for a write, then answers."""

    def __init__(self) -> None:
        self.built: list[tuple[str, str]] = []
        self.keys: list[str] = []
        self.models: dict[tuple[str, str], ScriptedChatModel] = {}

    async def __call__(self, provider: str, config: dict[str, Any], model_name: str, *_: object, **__: object) -> ScriptedChatModel:
        key = (provider, model_name)
        self.built.append(key)
        self.keys.append(str(config.get("modelKey")))
        return self.models.setdefault(
            key,
            ScriptedChatModel(
                AIMessage(content="", tool_calls=[{"name": CREATE, "args": {"title": "x"}, "id": "c1"}]),
                AIMessage(content=f"answered by {model_name}"),
            ),
        )

    def calls(self, model: tuple[str, str]) -> int:
        return self.models[model].calls if model in self.models else 0


@pytest.fixture
def providers(monkeypatch: pytest.MonkeyPatch) -> Iterator[Providers]:
    models = Providers()
    monkeypatch.setattr("app.utils.llm.get_generator_model_async", models)
    monkeypatch.setattr("app.utils.aimodels.get_generator_model_async", models)
    monkeypatch.setenv("PIPESHUB_AGENT_TRANSPORT", "langchain")
    yield models


def _parent_spec() -> RunSpec:
    wire = cast("dict[str, Any]", dump_contract(w.pinned_spec(tool_ids=[JIRA_SEARCH, JIRA_CREATE])))
    wire["budgets"]["maxChildren"] = 4
    return validate_contract(RunSpec, wire)


class _NodeJournal(w.PipesHubInMemoryJournal):
    """The reference journal as Node admits a pinned parent's child: with the child's own pin. Its policy is its
    parent's (CPD-5b); its ``llm`` is its role's configured entry when the role sets a model, else its parent's."""

    @override
    def _host_pinned(self, spec: dict[str, JsonValue], model: ChildSpecModel | None) -> dict[str, JsonValue]:
        extension = cast("dict[str, JsonValue]", cast("dict[str, JsonValue]", spec["extensions"])["x.pipeshub"])
        parent = extension.get("resolved")
        if parent is None:
            return spec
        fields = {key: value for key, value in child_pin(cast("dict[str, JsonValue]", parent)).items() if key != "digest"}
        if model is not None:
            fields["llm"] = {
                "modelKey": model.model_key,
                "provider": model.provider,
                "model": model.model,
                "configDigest": canonical_digest({"provider": model.provider, "model": model.model}),
            }
        extension["resolved"] = cast("JsonValue", dump_contract(assemble(fields)))
        return spec


def _rig(pins: bool) -> LoopRig:
    """``LoopRig.configured_models`` on a journal that pins a child as Node does."""
    clock = ManualClock()
    journal = cast(
        "w.PipesHubInMemoryJournal",
        _NodeJournal(
            clock=clock, lease_ms=LEASE_MS, extension_writers=cast("Any", EVENT_WRITERS), extension_models=EVENT_MODELS
        ),
    )
    services = w.Services()
    control = ControlPlane(services, clock)
    assembler = PipesHubRuntimeAssembler(services, model_factory=None, policies=run_policies(services, control))
    driver = RunDriver(journal=journal, assembler=w.ClaimAssembler(assembler), clock=clock)
    return LoopRig(clock, journal, services, control, driver, pins)


class Family:
    """A parent run on A and the children Node admits for it, with a pin (``on``) or without (``off``, ``shadow``)."""

    def __init__(self, rig: LoopRig, role_routes: dict[str, tuple[str, str]], run_id: str = PARENT) -> None:
        self.rig = rig
        self.run_id = run_id
        self._spawned = 0
        spec = _parent_spec()
        if rig.pins:
            spec = pinned(spec, rig.control.pin())
        rig.journal.admit(
            run_id,
            org_id=w.ORG,
            session_id=run_id if run_id != PARENT else SESSION,
            spec=spec,
            input_text="file the bug",
            role_routes={role: ":".join(route) for role, route in role_routes.items()},
        )

    @property
    def session(self) -> str:
        return self.run_id if self.run_id != PARENT else SESSION

    async def spawn(self) -> str:
        """The parent's worker asks for a researcher during an attempt, which then runs until it needs an approval."""
        if self.rig.journal.open_interactions(self.run_id):
            self.rig.approve_open(self.run_id)
        claim = await w.claim_next(self.rig.journal, self.run_id)
        admitted = await self.rig.journal.spawn(
            claim.token, ChildRequest(f"t9.{self._spawned}", "look it up", definition=RESEARCHER)
        )
        self._spawned += 1
        _ = await self.rig.driver.run_claimed(claim)
        return admitted.child_run_id

    async def attempt(self, run_id: str) -> str:
        return (await self.rig.attempt(run_id)).status

    def approve(self, run_id: str) -> None:
        self.rig.approve_open(run_id)

    def events(self, run_id: str, type_: str) -> list[JournalEvent]:
        """A run's events of one type: a child journals on its own stream."""
        stream = self.session if run_id == self.run_id else run_id
        return [e for e in self.rig.events(run_id, stream) if e.type == type_]

    def failed_with(self, run_id: str) -> list[object]:
        return [e.data.get("errorType") for e in self.events(run_id, "run.failed")]


def _family(pins: bool, role_routes: dict[str, tuple[str, str]] | None = None) -> Family:
    rig = _rig(pins)
    rig.control.models(A_ENTRY, B_ENTRY)
    rig.control.approvals(DEPLOYMENT_ASKS_FOR_WRITES)
    rig.journal.configured_models.update({":".join(B): "model-b", ":".join(C): "model-b"})
    return Family(rig, {"researcher": B} if role_routes is None else role_routes)


@pytest.mark.parametrize("pins", [True, False], ids=["pinned", "without_a_pin"])
async def test_qa37_i2_a_child_on_its_roles_model_calls_it_and_its_parent_keeps_its_own(
    providers: Providers, pins: bool
) -> None:
    family = _family(pins)

    child = await family.spawn()
    assert (providers.built, providers.calls(A), providers.calls(B)) == ([A], 1, 0)

    assert await family.attempt(child) == "suspended"
    assert providers.built == [A, B]
    assert providers.keys == ["model-1", "model-b"]
    assert (providers.calls(A), providers.calls(B)) == (1, 1)

    family.approve(child)
    assert await family.attempt(child) == "completed"
    family.approve(PARENT)
    assert await family.attempt(PARENT) == "completed"

    assert set(providers.built) == {A, B}
    assert (providers.calls(A), providers.calls(B)) == (2, 2)
    requested = {
        run_id: {e.data.get("provider") for e in family.events(run_id, "model.response")}
        for run_id in (PARENT, child)
    }
    assert requested == {PARENT: {"openai"}, child: {"anthropic"}}


@pytest.mark.parametrize("pins", [True, False], ids=["pinned", "without_a_pin"])
async def test_qa37_i2_a_child_whose_role_sets_no_model_runs_on_its_parents(providers: Providers, pins: bool) -> None:
    family = _family(pins, role_routes={})

    child = await family.spawn()
    _ = await family.attempt(child)

    assert set(providers.built) == {A}
    assert providers.keys == ["model-1", "model-1"]
    assert providers.calls(A) == 2


async def test_qa37_i5_a_pinned_childs_model_removed_before_its_claim_ends_the_child_and_builds_nothing(
    providers: Providers,
) -> None:
    family = _family(pins=True)
    child = await family.spawn()
    assert providers.built == [A]

    family.rig.control.models(A_ENTRY, llm("model-b", *C))
    status = await family.attempt(child)

    assert status == "failed"
    assert family.failed_with(child) == ["model_no_longer_configured"]
    assert providers.built == [A], "neither the parent's model for the child, nor the one now behind the role's key"
    assert providers.keys == ["model-1"], "no other provider's key was handed to a client"

    family.approve(PARENT)
    assert await family.attempt(PARENT) == "completed"
    assert set(providers.built) == {A}, "the parent goes on, on its own model"


async def test_qa37_i5_without_a_pin_a_removed_role_model_is_read_as_every_unpinned_run_reads_one(
    providers: Providers,
) -> None:
    """Existing behaviour of a run without a pin (CPD-6 D4), kept as it is: ``get_model_config`` answers a key it
    no longer finds with the configured list, and the run is built on its first entry. Only ``CONTROL_PLANE_PIN=on``
    refuses instead (the case above)."""
    family = _family(pins=False)
    child = await family.spawn()

    family.rig.control.models(A_ENTRY)
    status = await family.attempt(child)

    assert status != "failed"
    assert providers.built == [A, A]
    assert providers.keys == ["model-1", "model-1"]


async def test_qa37_i7_a_pinned_child_keeps_its_model_across_a_resume_after_the_roles_model_changed(
    providers: Providers,
) -> None:
    family = _family(pins=True)
    rig = family.rig
    child = await family.spawn()
    assert await family.attempt(child) == "suspended"
    assert providers.built == [A, B]

    # The admin re-points the key B was configured under to C and keeps B under another; the author moves the
    # role to C, which a run admitted from now on is given.
    rig.control.models(A_ENTRY, llm("model-b", *C), llm("model-b2", *B))
    family.approve(child)
    assert await family.attempt(child) == "completed"

    assert providers.built == [A, B, B]
    assert providers.keys == ["model-1", "model-b", "model-b2"], "the entry that still serves B, never C's under the old key"
    assert providers.calls(B) == 2

    later = Family(rig, {"researcher": C}, run_id="run-2")
    newer = await later.spawn()
    assert await later.attempt(newer) == "suspended"
    assert providers.built[-1] == C


async def test_qa37_i7_a_pinned_child_keeps_its_model_when_its_worker_dies_mid_run(providers: Providers) -> None:
    family = _family(pins=True)
    rig = family.rig
    child = await family.spawn()

    await rig.worker_dies_after_model_response(1, child)
    rig.control.models(A_ENTRY, llm("model-b", *C), llm("model-b2", *B))
    assert await family.attempt(child) == "suspended"

    assert providers.built == [A, B, B]


@pytest.mark.parametrize("pins", [True, False], ids=["pinned", "without_a_pin"])
async def test_qa37_k5_every_attempt_of_a_parent_and_its_child_names_the_same_models(
    providers: Providers, pins: bool
) -> None:
    family = _family(pins)
    rig = family.rig
    child = await family.spawn()
    named: dict[str, list[tuple[str, str, str | None]]] = {PARENT: [], child: []}

    for run_id in (child, child, child):
        claim = await w.claim_next(rig.journal, run_id)
        run = RunContext.from_claim(w.node_claim(claim))
        named[run_id].append((run.model.provider, run.model.model, run.model.model_key))
        if run.resolved is not None:
            assert run.resolved.llm is not None
            assert (run.resolved.llm.provider, run.resolved.llm.model, run.resolved.llm.model_key) == (*B, "model-b")
        _ = rig.clock.advance(LEASE_MS / 1000 + 1)
        _ = rig.journal.sweep()
    family.approve(PARENT)
    parent = RunContext.from_claim(w.node_claim(await w.claim_next(rig.journal, PARENT)))

    assert named[child] == [(*B, "model-b")] * 3
    assert (parent.model.provider, parent.model.model, parent.model.model_key) == (*A, "model-1")
