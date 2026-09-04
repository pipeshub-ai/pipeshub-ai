"""Phase 1b regression tests.

Each test here pins a guarantee the Phase 1 redesign claimed but did not
deliver. They are written to fail against the pre-fix tree, so a future
refactor that quietly reintroduces one of these is caught.

The recurring trap: a fake backend whose ``provision()`` returns without
ever suspending hides every concurrency bug, because the manager's
check-then-create window is only observable when provision actually
awaits. Every concurrency test below therefore uses a provision that
sleeps.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent_loop_lib.sandbox.coding.base import SandboxContext
from app.agent_loop_lib.sandbox.governor import (
    GovernorLimits,
    SandboxResourceGovernor,
    get_default_governor,
    reset_default_governor,
)
from app.agent_loop_lib.sandbox.manager import (
    SandboxLimitExceeded,
    SandboxLimits,
    SandboxManager,
    SandboxManagerError,
    SandboxType,
)


class _AwaitingBackend:
    """Backend whose provision suspends — the realistic case (a Docker
    daemon round-trip, an E2B API call), and the only one that exposes a
    check-then-create race."""

    def __init__(self, *, provision_delay: float = 0.05) -> None:
        self.sandbox_id = f"awaiting-{id(self)}"
        self._delay = provision_delay
        self.provisioned = False
        self.destroyed = False

    async def provision(self):
        await asyncio.sleep(self._delay)
        self.provisioned = True
        return None

    async def destroy(self) -> None:
        self.destroyed = True


class _HangingBackend(_AwaitingBackend):
    """Provision never completes — stands in for a wedged Docker daemon."""

    async def provision(self):
        await asyncio.sleep(3600)


class TestB2ConcurrencyCapHoldsUnderRealProvision:
    """`max_concurrent` must count in-flight creations, not just the
    instances already recorded after provision returned."""

    async def test_parallel_creates_respect_max_concurrent(self) -> None:
        created: list[_AwaitingBackend] = []

        def _factory() -> _AwaitingBackend:
            backend = _AwaitingBackend()
            created.append(backend)
            return backend

        mgr = SandboxManager()
        mgr.register_backend_factory(
            SandboxType.CODING, _factory, limits=SandboxLimits(max_concurrent=2),
        )

        results = await asyncio.gather(
            *[mgr.get_or_create(SandboxType.CODING) for _ in range(10)],
            return_exceptions=True,
        )
        succeeded = [r for r in results if not isinstance(r, BaseException)]
        denied = [r for r in results if isinstance(r, SandboxLimitExceeded)]

        assert len(succeeded) == 2, (
            f"max_concurrent=2 but {len(succeeded)} calls succeeded — the "
            f"check+reserve window is not closed"
        )
        assert len(denied) == 8
        assert mgr.active_count(SandboxType.CODING) == 2
        assert len(created) == 2, (
            f"{len(created)} backends were constructed for a cap of 2 — "
            f"denied callers still built (and possibly provisioned) a sandbox"
        )

    async def test_in_flight_slot_is_released_on_failure(self) -> None:
        """A failed provision must give its reserved slot back, or the cap
        leaks downward until no sandbox can ever be created."""
        attempts = {"n": 0}

        class _FailsOnce(_AwaitingBackend):
            async def provision(self):
                await asyncio.sleep(0)
                attempts["n"] += 1
                if attempts["n"] == 1:
                    raise RuntimeError("transient daemon error")
                self.provisioned = True

        mgr = SandboxManager()
        mgr.register_backend_factory(
            SandboxType.CODING, _FailsOnce, limits=SandboxLimits(max_concurrent=1),
        )
        with pytest.raises(SandboxManagerError):
            await mgr.get_or_create(SandboxType.CODING)
        sid, _ = await mgr.get_or_create(SandboxType.CODING)
        assert sid is not None


class TestB3ProvisionTimeoutDoesNotLeak:
    """A provision that times out has often already created the remote
    resource — a running container, a billed micro-VM. Dropping the object
    without `destroy()` leaks it until its own TTL, if it has one."""

    async def test_timed_out_backend_is_destroyed(self) -> None:
        leaked: list[_HangingBackend] = []

        def _factory() -> _HangingBackend:
            backend = _HangingBackend()
            leaked.append(backend)
            return backend

        mgr = SandboxManager()
        mgr.register_backend_factory(
            SandboxType.CODING, _factory,
            limits=SandboxLimits(provision_timeout_s=0.05),
        )
        with pytest.raises(SandboxManagerError):
            await mgr.get_or_create(SandboxType.CODING)

        assert leaked, "factory was never called"
        assert leaked[0].destroyed, (
            "provision timed out but destroy() was never called — the "
            "underlying container/VM is leaked"
        )

    async def test_failed_provision_backend_is_destroyed(self) -> None:
        leaked: list[_AwaitingBackend] = []

        class _Boom(_AwaitingBackend):
            async def provision(self):
                await asyncio.sleep(0)
                raise RuntimeError("daemon said no")

        def _factory() -> _Boom:
            backend = _Boom()
            leaked.append(backend)
            return backend

        mgr = SandboxManager()
        mgr.register_backend_factory(SandboxType.CODING, _factory)
        with pytest.raises(SandboxManagerError):
            await mgr.get_or_create(SandboxType.CODING)
        assert leaked[0].destroyed


class TestB4ProvisionErrorsKeepTheirCause:
    """"sandbox provision failed" with no cause tells an operator nothing."""

    async def test_timeout_error_names_the_timeout(self) -> None:
        mgr = SandboxManager()
        mgr.register_backend_factory(
            SandboxType.CODING, _HangingBackend,
            limits=SandboxLimits(provision_timeout_s=0.05),
        )
        with pytest.raises(SandboxManagerError) as excinfo:
            await mgr.get_or_create(SandboxType.CODING)
        assert "0.05" in str(excinfo.value)
        assert isinstance(excinfo.value.__cause__, (asyncio.TimeoutError, TimeoutError))

    async def test_underlying_error_is_chained(self) -> None:
        class _Boom(_AwaitingBackend):
            async def provision(self):
                await asyncio.sleep(0)
                raise RuntimeError("docker daemon unreachable")

        mgr = SandboxManager()
        mgr.register_backend_factory(SandboxType.CODING, _Boom)
        with pytest.raises(SandboxManagerError) as excinfo:
            await mgr.get_or_create(SandboxType.CODING)
        assert isinstance(excinfo.value.__cause__, RuntimeError)
        assert "docker daemon unreachable" in str(excinfo.value.__cause__)


class TestB5GovernorIsProcessWide:
    """A governor constructed per request caps nothing: N chats each get
    their own budget."""

    def setup_method(self) -> None:
        reset_default_governor()

    def teardown_method(self) -> None:
        reset_default_governor()

    def test_default_governor_is_a_singleton(self) -> None:
        first = get_default_governor(GovernorLimits(max_total_sandboxes=3))
        second = get_default_governor()
        assert first is second

    async def test_two_managers_share_one_budget(self) -> None:
        """Two per-request managers, one process-wide ceiling of 2."""
        gov = get_default_governor(GovernorLimits(max_total_sandboxes=2))

        managers = []
        for _ in range(2):
            mgr = SandboxManager(governor=gov)
            mgr.register_backend_factory(
                SandboxType.CODING, _AwaitingBackend, limits=SandboxLimits(),
            )
            managers.append(mgr)

        await managers[0].get_or_create(SandboxType.CODING)
        await managers[1].get_or_create(SandboxType.CODING)
        with pytest.raises(SandboxLimitExceeded):
            await managers[0].get_or_create(SandboxType.CODING)


class TestB7PerOrgFairnessApplies:
    """`CodingSandboxTool` calls `get_or_create(type, sandbox_id)` with no
    `ctx`, so the org must come from the context registered with the
    backend — otherwise `max_per_org` silently never fires."""

    async def test_per_org_cap_fires_without_ctx_argument(self) -> None:
        class _Factory:
            def create(self, ctx):
                return _AwaitingBackend()

        gov = SandboxResourceGovernor(
            GovernorLimits(max_total_sandboxes=100, max_per_org=2)
        )
        mgr = SandboxManager(governor=gov)
        mgr.register_backend(
            SandboxType.CODING, _Factory(),
            limits=SandboxLimits(),
            ctx=SandboxContext(org_id="orgA"),
        )

        # Exactly how the tool calls it: no ctx.
        await mgr.get_or_create(SandboxType.CODING)
        await mgr.get_or_create(SandboxType.CODING)
        with pytest.raises(SandboxLimitExceeded):
            await mgr.get_or_create(SandboxType.CODING)

        assert gov.snapshot()["per_org"] == {"orgA": 2}

    async def test_org_counter_drains_on_destroy(self) -> None:
        class _Factory:
            def create(self, ctx):
                return _AwaitingBackend()

        gov = SandboxResourceGovernor(GovernorLimits(max_per_org=2))
        mgr = SandboxManager(governor=gov)
        mgr.register_backend(
            SandboxType.CODING, _Factory(), ctx=SandboxContext(org_id="orgB"),
        )
        sid, _ = await mgr.get_or_create(SandboxType.CODING)
        await mgr.destroy(SandboxType.CODING, sid)
        assert gov.snapshot()["per_org"] == {}


class TestB1TypedBackendConfigReachesTheFactory:
    """`CodingSandboxConfig.local/e2b/docker` are the documented way to
    configure a backend. If they don't reach the factory that builds it, a
    deployment's hardened image and rlimits are silently replaced by the
    more permissive library defaults — a config regression that fails open
    and raises nothing."""

    def _registry(self, csc):
        from app.agent_loop_lib.sandbox.coding.registry import build_default_registry

        return build_default_registry(
            shared_config=csc, backend_options=csc.effective_backend_options(),
        )

    def test_docker_typed_fields_reach_the_factory(self) -> None:
        from app.agent_loop_lib.control_plane.config import (
            CodingSandboxConfig,
            DockerBackendConfig,
        )

        csc = CodingSandboxConfig(
            backend="docker",
            docker=DockerBackendConfig(
                image="mycorp/hardened:v3",
                memory_limit_mb=4096,
                egress_network="mynet",
                npm_registry="https://npm.internal/",
            ),
        )
        cfg = self._registry(csc).get("docker").config
        assert cfg.image == "mycorp/hardened:v3"
        assert cfg.memory_limit_mb == 4096
        assert cfg.egress_network == "mynet"
        assert cfg.npm_registry == "https://npm.internal/"

    def test_local_rlimits_are_flattened_and_reach_the_factory(self) -> None:
        from app.agent_loop_lib.control_plane.config import (
            CodingSandboxConfig,
            CodingSandboxRlimitsConfig,
            LocalBackendConfig,
        )

        csc = CodingSandboxConfig(
            local=LocalBackendConfig(
                rlimits=CodingSandboxRlimitsConfig(
                    max_memory_bytes=99, max_cpu_seconds=7,
                ),
            ),
        )
        cfg = self._registry(csc).get("local").config
        assert cfg.max_memory_bytes == 99
        assert cfg.max_cpu_seconds == 7

    def test_backend_options_override_typed_fields(self) -> None:
        from app.agent_loop_lib.control_plane.config import (
            CodingSandboxConfig,
            DockerBackendConfig,
        )

        csc = CodingSandboxConfig(
            backend="docker",
            docker=DockerBackendConfig(image="from-typed:v1"),
            backend_options={"docker": {"image": "from-options:v2"}},
        )
        assert self._registry(csc).get("docker").config.image == "from-options:v2"

    def test_projection_keys_match_every_factory_config(self) -> None:
        """A field added to a typed config but not to the factory's model
        (or renamed on either side) would otherwise be dropped in silence —
        `extra="forbid"` turns that into a loud error, and this asserts the
        two stay aligned."""
        from app.agent_loop_lib.control_plane.config import CodingSandboxConfig
        from app.agent_loop_lib.sandbox.coding.factories import BUILTIN_FACTORIES

        options = CodingSandboxConfig().effective_backend_options()
        for cls in BUILTIN_FACTORIES:
            # Raises ValidationError on any unknown or missing key.
            cls.config_model(**options[cls.backend_name])


class TestM1NoSecretsInSandboxSettings:
    """`SandboxSettings` is logged and dumped freely, and `backend_options`
    is `dict[str, Any]` — there is no `SecretStr` in it to hide behind."""

    async def test_e2b_key_never_enters_settings(self, monkeypatch) -> None:
        from app.agent_loop_lib.sandbox.coding.settings import (
            EnvSandboxSettingsLoader,
        )

        monkeypatch.setenv("SANDBOX_MODE", "e2b")
        monkeypatch.setenv("E2B_API_KEY", "sk-e2b-do-not-leak")

        settings = await EnvSandboxSettingsLoader().load(SandboxContext())
        assert "sk-e2b-do-not-leak" not in settings.model_dump_json()
        assert "sk-e2b-do-not-leak" not in repr(settings)

    def test_factory_config_masks_the_key(self) -> None:
        from app.agent_loop_lib.sandbox.coding.factories.e2b import (
            E2BCodingSandboxFactory,
        )

        cfg = E2BCodingSandboxFactory.config_model(api_key="sk-e2b-do-not-leak")
        assert "sk-e2b-do-not-leak" not in cfg.model_dump_json()
        assert "sk-e2b-do-not-leak" not in repr(cfg)

    def test_factory_still_resolves_the_key_from_env(self, monkeypatch) -> None:
        from app.agent_loop_lib.sandbox.coding.factories.e2b import (
            E2BCodingSandboxFactory,
        )

        monkeypatch.setenv("E2B_API_KEY", "sk-from-env")
        factory = E2BCodingSandboxFactory(config=E2BCodingSandboxFactory.config_model())
        assert factory._resolved_api_key() == "sk-from-env"


class TestH3ProviderTtlIsClamped:
    """A provider-side TTL longer than the manager's lifetime cap leaves a
    billed VM alive with nothing left to reap it if this process dies."""

    def test_ttl_never_exceeds_max_lifetime(self) -> None:
        from app.agent_loop_lib.sandbox.coding.factories.e2b import (
            E2BCodingSandboxFactory,
        )
        from app.agent_loop_lib.sandbox.coding.settings import SharedSandboxConfig

        factory = E2BCodingSandboxFactory(
            config=E2BCodingSandboxFactory.config_model(e2b_timeout=3600),
            shared=SharedSandboxConfig(max_lifetime_s=600),
        )
        assert factory._effective_ttl_s() == 600
        assert factory.capabilities().max_timeout_s == 600

    def test_shorter_provider_timeout_wins(self) -> None:
        from app.agent_loop_lib.sandbox.coding.factories.e2b import (
            E2BCodingSandboxFactory,
        )
        from app.agent_loop_lib.sandbox.coding.settings import SharedSandboxConfig

        factory = E2BCodingSandboxFactory(
            config=E2BCodingSandboxFactory.config_model(e2b_timeout=120),
            shared=SharedSandboxConfig(max_lifetime_s=1800),
        )
        assert factory._effective_ttl_s() == 120


class TestH2ContextReachesRemoteProviderTags:
    """Orphan cleanup and cost attribution have to answer "whose is this?"
    from the provider console alone, after this process is gone."""

    def test_e2b_provider_metadata_carries_ids_but_no_secrets(self) -> None:
        from app.agent_loop_lib.sandbox.coding.e2b import E2BCodingSandbox

        sandbox = E2BCodingSandbox(
            api_key="sk-should-not-appear",
            context=SandboxContext(
                org_id="org-9", user_id="user-9", conversation_id="conv-9",
            ),
        )
        metadata = sandbox._provider_metadata()
        assert metadata == {
            "org_id": "org-9", "user_id": "user-9", "conversation_id": "conv-9",
        }
        assert "sk-should-not-appear" not in str(metadata)

    def test_absent_context_yields_no_tags(self) -> None:
        from app.agent_loop_lib.sandbox.coding.e2b import E2BCodingSandbox

        assert E2BCodingSandbox()._provider_metadata() == {}


class TestM3CapacityDenialReachesTheModel:
    """Capacity is transient. A denial that escapes the tool as an
    exception reads to the agent as a hard failure; as a `ToolOutput` it
    reads as something to retry, which is what it is."""

    def test_governor_and_manager_share_one_exception_type(self) -> None:
        """Two same-named classes in different modules means a caller
        catching one silently misses the other."""
        from app.agent_loop_lib.sandbox import governor, manager

        assert governor.SandboxLimitExceeded is manager.SandboxLimitExceeded

    async def test_tool_returns_retryable_output_not_an_exception(self) -> None:
        from app.agent_loop_lib.tools.builtin.sandbox.coding_sandbox import (
            CodingSandboxTool,
        )

        class _Factory:
            def create(self, ctx):
                return _AwaitingBackend()

        gov = SandboxResourceGovernor(GovernorLimits(max_total_sandboxes=1))
        mgr = SandboxManager(governor=gov)
        mgr.register_backend(SandboxType.CODING, _Factory())
        await mgr.get_or_create(SandboxType.CODING)  # consume the only slot

        tool = CodingSandboxTool(mgr)
        output = await tool.execute(code='print("hi")', language="python")

        assert output.success is False
        assert output.error is not None
        assert "temporary capacity limit" in output.error
        # Told to reuse or retry — not to give up or invent a sandbox_id.
        assert "retry" in output.error.lower()


class TestB6MeteredGuardRegisteredExactlyOnce:
    """A metered backend gets the billing guard from `factory.middleware()`.
    If an operator ALSO lists it in `cfg.hooks`, registering both means two
    independent cumulative budgets and the same timeout capped twice at two
    different values."""

    def setup_method(self) -> None:
        reset_default_governor()

    def teardown_method(self) -> None:
        reset_default_governor()

    @staticmethod
    def _metered_factory():
        """A metered backend that needs no provider SDK."""
        from app.agent_loop_lib.sandbox.coding.base import (
            IsolationLevel,
            SandboxCapabilities,
        )
        from app.agent_loop_lib.sandbox.coding.factories.local import (
            LocalCodingSandboxFactory,
        )

        class _MeteredFactory(LocalCodingSandboxFactory):
            backend_name = "metered_test"

            def capabilities(self) -> SandboxCapabilities:
                return SandboxCapabilities(
                    isolation=IsolationLevel.MICROVM,
                    is_metered=True,
                    max_timeout_s=90.0,
                )

        return _MeteredFactory(config=_MeteredFactory.config_model())

    def _guard_count(self, hooks: list[str] | None) -> int:
        import asyncio

        from app.agent_loop_lib.control_plane.config import (
            CodingSandboxConfig,
            ControlPlaneConfig,
        )
        from app.agent_loop_lib.control_plane.control_plane import ControlPlane
        from app.agent_loop_lib.hooks.events import HookEvent

        factory = self._metered_factory()
        cfg = ControlPlaneConfig(
            coding_sandbox=CodingSandboxConfig(enabled=True, backend="local"),
            **({"hooks": hooks} if hooks else {}),
        )
        cp = ControlPlane(cfg)

        async def _run() -> int:
            with patch(
                "app.agent_loop_lib.sandbox.coding.registry."
                "SandboxBackendRegistry.get",
                return_value=factory,
            ):
                await cp.start()
            stack = cp._kernel.on(HookEvent.PRE_TOOL_USE)._stack
            count = sum(
                1 for _, mw in stack
                if "metered" in getattr(mw, "__qualname__", "")
            )
            await cp.stop()
            return count

        return asyncio.run(_run())

    def test_metered_backend_gets_the_guard(self) -> None:
        assert self._guard_count(hooks=None) == 1

    def test_guard_not_doubled_when_also_listed_in_hooks(self) -> None:
        assert self._guard_count(hooks=["e2b_sandbox_guard"]) == 1

    def test_guard_not_doubled_under_its_new_name_either(self) -> None:
        assert self._guard_count(hooks=["metered_sandbox_guard"]) == 1


class TestM12SettingsActuallyReachTheManager:
    """An env var that is parsed and then ignored is worse than one that
    does not exist: the operator sets it, sees no effect, and has no way to
    tell the difference from the setting not working."""

    def setup_method(self) -> None:
        reset_default_governor()

    def teardown_method(self) -> None:
        reset_default_governor()

    async def test_env_tuned_limits_are_applied(self, monkeypatch) -> None:
        from app.agents.agent_loop.sandbox_bridge import (
            build_coding_sandbox_manager,
        )

        monkeypatch.delenv("SANDBOX_MODE", raising=False)
        monkeypatch.setenv("SANDBOX_MAX_CONCURRENT_PER_REQUEST", "2")
        monkeypatch.setenv("SANDBOX_MAX_LIFETIME_S", "42.5")
        monkeypatch.setenv("SANDBOX_PROVISION_TIMEOUT_S", "7.5")

        manager = await build_coding_sandbox_manager()
        limits = manager._factories[SandboxType.CODING].limits
        assert limits.max_concurrent == 2
        assert limits.max_lifetime_s == 42.5
        assert limits.provision_timeout_s == 7.5

    async def test_explicit_argument_beats_the_env(self, monkeypatch) -> None:
        """A caller that already resolved a value must not have it re-read
        and possibly changed underneath them mid-request."""
        from app.agents.agent_loop.sandbox_bridge import (
            build_coding_sandbox_manager,
        )

        monkeypatch.delenv("SANDBOX_MODE", raising=False)
        monkeypatch.setenv("SANDBOX_MAX_CONCURRENT_PER_REQUEST", "2")

        manager = await build_coding_sandbox_manager(max_concurrent=9)
        assert manager._factories[SandboxType.CODING].limits.max_concurrent == 9

    async def test_governor_caps_come_from_env(self, monkeypatch) -> None:
        from app.agents.agent_loop.sandbox_bridge import (
            build_coding_sandbox_manager,
        )

        monkeypatch.delenv("SANDBOX_MODE", raising=False)
        monkeypatch.setenv("SANDBOX_MAX_TOTAL", "7")
        monkeypatch.setenv("SANDBOX_MAX_PER_ORG", "3")

        manager = await build_coding_sandbox_manager()
        assert manager._governor.limits.max_total_sandboxes == 7
        assert manager._governor.limits.max_per_org == 3

    @pytest.mark.parametrize("raw", ["0", "-1"])
    async def test_zero_or_negative_cap_means_unlimited(
        self, monkeypatch, raw: str,
    ) -> None:
        """Zeroing a cap is how an operator disables it. Reading it as
        "allow nothing" would make the platform refuse every sandbox."""
        from app.agent_loop_lib.sandbox.coding.settings import (
            EnvSandboxSettingsLoader,
        )

        monkeypatch.setenv("SANDBOX_MAX_TOTAL", raw)
        settings = await EnvSandboxSettingsLoader().load(SandboxContext())
        assert settings.governor.max_total_sandboxes is None

    async def test_network_flag_comes_from_settings(self, monkeypatch) -> None:
        from app.agents.agent_loop.sandbox_bridge import (
            build_coding_sandbox_manager,
        )

        monkeypatch.setenv("SANDBOX_MODE", "docker")
        monkeypatch.setenv("SANDBOX_ALLOW_NETWORK", "false")

        manager = await build_coding_sandbox_manager()
        factory = manager._factories[SandboxType.CODING].backend_factory
        assert factory.config.allow_network is False


class TestM8WarmupAndHealthAreReachable:
    """`warmup()` and `health_check()` existed but nothing called them, so
    the first `run_code` of a deployment paid for the image pull inside a
    user's request and a misconfigured backend was only discovered there."""

    async def test_warmup_pulls_image_and_creates_network(self) -> None:
        from app.agent_loop_lib.sandbox.coding.factories.docker import (
            DockerCodingSandboxFactory,
        )

        provider = MagicMock()
        provider.ensure_image = AsyncMock(return_value=False)
        provider.pull_image = AsyncMock()
        provider.ensure_egress_network = AsyncMock(return_value="net")

        factory = DockerCodingSandboxFactory(
            config=DockerCodingSandboxFactory.config_model(
                image="img:1", egress_network="net",
            ),
        )
        with patch(
            "app.agent_loop_lib.sandbox.coding.docker_client.get_default_provider",
            return_value=provider,
        ):
            await factory.warmup()

        provider.pull_image.assert_awaited_once_with("img:1")
        provider.ensure_egress_network.assert_awaited_once_with("net")

    async def test_warmup_skips_pull_when_image_present(self) -> None:
        from app.agent_loop_lib.sandbox.coding.factories.docker import (
            DockerCodingSandboxFactory,
        )

        provider = MagicMock()
        provider.ensure_image = AsyncMock(return_value=True)
        provider.pull_image = AsyncMock()
        provider.ensure_egress_network = AsyncMock(return_value="net")

        factory = DockerCodingSandboxFactory(
            config=DockerCodingSandboxFactory.config_model(image="img:1"),
        )
        with patch(
            "app.agent_loop_lib.sandbox.coding.docker_client.get_default_provider",
            return_value=provider,
        ):
            await factory.warmup()
        provider.pull_image.assert_not_awaited()

    async def test_health_check_reports_an_unreachable_daemon(self) -> None:
        from app.agent_loop_lib.sandbox.coding.factories.docker import (
            DockerCodingSandboxFactory,
        )

        provider = MagicMock()
        provider.ping = AsyncMock(return_value=False)
        factory = DockerCodingSandboxFactory(
            config=DockerCodingSandboxFactory.config_model(),
        )
        with patch(
            "app.agent_loop_lib.sandbox.coding.docker_client.get_default_provider",
            return_value=provider,
        ):
            health = await factory.health_check()
        assert health.available is False
        assert "unreachable" in (health.reason or "")

    async def test_registry_available_aggregates_every_backend(self) -> None:
        from app.agent_loop_lib.sandbox.coding.registry import (
            build_default_registry,
        )

        health = await build_default_registry().available()
        assert set(health) == {"local", "docker", "e2b"}
        # A backend that cannot answer must report why, not vanish.
        for name, result in health.items():
            assert result.available or result.reason, name

    async def test_local_warmup_is_a_harmless_no_op(self) -> None:
        """The base `warmup()` default has to be safe for backends with
        nothing to prepare, or startup would need a per-backend branch."""
        from app.agent_loop_lib.sandbox.coding.factories.local import (
            LocalCodingSandboxFactory,
        )

        factory = LocalCodingSandboxFactory(
            config=LocalCodingSandboxFactory.config_model(),
        )
        assert await factory.warmup() is None


class TestNetworkFlagStaysConsistentAcrossLayers:
    """`SANDBOX_ALLOW_NETWORK` is read in two places that cannot import each
    other: `EnvSandboxSettingsLoader` (the library, which configures the
    backend) and `sandbox_network_enabled()` (the PipesHub adapter, which
    gates prompt text and tool descriptions).

    They must agree. If they drift, the model is told it has network while
    the sandbox denies it — and it burns turns retrying a fetch that can
    never succeed.
    """

    @pytest.mark.parametrize(
        "raw", ["true", "false", "0", "1", "no", "off", "yes", "on", "", "TRUE", "False"],
    )
    async def test_both_readers_agree(self, monkeypatch, raw: str) -> None:
        from app.agent_loop_lib.sandbox.coding.settings import (
            EnvSandboxSettingsLoader,
        )
        from app.agents.agent_loop.sandbox_bridge import sandbox_network_enabled

        monkeypatch.setenv("SANDBOX_ALLOW_NETWORK", raw)
        settings = await EnvSandboxSettingsLoader().load(SandboxContext())
        assert settings.allow_network == sandbox_network_enabled(), raw

    async def test_both_default_to_enabled_when_unset(self, monkeypatch) -> None:
        from app.agent_loop_lib.sandbox.coding.settings import (
            EnvSandboxSettingsLoader,
        )
        from app.agents.agent_loop.sandbox_bridge import sandbox_network_enabled

        monkeypatch.delenv("SANDBOX_ALLOW_NETWORK", raising=False)
        settings = await EnvSandboxSettingsLoader().load(SandboxContext())
        assert settings.allow_network is True
        assert sandbox_network_enabled() is True


class TestReconnectPreservesTheProviderClock:
    """`expires_at` tells callers when E2B will reclaim the VM. E2B counts
    from when IT created the sandbox, so a reconnect that restarts the clock
    reports an expiry that is simply wrong — and the closer to the real
    deadline you reconnect, the wronger it gets. A caller trusting it would
    hand work to a sandbox that is about to disappear underneath it.
    """

    def _attached(self, *, created_at: float | None, timeout: int = 300):
        from app.agent_loop_lib.sandbox.coding.e2b import E2BCodingSandbox

        return E2BCodingSandbox.attach(
            MagicMock(), sandbox_id="sbx-1", e2b_timeout=timeout,
            created_at=created_at,
        )

    def test_expiry_is_measured_from_the_original_creation(self) -> None:
        import time

        created = time.time() - 280          # VM is 280s into a 300s life
        sandbox = self._attached(created_at=created, timeout=300)

        remaining = sandbox.expires_at - time.time()
        assert 0 < remaining < 30, (
            f"reports {remaining:.0f}s left on a VM with ~20s to live"
        )

    def test_ref_round_trips_the_creation_time(self) -> None:
        """`SandboxRef.created_at` is the only record of the provider clock
        that survives this process, so reconnect must read it back."""
        import time

        created = time.time() - 120
        sandbox = self._attached(created_at=created, timeout=300)
        assert sandbox.ref.created_at == created
        assert sandbox.ref.expires_at == created + 300

    def test_unknown_creation_time_does_not_crash(self) -> None:
        """Falling back to "now" over-reports, but a missing timestamp must
        not take the reconnect down."""
        sandbox = self._attached(created_at=None, timeout=300)
        assert sandbox.expires_at is not None

    async def test_factory_reconnect_passes_the_ref_timestamp(self) -> None:
        import sys
        import time
        import types

        from app.agent_loop_lib.sandbox.coding.base import SandboxRef
        from app.agent_loop_lib.sandbox.coding.factories.e2b import (
            E2BCodingSandboxFactory,
        )

        created = time.time() - 200
        ref = SandboxRef(
            backend="e2b", sandbox_id="sbx-9", created_at=created,
            expires_at=created + 300,
        )

        # The SDK is an optional dependency; stub the one symbol reconnect uses.
        fake_module = types.ModuleType("e2b_code_interpreter")
        fake_module.AsyncSandbox = MagicMock(connect=AsyncMock(return_value=MagicMock()))
        factory = E2BCodingSandboxFactory(
            config=E2BCodingSandboxFactory.config_model(
                api_key="sk-test", e2b_timeout=300,
            ),
        )
        with patch.dict(sys.modules, {"e2b_code_interpreter": fake_module}):
            sandbox = await factory.reconnect(ref)

        assert sandbox.ref.created_at == created


class TestNetworkFlagReachesRemoteBackends:
    """`SANDBOX_ALLOW_NETWORK=false` is an operator saying generated code must
    not reach the internet. Docker honoured it; E2B did not — the micro-VM was
    created with `allow_internet_access` at its default of True, so the code
    got full egress from a deployment that had explicitly disabled it.
    """

    def setup_method(self) -> None:
        reset_default_governor()

    def teardown_method(self) -> None:
        reset_default_governor()

    async def _e2b_factory(self, monkeypatch, allow_network: str):
        from app.agent_loop_lib.sandbox.coding.factories.e2b import (
            E2BCodingSandboxFactory,
        )
        from app.agents.agent_loop.sandbox_bridge import (
            build_coding_sandbox_manager,
        )

        monkeypatch.setenv("SANDBOX_MODE", "e2b")
        monkeypatch.setenv("SANDBOX_ALLOW_NETWORK", allow_network)
        with patch.object(E2BCodingSandboxFactory, "is_installed", return_value=True):
            manager = await build_coding_sandbox_manager()
        return manager._factories[SandboxType.CODING].backend_factory

    async def test_disabling_network_reaches_the_e2b_vm(self, monkeypatch) -> None:
        factory = await self._e2b_factory(monkeypatch, "false")
        assert factory.config.allow_internet_access is False
        # And the capability must report it, or the contract suite's
        # "supports_network=False means denied" check is meaningless here.
        assert factory.capabilities().supports_network is False

    async def test_enabled_network_still_reaches_the_e2b_vm(self, monkeypatch) -> None:
        factory = await self._e2b_factory(monkeypatch, "true")
        assert factory.config.allow_internet_access is True

    async def test_package_installs_keep_their_own_channel(self, monkeypatch) -> None:
        """`allow_network_on_install` is deliberately independent: the Docker
        backend runs code with `network_mode=none` while the install phase
        joins a dedicated egress bridge to a configured registry. Coupling
        the two would break `install_packages` for every deployment that
        disables code egress.
        """
        factory = await self._e2b_factory(monkeypatch, "false")
        assert factory.shared.allow_network_on_install is True
