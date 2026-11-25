from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

import pytest

from app.agent_loop_lib.sandbox.load_ledger import FLAG_HELD, FLAG_QUALIFYING
from app.services.sandbox_alerts.config import AlertSettings
from app.services.sandbox_alerts.leader import AlertLeader
from app.services.sandbox_alerts.loop import SandboxAlertRunner, run_sandbox_alert_loop
from tests.unit.services.sandbox_alerts.fakes import FakeConfig, FakeProducer

if TYPE_CHECKING:
    from pathlib import Path

T0 = 1_800_000_000.0
# A 2-minute window: 12 ticks, so 2 busy ticks are a 16 % share.
SETTINGS = AlertSettings(window_s=120, eval_s=10, warn_busy_pct=10, warn_min_waited=2,
                         crit_min_refused=3, crit_refused_pct=5, cooldown_s=86400)
SNAPSHOT = {"limit": 1, "ceiling": 1, "floor": 1, "mode": "compose", "live": None}
WARN_KEY = "/services/sandbox/alerts/install/warning"
CRIT_KEY = "/services/sandbox/alerts/install/critical"


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> float:
        return self.now


class Env:
    def __init__(self, tmp_path: Path) -> None:
        self.state = tmp_path / "state"
        self.clock = Clock()
        self.store = FakeConfig(self.clock)
        self.producer = FakeProducer()
        self.built = 0

    async def factory(self) -> FakeProducer:
        self.built += 1
        return self.producer

    def runner(self, settings: AlertSettings = SETTINGS) -> SandboxAlertRunner:
        return SandboxAlertRunner(
            self.store, settings, self.state,  # type: ignore[arg-type]
            producer_factory=self.factory, snapshot=lambda: dict(SNAPSHOT),
            clock=self.clock, host="test-host",
        )

    def load(self, *, waited: int = 0, refused: int = 0, runs: int | None = None, busy_ticks: int = 0) -> None:
        """Replace the one worker file with these counts in the current minute."""
        load = self.state / "load"
        load.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state.chmod(0o700)
        now = self.clock.now
        tick = int(now // 10)
        (load / "w-1-1.json").write_text(json.dumps({
            "v": 1, "pid": 1, "startedAt": T0, "updatedAt": now, "host": "h", "tickSeconds": 10, "minuteSeconds": 60,
            "minutes": {str(int(now // 60)): {
                "runs": waited if runs is None else runs, "waited": waited, "refused": refused,
                "waitedUnqualified": 0, "refusedUnqualified": 0, "cancelledWaiting": 0,
                "waitedByReason": {"slots": waited, "memory": 0, "cpu": 0, "orgShare": 0}, "maxWaitS": 30.0,
            }},
            "ticks": {str(tick - i): FLAG_QUALIFYING | FLAG_HELD for i in range(busy_ticks)},
        }))

    def warn(self) -> None:
        self.load(waited=5, runs=100, busy_ticks=3)

    def critical(self) -> None:
        self.load(waited=10, refused=10, runs=20, busy_ticks=3)

    def quiet(self) -> None:
        self.load(runs=10)


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


async def test_a_worker_that_is_not_the_leader_does_not_evaluate(env: Env) -> None:
    other = AlertLeader(env.state)
    assert other.try_acquire()
    env.warn()
    try:
        assert await env.runner().run_once() is None
    finally:
        other.release()
    assert env.store.values == {}


async def test_nothing_to_report_claims_nothing(env: Env) -> None:
    env.quiet()
    decision = await env.runner().run_once()
    assert decision is not None and decision.level is None
    assert env.store.values == {}
    assert env.producer.sent == []


async def test_a_warning_is_claimed_and_published_once(env: Env, caplog: pytest.LogCaptureFixture) -> None:
    env.warn()
    runner = env.runner()
    with caplog.at_level(logging.INFO):
        await runner.run_once()
        env.clock.now += 10
        await runner.run_once()
    assert len(env.producer.sent) == 1
    topic, event, key = env.producer.sent[0]
    assert topic == "notification"
    assert event["type"] == "SANDBOX_CAPACITY_WARNING"
    assert key == f"sandbox-capacity-{event['payload']['alertId']}"
    assert env.store.values[WARN_KEY]["alertId"] == event["payload"]["alertId"]
    assert any("sandbox load alert fired level=warning" in r.getMessage() for r in caplog.records)
    assert any("suppressed: sent within the last 86400 s" in r.getMessage() for r in caplog.records)


async def test_a_critical_alert_also_uses_up_the_warning(env: Env) -> None:
    env.critical()
    runner = env.runner()
    await runner.run_once()
    assert [e["type"] for _, e, _ in env.producer.sent] == ["SANDBOX_CAPACITY_CRITICAL"]
    assert CRIT_KEY in env.store.values and WARN_KEY in env.store.values
    env.clock.now += 10
    env.warn()
    await runner.run_once()
    assert len(env.producer.sent) == 1


async def test_a_failed_publish_releases_the_claim_and_retries(env: Env) -> None:
    env.warn()
    env.producer.result = False
    runner = env.runner()
    await runner.run_once()
    assert WARN_KEY not in env.store.values
    env.producer.result = True
    env.clock.now += 10
    env.warn()
    await runner.run_once()
    assert len(env.producer.sent) == 2
    assert env.store.values[WARN_KEY]["alertId"] == env.producer.sent[-1][1]["payload"]["alertId"]


async def test_a_failed_snapshot_releases_the_claim_and_retries(env: Env, caplog: pytest.LogCaptureFixture) -> None:
    env.warn()
    calls = 0

    def snapshot() -> dict:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("governor gone")
        return dict(SNAPSHOT)

    runner = SandboxAlertRunner(
        env.store, SETTINGS, env.state,  # type: ignore[arg-type]
        producer_factory=env.factory, snapshot=snapshot, clock=env.clock, host="test-host",
    )
    with caplog.at_level(logging.WARNING):
        await runner.run_once()
    assert env.producer.sent == []
    assert WARN_KEY not in env.store.values
    assert any("not sent" in r.getMessage() for r in caplog.records)
    env.clock.now += 10
    env.warn()
    await runner.run_once()
    assert [e["type"] for _, e, _ in env.producer.sent] == ["SANDBOX_CAPACITY_WARNING"]


async def test_a_store_outage_sends_nothing(env: Env, caplog: pytest.LogCaptureFixture) -> None:
    env.warn()
    env.store.fail = True
    with caplog.at_level(logging.WARNING):
        await env.runner().run_once()
    assert env.producer.sent == []
    assert any("not sent: config store unavailable" in r.getMessage() for r in caplog.records)


async def test_stale_files_are_deleted(env: Env) -> None:
    env.quiet()
    stale = env.state / "load" / "w-9-9.json"
    stale.write_text(json.dumps({"v": 1, "updatedAt": T0 - 10_000}))
    await env.runner().run_once()
    assert not stale.exists()


class TestTheLoop:
    @pytest.mark.parametrize(
        ("env_vars", "why"),
        [
            ({"SANDBOX_ALERTS": "false", "SANDBOX_MODE": "docker"}, "SANDBOX_ALERTS=false"),
            ({"SANDBOX_MODE": "e2b"}, "E2B"),
            ({"SANDBOX_MODE": ""}, "no code sandbox"),
        ],
    )
    async def test_it_exits_at_once_where_alerts_are_off(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, env: Env,
        env_vars: dict[str, str], why: str,
    ) -> None:
        for key, value in env_vars.items():
            monkeypatch.setenv(key, value)
        with caplog.at_level(logging.INFO):
            await asyncio.wait_for(run_sandbox_alert_loop(env.store, state_directory=env.state), 5)  # type: ignore[arg-type]
        assert any("sandbox load alerts off:" in r.getMessage() and why in r.getMessage() for r in caplog.records)

    async def test_cancel_propagates_and_cleans_up(self, monkeypatch: pytest.MonkeyPatch, env: Env) -> None:
        monkeypatch.setenv("SANDBOX_MODE", "docker")
        monkeypatch.delenv("SANDBOX_ALERTS", raising=False)
        env.clock.now = __import__("time").time()
        env.warn()
        task = asyncio.create_task(run_sandbox_alert_loop(
            env.store, settings=SETTINGS, state_directory=env.state,  # type: ignore[arg-type]
            producer_factory=env.factory, snapshot=lambda: dict(SNAPSHOT),
        ))
        for _ in range(200):
            if env.producer.sent:
                break
            await asyncio.sleep(0.01)
        assert env.producer.sent
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert env.producer.cleaned
        assert AlertLeader(env.state).try_acquire()


class TestBackToNormal:
    async def fire(self, env: Env, runner: SandboxAlertRunner, kind: str = "warn") -> None:
        getattr(env, kind)()
        await runner.run_once()

    async def tick(self, env: Env, runner: SandboxAlertRunner, seconds: float, kind: str = "quiet") -> None:
        env.clock.now += seconds
        getattr(env, kind)()
        await runner.run_once()

    def resolved(self, env: Env) -> list[dict]:
        return [e for _, e, _ in env.producer.sent if e["type"] == "SANDBOX_CAPACITY_RESOLVED"]

    async def test_one_notice_after_a_full_clear_window(self, env: Env) -> None:
        runner = env.runner()
        await self.fire(env, runner)
        await self.tick(env, runner, 200)  # the window has moved past the load: clear from here
        await self.tick(env, runner, 119)
        assert self.resolved(env) == []
        await self.tick(env, runner, 1)
        notices = self.resolved(env)
        assert len(notices) == 1
        assert notices[0]["email"] is False
        assert notices[0]["payload"]["level"] == "warning"
        assert env.store.values[WARN_KEY]["state"] == "resolved"
        await self.tick(env, runner, 120)
        assert len(self.resolved(env)) == 1

    async def test_the_clock_restarts_when_the_condition_returns(self, env: Env) -> None:
        runner = env.runner()
        await self.fire(env, runner)
        await self.tick(env, runner, 200)
        await self.tick(env, runner, 80)
        await self.tick(env, runner, 10, "warn")
        await self.tick(env, runner, 200)
        await self.tick(env, runner, 100)
        assert self.resolved(env) == []
        await self.tick(env, runner, 20)
        assert len(self.resolved(env)) == 1

    async def test_a_restart_does_not_send_it_again(self, env: Env) -> None:
        runner = env.runner()
        await self.fire(env, runner)
        await self.tick(env, runner, 200)
        await self.tick(env, runner, 120)
        assert len(self.resolved(env)) == 1
        await runner.close()
        again = env.runner()
        await self.tick(env, again, 10)
        await self.tick(env, again, 130)
        assert len(self.resolved(env)) == 1

    async def test_a_critical_that_clears_to_a_warning_sends_nothing_until_both_clear(self, env: Env) -> None:
        runner = env.runner()
        await self.fire(env, runner, "critical")
        for _ in range(20):
            await self.tick(env, runner, 10, "warn")
        assert self.resolved(env) == []
        await self.tick(env, runner, 200)
        await self.tick(env, runner, 120)
        notices = self.resolved(env)
        assert len(notices) == 1
        assert notices[0]["payload"]["level"] == "critical"
        assert env.store.values[CRIT_KEY]["state"] == "resolved"
        assert env.store.values[WARN_KEY]["state"] == "resolved"

    async def test_the_cooldown_expiry_is_kept(self, env: Env) -> None:
        runner = env.runner()
        await self.fire(env, runner)
        await self.tick(env, runner, 200)
        await self.tick(env, runner, 120)
        assert env.store.ttls[WARN_KEY] == 86400 - 320

    async def test_a_new_overload_after_the_notice_alerts_again(self, env: Env) -> None:
        runner = env.runner()
        await self.fire(env, runner, "critical")
        await self.tick(env, runner, 200)
        await self.tick(env, runner, 120)
        assert len(self.resolved(env)) == 1
        await self.tick(env, runner, 3600, "warn")
        assert [e["type"] for _, e, _ in env.producer.sent] == [
            "SANDBOX_CAPACITY_CRITICAL", "SANDBOX_CAPACITY_RESOLVED", "SANDBOX_CAPACITY_WARNING",
        ]
        assert env.store.values[WARN_KEY]["state"] == "claimed"
        await self.tick(env, runner, 10, "warn")
        assert len(env.producer.sent) == 3


async def test_with_signing_on_in_enforce_each_org_gets_its_alert_signed_under_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ENVELOPE_SIGN=true with ORG_CONTEXT_MODE at its default (enforce): Node refuses an unsigned alert, so each active org gets
    a copy signed under that org (its ``ctx.orgId`` and ``payload.orgId``), and none is sent unsigned."""
    from app.core import envelope

    monkeypatch.setenv("ENVELOPE_SIGN", "true")
    monkeypatch.delenv("ORG_CONTEXT_MODE", raising=False)
    monkeypatch.delenv("ENVELOPE_CTX_REQUIRED", raising=False)
    monkeypatch.setattr(envelope, "_sign_key", envelope.derive_key("test-secret"))

    class SigningProducer(FakeProducer):
        async def send_message(self, topic: str, message: dict, key: str | None = None) -> bool:
            return await super().send_message(topic, envelope.sign_message(topic, message), key)

    async def org_ids() -> list[str]:
        return ["org-a", "org-b"]

    env = Env(tmp_path)
    env.producer = SigningProducer()
    runner = SandboxAlertRunner(
        env.store, SETTINGS, env.state,  # type: ignore[arg-type]
        producer_factory=env.factory, snapshot=lambda: dict(SNAPSHOT), clock=env.clock, host="test-host", org_ids=org_ids,
    )
    env.warn()
    await runner.run_once()
    sent = env.producer.sent
    assert [m["ctx"]["orgId"] for _, m, _ in sent] == ["org-a", "org-b"]
    for _, message, _ in sent:
        opened = await envelope.open_envelope("notification", message, keys=(envelope.derive_key("test-secret"),))
        assert opened.ctx.org_id == message["ctx"]["orgId"] == opened.message["payload"]["orgId"]
        assert opened.message["audience"] == "platformAdmins"


class _OrgFlakyProducer(FakeProducer):
    """Accepts every org's copy but ``failing``'s, which it refuses while ``failing`` is set."""

    def __init__(self) -> None:
        super().__init__()
        self.failing: str | None = None

    async def send_message(self, topic: str, message: dict, key: str | None = None) -> bool:
        if message["payload"].get("orgId") == self.failing:
            return False
        return await super().send_message(topic, message, key)


def _signed_runner(env: Env, monkeypatch: pytest.MonkeyPatch, orgs: list[str]) -> SandboxAlertRunner:
    monkeypatch.setenv("ENVELOPE_SIGN", "true")
    monkeypatch.delenv("ORG_CONTEXT_MODE", raising=False)
    monkeypatch.delenv("ENVELOPE_CTX_REQUIRED", raising=False)

    async def org_ids() -> list[str]:
        return list(orgs)

    return SandboxAlertRunner(
        env.store, SETTINGS, env.state,  # type: ignore[arg-type]
        producer_factory=env.factory, snapshot=lambda: dict(SNAPSHOT), clock=env.clock, host="test-host", org_ids=org_ids,
    )


def _copies(env: Env, kind: str) -> list[tuple[str, str]]:
    return [(m["payload"]["orgId"], m["payload"]["alertId"]) for _, m, _ in env.producer.sent if m["type"] == kind]


async def test_an_alert_one_org_took_is_sent_again_only_to_the_org_that_did_not_with_the_same_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Node keeps one delivery per alert id and org: a retry under a new id would notify (and mail) the org that has it again."""
    env = Env(tmp_path)
    env.producer = _OrgFlakyProducer()
    env.producer.failing = "org-b"
    runner = _signed_runner(env, monkeypatch, ["org-a", "org-b"])
    env.warn()
    await runner.run_once()
    [(org, alert_id)] = _copies(env, "SANDBOX_CAPACITY_WARNING")
    assert org == "org-a"
    env.producer.failing = None
    env.clock.now += 10
    env.warn()
    await runner.run_once()
    assert _copies(env, "SANDBOX_CAPACITY_WARNING") == [("org-a", alert_id), ("org-b", alert_id)]
    env.clock.now += 10
    await runner.run_once()
    assert len(_copies(env, "SANDBOX_CAPACITY_WARNING")) == 2      # nothing more once every org has it
    assert env.store.values[WARN_KEY]["alertId"] == alert_id       # the claim stayed the alert's


async def test_a_resolved_notice_one_org_took_is_sent_again_only_to_the_other_with_the_same_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = Env(tmp_path)
    env.producer = _OrgFlakyProducer()
    runner = _signed_runner(env, monkeypatch, ["org-a", "org-b"])
    env.warn()
    await runner.run_once()
    env.producer.failing = "org-b"
    for _ in range(33):                       # the window moves past the load, then a full clear window
        env.clock.now += 10
        env.quiet()
        await runner.run_once()
    [(org, notice_id)] = _copies(env, "SANDBOX_CAPACITY_RESOLVED")
    assert org == "org-a"
    env.producer.failing = None
    for _ in range(3):
        env.clock.now += 10
        env.quiet()
        await runner.run_once()
    assert _copies(env, "SANDBOX_CAPACITY_RESOLVED") == [("org-a", notice_id), ("org-b", notice_id)]


async def test_an_alert_no_org_took_gives_its_claims_back_so_the_next_evaluation_sends_it_anew(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nobody has it, so a new id harms nothing, and a restart or another worker can send it too (nothing is held)."""
    env = Env(tmp_path)
    env.producer = _OrgFlakyProducer()
    env.producer.failing = "org-a"
    runner = _signed_runner(env, monkeypatch, ["org-a"])
    env.warn()
    await runner.run_once()
    assert _copies(env, "SANDBOX_CAPACITY_WARNING") == []
    env.producer.failing = None
    env.clock.now += 10
    env.warn()
    await runner.run_once()
    assert [o for o, _ in _copies(env, "SANDBOX_CAPACITY_WARNING")] == ["org-a"]


async def test_a_copy_that_times_out_does_not_stop_the_later_orgs_and_is_sent_again_under_the_same_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A timed-out copy drops the producer (it may hang again); the next org's copy is sent on a new one in the same pass."""

    class _HangsFor(_OrgFlakyProducer):
        async def send_message(self, topic: str, message: dict, key: str | None = None) -> bool:
            if message["payload"].get("orgId") == self.failing:
                raise TimeoutError
            return await super().send_message(topic, message, key)

    env = Env(tmp_path)
    env.producer = _HangsFor()
    env.producer.failing = "org-b"
    runner = _signed_runner(env, monkeypatch, ["org-a", "org-b", "org-c"])
    env.warn()
    await runner.run_once()
    copies = _copies(env, "SANDBOX_CAPACITY_WARNING")
    assert [o for o, _ in copies] == ["org-a", "org-c"]
    alert_id = copies[0][1]
    assert env.built == 2 and env.producer.cleaned            # the hung producer was dropped and a new one built for org-c
    env.producer.failing = None
    env.clock.now += 10
    env.warn()
    await runner.run_once()
    assert _copies(env, "SANDBOX_CAPACITY_WARNING") == [("org-a", alert_id), ("org-c", alert_id), ("org-b", alert_id)]
