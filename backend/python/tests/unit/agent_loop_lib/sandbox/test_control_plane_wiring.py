"""Integration tests: ControlPlane sandbox wiring with the registry.

Verifies that ``ControlPlane.start()`` correctly uses the registry to wire
sandbox backends, tools, and factory middleware for each backend name."""

from __future__ import annotations


import pytest

from app.agent_loop_lib.control_plane.config import (
    CodingSandboxConfig,
    GovernorConfig,
)


class TestCodingSandboxConfigDefaults:
    def test_backend_options_default_empty(self) -> None:
        csc = CodingSandboxConfig()
        assert csc.backend_options == {}

    def test_provision_timeout_default(self) -> None:
        csc = CodingSandboxConfig()
        assert csc.provision_timeout_s == 60.0

    def test_governor_defaults(self) -> None:
        csc = CodingSandboxConfig()
        assert csc.governor.max_total_sandboxes == 50
        assert csc.governor.max_per_org == 10

    def test_backend_options_override(self) -> None:
        csc = CodingSandboxConfig(
            backend="docker",
            backend_options={"docker": {"image": "custom:v2", "memory_limit_mb": 1024}},
        )
        assert csc.backend_options["docker"]["image"] == "custom:v2"
        assert csc.backend_options["docker"]["memory_limit_mb"] == 1024

    def test_governor_config_override(self) -> None:
        csc = CodingSandboxConfig(
            governor=GovernorConfig(max_total_sandboxes=20, max_per_org=3),
        )
        assert csc.governor.max_total_sandboxes == 20
        assert csc.governor.max_per_org == 3


class TestRegistryDispatchFromConfig:
    """Test that ``build_default_registry`` correctly creates factories
    from ``CodingSandboxConfig``."""

    def test_local_backend_in_default_registry(self) -> None:
        from app.agent_loop_lib.sandbox.coding.registry import build_default_registry

        registry = build_default_registry()
        assert "local" in registry
        factory = registry.get("local")
        assert factory.backend_name == "local"

    def test_docker_backend_in_default_registry(self) -> None:
        from app.agent_loop_lib.sandbox.coding.registry import build_default_registry

        registry = build_default_registry()
        assert "docker" in registry

    def test_every_builtin_backend_is_registered(self) -> None:
        """Registration is independent of whether the provider SDK is
        installed. Omitting an uninstalled backend would report it as
        "unknown sandbox backend", sending the operator after a typo
        instead of a missing package."""
        from app.agent_loop_lib.sandbox.coding.registry import build_default_registry

        registry = build_default_registry()
        assert set(registry.names()) == {"local", "docker", "e2b"}

    def test_uninstalled_backend_reports_the_real_reason(self) -> None:
        from app.agent_loop_lib.sandbox.coding.registry import build_default_registry

        registry = build_default_registry()
        try:
            import e2b_code_interpreter  # noqa: F401
        except ImportError:
            pass
        else:
            pytest.skip("e2b SDK is installed; nothing uninstalled to assert on")

        with pytest.raises(ValueError, match="dependencies are not installed"):
            registry.get("e2b")

    def test_invalid_backend_options_surface_as_validation_errors(self) -> None:
        """A typo in backend options is a deployment mistake the operator
        has to see with its field errors — not one silently swallowed and
        re-reported later as an unrelated 'unknown backend'."""
        from pydantic import ValidationError

        from app.agent_loop_lib.sandbox.coding.registry import build_default_registry

        with pytest.raises(ValidationError):
            build_default_registry(
                backend_options={"docker": {"imagge": "typo/sandbox:v1"}},
            )

    def test_unknown_backend_raises_with_known_names(self) -> None:
        from app.agent_loop_lib.sandbox.coding.registry import build_default_registry

        registry = build_default_registry()
        with pytest.raises(ValueError, match="unknown sandbox backend 'nonexistent'"):
            registry.get("nonexistent")

    def test_backend_options_passed_to_factory(self) -> None:
        from app.agent_loop_lib.sandbox.coding.registry import build_default_registry

        registry = build_default_registry(
            backend_options={"docker": {"image": "custom:v3"}},
        )
        factory = registry.get("docker")
        assert factory.config.image == "custom:v3"

    async def test_local_factory_health_check(self) -> None:
        from app.agent_loop_lib.sandbox.coding.registry import build_default_registry

        registry = build_default_registry()
        factory = registry.get("local")
        health = await factory.health_check()
        assert health.available is True

    async def test_available_returns_all_backends(self) -> None:
        from app.agent_loop_lib.sandbox.coding.registry import build_default_registry

        registry = build_default_registry()
        health_map = await registry.available()
        assert "local" in health_map
        assert health_map["local"].available is True


class TestFactoryMiddleware:
    """Test that factories correctly return middleware for metered backends."""

    def test_local_factory_no_middleware(self) -> None:
        from app.agent_loop_lib.sandbox.coding.factories.local import (
            LocalCodingSandboxFactory,
        )

        factory = LocalCodingSandboxFactory(
            config=LocalCodingSandboxFactory.config_model(),
        )
        assert factory.middleware() == []

    def test_docker_factory_no_middleware(self) -> None:
        from app.agent_loop_lib.sandbox.coding.factories.docker import (
            DockerCodingSandboxFactory,
        )

        factory = DockerCodingSandboxFactory(
            config=DockerCodingSandboxFactory.config_model(),
        )
        assert factory.middleware() == []

    def test_e2b_factory_returns_metered_guard(self) -> None:
        from app.agent_loop_lib.sandbox.coding.factories.e2b import (
            E2BCodingSandboxFactory,
        )

        factory = E2BCodingSandboxFactory(
            config=E2BCodingSandboxFactory.config_model(),
        )
        specs = factory.middleware()
        assert len(specs) == 1
        spec = specs[0]
        assert spec.name == "metered_sandbox_guard"
        assert spec.event == "pre_tool_use"
        assert spec.path_pattern == "/toolsets/coding_sandbox/**"
        assert callable(spec.middleware)

    def test_metered_guard_comes_from_the_capability_not_the_provider(self) -> None:
        """The guard is keyed off `is_metered`, so a future metered
        provider cannot lose its billing cap by omitting an override."""
        from app.agent_loop_lib.sandbox.coding.factories.docker import (
            DockerCodingSandboxFactory,
        )
        from app.agent_loop_lib.sandbox.coding.factories.local import (
            LocalCodingSandboxFactory,
        )

        for cls in (LocalCodingSandboxFactory, DockerCodingSandboxFactory):
            factory = cls(config=cls.config_model())
            assert factory.capabilities().is_metered is False
            assert factory.middleware() == []


class TestFactoryCapabilities:
    """Test that factories declare correct capabilities."""

    def test_local_capabilities(self) -> None:
        from app.agent_loop_lib.sandbox.coding.base import IsolationLevel
        from app.agent_loop_lib.sandbox.coding.factories.local import (
            LocalCodingSandboxFactory,
        )

        factory = LocalCodingSandboxFactory(
            config=LocalCodingSandboxFactory.config_model(),
        )
        caps = factory.capabilities()
        assert caps.isolation == IsolationLevel.HOST
        assert caps.is_metered is False
        assert caps.supports_reconnect is False

    def test_docker_capabilities(self) -> None:
        from app.agent_loop_lib.sandbox.coding.base import IsolationLevel
        from app.agent_loop_lib.sandbox.coding.factories.docker import (
            DockerCodingSandboxFactory,
        )

        factory = DockerCodingSandboxFactory(
            config=DockerCodingSandboxFactory.config_model(),
        )
        caps = factory.capabilities()
        assert caps.isolation == IsolationLevel.CONTAINER
        assert caps.is_metered is False

    def test_e2b_capabilities(self) -> None:
        from app.agent_loop_lib.sandbox.coding.base import IsolationLevel
        from app.agent_loop_lib.sandbox.coding.factories.e2b import (
            E2BCodingSandboxFactory,
        )

        factory = E2BCodingSandboxFactory(
            config=E2BCodingSandboxFactory.config_model(),
        )
        caps = factory.capabilities()
        assert caps.isolation == IsolationLevel.MICROVM
        assert caps.is_metered is True
        assert caps.supports_streaming is True
        assert caps.supports_reconnect is True


class TestManagerWithRegistry:
    """Integration: SandboxManager with registry-dispatched backends."""

    async def test_manager_creates_local_sandbox_via_registry(self) -> None:
        from app.agent_loop_lib.sandbox.coding.base import SandboxContext
        from app.agent_loop_lib.sandbox.coding.local import LocalCodingSandbox
        from app.agent_loop_lib.sandbox.coding.registry import build_default_registry
        from app.agent_loop_lib.sandbox.manager import (
            SandboxLimits,
            SandboxManager,
            SandboxType,
        )

        registry = build_default_registry()
        factory = registry.get("local")
        manager = SandboxManager()
        manager.register_backend(
            SandboxType.CODING,
            factory,
            limits=SandboxLimits(max_concurrent=2),
        )

        sid, backend = await manager.get_or_create(
            SandboxType.CODING, ctx=SandboxContext(org_id="test-org"),
        )
        assert isinstance(backend, LocalCodingSandbox)
        assert manager.active_count(SandboxType.CODING) == 1

        await manager.destroy_all()
        assert manager.active_count(SandboxType.CODING) == 0

    async def test_manager_with_governor_from_config(self) -> None:
        from app.agent_loop_lib.sandbox.coding.base import SandboxContext
        from app.agent_loop_lib.sandbox.coding.registry import build_default_registry
        from app.agent_loop_lib.sandbox.governor import (
            GovernorLimits,
            SandboxResourceGovernor,
        )
        from app.agent_loop_lib.sandbox.manager import (
            SandboxLimits,
            SandboxManager,
            SandboxType,
        )

        governor = SandboxResourceGovernor(
            GovernorLimits(max_total_sandboxes=1, max_per_org=1),
        )
        registry = build_default_registry()
        factory = registry.get("local")
        manager = SandboxManager(governor=governor)
        manager.register_backend(
            SandboxType.CODING,
            factory,
            limits=SandboxLimits(max_concurrent=5),
        )

        sid, _ = await manager.get_or_create(
            SandboxType.CODING, ctx=SandboxContext(org_id="org-a"),
        )
        assert governor.snapshot()["total"] == 1

        await manager.destroy(SandboxType.CODING, sid)
        assert governor.snapshot()["total"] == 0
