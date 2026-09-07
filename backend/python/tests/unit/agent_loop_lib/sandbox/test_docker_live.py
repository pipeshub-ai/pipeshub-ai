"""Live Docker tests — a real daemon, real containers, real npm/pip.

Skipped automatically when no daemon or sandbox image is present. These
cover what a fake client structurally cannot: that the container isolation
flags actually take effect, that concurrency limits hold when provisioning
has real latency, and that nothing is left running afterwards.

Run explicitly with:
    pytest tests/unit/agent_loop_lib/sandbox/test_docker_live.py \\
        -m docker --timeout=600 --timeout-method=thread
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from app.agent_loop_lib.sandbox.coding.base import CodeRequest, SandboxContext
from app.agent_loop_lib.sandbox.coding.docker import DockerCodingSandbox
from app.agent_loop_lib.sandbox.coding.docker_client import DockerClientProvider
from app.agent_loop_lib.sandbox.governor import (
    GovernorLimits,
    SandboxResourceGovernor,
)
from app.agent_loop_lib.sandbox.manager import (
    SandboxLimitExceeded,
    SandboxLimits,
    SandboxManager,
    SandboxType,
)

from .contract.conftest import DOCKER_TEST_IMAGE, docker_available

pytestmark = [
    pytest.mark.docker,
    pytest.mark.timeout(600, method="thread"),
]


def _require_docker() -> None:
    available, reason = docker_available()
    if not available:
        pytest.skip(reason)


def _running_sandbox_containers() -> set[str]:
    """Ids of containers started from the sandbox image, running or not.

    Compared before/after a test: the sandbox always removes its containers
    (`remove(force=True)` in a finally), so anything left behind is a leak.
    """
    import docker

    client = docker.from_env()
    try:
        return {
            c.id for c in client.containers.list(all=True)
            if DOCKER_TEST_IMAGE in (c.image.tags or [])
        }
    finally:
        client.close()


@pytest.fixture
def no_leaked_containers():
    _require_docker()
    before = _running_sandbox_containers()
    yield
    leaked = _running_sandbox_containers() - before
    assert not leaked, f"{len(leaked)} sandbox container(s) leaked: {leaked}"


def _make_sandbox(tmp_path, name: str, provider=None, **kwargs) -> DockerCodingSandbox:
    return DockerCodingSandbox(
        working_dir=str(tmp_path / name),
        image=DOCKER_TEST_IMAGE,
        image_node_modules="/home/sandbox/node_modules",
        provider=provider or DockerClientProvider(max_workers=4),
        **kwargs,
    )


class TestLiveExecution:
    async def test_typescript_and_python_both_run(
        self, tmp_path, no_leaked_containers,
    ) -> None:
        sb = _make_sandbox(tmp_path, "run")
        await sb.provision()
        try:
            ts = await sb.execute(CodeRequest(
                code='console.log("ts:" + (6 * 7));', language="typescript",
            ))
            assert ts.exit_code == 0, ts.stderr
            assert "ts:42" in ts.stdout

            py = await sb.execute(CodeRequest(
                code='print("py:" + str(6 * 7))', language="python",
            ))
            assert py.exit_code == 0, py.stderr
            assert "py:42" in py.stdout
        finally:
            await sb.destroy()
            sb._provider.close()

    async def test_deliverable_written_to_output_dir_is_extracted(
        self, tmp_path, no_leaked_containers,
    ) -> None:
        """The container is removed after every run, so a deliverable that
        isn't copied back out is simply gone."""
        sb = _make_sandbox(tmp_path, "artifacts")
        await sb.provision()
        try:
            result = await sb.execute(CodeRequest(
                code=(
                    'import os\n'
                    'p = os.path.join(os.environ["OUTPUT_DIR"], "report.csv")\n'
                    'open(p, "w").write("a,b\\n1,2\\n")\n'
                ),
                language="python",
            ))
            assert result.exit_code == 0, result.stderr
            assert any("report.csv" in a for a in result.artifacts)
            assert await sb.download_file("output/report.csv") == b"a,b\n1,2\n"
        finally:
            await sb.destroy()
            sb._provider.close()

    async def test_run_container_has_no_network_and_no_host_mounts(
        self, tmp_path, no_leaked_containers,
    ) -> None:
        """Isolation posture asserted on the kwargs the daemon actually
        received: no network, and no bind mount that would expose the host
        filesystem to generated code."""
        from docker.models.containers import ContainerCollection

        provider = DockerClientProvider(max_workers=2)
        created: list[dict] = []
        real_create = ContainerCollection.create

        # Patched on the class, not the instance: `client.containers` builds
        # a fresh ContainerCollection on every access, so an instance-level
        # spy would be attached to a throwaway object and never fire.
        def _spy(self, *args, **kwargs):
            created.append(kwargs)
            return real_create(self, *args, **kwargs)

        sb = _make_sandbox(tmp_path, "isolation", provider=provider)
        await sb.provision()
        try:
            with patch.object(ContainerCollection, "create", _spy):
                result = await sb.execute(CodeRequest(
                    code='print("isolated")', language="python", allow_network=True,
                ))
            assert result.exit_code == 0, result.stderr
        finally:
            await sb.destroy()
            provider.close()

        assert created, "no container was created"
        kwargs = created[0]
        # allow_network defaults False on the backend, so a request asking
        # for network must still get none.
        assert kwargs.get("network_mode") == "none"
        assert not kwargs.get("volumes"), f"host mounts present: {kwargs.get('volumes')}"
        assert not kwargs.get("binds"), f"host binds present: {kwargs.get('binds')}"

    async def test_real_pip_install_over_the_egress_network(
        self, tmp_path, no_leaked_containers,
    ) -> None:
        """The install phase is the only container that gets network, on a
        dedicated bridge rather than the caller's default network."""
        sb = _make_sandbox(tmp_path, "install", allow_network=True)
        await sb.provision()
        try:
            install = await sb.install_packages(["six"], "python")
            assert install.success, install.stderr

            result = await sb.execute(CodeRequest(
                code="import six; print('six:' + six.__version__)",
                language="python",
            ))
            assert result.exit_code == 0, result.stderr
            assert "six:" in result.stdout
        finally:
            await sb.destroy()
            sb._provider.close()


class TestLiveConcurrency:
    """Concurrency limits against a real daemon.

    Note on what does and does not race: `DockerCodingSandbox.provision()`
    only makes directories and never suspends — the container is created
    later, in `execute()` — so the check-then-create window stays closed
    here even with the old instance-only counting. The window opens for any
    backend whose provision does I/O: E2B's `AsyncSandbox.create`, Daytona,
    Bedrock, or Docker itself once it has to pull a cold image. That case is
    `test_max_concurrent_holds_when_provision_suspends` below, and the same
    property at unit speed in `test_phase1b_regressions.py`.
    """

    async def test_max_concurrent_holds_against_a_real_daemon(
        self, tmp_path, no_leaked_containers,
    ) -> None:
        _require_docker()
        provider = DockerClientProvider(max_workers=4)
        created: list[DockerCodingSandbox] = []

        def _factory() -> DockerCodingSandbox:
            sb = _make_sandbox(
                tmp_path, f"conc-{len(created)}", provider=provider,
            )
            created.append(sb)
            return sb

        manager = SandboxManager()
        manager.register_backend_factory(
            SandboxType.CODING, _factory,
            limits=SandboxLimits(max_concurrent=3, provision_timeout_s=120),
        )
        try:
            results = await asyncio.gather(
                *[manager.get_or_create(SandboxType.CODING) for _ in range(10)],
                return_exceptions=True,
            )
            succeeded = [r for r in results if not isinstance(r, BaseException)]
            denied = [r for r in results if isinstance(r, SandboxLimitExceeded)]

            assert len(succeeded) == 3, f"cap of 3 admitted {len(succeeded)}"
            assert len(denied) == 7
            assert manager.active_count(SandboxType.CODING) == 3
        finally:
            await manager.destroy_all()
            provider.close()

    async def test_max_concurrent_holds_when_provision_suspends(
        self, tmp_path, no_leaked_containers,
    ) -> None:
        """The case that actually races: a provision that awaits.

        Every remote backend provisions over the network, and Docker does
        too the moment it has to pull a cold image. Counting only sandboxes
        already recorded lets all 10 callers through the check while the
        first is still awaiting — so the reservation has to happen before
        the await, not after it.
        """
        _require_docker()
        provider = DockerClientProvider(max_workers=4)
        counter = {"n": 0}

        class _SlowProvision(DockerCodingSandbox):
            async def provision(self):
                # Stands in for an image pull / remote create round-trip.
                await asyncio.sleep(0.2)
                return await super().provision()

        def _factory() -> _SlowProvision:
            counter["n"] += 1
            return _SlowProvision(
                working_dir=str(tmp_path / f"slow-{counter['n']}"),
                image=DOCKER_TEST_IMAGE,
                image_node_modules="/home/sandbox/node_modules",
                provider=provider,
            )

        manager = SandboxManager()
        manager.register_backend_factory(
            SandboxType.CODING, _factory,
            limits=SandboxLimits(max_concurrent=3, provision_timeout_s=120),
        )
        try:
            results = await asyncio.gather(
                *[manager.get_or_create(SandboxType.CODING) for _ in range(10)],
                return_exceptions=True,
            )
            succeeded = [r for r in results if not isinstance(r, BaseException)]
            denied = [r for r in results if isinstance(r, SandboxLimitExceeded)]

            assert len(succeeded) == 3, (
                f"cap of 3 admitted {len(succeeded)} concurrent provisions"
            )
            assert len(denied) == 7
            assert manager.active_count(SandboxType.CODING) == 3

            # And the admitted sandboxes are genuinely usable.
            _, sb = await manager.get_or_create(
                SandboxType.CODING, sandbox_id=succeeded[0][0],
            )
            result = await sb.execute(
                CodeRequest(code='print("admitted")', language="python")
            )
            assert result.exit_code == 0, result.stderr
        finally:
            await manager.destroy_all()
            provider.close()

    async def test_governor_bounds_sandboxes_across_separate_managers(
        self, tmp_path, no_leaked_containers,
    ) -> None:
        """Two per-request managers, one process ceiling — the multi-chat
        case that per-manager limits cannot bound."""
        _require_docker()
        provider = DockerClientProvider(max_workers=4)
        governor = SandboxResourceGovernor(GovernorLimits(max_total_sandboxes=2))
        managers = []

        for i in range(3):
            mgr = SandboxManager(governor=governor)
            mgr.register_backend_factory(
                SandboxType.CODING,
                lambda i=i: _make_sandbox(tmp_path, f"gov-{i}", provider=provider),
                limits=SandboxLimits(provision_timeout_s=120),
            )
            managers.append(mgr)

        try:
            await managers[0].get_or_create(SandboxType.CODING)
            await managers[1].get_or_create(SandboxType.CODING)
            with pytest.raises(SandboxLimitExceeded):
                await managers[2].get_or_create(SandboxType.CODING)
            assert governor.snapshot()["total"] == 2
        finally:
            for mgr in managers:
                await mgr.destroy_all()
            provider.close()

    async def test_concurrent_runs_share_one_client_and_bounded_executor(
        self, tmp_path, no_leaked_containers,
    ) -> None:
        """One client per process, and container work on the provider's own
        executor — running it on asyncio's default pool lets a few
        concurrent sandboxes starve every other subsystem sharing it."""
        _require_docker()
        provider = DockerClientProvider(max_workers=2)
        sandboxes = [
            _make_sandbox(tmp_path, f"shared-{i}", provider=provider)
            for i in range(4)
        ]
        for sb in sandboxes:
            await sb.provision()

        executor_threads: set[str] = set()
        real_run_blocking = provider.run_blocking

        async def _tracking(fn, *args, **kwargs):
            import threading

            def _wrapped(*a, **kw):
                executor_threads.add(threading.current_thread().name)
                return fn(*a, **kw)

            return await real_run_blocking(_wrapped, *args, **kwargs)

        provider.run_blocking = _tracking
        try:
            results = await asyncio.gather(*[
                sb.execute(CodeRequest(code='print("parallel")', language="python"))
                for sb in sandboxes
            ])
            assert all(r.exit_code == 0 for r in results), [r.stderr for r in results]
            assert all(sb._provider is provider for sb in sandboxes)
            assert executor_threads, "no blocking work was recorded"
            assert all(
                name.startswith("docker-sandbox") for name in executor_threads
            ), f"container work ran off the dedicated executor: {executor_threads}"
            # max_workers=2 is the concurrency ceiling for blocking calls.
            assert len(executor_threads) <= 2
        finally:
            for sb in sandboxes:
                await sb.destroy()
            provider.close()


class TestLiveTeardown:
    async def test_provision_timeout_leaves_no_container_behind(
        self, tmp_path, no_leaked_containers,
    ) -> None:
        """A timed-out provision has often already made the container; the
        manager has to destroy it or it runs until something else reaps it."""
        _require_docker()
        provider = DockerClientProvider(max_workers=2)
        destroyed: list[bool] = []

        class _SlowProvision(DockerCodingSandbox):
            async def provision(self):
                await asyncio.sleep(30)

            async def destroy(self) -> None:
                destroyed.append(True)
                await super().destroy()

        manager = SandboxManager()
        manager.register_backend_factory(
            SandboxType.CODING,
            lambda: _SlowProvision(
                working_dir=str(tmp_path / "timeout"),
                image=DOCKER_TEST_IMAGE,
                provider=provider,
            ),
            limits=SandboxLimits(provision_timeout_s=0.5),
        )
        try:
            with pytest.raises(Exception, match="provision"):
                await manager.get_or_create(SandboxType.CODING)
            assert destroyed, "timed-out sandbox was never destroyed"
        finally:
            await manager.destroy_all()
            provider.close()

    async def test_destroy_all_removes_every_container(
        self, tmp_path, no_leaked_containers,
    ) -> None:
        _require_docker()
        provider = DockerClientProvider(max_workers=4)
        manager = SandboxManager()
        counter = {"n": 0}

        def _factory() -> DockerCodingSandbox:
            counter["n"] += 1
            return _make_sandbox(tmp_path, f"teardown-{counter['n']}", provider=provider)

        manager.register_backend_factory(
            SandboxType.CODING, _factory,
            limits=SandboxLimits(provision_timeout_s=120),
        )
        try:
            for _ in range(3):
                _, sb = await manager.get_or_create(SandboxType.CODING)
                await sb.execute(CodeRequest(code='print("x")', language="python"))
            assert manager.active_count(SandboxType.CODING) == 3
        finally:
            await manager.destroy_all()
            provider.close()
        assert manager.active_count(SandboxType.CODING) == 0


class TestLiveContextTagging:
    async def test_context_reaches_the_backend(self, tmp_path) -> None:
        _require_docker()
        sb = _make_sandbox(
            tmp_path, "ctx",
            context=SandboxContext(org_id="org-live", conversation_id="conv-live"),
        )
        try:
            assert sb._context.org_id == "org-live"
            assert sb._context.conversation_id == "conv-live"
        finally:
            sb._provider.close()
