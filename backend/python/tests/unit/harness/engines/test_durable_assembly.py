"""CC-5 and the PipesHub pieces of a durable engine attempt (E1): which workers run engines, what of a run's
catalog an engine may call, the binding its gateway token names, and the presenting publication log."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from app.harness.artifacts.events import PRESENTED
from app.harness.engines import worker as engine_worker_module
from app.harness.engines.durable import (
    TaskCardPublicationLog,
    attempt_binding,
    engine_grant,
)
from app.harness.engines.presenter import PROGRESS, PipesHubEngineRunPresenter
from app.harness.worker.capabilities import WORKER_CAPABILITIES, advertised
from harness_core.artifacts.types import ArtifactCard
from harness_core.engines.durable.ports import ProgressStamp
from harness_core.interactions.ports import RunBinding
from harness_core.testing.contracts.tool_catalog import offered
from harness_core.testing.engines.durable import engine_run_spec
from harness_core.tools.base import Tag
from harness_core.tools.tags import TAG_LIFECYCLE_TERMINAL, TAG_UI_ONLY

if TYPE_CHECKING:
    from collections.abc import Sequence

    import pytest

    from harness_core.effects.types import ToolEffect
    from harness_core.journal.types import JournalEvent, NewEvent

VECTORS = Path(__file__).resolve().parents[6] / "contracts" / "harness" / "vectors"


async def test_cc5_engines_are_off_unless_switched_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(engine_worker_module.ENGINE_RUNS_ENV, raising=False)
    assert await engine_worker_module.plan_engines(cast("Any", None)) is None
    monkeypatch.setenv(engine_worker_module.ENGINE_RUNS_ENV, "on")
    assert engine_worker_module.engine_runs_enabled()


class _StartingBroker:
    """A broker that answers only after ``refusals`` attempts, on a clock the test advances by each sleep."""

    def __init__(self, refusals: int) -> None:
        self.refusals = refusals
        self.attempts = 0
        self.now = 0.0
        self.sleeps: list[float] = []

    async def open(self, _config: object) -> object:
        from harness_core.sandbox.errors import BrokerUnavailable

        self.attempts += 1
        if self.attempts <= self.refusals:
            raise BrokerUnavailable("broker not up yet")
        return "broker"

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@dataclass
class _Services:
    def config_service(self) -> object:
        return object()


async def test_e2e2_fb1_the_worker_waits_for_a_broker_that_is_still_starting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(engine_worker_module.ENGINE_RUNS_ENV, "true")
    broker = _StartingBroker(refusals=5)
    plan = await engine_worker_module.plan_engines(
        cast("Any", _Services()), open_broker=broker.open, sleep=broker.sleep, clock=lambda: broker.now
    )
    assert plan is not None
    assert plan.broker == "broker"
    assert broker.attempts == 6
    assert broker.sleeps == [1.0, 2.0, 4.0, 8.0, 10.0]


async def test_e2e2_fb1_the_worker_gives_up_on_the_broker_after_its_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(engine_worker_module.ENGINE_RUNS_ENV, "true")
    broker = _StartingBroker(refusals=10_000)
    plan = await engine_worker_module.plan_engines(
        cast("Any", _Services()), open_broker=broker.open, sleep=broker.sleep, clock=lambda: broker.now
    )
    assert plan is None
    assert broker.now <= engine_worker_module.BROKER_WAIT_S + 10.0
    assert broker.now >= engine_worker_module.BROKER_WAIT_S - 10.0


def test_cc5_without_engines_the_advertisement_drops_executor_engine() -> None:
    assert "executor_engine" in {f.root for f in WORKER_CAPABILITIES.features}
    assert "executor_engine" not in {f.root for f in advertised(engines=False).features}


def test_the_gateway_is_reached_where_the_deployment_says(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(engine_worker_module.GATEWAY_URL_ENV, "http://worker-3:8098/harness/tool-gateway/")
    assert engine_worker_module.gateway_base_url(8098) == "http://worker-3:8098/harness/tool-gateway"


@dataclass
class _Registry:
    internal: set[str] = field(default_factory=set[str])
    tags: dict[str, tuple[Tag, ...]] = field(default_factory=dict[str, tuple[Tag, ...]])

    def resolve_by_name(self, name: str) -> object:
        class Internal:
            def handle(self) -> None: ...

        return Internal() if name in self.internal else object()

    def tags_for_name(self, name: str) -> tuple[Tag, ...]:
        return self.tags.get(name, ())


def test_s1c_an_engine_never_gets_a_tool_that_asks_the_person_or_steers_the_loop() -> None:
    effects: dict[str, ToolEffect] = {
        "search": offered("search").effect,
        "internaltools__ask_user_question": offered("internaltools__ask_user_question").effect,
    }
    registry = _Registry(tags={"internaltools__ask_user_question": (TAG_UI_ONLY, TAG_LIFECYCLE_TERMINAL)})
    assert sorted(engine_grant(cast("Any", registry), effects, lambda effect: effect.effect == "read")) == ["search"]


def test_an_engine_gets_the_grant_within_its_profile_without_the_loops_own_tools() -> None:
    effects: dict[str, ToolEffect] = {
        "search": offered("search").effect,
        "create": offered("create", "write").effect,
        "fetch_tools": offered("fetch_tools").effect,
        "create_plan": offered("create_plan").effect,
    }
    granted = engine_grant(
        cast("Any", _Registry({"create_plan"})), effects, lambda effect: effect.effect in ("read", "write")
    )
    assert sorted(granted) == ["create", "search"]
    reads_only = engine_grant(cast("Any", _Registry()), effects, lambda effect: effect.effect == "read")
    assert sorted(reads_only) == ["create_plan", "search"]


def test_emptyout_an_engine_never_gets_the_loops_code_sandbox() -> None:
    """EMPTYOUT: ``run_code`` ran in the loop's own coding sandbox, so what Codex's program wrote never reached the
    engine's workspace and the deliverable stayed empty. An engine runs code in its own shell, in its workspace.
    ``PipesHubCodingSandboxTool`` lives outside the loop's modules, so the rule is by name."""
    effects: dict[str, ToolEffect] = {
        "search": offered("search").effect,
        **{name: offered(name, "execute").effect for name in ("run_code", "install_packages", "read_sandbox_file")},
    }
    assert sorted(engine_grant(cast("Any", _Registry()), effects, lambda _effect: True)) == ["search"]


def test_the_attempt_binding_names_the_pins_digests_as_node_mints_them() -> None:
    vector = json.loads((VECTORS / "gateway-token.json").read_text())
    spec = engine_run_spec()
    tools = [{"toolId": t, "descriptorDigest": "sha256:" + "1" * 64} for t in dict.fromkeys(vector["toolIds"])]
    spec = spec.model_validate(
        {**spec.model_dump(mode="json", by_alias=True), "grants": {"tools": tools, "sandboxProfile": "none", "egress": "none"}}
    )

    @dataclass
    class _Claim:
        org_id: str = "org-1"
        session_id: str = "s-1"
        run_id: str = "r-1"
        attempt_id: str = "r-1.a2"
        fence: int = 2
        run: object = None

    claim = _Claim()
    claim.spec = spec  # type: ignore[attr-defined]
    binding = attempt_binding(cast("Any", claim))
    assert binding.tool_ids_digest == vector["toolIdsDigest"]
    assert (binding.tenant, binding.scope, binding.run_id, binding.attempt_id, binding.fence) == (
        "org-1", "s-1", "r-1", "r-1.a2", 2,
    )


@dataclass
class _Attempt:
    appended: list[list[NewEvent]] = field(default_factory=list[list["NewEvent"]])

    async def append(self, events: Sequence[NewEvent]) -> None:
        self.appended.append(list(events))

    async def run_events(self) -> Sequence[JournalEvent]:
        return ()

    def stamp(self) -> ProgressStamp:
        return ProgressStamp("r-1", "trn_1", iter(range(1000)).__next__)


async def test_a_presented_card_is_journaled_with_its_line_on_the_task_card() -> None:
    attempt = _Attempt()
    log = TaskCardPublicationLog(cast("Any", attempt), PipesHubEngineRunPresenter())
    card = ArtifactCard("a1", 1, "report.md", "text/markdown", 10, "file", "draft")
    await log.presented(RunBinding("org-1", "s-1", "r-1"), card)
    [batch] = attempt.appended
    assert [e.type for e in batch] == [PRESENTED, PROGRESS]
    assert batch[1].data["kind"] == "artifact" and batch[1].data["data"]["lifecycle"] == "draft"  # type: ignore[index]


def test_r10_rl3_the_attempt_binding_names_a_pinned_account_as_node_mints_it() -> None:
    """Round 10 (found by WP-43's readiness gate on RL-3): a route that pins a model account (a user's own key, A1)
    is ``provider:model@kind:account`` in the token Node mints (``modelRouteId``); the worker's binding left the
    account out, so the Tool Gateway refused every call of such a run (``binding_mismatch``)."""
    from harness_core.access.types import (
        AccountKind,
        AccountRef,
        PinnedRoute,
        route_digest,
    )

    spec = engine_run_spec()
    dumped = spec.model_dump(mode="json", by_alias=True)
    dumped["model"]["policy"] = [
        {
            "provider": "openai",
            "model": "gpt-x",
            "access": {"route": "gateway", "accountKind": "user_api_key", "accountId": "users/user-2/openAI", "pays": "user"},
        }
    ]
    spec = spec.model_validate(dumped)

    @dataclass
    class _Claim:
        org_id: str = "org-1"
        session_id: str = "s-1"
        run_id: str = "r-1"
        attempt_id: str = "r-1.a1"
        fence: int = 1
        run: object = None

    claim = _Claim()
    claim.spec = spec  # type: ignore[attr-defined]
    pinned = PinnedRoute("openai", "gpt-x", account=AccountRef(AccountKind("user_api_key"), "users/user-2/openAI"))
    assert attempt_binding(cast("Any", claim)).routes_digest == route_digest((pinned,))
