"""SEC-EG-14 on real Docker (C6.12, WP-45, WP-61): a sandbox from the Docker broker sends through the egress proxy
container (``egress_entry`` with ``EGRESS_RUN_MAX_MB=1`` and a 1 MiB up threshold). Model Gateway uploads past that
size are never judged; outside uploads (to an internal host ``EGRESS_PRIVATE_ALLOW`` opens) past the cap are refused
``volume_cap_exceeded`` and nothing more reaches that host, while the run's next Model Gateway call goes on marked
``X-Egress-Capped: volume`` so the gateway ends the run; another run's sandbox goes on, and the proxy's flush raises
the run's alerts. Every object is a ``pxh-*`` one on the rig's explicit subnet; runc lane."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import pytest

from harness_core.sandbox.types import EgressDestination
from tests.unit.agent_loop_lib.sandbox.contract.conftest import skip_without_docker
from tests.unit.harness.sandbox_broker.egress_stack import (
    HELPER_IMAGE,
    TARGET_PORT,
    StackOptions,
    start_stack,
    stop_stack,
)
from tests.unit.harness.sandbox_broker.test_egress_security_docker import (
    MODEL,
    Sandbox,
    acquire,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.harness.sandbox.docker_broker import DockerSandboxBroker
    from harness_core.sandbox.types import RuntimeMode
    from tests.unit.harness.sandbox_broker.egress_stack import EgressStack

pytestmark = [pytest.mark.docker, pytest.mark.timeout(600, method="thread")]

CHUNK_KIB = 600
UPLOAD = "/upload"
# ``python -m http.server`` answers a POST 501, after the body reached it: what matters is that it was forwarded.
OUTSIDE_ANSWER = 501


def _uploads(stack: EgressStack) -> int:
    """POSTs the outside host received (``http.server`` logs each request line)."""
    return sum(1 for line in stack.logs(stack.target).splitlines() if f'"POST {UPLOAD} ' in line)


@pytest.fixture(scope="module")
def egress_stack() -> Iterator[EgressStack]:
    import docker

    skip_without_docker()
    client = docker.from_env()
    try:
        client.images.get(HELPER_IMAGE)
    except Exception:
        client.close()
        pytest.skip(f"image {HELPER_IMAGE!r} not present")
    stack = start_stack(
        client,
        StackOptions(
            proxy_env=lambda stack: {
                "EGRESS_RUN_MAX_MB": "1",
                "EGRESS_VOLUME_ALERT_UP_MB": "1",
                # The stack's plain HTTP server, by name, stands in for an outside destination.
                "EGRESS_PRIVATE_ALLOW": f"{stack.target}:{TARGET_PORT}",
            }
        ),
    )
    yield stack
    stop_stack(stack)
    client.close()


async def _upload(box: Sandbox, kib: int, url: str, headers: dict[str, str] | None = None) -> tuple[int, str, str]:
    _ = await box.out(["/bin/sh", "-c", f"head -c {kib * 1024} /dev/zero > /tmp/.wp45-body"])
    return await box.http(
        url,
        {"Content-Type": "application/octet-stream", **(headers or {})},
        method="POST",
        data="@/tmp/.wp45-body",
    )


async def _model(box: Sandbox, kib: int) -> tuple[int, str, str]:
    return await _upload(box, kib, f"{MODEL}/v1/messages", {"Authorization": f"Bearer {box.model_ph}"})


def _proxy_lines(stack: EgressStack, event: str) -> list[str]:
    return [line for line in stack.logs(stack.proxy).splitlines() if event in line]


async def _wait_for(stack: EgressStack, event: str, needle: str, *, timeout_s: float = 30.0) -> list[str]:
    deadline = time.monotonic() + timeout_s
    while True:
        found = [line for line in _proxy_lines(stack, event) if needle in line]
        if found or time.monotonic() > deadline:
            return found
        await asyncio.sleep(0.5)


async def test_sec_eg_14_past_its_outside_cap_a_run_is_refused_marked_and_another_run_goes_on(
    egress_docker_broker: DockerSandboxBroker, egress_stack: EgressStack, runtime_mode: RuntimeMode
) -> None:
    outside = EgressDestination(egress_stack.target, (TARGET_PORT,))
    capped = await acquire(egress_docker_broker, runtime_mode, run_id="run-wp45-a", org="org-wp45", extra=(outside,))
    other = await acquire(egress_docker_broker, runtime_mode, run_id="run-wp45-b", org="org-wp45", extra=(outside,))
    target = f"http://{egress_stack.target}:{TARGET_PORT}{UPLOAD}"
    try:
        # WP-61: the Model Gateway's traffic is never judged, however large.
        for _ in range(3):
            assert (await _model(capped, CHUNK_KIB))[0] == 200
        first = await _upload(capped, CHUNK_KIB, target)
        assert first[0] == OUTSIDE_ANSWER, first
        uploads = _uploads(egress_stack)
        assert (await _upload(capped, CHUNK_KIB, target))[:2] == (403, "volume_cap_exceeded")
        await asyncio.sleep(1)
        assert _uploads(egress_stack) == uploads, (
            "nothing past the cap reached the outside host"
        )
        # The run's next model call goes on, marked, for the gateway to end the run with its reason.
        assert (await _model(capped, 4))[0] == 200
        await asyncio.sleep(1)
        assert egress_stack.gateway_marks()[-1] == ("/v1/messages", "volume")
        assert (await _upload(other, 4, target))[0] == OUTSIDE_ANSWER, "another run of the same org is not capped"
        assert (await _model(other, 4))[0] == 200
        await asyncio.sleep(1)
        assert egress_stack.gateway_marks()[-1] == ("/v1/messages", ""), "another run's calls are not marked"
        refused = await _wait_for(egress_stack, "egress.refused", "reason=volume_cap_exceeded")
        assert refused and all("runId=run-wp45-a" in line for line in refused)
        # The flush (every 10 s) raises the run's alerts: past the up threshold, and the cap.
        alerts = await _wait_for(egress_stack, "egress.volume_alert", "kind=run_cap")
        assert alerts and "runId=run-wp45-a" in alerts[0] and "alias=other" in alerts[0]
        threshold = await _wait_for(egress_stack, "egress.volume_alert", "kind=run_threshold")
        assert threshold and "direction=up" in threshold[0] and "alias=other" in threshold[0]
        assert not [line for line in _proxy_lines(egress_stack, "egress.volume_alert") if "run-wp45-b" in line]
    finally:
        await egress_docker_broker.release(capped.handle)
        await egress_docker_broker.release(other.handle)
