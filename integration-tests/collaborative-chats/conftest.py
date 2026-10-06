"""Fixtures shared by the collaborative-chats journeys.

The owning phase of each journey (80 section 5) fills its body; the helpers a
journey needs are re-exported here so no journey builds its own users or teams.

Journeys marked ``collab_stack`` run on the real-HTTP lane (``stack/README.md``): one session-wide
stack, reset between tests. Flag-dependent tests choose the flag with ``flag_off`` / ``flag_on``.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator

import pytest

from helper.collab_stack.client import Api
from helper.collab_stack.fake_backend import FakeBackend
from helper.collab_stack.flags import FeatureFlags
from helper.collab_stack.node_api import node_binary_dir
from helper.collab_stack.python_services import graph_backend, real_python_enabled
from helper.collab_stack.stack import CollabStack, Roster, install_signal_cleanup
from helper.second_user import second_user  # noqa: F401 - fixture


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:  # noqa: ARG001
    """Two modes share one package. With ``PCC_E2E_REAL_PYTHON=1`` the Python services are real and the fake only serves the model
    endpoint, so the journeys that script the fake's AI routes cannot run; without it the real-Python tests have no services."""
    real = real_python_enabled()
    for item in items:
        if "collab_real_python" in item.keywords and not real:
            item.add_marker(pytest.mark.skip(reason="needs the real Python services: run stack/run.sh --real-python (PCC_E2E_REAL_PYTHON=1)"))
        elif real and "collab_stack" in item.keywords and not ({"collab_real_python", "collab_both_modes"} & set(item.keywords)):
            item.add_marker(pytest.mark.skip(reason="scripts the fake AI backend; runs in the fake-Python lane, not with PCC_E2E_REAL_PYTHON=1"))


@pytest.fixture(scope="session")
def graph_db() -> str:
    """The graph the real Python services run on: ``neo4j`` or ``arangodb`` (``PCC_E2E_GRAPH``)."""
    return graph_backend()


@pytest.fixture(scope="session")
def stack() -> Iterator[CollabStack]:
    """Containers, fake backend and the Node API, torn down at session end even on failure."""
    if shutil.which("docker") is None:
        pytest.skip("docker is not available; the collab_stack lane needs it")
    if not (node_binary_dir() / "node").exists():
        pytest.skip(f"no node binary in {node_binary_dir()} (set PCC_E2E_NODE_BIN)")
    booted = CollabStack()
    install_signal_cleanup(booted)
    try:
        booted.start()
        yield booted
    finally:
        booted.stop()


@pytest.fixture(autouse=True)
def _reset_stack_between_tests(request: pytest.FixtureRequest) -> None:
    """A module that sets ``STACK_KEEP_STATE = True`` builds up state across its tests (boot, reboot, converge)."""
    if "stack" in request.fixturenames and not getattr(request.module, "STACK_KEEP_STATE", False):
        request.getfixturevalue("stack").reset_state()


@pytest.fixture
def api(stack: CollabStack) -> Api:
    assert stack.api is not None
    return stack.api


@pytest.fixture
def fake(stack: CollabStack) -> FakeBackend:
    return stack.fake


@pytest.fixture
def roster(stack: CollabStack) -> Roster:
    assert stack.roster is not None
    return stack.roster


@pytest.fixture(scope="session")
def flags(stack: CollabStack) -> FeatureFlags:
    assert stack.flags is not None
    return stack.flags


@pytest.fixture
def flag_off(flags: FeatureFlags) -> Iterator[None]:
    with flags.value(False):
        yield


@pytest.fixture
def flag_on(flags: FeatureFlags) -> Iterator[None]:
    with flags.value(True):
        yield


@pytest.fixture(scope="module")
def flag_on_for_module(flags: FeatureFlags) -> Iterator[None]:
    """One toggle per module instead of one per test; each toggle waits out the API's 10 s flag cache."""
    with flags.value(True):
        yield


@pytest.fixture(scope="module")
def flag_off_for_module(flags: FeatureFlags) -> Iterator[None]:
    with flags.value(False):
        yield
