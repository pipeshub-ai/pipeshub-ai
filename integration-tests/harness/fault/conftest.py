"""Fixtures for the L4 fault suite: the Docker infrastructure once, Node once per broker.

    cd integration-tests/harness/fault
    python -m pytest --broker kafka --fault-artifacts ./out

Needs Docker, ``npm ci`` in backend/nodejs/apps, and a Python with the backend
installed (the worker runs ``app.harness_main``).
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

import stack

BROKERS = ("redis", "kafka")


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--broker", choices=(*BROKERS, "all"), default="all", help="message broker(s) to run the catalog on")
    parser.addoption("--fault-artifacts", default=None, help="directory for logs and result files (default: a pytest tmp dir)")
    parser.addoption(
        "--port-base", type=int, default=None,
        help="host ports from here up (mongo, redis, kafka, node, ...) and a compose project of its own",
    )


def pytest_configure(config: pytest.Config) -> None:
    stack.configure(config.getoption("--port-base"))


def selected_brokers(config: pytest.Config) -> tuple[str, ...]:
    choice = config.getoption("--broker")
    return BROKERS if choice == "all" else (choice,)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """One broker's session at a time across modules: two would start two Nodes on the same port."""
    order = {broker: index for index, broker in enumerate(BROKERS)}

    def broker_of(item: pytest.Item) -> int:
        callspec = getattr(item, "callspec", None)
        return order.get(callspec.params.get("broker"), -1) if callspec is not None else -1

    items.sort(key=broker_of)


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "broker" in metafunc.fixturenames:
        metafunc.parametrize("broker", selected_brokers(metafunc.config), scope="session")


@pytest.fixture(scope="session")
def artifacts(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> Path:
    chosen = request.config.getoption("--fault-artifacts")
    root = Path(chosen).resolve() if chosen else tmp_path_factory.mktemp("harness-fault")
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture(scope="session")
def infrastructure(request: pytest.FixtureRequest):
    stack.infrastructure_down()
    stack.infrastructure_up("kafka" if "kafka" in selected_brokers(request.config) else "redis")
    yield
    stack.infrastructure_down()


@pytest.fixture(scope="session")
def fault_stack(broker: str, infrastructure: None, artifacts: Path):
    out = artifacts / broker
    out.mkdir(parents=True, exist_ok=True)
    # Nothing another broker's session left in Redis (hints, streams, live frames) leaks into this one.
    stack.compose("exec", "-T", "redis", "redis-cli", "FLUSHALL")
    env = stack.stack_env(broker, f"harness_fault_{broker}_{int(time.time())}")
    node_log = out / "node.log"
    node = stack.node_process(env, node_log)
    node.start()
    yield stack.FaultStack(broker=broker, env=env, node=node, node_log=node_log, out=out)
    node.kill9()


@pytest.fixture(scope="session")
def credential(fault_stack: stack.FaultStack) -> str:
    return stack.worker_credential()
