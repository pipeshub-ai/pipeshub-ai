"""Tests for ``SandboxBackendRegistry``, ``build_default_registry``, and the
concrete factory system (``build_factory``, ``LocalCodingSandboxFactory``)."""

from __future__ import annotations

import uuid

import pytest
from pydantic import BaseModel

from app.agent_loop_lib.sandbox.base import SandboxInfo
from app.agent_loop_lib.sandbox.coding.base import (
    CodeRequest,
    CodeResult,
    CodingSandboxBackend,
    CodingLanguage,
    InstallResult,
    IsolationLevel,
    SandboxCapabilities,
    SandboxContext,
)
from app.agent_loop_lib.sandbox.coding.registry import (
    BackendHealth,
    SandboxBackendFactory,
    SandboxBackendRegistry,
    build_default_registry,
)


# ---------------------------------------------------------------------------
# Fake factory / backend for testing
# ---------------------------------------------------------------------------

class FakeConfig(BaseModel):
    name: str = "test"


_FAKE_SANDBOX_ID = uuid.uuid4().hex


class FakeCodingSandbox(CodingSandboxBackend):
    """Minimal concrete backend — every abstract method is a no-op."""

    @property
    def sandbox_id(self) -> str:
        return _FAKE_SANDBOX_ID

    async def provision(self) -> SandboxInfo:
        return SandboxInfo(sandbox_id=self.sandbox_id, status="ready")

    async def execute(self, request: CodeRequest) -> CodeResult:
        return CodeResult(
            stdout="ok",
            stderr="",
            exit_code=0,
            language=request.language,
            duration_ms=0.0,
        )

    async def install_packages(
        self, packages: list[str], language: CodingLanguage
    ) -> InstallResult:
        return InstallResult(success=True)

    async def upload_file(self, path: str, content: bytes) -> None:
        pass

    async def download_file(self, path: str) -> bytes:
        return b""

    async def list_files(self) -> list[str]:
        return []

    async def destroy(self) -> None:
        pass

    @property
    def capabilities(self) -> SandboxCapabilities:
        return SandboxCapabilities(isolation=IsolationLevel.HOST)


class FakeSandboxFactory(SandboxBackendFactory):
    backend_name = "fake"
    config_model = FakeConfig

    def is_installed(self) -> bool:
        return True

    async def health_check(self) -> BackendHealth:
        return BackendHealth(available=True)

    def create(self, ctx: SandboxContext) -> CodingSandboxBackend:
        return FakeCodingSandbox()

    def capabilities(self) -> SandboxCapabilities:
        return SandboxCapabilities(isolation=IsolationLevel.HOST)


class _UnhealthyFactory(FakeSandboxFactory):
    """A factory that reports itself as unavailable."""

    backend_name = "unhealthy"

    async def health_check(self) -> BackendHealth:
        return BackendHealth(available=False, reason="daemon not running")


def _make_factory(name: str = "fake") -> FakeSandboxFactory:
    """Return a ``FakeSandboxFactory`` with a custom ``backend_name``."""
    factory = FakeSandboxFactory(config=FakeConfig())
    factory.backend_name = name  # type: ignore[misc]
    return factory


# ---------------------------------------------------------------------------
# TestSandboxBackendRegistry
# ---------------------------------------------------------------------------

class TestSandboxBackendRegistry:

    def test_register_and_get(self) -> None:
        registry = SandboxBackendRegistry()
        factory = _make_factory()
        registry.register(factory)
        assert registry.get("fake") is factory

    def test_register_duplicate_raises(self) -> None:
        registry = SandboxBackendRegistry()
        registry.register(_make_factory("dup"))
        with pytest.raises(ValueError, match="already registered"):
            registry.register(_make_factory("dup"))

    def test_get_unknown_raises_with_known_names(self) -> None:
        registry = SandboxBackendRegistry()
        registry.register(_make_factory("alpha"))
        with pytest.raises(ValueError, match="alpha"):
            registry.get("nope")

    def test_names_returns_sorted(self) -> None:
        registry = SandboxBackendRegistry()
        registry.register(_make_factory("b"))
        registry.register(_make_factory("a"))
        assert registry.names() == ["a", "b"]

    def test_contains(self) -> None:
        registry = SandboxBackendRegistry()
        registry.register(_make_factory("present"))
        assert "present" in registry
        assert "absent" not in registry

    async def test_available_aggregates_health(self) -> None:
        registry = SandboxBackendRegistry()

        healthy = FakeSandboxFactory(config=FakeConfig())
        unhealthy = _UnhealthyFactory(config=FakeConfig())

        registry.register(healthy)
        registry.register(unhealthy)

        results = await registry.available()
        assert "fake" in results
        assert "unhealthy" in results
        assert results["fake"].available is True
        assert results["unhealthy"].available is False


# ---------------------------------------------------------------------------
# TestBuildDefaultRegistry
# ---------------------------------------------------------------------------

class TestBuildDefaultRegistry:

    def test_local_always_registered(self) -> None:
        registry = build_default_registry()
        assert "local" in registry.names()

    def test_unknown_backend_in_options_ignored(self) -> None:
        registry = build_default_registry(
            backend_options={"nonexistent": {"some_key": "some_val"}}
        )
        assert "local" in registry.names()


# ---------------------------------------------------------------------------
# TestFactories
# ---------------------------------------------------------------------------

class TestFactories:

    def test_local_factory_is_installed(self) -> None:
        from app.agent_loop_lib.sandbox.coding.factories.local import (
            LocalCodingSandboxFactory,
        )

        factory = LocalCodingSandboxFactory(config=LocalCodingSandboxFactory.config_model())
        assert factory.is_installed() is True

    def test_local_factory_create_returns_local_sandbox(self) -> None:
        from app.agent_loop_lib.sandbox.coding.factories.local import (
            LocalCodingSandboxFactory,
        )
        from app.agent_loop_lib.sandbox.coding.local import LocalCodingSandbox

        factory = LocalCodingSandboxFactory(config=LocalCodingSandboxFactory.config_model())
        sandbox = factory.create(SandboxContext())
        assert isinstance(sandbox, LocalCodingSandbox)

    async def test_local_factory_health_check(self) -> None:
        from app.agent_loop_lib.sandbox.coding.factories.local import (
            LocalCodingSandboxFactory,
        )

        factory = LocalCodingSandboxFactory(config=LocalCodingSandboxFactory.config_model())
        health = await factory.health_check()
        assert health.available is True

    def test_build_factory_unknown_raises(self) -> None:
        from app.agent_loop_lib.sandbox.coding.factories import build_factory

        with pytest.raises(ValueError, match="unknown sandbox backend"):
            build_factory("nonexistent")
