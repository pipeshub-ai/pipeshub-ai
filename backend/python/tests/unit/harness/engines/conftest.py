"""Fixtures of the engines' real lane (``-m docker``): skipped without Docker, the engine image or the helper image.

A real lane on runc (a daemon without ``runsc``, as CI's, or ``HARNESS_TEST_SANDBOX_RUNTIME=runc``) runs its engines
with the documented dev and test override (``PIPESHUB_ENGINES_WITHOUT_GVISOR``, ``policy/release.json`` ``engineIsolation``): the release
floor is gVisor, and production refuses such a host (``test_gvisor_floor.py``)."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from harness_core.engines.isolation import OVERRIDE_ENV
from harness_core.sandbox.types import RuntimeMode
from tests.unit.harness.engines.real_stack import (
    engine_broker,
    lane_available,
    lane_mode,
    start_stack,
    stop_stack,
)
from tests.unit.harness.sandbox_broker.conftest import cleanup, cleanup_networks

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from app.harness.sandbox.docker_broker import DockerSandboxBroker
    from tests.unit.harness.engines.real_stack import RealStack


@pytest.fixture(autouse=True)
def _engines_without_gvisor_on_a_runc_host(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.get_closest_marker("docker") is not None and lane_mode() is RuntimeMode.DOCKER_HARDENED:
        monkeypatch.setenv(OVERRIDE_ENV, "true")


@pytest.fixture(scope="module")
def real_stack() -> Iterator[RealStack]:
    available, reason = lane_available()
    if not available:
        pytest.skip(reason)
    stack = start_stack()
    yield stack
    stop_stack(stack)


@pytest.fixture
async def stack(real_stack: RealStack) -> AsyncIterator[RealStack]:
    async with real_stack.serving() as serving:
        yield serving


@pytest.fixture
async def broker(stack: RealStack) -> AsyncIterator[DockerSandboxBroker]:
    namespace = f"io.pipeshub.e0real.{uuid.uuid4().hex[:8]}"
    prefix = "pxh-e0r-" + namespace.rsplit(".", 1)[1]
    created, provider = await engine_broker(stack, namespace, prefix)
    yield created
    await created.aclose()
    await provider.run_blocking(cleanup, provider, namespace, prefix)
    await provider.run_blocking(cleanup_networks, provider, namespace, stack.proxy)
    provider.close()
