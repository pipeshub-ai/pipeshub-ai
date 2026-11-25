"""PR1-S2 on a real daemon: the legacy ``coding_sandbox``/``database_sandbox`` executor runs through the Docker broker."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from app.harness.sandbox.docker_broker import DockerSandboxBroker
from app.sandbox.broker_executor import BrokerExecutor
from harness_core.ids import RandomIds
from tests.unit.agent_loop_lib.sandbox.contract.conftest import DOCKER_TEST_IMAGE

if TYPE_CHECKING:
    from pathlib import Path

    from app.harness.sandbox.docker_client import DockerClientProvider
    from harness_core.testing.clock import ManualClock

pytestmark = [pytest.mark.docker, pytest.mark.timeout(300, method="thread")]


@pytest.fixture
async def executor(
    provider: DockerClientProvider, clock: ManualClock, namespace: str, volume_prefix: str,
    cleanup_namespace: None, tmp_path: Path,
) -> BrokerExecutor:
    broker = await DockerSandboxBroker.create(
        provider=provider, clock=clock, ids=RandomIds(), label_namespace=namespace, volume_prefix=volume_prefix,
    )

    async def source() -> DockerSandboxBroker:
        return broker

    return BrokerExecutor(broker=source, image=DOCKER_TEST_IMAGE, work_root=str(tmp_path))


async def test_python_writes_an_artifact(executor: BrokerExecutor) -> None:
    code = "import os\nopen(os.path.join(os.environ['OUTPUT_DIR'], 'out.txt'), 'w').write('hi')\nprint('done')"
    result = await executor.execute(code, "python")
    assert result.success, result
    assert result.stdout.strip() == "done"
    [artifact] = result.artifacts
    assert artifact.file_name == "out.txt"
    with open(artifact.file_path) as f:
        assert f.read() == "hi"
    assert os.path.isfile(artifact.file_path)


async def test_sqlite_answers_as_csv(executor: BrokerExecutor) -> None:
    result = await executor.execute("create table t(n int); insert into t values (7); select n from t;", "sqlite")
    assert result.success, result
    assert result.stdout.split() == ["n", "7"]


async def test_typescript_runs(executor: BrokerExecutor) -> None:
    result = await executor.execute("const n: number = 6 * 7;\nconsole.log(n);", "typescript")
    assert result.success, result
    assert result.stdout.strip().endswith("42")


async def test_the_run_has_no_network(executor: BrokerExecutor) -> None:
    code = (
        "import socket\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 53), timeout=2); print('reached')\n"
        "except OSError:\n"
        "    print('offline')\n"
    )
    result = await executor.execute(code, "python")
    assert result.stdout.strip() == "offline"
