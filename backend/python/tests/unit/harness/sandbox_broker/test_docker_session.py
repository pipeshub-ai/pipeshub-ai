"""MCPLEAK: a test process's Docker resources are removed by its session finalizer, and the stale reaper removes only
its own suite's resources of a dead process of this user (``tests/support/docker_session.py``)."""

from __future__ import annotations

import subprocess
import sys
import time
import uuid
from typing import TYPE_CHECKING, Any

import docker
import docker.errors
import pytest

from tests.support import docker_session
from tests.unit.agent_loop_lib.sandbox.contract.conftest import (
    DOCKER_TEST_IMAGE,
    skip_without_docker,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = [pytest.mark.docker, pytest.mark.timeout(120, method="thread")]


@pytest.fixture
def client() -> Iterator[Any]:
    skip_without_docker()
    docker_client = docker.from_env()
    yield docker_client
    docker_client.close()


@pytest.fixture
def suite(client: Any) -> Iterator[str]:  # noqa: ANN401
    name = f"mcpleak-{uuid.uuid4().hex[:8]}"
    yield name
    for info in client.api.containers(all=True, filters={"label": [f"{docker_session.SUITE}={name}"]}):
        client.api.remove_container(info["Id"], force=True)


def _dead_session() -> str:
    exited = subprocess.Popen([sys.executable, "-c", "pass"])
    _ = exited.wait()
    return f"{docker_session._HOST}:{exited.pid}:dead"  # pyright: ignore[reportPrivateUsage]


def _container(client: Any, labels: dict[str, str]) -> str:  # noqa: ANN401
    created = client.api.create_container(
        DOCKER_TEST_IMAGE, command=["true"], name=f"pxh-mcpleak-{uuid.uuid4().hex[:8]}", labels=labels
    )
    return str(created["Id"])


def _exists(client: Any, container: str) -> bool:  # noqa: ANN401
    try:
        client.api.inspect_container(container)
    except docker.errors.NotFound:
        return False
    return True


def test_the_session_finalizer_removes_this_process_containers_and_no_other(client: Any, suite: str) -> None:  # noqa: ANN401
    mine = _container(client, docker_session.labels(suite))
    other = _container(client, {**docker_session.labels(suite), docker_session.SESSION: _dead_session()})

    assert docker_session.remove_session(suite, client) == 1
    assert not _exists(client, mine)
    assert _exists(client, other)


def test_the_stale_reaper_removes_only_a_dead_session_of_its_own_suite_and_user(client: Any, suite: str) -> None:  # noqa: ANN401
    dead = _dead_session()
    own = docker_session.labels(suite)
    stale = _container(client, {**own, docker_session.SESSION: dead})
    live = _container(client, own)
    other_suite = _container(client, {**own, docker_session.SUITE: f"{suite}-other", docker_session.SESSION: dead})
    other_user = _container(client, {**own, docker_session.USER: "someone-else", docker_session.SESSION: dead})
    other_host = _container(client, {**own, docker_session.SESSION: "elsewhere:1:x"})
    try:
        assert docker_session.reap_stale(client, suite) == 0, "younger than the bound"
        assert docker_session.reap_stale(client, suite, older_than_s=0) == 1
        assert not _exists(client, stale)
        assert all(_exists(client, c) for c in (live, other_suite, other_user, other_host))
    finally:
        for container in (other_suite, other_user):
            client.api.remove_container(container, force=True)


def test_bugs_a_the_reaper_removes_a_live_looking_session_past_its_lifetime_and_keeps_one_within_it(
    client: Any, suite: str  # noqa: ANN401
) -> None:
    """A reused pid or a hung run looks alive for ever: its resources still go once past their own lifetime."""
    elsewhere = {docker_session.SESSION: "elsewhere:1:x"}
    expired = _container(client, {**docker_session.labels(suite), **elsewhere, docker_session.LIFETIME: "1"})
    within = _container(client, {**docker_session.labels(suite), **elsewhere})
    time.sleep(2.5)
    assert docker_session.reap_stale(client, suite) == 1
    assert not _exists(client, expired)
    assert _exists(client, within)


def test_bugs_a_the_e2e4_arango_carries_the_session_labels_and_goes_at_teardown(
    client: Any, suite: str, monkeypatch: pytest.MonkeyPatch  # noqa: ANN401
) -> None:
    """E2E4's ``pxh-e2e4-arango-*`` ran for days: it carried no session label, so no finalizer or reaper saw it."""
    from tests.unit.harness.engines.matrix import arango

    try:
        client.images.get(arango.ARANGO_IMAGE)
    except docker.errors.ImageNotFound:
        pytest.skip(f"{arango.ARANGO_IMAGE} not available locally")
    monkeypatch.setattr(arango, "_wait_http", lambda _server: None)
    server = arango.start_arango(suite=suite)
    try:
        labels = client.api.inspect_container(server.name)["Config"]["Labels"]
        assert labels["pxh.e2e4"] and labels["pipeshub.agent"] == "E2E4"
        assert {k: labels.get(k) for k in docker_session.labels(suite)} == docker_session.labels(suite)
        # What an interrupted run's ``atexit`` does.
        assert docker_session.remove_session(suite, client) == 1
        assert not _exists(client, server.name)
    finally:
        arango.stop_arango(server)
