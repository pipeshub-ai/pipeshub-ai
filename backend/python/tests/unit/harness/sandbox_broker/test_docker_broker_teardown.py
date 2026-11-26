"""QA31F: what the Docker broker's create and removal failures mean to a run.

A missing image is a refusal an admin must fix, never a wait. A removal the daemon did not finish in time is the
broker's to retry, never a crash of the turn that already ended (the e2e3 redrive row was quarantined
``crash_loop`` because a ``ReadTimeout`` from ``remove_container`` left the turn's ``finally``).
"""

from __future__ import annotations

from typing import Any

import docker.errors
import pytest
import requests

from app.harness.sandbox.docker_broker import (
    _Sandbox,  # pyright: ignore[reportPrivateUsage]
)
from harness_core.sandbox.errors import SandboxRefusal
from tests.unit.harness.sandbox_broker.test_docker_broker import (
    LEASE,
    _broker_on,  # pyright: ignore[reportPrivateUsage]
    _FakeApi,  # pyright: ignore[reportPrivateUsage]
    _FakeProvider,  # pyright: ignore[reportPrivateUsage]
    _spec,  # pyright: ignore[reportPrivateUsage]
)


class _NoImageApi(_FakeApi):
    def create_container(self, image: str, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        raise docker.errors.ImageNotFound(f"No such image: {image}")


class _SlowRemoveApi(_FakeApi):
    """``remove_container`` times out ``failures`` times; the daemon goes on removing, and ``wait`` for the
    ``removed`` condition answers once it is done (``finishes``) or times out too."""

    def __init__(self, failures: int, *, finishes: bool = False, error: Exception | None = None) -> None:
        super().__init__()
        self.failures = failures
        self.finishes = finishes
        self.error = error or requests.exceptions.ReadTimeout("UnixHTTPConnectionPool: Read timed out.")
        self.removed: list[str] = []
        self.waited: list[tuple[str, str | None, int | None]] = []

    def remove_container(self, container: str, **_: Any) -> None:  # noqa: ANN401
        if self.failures:
            self.failures -= 1
            raise self.error
        self.removed.append(container)

    def wait(self, container: str, timeout: int | None = None, condition: str | None = None) -> dict[str, Any]:
        self.waited.append((container, condition, timeout))
        if not self.finishes:
            raise requests.exceptions.ReadTimeout("UnixHTTPConnectionPool: Read timed out.")
        self.removed.append(container)
        return {"StatusCode": 137}


def _in_progress() -> docker.errors.APIError:
    response = requests.Response()
    response.status_code = 409
    return docker.errors.APIError(
        "409 Conflict", response=response, explanation="removal of container ctr_1 is already in progress"
    )


def _with(api: _FakeApi) -> _FakeProvider:
    provider = _FakeProvider()
    provider.client.api = api
    return provider


async def test_qa31f_7_a_missing_image_is_refused_engine_image_missing() -> None:
    broker = _broker_on(_with(_NoImageApi()))
    with pytest.raises(SandboxRefusal) as refused:
        await broker.acquire(_spec(), lease=LEASE, idempotency_key="k")
    assert refused.value.reason == "engine_image_missing"


async def test_qa31f_8_a_removal_the_daemon_did_not_finish_is_retried_by_the_broker_not_raised() -> None:
    api = _SlowRemoveApi(failures=1)
    broker = _broker_on(_with(api))
    sandbox = _Sandbox("sbx_1", "ctr_1", _spec())
    broker._sandboxes["sbx_1"] = sandbox  # pyright: ignore[reportPrivateUsage]
    _ = broker.ledger.record("k", "sbx_1", LEASE)

    await broker.release(type("Handle", (), {"id": "sbx_1"})())
    assert api.removed == []

    _ = await broker.sweep_warm()
    assert api.removed == ["ctr_1"]
    _ = await broker.sweep_warm()
    assert api.removed == ["ctr_1"]


@pytest.mark.parametrize("slow", ["timeout", "in_progress"])
async def test_bugs_a_a_removal_the_daemon_finishes_late_is_waited_for_not_deferred(slow: str) -> None:
    """rx5 (verify lane): the daemon took more than the client's 60 s to remove a sandbox. The broker now waits
    for the ``removed`` condition, bounded, before it hands the removal to the sweep."""
    from app.harness.sandbox.docker_broker import REMOVE_WAIT_S

    api = _SlowRemoveApi(failures=1, finishes=True, error=_in_progress() if slow == "in_progress" else None)
    broker = _broker_on(_with(api))
    broker._sandboxes["sbx_1"] = _Sandbox("sbx_1", "ctr_1", _spec())  # pyright: ignore[reportPrivateUsage]
    _ = broker.ledger.record("k", "sbx_1", LEASE)

    await broker.release(type("Handle", (), {"id": "sbx_1"})())
    assert api.waited == [("ctr_1", "removed", REMOVE_WAIT_S)]
    assert api.removed == ["ctr_1"]
    assert broker._removals == {}  # pyright: ignore[reportPrivateUsage]


async def test_bugs_a_any_other_removal_error_is_not_waited_for() -> None:
    response = requests.Response()
    response.status_code = 500
    api = _SlowRemoveApi(failures=1, finishes=True, error=docker.errors.APIError("500", response=response))
    broker = _broker_on(_with(api))
    broker._sandboxes["sbx_1"] = _Sandbox("sbx_1", "ctr_1", _spec())  # pyright: ignore[reportPrivateUsage]
    _ = broker.ledger.record("k", "sbx_1", LEASE)

    await broker.release(type("Handle", (), {"id": "sbx_1"})())
    assert api.waited == []
    assert broker._removals == {"sbx_1": "ctr_1"}  # pyright: ignore[reportPrivateUsage]


class _LateCreateApi(_FakeApi):
    """The daemon is slow: ``create_container`` times out on the client, and the container it made after holds the
    sandbox's volume, so removing that volume answers 409 "volume is in use"."""

    def create_container(self, image: str, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        raise requests.exceptions.ReadTimeout("UnixHTTPConnectionPool(host='localhost', port=None): Read timed out.")

    def volumes(self, **_: Any) -> dict[str, Any]:  # noqa: ANN401
        return {"Volumes": [{"Name": "pxh-sbx-volume"}]}

    def remove_volume(self, name: str, force: bool = False) -> None:
        response = requests.Response()
        response.status_code = 409
        raise docker.errors.APIError("409 Conflict", response=response, explanation=f"remove {name}: volume is in use")


async def test_r14_brk_1_a_create_the_slow_daemon_timed_out_is_an_outage_even_when_its_cleanup_fails() -> None:
    """Round 14 gate (recs2 r5 on a loaded host): ``create_container`` timed out, the cleanup's volume removal hit
    409 because the late container still held it, and that cleanup error replaced the timeout, so the kernel saw a
    crash (``engine_kernel_error``) instead of the broker outage it waits out. The outage is raised; what the cleanup
    could not remove is left to the label sweep."""
    from harness_core.sandbox.errors import BrokerUnavailable

    broker = _broker_on(_with(_LateCreateApi()))
    with pytest.raises(BrokerUnavailable) as raised:
        await broker.acquire(_spec(), lease=LEASE, idempotency_key="k-late")
    assert "ReadTimeout" in str(raised.value)
