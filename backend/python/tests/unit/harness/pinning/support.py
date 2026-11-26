"""QA-1 rigs: a run in flight on the loop or an engine, and the control plane an admin changes under it.

``ControlPlane`` is the one seam the cases change policy through. Today it writes where the worker reads per
attempt (the configuration store), so a case shows what reaches a run in flight now. CPD-6 moves the worker
to the pinned ``ResolvedRunConfig`` and a narrow-only live layer (02-lld §5.3, §8.3); it points this seam at
the control-plane write path, the change notifier and the internal live route, and the cases stay as written.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any, Final, cast

import pytest

from app.config.constants.service import config_node_constants
from app.control_plane.contracts.generated.change_event import ChangeEvent
from app.control_plane.contracts.generated.live_layer import (
    LiveLayer as LiveLayerDocument,
)
from app.control_plane.runtime.deployment import DeploymentLayer
from app.control_plane.runtime.live_layer import (
    LiveLayerUnavailable,
    ReloadingLiveLayerCache,
)
from app.control_plane.runtime.pinning import (
    PinSnapshot,
    PinSubject,
    assemble,
    inherited_dimensions,
    parent_config_layer,
    pin_layers,
    release_layer,
)
from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from app.harness.engines.durable import (
    DurableEngineServices,
    PipesHubEngineAssembler,
    attempt_binding,
)
from app.harness.run_policy.select import RunPolicies
from app.services.featureflag.platform_settings import PLATFORM_FLAG_DEFAULTS
from harness_core.contracts import canonical_digest, dump_contract, validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.driver import RunDriver
from harness_core.effects.log import RequestEffectLog
from harness_core.engines.registry import AllowlistEngineRegistry
from harness_core.failpoints import Arm, Nth, Raise
from harness_core.logs import HarnessLog
from harness_core.policy import DimensionPolicyResolver, ScopedLayer
from harness_core.testing import (
    ManualClock,
    ModelScript,
    Reply,
    ScriptedModel,
    ToolCallSpec,
    Turn,
)
from harness_core.testing.contracts.tool_endpoint import claims_for
from harness_core.testing.durable import ScriptedModelPort
from harness_core.testing.engines.durable import (
    FAKE_VERSION,
    EngineWorld,
    engine_profile,
)
from harness_core.testing.failpoints import armed
from tests.unit.harness.assembly import world as w

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from pydantic import JsonValue

    from app.harness.assembly.assembler import ModelFactory
    from app.harness.journal_node import NodeClaim
    from harness_core.clock import Clock
    from harness_core.effects.runner import HoldDecision
    from harness_core.effects.types import EffectCall
    from harness_core.journal.types import JournalEvent
    from harness_core.kernel.durable.driver import Disposition

APPROVALS: Final = config_node_constants.AGENT_HARNESS_APPROVALS.value
AI_MODELS: Final = config_node_constants.AI_MODELS.value
ENGINE_PROFILES: Final = config_node_constants.AGENT_HARNESS_ENGINE_PROFILES.value
POLICY_DOCUMENTS: Final = frozenset(
    {
        APPROVALS,
        ENGINE_PROFILES,
        config_node_constants.AGENT_HARNESS_MODEL_ACCESS.value,
    }
)
# NFR-23: a narrowing reaches a running attempt within 5 s with the notifier, within the 30 s TTL without it.
NOTIFIED_WITHIN_S: Final = 5.0
LIVE_TTL_S: Final = 30.0
# Long enough that advancing the clock past the live layer's TTL never costs an attempt its lease.
LEASE_MS: Final = 600_000

JIRA_SEARCH: Final = f"jira@{w.JIRA}/search_issues"
JIRA_CREATE: Final = f"jira@{w.JIRA}/create_issue"
SEARCH: Final = "jira__search_issues"
CREATE: Final = "jira__create_issue"

DEPLOYMENT_ASKS_FOR_WRITES: Final[JsonValue] = {"default": {"writes": "ask"}}
DEPLOYMENT_DENIES_WRITES: Final[JsonValue] = {"default": {"writes": "deny"}}
DEPLOYMENT_FLOW_DENIES: Final[JsonValue] = {"default": {"flow": {"level": "deny"}}}
NO_RULES: Final[JsonValue] = None
# The Labs stages of the assembly world (``w.Services``): skills off, the rest at their release defaults.
PINNED_STAGES: Final = {
    flag: PLATFORM_FLAG_DEFAULTS[flag] and flag != "ENABLE_SKILLS"
    for flag in ("ENABLE_SKILLS", "ENABLE_USER_CONTEXT", "ENABLE_KNOWLEDGE_PUBLICATIONS", "ENABLE_NAMED_ENTITY_EXTRACTION")
}


def call(name: str, **args: JsonValue) -> Reply:
    return Reply(tool_calls=[ToolCallSpec(name=name, args=dict(args))])


def llm(model_key: str, provider: str, model: str) -> dict[str, Any]:
    return {"modelKey": model_key, "provider": provider, "configuration": {"model": model}, "isDefault": True}


class _PartitionableConfig:
    """The configuration store, which a worker reading it per attempt can lose: then it answers as the production
    ``ConfigurationService`` does on an outage (the default, or a raise when the caller asks for one)."""

    LIVE_KEYS: Final = frozenset({APPROVALS, AI_MODELS})

    def __init__(self, inner: Any) -> None:  # noqa: ANN401
        self._inner = inner
        self.partitioned = False
        self.forbidden: frozenset[str] = frozenset()
        self.read: list[str] = []

    async def get_config(self, key: str, default: Any = None, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        self.read.append(key)
        if key in self.forbidden:
            raise AssertionError(f"a pinned run read {key}")
        if self.partitioned and key in self.LIVE_KEYS:
            if kwargs.get("raise_on_error"):
                raise ConnectionError(f"configuration store unreachable reading {key}")
            return default
        return await self._inner.get_config(key, default, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        return getattr(self._inner, name)


def _live(approvals: JsonValue) -> dict[str, JsonValue]:
    return {"schemaVersion": 1, "scope": {"kind": "live"}, "approvals": approvals}


class ControlPlane:
    """What an org admin changes while runs are in flight, and the plumbing that carries it to a worker: the
    control plane's write (the org head moves), the change notifier, and the internal live route behind the
    worker's live layer. Admission pins from it (``pin``), as Node's does."""

    def __init__(self, services: w.Services, clock: Clock) -> None:
        self._config = _PartitionableConfig(services.config)
        services.config = self._config  # type: ignore[assignment]
        services.graph.config_service = self._config
        self._document: dict[str, Any] = {}
        self._models: list[dict[str, Any]] = [llm("model-1", "openai", "gpt-x")]
        self._kill_switch = False
        self._ceiling: dict[str, Any] | None = None
        self._narrowing: dict[str, Any] = {}
        self._repositories: dict[str, Any] | None = None
        self._head = 1
        self._route_up = True
        self.notifier_up = True
        self.live = ReloadingLiveLayerCache(self, clock)

    # ---- what the admin does

    def narrowing(self, **members: Any) -> None:  # noqa: ANN401 - JSON
        """Members an extension keeps in the org's model-access narrowing: the live layer's ``models`` and the
        model-access document's entry, as Node writes both."""
        self._narrowing = dict(members)
        entry = {"disabledRules": [], "consumerPlans": False, **members}
        self._config.put(config_node_constants.AGENT_HARNESS_MODEL_ACCESS.value, {"orgs": {w.ORG: entry}})
        self._committed("models")

    def approvals(self, document: JsonValue) -> None:
        self._document = dict(cast("dict[str, Any]", document or {}))
        self._config.put(APPROVALS, document)
        self._committed("approvals")

    def models(self, *configs: dict[str, Any]) -> None:
        self._models = list(configs)
        self._config.put(AI_MODELS, {"llm": list(configs)})
        self._committed("ai-models")

    def engine_ceiling(self, narrowing: dict[str, Any] | None) -> None:
        """The org's engine-profile ceiling, as an extension's org settings store it."""
        self._ceiling = narrowing
        self._config.put(ENGINE_PROFILES, None if narrowing is None else {"orgs": {w.ORG: {"ceiling": narrowing}}})
        self._committed("executor")

    def repositories(self, org: dict[str, Any] | None) -> None:
        """The org's git hosts section (``PUT …/policy/org/current/repositories``); ``None``: never saved."""
        self._repositories = org
        self._committed("repositories")

    def notifier(self, *, up: bool) -> None:
        self.notifier_up = up

    def partition(self) -> None:
        """The live layer can no longer be refreshed (notifier and internal live route unreachable)."""
        self.notifier_up = False
        self._route_up = False
        self._config.partitioned = True

    def forbid_policy_reads(self) -> None:
        """From now on, reading a policy document a pinned run has in its pin fails the test."""
        self._config.forbidden = POLICY_DOCUMENTS

    def kill_switch(self, *, on: bool) -> None:
        self._kill_switch = on
        self._committed("rollout")

    def _committed(self, touched: str) -> None:
        self._head += 1
        if not self.notifier_up:
            return
        event = ChangeEvent.model_validate(
            {
                "type": "cp.changed",
                "orgId": w.ORG,
                "revision": self._head,
                "previous": self._head - 1,
                "touched": [{"kind": "policy-section", "id": touched}],
                "narrowing": False,
            }
        )
        # The worker's subscriber hands each event of the topic to its live layer.
        self.live.on_change(event)

    # ---- the internal live route (``LiveLayerSource``)

    async def load(self, org_id: str, since: int | None) -> LiveLayerDocument | None:
        if not self._route_up:
            raise LiveLayerUnavailable("the internal live route is unreachable")
        if since == self._head:
            return None
        layers: list[dict[str, JsonValue]] = [
            {"layer": _live(rules)}
            for rules in (self._document.get("default"),)
            if rules is not None
        ]
        if self._ceiling is not None:
            layers.append({"layer": {"schemaVersion": 1, "scope": {"kind": "live"}, "engineProfile": self._ceiling}})
        if self._narrowing:
            layers.append({"layer": {"schemaVersion": 1, "scope": {"kind": "live"}, "models": dict(self._narrowing)}})
        if self._kill_switch:
            layers.append(
                {"layer": {"schemaVersion": 1, "scope": {"kind": "live"}, "rollout": {"AGENT_HARNESS_KILL_SWITCH": True}}}
            )
        return LiveLayerDocument.model_validate(
            {
                "schemaVersion": 1, "orgId": org_id, "revision": self._head, "layers": layers, "revokedBindings": [],
                **({"repositories": {"org": self._repositories}} if self._repositories is not None else {}),
            }
        )

    # ---- admission

    def pin(self, *, agent_key: str | None = w.AGENT) -> dict[str, JsonValue]:
        """The run's pin as admission resolves it now: the approvals document's deployment layer, the Labs stages
        and the model behind ``model-1``."""

        def approvals(rules: object) -> dict[str, JsonValue] | None:
            return None if rules is None else {"approvals": cast("JsonValue", rules)}

        def layer(kind: str, sections: dict[str, JsonValue] | None) -> dict[str, JsonValue] | None:
            return None if sections is None else {"schemaVersion": 1, "scope": {"kind": kind}, **sections}

        org: dict[str, JsonValue] = {"rollout": dict(PINNED_STAGES)}
        if self._ceiling is not None:
            org["engineProfile"] = self._ceiling
        layers = {
            f"org:{w.ORG}": layer("org", cast("dict[str, JsonValue]", org)),
        }
        subject = PinSubject(
            org_id=w.ORG, actor_id=w.ACTOR, agent_key=agent_key, chat_mode="quick", surface="web",
            request={"schemaVersion": 1, "scope": {"kind": "request"}},
        )
        snapshot = PinSnapshot(
            head_revision=self._head,
            layers={scope: values for scope, values in layers.items() if values is not None},
            deployment=layer("deployment", approvals(self._document.get("default"))),
        )
        resolved = DimensionPolicyResolver().resolve(pin_layers(subject, snapshot))
        assert resolved.config is not None, resolved.refusal
        # The spec asks for ``model-1`` (``w.pinned_spec``): the entry admission finds behind it now.
        entry = next((m for m in self._models if m.get("modelKey") == "model-1"), None)
        return cast(
            "dict[str, JsonValue]",
            dump_contract(
                assemble(
                    {
                        "schemaVersion": 1,
                        "headRevision": self._head,
                        "routing": {"path": "durable", "reason": None},
                        "scope": {"agentKey": agent_key, "projectId": None},
                        "effectiveSet": {"items": [], "digest": canonical_digest([])},
                        "effectiveConfig": cast("JsonValue", resolved.config),
                        "llm": None
                        if entry is None
                        else {
                            "modelKey": entry["modelKey"],
                            "provider": entry["provider"],
                            "model": entry["configuration"]["model"],
                            "configDigest": canonical_digest({"provider": entry["provider"], "model": entry["configuration"]["model"]}),
                        },
                        "deploymentDigest": canonical_digest({}),
                    }
                )
            ),
        )


def child_pin(parent: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """A child's pin as admission resolves it (CPD-5b): release ⊓ parent ⊓ request. No org or agent layer is read
    again, so whatever was narrowed after the root's admission reaches the child through the live layer only. The
    model and the Labs stages are the parent's: a child is part of its parent's run."""
    parent_config = cast("dict[str, JsonValue]", parent["effectiveConfig"])
    resolved = DimensionPolicyResolver().resolve(
        [release_layer(), ScopedLayer("parent", parent_config_layer(parent_config)), ScopedLayer("request", {})]
    )
    assert resolved.config is not None, resolved.refusal
    config = inherited_dimensions(resolved.config, parent_config)
    fields = {key: value for key, value in parent.items() if key != "digest"}
    fields["effectiveConfig"] = cast("JsonValue", config)
    fields["routing"] = {"path": "durable", "reason": "inherited"}
    return cast("dict[str, JsonValue]", dump_contract(assemble(fields)))


def pinned(spec: RunSpec, pin: dict[str, JsonValue]) -> RunSpec:
    """``spec`` as Node hands it to a worker that advertises ``resolved_config``: with the run's pin."""
    wire = cast("dict[str, Any]", dump_contract(spec))
    wire["extensions"]["x.pipeshub"]["resolved"] = pin
    return validate_contract(RunSpec, wire)


def run_policies(services: w.Services, control: ControlPlane) -> RunPolicies:
    """The worker's policies as its composition root builds them: the deployment layer the conftest set (no flow
    rule) and the live layer the control plane feeds."""
    return RunPolicies(services, deployment=DeploymentLayer(), live=control.live)  # type: ignore[arg-type]


class _Died(BaseException):
    """The worker process is gone: nothing of the attempt runs after it."""


@dataclasses.dataclass
class LoopRig:
    """A durable loop run on the in-memory journal, driven by ``RunDriver`` through the production assembler."""

    clock: ManualClock
    journal: w.PipesHubInMemoryJournal
    services: w.Services
    control: ControlPlane
    driver: RunDriver

    # Whether admission pins (``CONTROL_PLANE_PIN=on`` and a worker advertising ``resolved_config``).
    pins: bool = True

    @classmethod
    def scripted(cls, *replies: Reply, pins: bool = True) -> LoopRig:
        """The run's model answers ``replies`` in order, across attempts."""
        port = ScriptedModelPort(ScriptedModel(ModelScript(id="qa1", turns=[Turn(reply=r) for r in replies])))
        return cls._build(w.model_factory(port), pins=pins)

    @classmethod
    def configured_models(cls, *, pins: bool = True) -> LoopRig:
        """Each attempt builds its model as the worker does: from its pin, or from the LLM configuration."""
        return cls._build(None, pins=pins)

    @classmethod
    def _build(cls, model_factory: ModelFactory | None, *, pins: bool) -> LoopRig:
        clock = ManualClock()
        journal = w.pipeshub_journal(clock=clock, lease_ms=LEASE_MS)
        services = w.Services()
        control = ControlPlane(services, clock)
        assembler = PipesHubRuntimeAssembler(services, model_factory=model_factory, policies=run_policies(services, control))
        driver = RunDriver(journal=journal, assembler=w.ClaimAssembler(assembler), clock=clock)
        return cls(clock, journal, services, control, driver, pins)

    def admit(
        self, run_id: str = "run-1", *, session_id: str = "ses-1", pin: dict[str, JsonValue] | None = None
    ) -> dict[str, JsonValue] | None:
        """Admits the run under the policy of this moment (or with ``pin``, a child's); answers its pin."""
        spec = w.pinned_spec(tool_ids=[JIRA_SEARCH, JIRA_CREATE])
        if self.pins:
            pin = pin if pin is not None else self.control.pin()
            spec = pinned(spec, pin)
        self.journal.admit(run_id, org_id=w.ORG, session_id=session_id, spec=spec, input_text="file the bug")
        return pin

    async def attempt(self, run_id: str = "run-1") -> Disposition:
        return await self.driver.run_claimed(await w.claim_next(self.journal, run_id))

    async def worker_dies_after_model_response(self, nth: int, run_id: str = "run-1") -> None:
        """The worker dies once the ``nth`` model response is committed; its lease lapses and the run is queued."""
        with armed({"worker.model.after_response_commit": Arm(Raise(_Died), Nth(nth))}), pytest.raises(_Died):
            _ = await self.attempt(run_id)
        _ = self.clock.advance(LEASE_MS / 1000 + 1)
        _ = self.journal.sweep()

    def approve_open(self, run_id: str = "run-1") -> None:
        [interaction] = self.journal.open_interactions(run_id)
        self.journal.resolve_interaction(run_id, interaction, "approved")

    def events(self, run_id: str = "run-1", session_id: str = "ses-1") -> list[JournalEvent]:
        return [e for e in self.journal.events(session_id) if e.run_id == run_id]

    def ran(self, tool_id: str, run_id: str = "run-1", session_id: str = "ses-1") -> int:
        """Calls of ``tool_id`` that reached the world (an intent is journaled before every effect)."""
        return sum(1 for e in self.events(run_id, session_id) if e.type == "tool.intent" and e.data.get("toolId") == tool_id)

    def asked(self, run_id: str = "run-1", session_id: str = "ses-1") -> int:
        return sum(1 for e in self.events(run_id, session_id) if e.type == "interaction.requested")

    def refused(self, run_id: str = "run-1", session_id: str = "ses-1") -> int:
        return sum(1 for e in self.events(run_id, session_id) if e.type == "tool.denied")


def on_search(monkeypatch: pytest.MonkeyPatch, side_effect: Callable[[], None]) -> None:
    """Runs ``side_effect`` inside the run's attempt, while its read tool executes: a change mid-attempt."""

    async def search_issues(self: w.FakeJira, jql: str) -> str:
        side_effect()
        return f'{{"issues": ["{jql}"]}}'

    w.replace_tool(monkeypatch, w.FakeJira, "search_issues", search_issues)


class PendingHold:
    """The run's approval hold as an engine meets it when nobody has decided yet: the call is offered."""

    def __init__(self) -> None:
        self.offered = 0

    async def hold(self, call: EffectCall) -> HoldDecision | None:
        self.offered += 1
        return None


class _NoRoute:
    """No Node behind the attempt: building an engine run touches no workspace route."""

    def log(self, name: str) -> HarnessLog:
        return HarnessLog.named(name)

    def secret_values(self) -> tuple[str, ...]:
        return ()


@dataclasses.dataclass
class EngineAttemptRun:
    """One engine attempt as the production assembler builds it; ``call`` is the engine calling a tool."""

    call_tool: Callable[[str, dict[str, JsonValue]], Awaitable[str]]
    hold: PendingHold
    # The names of the tools the engine is offered.
    offered: Callable[[], Awaitable[tuple[str, ...]]]

    async def call(self, name: str, **args: JsonValue) -> str:
        return await self.call_tool(name, dict(args))


class EngineRig:
    """A durable engine run on the in-memory journal; each attempt is built by ``PipesHubEngineAssembler``."""

    def __init__(
        self, services: w.Services | None = None, spec: RunSpec | None = None, *, pins: bool = True
    ) -> None:
        self.world = EngineWorld(lease_ms=LEASE_MS)
        self.services = services or w.Services()
        self._spec = spec
        self.clock = self.world.clock
        self.control = ControlPlane(self.services, self.clock)
        self.pins = pins
        engines = DurableEngineServices(
            broker=self.world.broker,
            tool_host=self.world.host,
            engines=AllowlistEngineRegistry([self.world.adapter], [FAKE_VERSION]),
            config=self.world.config,
            objects=self.world.objects,
            route_for=lambda _token: _NoRoute(),  # type: ignore[arg-type,return-value]
            clock=self.clock,
            placeholder_key=b"\x00" * 32,
        )
        port = ScriptedModelPort(ScriptedModel(ModelScript(id="qa1", turns=[Turn(reply=Reply(text="ok"))])))
        self.assembler = PipesHubEngineAssembler(
            self.services, engines, model_factory=w.model_factory(port), policies=run_policies(self.services, self.control)
        )
        self._admitted = False

    def _admit(self) -> None:
        """The run is admitted when its first attempt is asked for, under the policy of that moment."""
        if self._admitted:
            return
        spec = self._spec or w.pinned_spec(tool_ids=[JIRA_SEARCH, JIRA_CREATE])
        if self.pins:
            spec = pinned(spec, self.control.pin())
        self.world.journal.admit("run-1", org_id=w.ORG, session_id="ses-1", spec=spec, input_text="file the bug")
        self._admitted = True

    async def attempt(self) -> EngineAttemptRun:
        self._admit()
        claimed = await self.world.journal.claim("run-1", worker_id="w-qa1")
        assert claimed is not None
        node: NodeClaim = w.node_claim(claimed)
        hold = PendingHold()
        attempt = dataclasses.replace(
            self.world.kernel().attempt_of(node, engine_profile()), hold=hold, effect_log=RequestEffectLog()
        )
        built = await self.assembler.build(attempt)
        claims = claims_for(attempt_binding(node))

        async def call_tool(name: str, args: dict[str, JsonValue]) -> str:
            return (await built.tools.call_tool(claims, name, args)).disposition

        async def offered() -> tuple[str, ...]:
            return tuple(tool.name for tool in await built.tools.list_tools(claims))

        return EngineAttemptRun(call_tool, hold, offered)

    async def next_attempt(self) -> EngineAttemptRun:
        """The attempt's worker is gone; the run is claimed again."""
        self.world.expire()
        return await self.attempt()
