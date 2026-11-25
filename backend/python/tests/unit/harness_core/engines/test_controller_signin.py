"""CS-1..CS-9 (SIGNIN-2, SIGNIN-5): ``EngineController`` on a run pinned on its actor's own plan.

The sandbox reaches the tool gateway and the signed vendor hosts only (never the model gateway), is wiped of whatever
an earlier turn left and signed in before the engine opens, is never snapshotted, and is kept for the conversation's
next turn when the turn ends cleanly (``test_controller_signin_kept.py`` covers keeping it); a sign-in the person (or
an admin, the org's switch, erasure, sharing) ends stops the turn with ``account_disconnected`` and wipes it.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest

from harness_core.engines.events import EngineState, SignInProgress, TurnCompleted
from harness_core.engines.signin import (
    KeptSignIn,
    PlanSignIn,
    SignedIn,
    SignInHosts,
    SignInRefused,
)
from harness_core.engines.types import EngineLaunch, SessionHealth
from harness_core.testing.engines.fake_engine import (
    CONFIG_PATH,
    TOOLS_TOKEN_ENV,
    FakeEngineConfig,
)
from harness_core.testing.engines.loop import list_gateway_tools
from harness_core.testing.sandbox.egress import EgressDenied
from tests.unit.harness_core.engines.test_controller import (
    RUN,
    WatchingAdapter,
    World,
    task,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from harness_core.engines.events import EngineEvent
    from harness_core.engines.types import EngineTask, FollowUp
    from harness_core.interactions.ports import RunBinding

HOSTS = SignInHosts(sandbox=("api.anthropic.com", "platform.claude.com"), link=frozenset({"claude.com"}))
OWN = PlanSignIn(user_id="user-1", account_id="users/user-1/claude-plan")


class PlanSession:
    """An engine on the person's own plan: it needs no model gateway; ``hold`` keeps the turn until interrupted."""

    def __init__(self, engine: Any, *, hold: bool) -> None:  # noqa: ANN401
        self._engine = engine
        self._hold = hold
        self._interrupted = asyncio.Event()
        self.compacted: list[str | None] = []

    async def run_turn(self, task: EngineTask | FollowUp) -> AsyncIterator[EngineEvent]:
        if self._hold:
            await self._interrupted.wait()
            yield TurnCompleted(_meta(task.turn_id, 0), "interrupted")
            return
        yield TurnCompleted(_meta(task.turn_id, 0), "completed", "done")

    async def steer(self, text: str) -> None:
        return

    async def interrupt(self) -> None:
        self._interrupted.set()

    async def answer(self, request_id: str, decision: Any) -> None:  # noqa: ANN401
        return

    async def capture_state(self) -> EngineState:
        return EngineState(self._engine, 1, b"{}")

    async def compact(self, focus: str | None, *, instructions: str = "") -> Any:  # noqa: ANN401
        from harness_core.context.user_compact import CompactReport

        self.compacted.append(focus)
        return CompactReport("compacted", before_tokens=900, after_tokens=100)

    async def health(self) -> SessionHealth:
        return SessionHealth(alive=True)

    async def close(self) -> None:
        return


def _meta(turn_id: str, seq: int) -> Any:  # noqa: ANN401
    from harness_core.engines.events import EngineEventMeta

    return EngineEventMeta(turn_id, seq)


class SigningAdapter(WatchingAdapter):
    """``FakeEngine`` with a login of its own; records what the controller asked of it, in order."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.calls: list[str] = []
        self.reached: dict[str, asyncio.Event] = {"sign_in": asyncio.Event(), "open": asyncio.Event()}
        self.launches: list[EngineLaunch] = []
        self.hold = False
        self.sign_in_waits: asyncio.Event | None = None
        # Whether the sandbox's engine is signed in now, as its own status would say (by sandbox id).
        self.logged_in: set[str] = set()
        self.parks: list[frozenset[int]] = []
        self.sessions: list[PlanSession] = []
        self.park_ok = True
        # What each opened session's tool listing got: the tool names, or the proxy's refusal reason (SIGNFIX).
        self.tool_listings: list[tuple[str, ...] | str] = []
        self.own_models: list[str | None] = []

    def own_login(self, launch: EngineLaunch, model: str | None = None) -> EngineLaunch:
        self.calls.append("own_login")
        self.own_models.append(model)
        return dataclasses.replace(launch, env={k: v for k, v in launch.env.items() if "TOKEN" not in k or "TOOLS" in k})

    async def sign_in(
        self,
        sandbox: Any,  # noqa: ANN401
        launch: EngineLaunch,
        hosts: SignInHosts,
        prompt: Callable[[], Any],
        progress: Callable[[str, str | None, str | None], Awaitable[None]],
    ) -> SignedIn:
        self.calls.append("sign_in")
        self.reached["sign_in"].set()
        async with prompt() as attempt:
            await progress("requested", None, None)
            if self.sign_in_waits is not None:
                await self.sign_in_waits.wait()
            request = await attempt.code(5)
            assert request.code == "c#s"
        await progress("signed_in", None, "team")
        self.logged_in.add(sandbox.id)
        return SignedIn("ada@example.com", "team")

    async def sign_out(self, sandbox: Any, launch: EngineLaunch) -> None:  # noqa: ANN401
        self.calls.append("sign_out")
        self.logged_in.discard(sandbox.id)

    async def signed_in(self, sandbox: Any, launch: EngineLaunch) -> SignedIn | None:  # noqa: ANN401
        self.calls.append("signed_in")
        return SignedIn("ada@example.com", "team") if sandbox.id in self.logged_in else None

    async def processes(self, sandbox: Any) -> frozenset[int]:  # noqa: ANN401
        return frozenset({1, 7})

    async def park(self, sandbox: Any, spare: frozenset[int]) -> bool:  # noqa: ANN401
        self.calls.append("park")
        self.parks.append(spare)
        return self.park_ok

    async def _list_tools(self, sandbox: Any, launch: EngineLaunch) -> None:  # noqa: ANN401
        """As Claude Code does when it starts: its MCP server's tools, through the sandbox's egress. A refusal is
        recorded, never raised (the engine runs on without its tools)."""
        config = FakeEngineConfig.from_json(launch.files[CONFIG_PATH])
        headers = {"authorization": f"Bearer {launch.env[TOOLS_TOKEN_ENV]}"}
        try:
            listed = await list_gateway_tools(sandbox.egress, config.tools_url, headers)
        except EgressDenied as denied:
            self.tool_listings.append(denied.reason or "denied")
            return
        self.tool_listings.append(tuple(sorted(name for name, _ in listed)))

    async def open(self, sandbox, launch, restore, *, correlation=None):  # type: ignore[no-untyped-def] # noqa: ANN201
        self.calls.append("open")
        self.reached["open"].set()
        self.launches.append(launch)
        await self._list_tools(sandbox, launch)
        session = PlanSession(launch.engine, hold=self.hold)
        self.sessions.append(session)
        return session


@dataclass
class FakeAttempt:
    attempt_id: str
    codes: asyncio.Queue[str] = field(default_factory=asyncio.Queue)
    shown: list[str] = field(default_factory=list[str])

    def show(self, link: str) -> None:
        self.shown.append(link)

    async def code(self, timeout_s: float) -> Any:  # noqa: ANN401
        from harness_core.engines.signin import CodeRequest

        return CodeRequest(await asyncio.wait_for(self.codes.get(), timeout_s), work_plans_only=True)

    async def reply(self, reply: Any) -> None:  # noqa: ANN401
        return


@dataclass
class FakeRelay:
    attempts: list[FakeAttempt] = field(default_factory=list[FakeAttempt])
    owners: list[str] = field(default_factory=list[str])
    revoke: asyncio.Event = field(default_factory=asyncio.Event)
    kept: dict[str, KeptSignIn] = field(default_factory=dict[str, KeptSignIn])
    ttls: list[int] = field(default_factory=list[int])
    # When a revocation was remembered (epoch s); a kept sign-in from before it is ended.
    revoked_at: float | None = None

    async def keep(self, key: str, kept: KeptSignIn, ttl_s: int) -> None:
        self.kept[key] = kept
        self.ttls.append(ttl_s)

    async def take(self, key: str, *, only: KeptSignIn | None = None) -> KeptSignIn | None:
        found = self.kept.get(key)
        if found is None or (only is not None and found != only):
            return None
        return self.kept.pop(key)

    async def ended_since(self, run: RunBinding, owner_id: str, at: float) -> bool:
        return self.revoked_at is not None and self.revoked_at >= at

    @contextlib.asynccontextmanager
    async def attempt(self, run: RunBinding, owner_id: str, attempt_id: str) -> AsyncIterator[FakeAttempt]:
        self.owners.append(owner_id)
        attempt = FakeAttempt(attempt_id)
        attempt.codes.put_nowait("c#s")
        self.attempts.append(attempt)
        yield attempt

    @contextlib.asynccontextmanager
    async def revoked(self, run: RunBinding, owner_id: str) -> AsyncIterator[asyncio.Event]:
        self.owners.append(owner_id)
        yield self.revoke


class Idle:
    """The controller's sleep: its ticker's short waits pass, a kept sandbox's idle wait lasts until ``end``."""

    def __init__(self) -> None:
        self.ended = asyncio.Event()
        self.asked: list[float] = []

    async def sleep(self, seconds: float) -> None:
        if seconds < 60:
            await asyncio.sleep(0.001)
            return
        self.asked.append(seconds)
        await self.ended.wait()

    def end(self) -> None:
        self.ended.set()


def _world(
    *, relay: FakeRelay | None, hosts: SignInHosts | None = HOSTS, idle: Idle | None = None, **overrides: object
) -> World:
    world = World(adapter=SigningAdapter(), config_overrides=dict(overrides), sleep=(idle or Idle()).sleep)
    base = world.make_controller

    def make(key: bytes = b"k" * 32) -> Any:  # noqa: ANN401
        controller = base(key)
        controller._signin_relay = relay  # pyright: ignore[reportPrivateUsage]
        controller._signin_hosts = hosts  # pyright: ignore[reportPrivateUsage]
        return controller

    world.make_controller = make  # type: ignore[method-assign]
    world.controller = make()
    return world


def _adapter(world: World) -> SigningAdapter:
    assert isinstance(world.adapter, SigningAdapter)
    return world.adapter


@pytest.mark.parametrize(("relay", "hosts"), [(None, HOSTS), (FakeRelay(), None)])
async def test_cs1_without_the_relay_or_signed_hosts_an_own_plan_run_is_refused_before_a_sandbox(
    relay: FakeRelay | None, hosts: SignInHosts | None
) -> None:
    world = _world(relay=relay, hosts=hosts)
    with pytest.raises(SignInRefused) as refused:
        _ = await world.controller.run_turn(world.run(own_login=OWN), task(0, "say done"))
    assert refused.value.reason_code == "signin_unavailable"
    assert world.broker.acquired == []


async def test_cs2_the_sandbox_tunnels_to_the_signed_hosts_and_never_reaches_the_model_gateway() -> None:
    relay = FakeRelay()
    world = _world(relay=relay, keep_warm_s=300)
    outcome = await world.controller.run_turn(world.run(own_login=OWN), task(0, "say done"))
    assert (outcome.reason, outcome.final_text) == ("completed", "done")
    [(spec, _)] = world.broker.acquired
    assert {(d.host, d.ports) for d in spec.egress.destinations} == {
        ("tool-gateway", (8097,)), ("api.anthropic.com", (443,)), ("platform.claude.com", (443,))}
    assert {r.destination for r in spec.egress.token_routes} == {"tool-gateway"}
    tunnels = [d for d in spec.egress.destinations if d.host != "tool-gateway"]
    assert all(d.upstream is None for d in tunnels)
    # Kept for the conversation's next turn for the idle time, whatever the deployment's keep-warm (SIGNIN-5).
    [kept] = world.broker.sandboxes
    assert world.broker.ledger.by_id(kept).warm_until is not None  # type: ignore[union-attr]
    assert relay.owners[:2] == ["user-1", "user-1"]


async def test_cs3_signed_in_before_the_engine_opens_and_signed_out_after_it_closes() -> None:
    world = _world(relay=FakeRelay())
    _ = await world.controller.run_turn(world.run(own_login=OWN), task(0, "say done"))
    adapter = _adapter(world)
    # Wiped first (nothing was kept), signed in, and the turn's leftover processes ended before it is kept.
    assert adapter.calls == ["own_login", "sign_out", "sign_in", "open", "park"]
    assert adapter.parks == [frozenset({1, 7})]
    [launch] = adapter.launches
    assert not any(k for k in launch.env if "TOKEN" in k and "TOOLS" not in k)
    lines = [e for _, e in world.sink.events if isinstance(e, SignInProgress)]
    assert [(e.state, e.plan, e.attempt_id) for e in lines] == [
        ("requested", None, f"sin_{RUN}.a1"), ("signed_in", "team", f"sin_{RUN}.a1")]
    assert all(e.meta.seq > 1_000 for e in lines)


async def test_r38_the_plan_runs_the_model_its_pin_names() -> None:
    """ADR-MA-01 (R38-AUTHZ): the configured Claude model Node pinned reaches the own-login launch."""
    world = _world(relay=FakeRelay())
    pinned = dataclasses.replace(OWN, model="claude-sonnet-4-5")
    _ = await world.controller.run_turn(world.run(own_login=pinned), task(0, "say done"))
    assert _adapter(world).own_models == ["claude-sonnet-4-5"]


async def test_cs4_a_gateway_run_never_signs_in() -> None:
    world = _world(relay=FakeRelay())
    adapter = _adapter(world)
    world.adapter = WatchingAdapter()  # the stock fake engine on the gateway
    world.controller = world.make_controller()
    outcome = await world.controller.run_turn(world.run(), task(0, "say done"))
    assert outcome.reason == "completed"
    assert adapter.calls == []
    assert not [e for _, e in world.sink.events if isinstance(e, SignInProgress)]


async def test_cs5_ended_while_signing_in_the_turn_is_refused_and_the_sandbox_signed_out() -> None:
    relay = FakeRelay()
    world = _world(relay=relay)
    adapter = _adapter(world)
    adapter.sign_in_waits = asyncio.Event()
    running = asyncio.ensure_future(world.controller.run_turn(world.run(own_login=OWN), task(0, "say done")))
    await asyncio.wait_for(adapter.reached["sign_in"].wait(), 5)
    relay.revoke.set()
    with pytest.raises(SignInRefused) as refused:
        _ = await running
    assert refused.value.reason_code == "account_disconnected"
    assert adapter.calls == ["own_login", "sign_out", "sign_in", "sign_out"]
    assert world.broker.sandboxes == {}
    assert relay.kept == {}


async def test_cs6_ended_mid_turn_the_engine_stops_and_the_turn_says_why() -> None:
    relay = FakeRelay()
    world = _world(relay=relay)
    adapter = _adapter(world)
    adapter.hold = True
    running = asyncio.ensure_future(world.controller.run_turn(world.run(own_login=OWN), task(0, "say done")))
    await asyncio.wait_for(adapter.reached["open"].wait(), 5)
    relay.revoke.set()
    with pytest.raises(SignInRefused) as refused:
        _ = await asyncio.wait_for(running, 5)
    assert refused.value.reason_code == "account_disconnected"
    assert adapter.calls[-1] == "sign_out"
    assert world.broker.sandboxes == {}


async def test_cs7_no_snapshot_is_taken_or_restored_for_a_signed_in_sandbox() -> None:
    from harness_core.engines.snapshots import (
        ObjectStoreSandboxSnapshots,
        SnapshotPolicy,
    )

    world = _world(relay=FakeRelay(), snapshot=SnapshotPolicy(paths=("/tmp/cache",)))
    world.snapshot_store = ObjectStoreSandboxSnapshots(world.objects)
    world.controller = world.make_controller()
    _ = await world.controller.run_turn(world.run(own_login=OWN), task(0, "say done"))
    assert world.sink.snapshots == []


async def test_cs8_a_sign_in_line_is_journaled_at_once_not_with_the_next_batch() -> None:
    from harness_core.engines.durable.sink import JournalEngineEventSink
    from harness_core.engines.events import EngineEventMeta
    from harness_core.interactions.ports import RunBinding
    from harness_core.testing.engines.durable import RecordingPresenter

    committed: list[object] = []

    async def commit(_cid: str, events: list[object]) -> None:
        committed.append(events)

    class ShowsSignIn(RecordingPresenter):
        def event(self, stamp: Any, event: Any) -> list[Any]:  # noqa: ANN401
            return [self._line(stamp, "signin", "")]  # pyright: ignore[reportPrivateUsage]

    sink = JournalEngineEventSink("r", "r", ShowsSignIn(), commit)  # type: ignore[arg-type]
    sink.begin("trn_x", 1)
    line = SignInProgress(EngineEventMeta("trn_x", 1_000_001), "sin_1", "requested")
    await sink.event(RunBinding("o", "s", "r"), line)
    assert len(committed) == 1


def test_cs9_the_token_patterns_refuse_claude_oauth_tokens_in_any_egress() -> None:
    import re

    from harness_core.sandbox.egress.policy import DEFAULT_FORBIDDEN_PATTERNS

    patterns = [re.compile(p) for p in DEFAULT_FORBIDDEN_PATTERNS]
    for token in ("sk-ant-oat01-" + "A1b2_" * 10, "sk-ant-ort01-" + "Z9-x" * 10):
        assert any(p.search(f"Authorization: Bearer {token}") for p in patterns), token


async def test_cs_cf13_a_turn_on_the_actors_own_plan_keeps_the_engines_usage_the_ledger_never_sees_it() -> None:
    """OWD-8's SIGNIN exception: the engine calls the vendor directly, so no gateway ledger holds the turn; the
    turn's usage is the engine's own report, not an empty ledger difference."""
    world = _world(relay=FakeRelay(), keep_warm_s=300)
    outcome = await world.controller.run_turn(world.run(own_login=OWN, usage=world.gateway), task(0, "say done"))
    assert outcome.reason == "completed"
    assert outcome.usage_source == "engine"
