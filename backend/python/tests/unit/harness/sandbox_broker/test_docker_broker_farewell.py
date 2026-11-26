"""GAPS-2: a kept sign-in the broker removes on its own is signed out at the vendor first.

The worker that parked a signed-in sandbox runs ``claude auth logout`` at the idle end; if it died, the broker's
keep-warm expiry, its lifetime reap, a crash loop's reap or its orphan sweep removed the sandbox with the login never
revoked. Now each of those runs the sandbox's farewell (named by ``FAREWELL_LABEL``) first: bounded, best effort,
its output never read, and one ``engine_account.signed_out`` audit line of ids and the outcome.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

import pytest

from app.harness.sandbox.docker_broker import (
    _Sandbox,  # pyright: ignore[reportPrivateUsage]
)
from harness_core.sandbox.types import FAREWELL_LABEL, LIFETIME_LABEL, SandboxFarewell
from harness_engines.claude_code.signin import FAREWELL
from tests.unit.harness.sandbox_broker.test_docker_broker import (
    LEASE,
    _broker_on,  # pyright: ignore[reportPrivateUsage]
    _FakeApi,  # pyright: ignore[reportPrivateUsage]
    _FakeProvider,  # pyright: ignore[reportPrivateUsage]
    _spec,  # pyright: ignore[reportPrivateUsage]
)

KEPT = {"orgId": "org-1", "sessionId": "ses-1", "pipeshub.signin.kept": "1", FAREWELL_LABEL: "claude_code"}
SECRET_OUTPUT = b"Logged out ada@example.com sk-ant-FAKE"


class _ExecApi(_FakeApi):
    """Containers by labels, and execs that exit with ``code`` (or never finish when ``hang``)."""

    def __init__(self, code: int = 0, *, hang: bool = False) -> None:
        super().__init__()
        self.code = code
        self.hang = hang
        self.released = threading.Event()
        self.listed: dict[str, dict[str, str]] = {}
        self.execs: list[tuple[str, list[str], dict[str, str], str]] = []
        self.removed: list[str] = []

    def containers(self, **kwargs: Any) -> list[dict[str, Any]]:  # noqa: ANN401
        wanted = (kwargs.get("filters") or {}).get("label", [])

        def matches(labels: dict[str, str]) -> bool:
            return all(
                labels.get(f.split("=", 1)[0]) == f.split("=", 1)[1] if "=" in f else f in labels for f in wanted
            )

        return [
            {"Id": cid, "Labels": labels, "State": "running"}
            for cid, labels in self.listed.items()
            if cid not in self.removed and matches(labels)
        ]

    def exec_create(self, container: str, cmd: list[str], **kwargs: Any) -> dict[str, str]:  # noqa: ANN401
        self.execs.append((container, cmd, dict(kwargs.get("environment") or {}), kwargs.get("user", "")))
        return {"Id": f"exec-{len(self.execs)}"}

    def exec_start(self, exec_id: str, **_: Any) -> bytes:  # noqa: ANN401
        if self.hang:
            _ = self.released.wait(5)
        return SECRET_OUTPUT

    def exec_inspect(self, exec_id: str) -> dict[str, Any]:
        return {"Running": False, "ExitCode": self.code}

    def remove_container(self, container: str, **_: Any) -> None:  # noqa: ANN401
        self.removed.append(container)

    def remove_volume(self, name: str, **_: Any) -> None:  # noqa: ANN401
        return


class _ThreadProvider(_FakeProvider):
    """Blocking calls on a thread, as the real provider runs them: a hung exec does not block the event loop."""

    async def run_blocking(self, fn: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        import asyncio

        return await asyncio.to_thread(fn, *args, **kwargs)


def _world(api: _ExecApi, farewell: SandboxFarewell = FAREWELL, provider: _FakeProvider | None = None) -> Any:  # noqa: ANN401
    provider = provider or _FakeProvider()
    provider.client.api = api
    return _broker_on(provider, farewells={"claude_code": farewell})


def _listed(broker: Any, api: _ExecApi, sandbox_id: str, labels: dict[str, str], created: int = 0) -> str:  # noqa: ANN401
    ns = broker._labels  # pyright: ignore[reportPrivateUsage]
    container = f"ctr-{sandbox_id}"
    api.listed[container] = {
        ns.managed: "1", ns.role: "sandbox", ns.sandbox_id: sandbox_id, ns.key: f"key-{sandbox_id}",
        ns.created: str(created), **labels,
    }
    return container


def _audit(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("engine_account.signed_out")]


async def test_gaps2_1_a_kept_sandbox_whose_keep_warm_expired_is_logged_out_before_it_is_removed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="app.harness.sandbox.broker")
    api = _ExecApi(code=0)
    broker = _world(api)
    broker._sandboxes["sbx_1"] = _Sandbox("sbx_1", "ctr_1", _spec(labels=KEPT))  # pyright: ignore[reportPrivateUsage]
    _ = broker.ledger.record("k", "sbx_1", LEASE)
    await broker.release(type("Handle", (), {"id": "sbx_1"})(), keep_warm_s=1)
    broker._clock.advance(2)  # pyright: ignore[reportPrivateUsage]

    assert await broker.sweep_warm() == 1
    [(container, argv, env, user)] = api.execs
    assert (container, tuple(argv), user) == ("ctr_1", FAREWELL.argv, "1000:1000")
    assert env == dict(FAREWELL.env)
    assert api.removed == ["ctr_1"]
    [line] = _audit(caplog)
    assert "by=broker" in line and "reason=warm_expired" in line and "outcome=done" in line
    assert "sandboxId=sbx_1" in line and "orgId=org-1" in line
    # What the command printed (it may name the account) is never logged.
    assert "ada@example.com" not in caplog.text and "sk-ant-" not in caplog.text


async def test_gaps2_2_a_holder_that_released_it_said_its_own_farewell_and_a_plain_sandbox_has_none(
    caplog: pytest.LogCaptureFixture,
) -> None:
    api = _ExecApi()
    broker = _world(api)
    broker._sandboxes["sbx_1"] = _Sandbox("sbx_1", "ctr_1", _spec(labels=KEPT))  # pyright: ignore[reportPrivateUsage]
    broker._sandboxes["sbx_2"] = _Sandbox("sbx_2", "ctr_2", _spec())  # pyright: ignore[reportPrivateUsage]
    _ = broker.ledger.record("k1", "sbx_1", LEASE)
    _ = broker.ledger.record("k2", "sbx_2", LEASE)
    await broker.release(type("Handle", (), {"id": "sbx_1"})())
    await broker.release(type("Handle", (), {"id": "sbx_2"})(), keep_warm_s=1)
    broker._clock.advance(2)  # pyright: ignore[reportPrivateUsage]
    _ = await broker.sweep_warm()
    assert api.execs == []
    assert sorted(api.removed) == ["ctr_1", "ctr_2"]
    assert _audit(caplog) == []


@pytest.mark.parametrize("labels", [{"orgId": "org-1", "sessionId": "ses-1", "pipeshub.signin.kept": "1"},
                                    {LIFETIME_LABEL: "60"}])
async def test_gaps2_3_a_crash_loop_reap_and_a_lifetime_reap_log_out_first(
    labels: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="app.harness.sandbox.broker")
    api = _ExecApi(code=0)
    broker = _world(api)
    container = _listed(broker, api, "sbx_9", {**KEPT, LIFETIME_LABEL: "60"})
    broker._clock.advance(120)  # pyright: ignore[reportPrivateUsage]
    reaped = await (broker.reap(labels=labels, older_than_s=0) if "orgId" in labels else broker.reap_expired())
    assert reaped == 1
    assert [e[0] for e in api.execs] == [container]
    assert api.removed == [container]
    [line] = _audit(caplog)
    assert "reason=reaped" in line and "outcome=done" in line


@pytest.mark.parametrize(
    ("code", "outcome"), [(SandboxFarewell.NOTHING_TO_DO, "nothing_to_do"), (1, "failed"), (137, "failed")]
)
async def test_gaps2_4_a_farewell_that_finds_nothing_or_fails_never_keeps_the_sandbox(
    code: int, outcome: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="app.harness.sandbox.broker")
    api = _ExecApi(code=code)
    broker = _world(api)
    container = _listed(broker, api, "sbx_3", KEPT)
    assert await broker.reap(labels={"sessionId": "ses-1"}, older_than_s=0) == 1
    assert api.removed == [container]
    assert f"outcome={outcome}" in _audit(caplog)[0]


async def test_gaps2_5_a_farewell_that_hangs_is_cut_off_at_its_timeout_and_the_sandbox_still_goes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="app.harness.sandbox.broker")
    api = _ExecApi(hang=True)
    broker = _world(api, SandboxFarewell(argv=FAREWELL.argv, env=FAREWELL.env, timeout_s=0.2), _ThreadProvider())
    container = _listed(broker, api, "sbx_4", KEPT)
    try:
        assert await broker.reap(labels={"sessionId": "ses-1"}, older_than_s=0) == 1
    finally:
        api.released.set()
    assert api.removed == [container]
    assert "outcome=timeout" in _audit(caplog)[0]


async def test_gaps2_6_an_orphan_from_before_a_broker_restart_is_ended_after_the_longest_idle(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="app.harness.sandbox.broker")
    api = _ExecApi(code=0)
    broker = _world(api)
    assert FAREWELL.orphan_after_s == 2 * 60 * 60 + 2 * 60
    orphan = _listed(broker, api, "sbx_5", KEPT)
    held = _listed(broker, api, "sbx_6", KEPT)
    broker._sandboxes["sbx_6"] = _Sandbox("sbx_6", held, _spec(labels=KEPT))  # pyright: ignore[reportPrivateUsage]
    plain = _listed(broker, api, "sbx_7", {"orgId": "org-1"})

    assert await broker.sweep_warm() == 0  # first seen now
    broker._clock.advance(FAREWELL.orphan_after_s - 1)  # pyright: ignore[reportPrivateUsage]
    assert await broker.sweep_warm() == 0
    broker._clock.advance(1)  # pyright: ignore[reportPrivateUsage]
    assert await broker.sweep_warm() == 1
    assert [e[0] for e in api.execs] == [orphan]
    assert api.removed == [orphan]
    assert held not in api.removed and plain not in api.removed
    assert "reason=orphaned" in _audit(caplog)[0]


async def test_gaps2_7_an_orphan_a_turn_takes_back_is_no_orphan_and_its_clock_restarts() -> None:
    api = _ExecApi(code=0)
    broker = _world(api)
    container = _listed(broker, api, "sbx_8", KEPT)
    _ = await broker.sweep_warm()
    broker._clock.advance(FAREWELL.orphan_after_s - 10)  # pyright: ignore[reportPrivateUsage]
    # A turn adopts it, then releases it again with no keep-warm left in this process's memory.
    broker._sandboxes["sbx_8"] = _Sandbox("sbx_8", container, _spec(labels=KEPT))  # pyright: ignore[reportPrivateUsage]
    _ = await broker.sweep_warm()
    del broker._sandboxes["sbx_8"]  # pyright: ignore[reportPrivateUsage]
    broker._clock.advance(20)  # pyright: ignore[reportPrivateUsage]
    assert await broker.sweep_warm() == 0
    assert api.removed == []


def test_gaps2_8_the_claude_code_farewell_logs_out_then_wipes_and_carries_no_model_route_or_secret() -> None:
    from harness_engines.claude_code.signin import (
        LOGOUT,
        OWN_LOGIN_FORBIDDEN_ENV,
        PROXY_ENV,
        WIPE,
    )

    script = FAREWELL.argv[2]
    assert FAREWELL.argv[:2] == ("sh", "-c")
    assert script.startswith("test -e /state/claude/.credentials.json || exit 3; ")
    assert script.index(" ".join(LOGOUT)) < script.index(WIPE)
    assert not set(FAREWELL.env) & OWN_LOGIN_FORBIDDEN_ENV
    assert not any("placeholder" in v or v.startswith("pxh-") for v in FAREWELL.env.values())
    assert {k: FAREWELL.env[k] for k in PROXY_ENV} == PROXY_ENV
    assert FAREWELL.env["CLAUDE_CONFIG_DIR"] == "/state/claude"
