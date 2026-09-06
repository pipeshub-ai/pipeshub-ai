"""Security-focused tests for the sandbox abstraction.

These verify that sandbox boundaries, environment sanitisation, package
validation, and cross-manager isolation hold — regardless of which backend
is in use.
"""

from __future__ import annotations

import io
import tarfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent_loop_lib.sandbox.coding.base import (
    SandboxContext,
    SandboxRef,
)
from app.agent_loop_lib.sandbox.coding.environment import sanitized_subprocess_env
from app.agent_loop_lib.sandbox.coding.local import LocalCodingSandbox
from app.agent_loop_lib.sandbox.coding.validation import validate_package_spec
from app.agent_loop_lib.sandbox.manager import (
    SandboxLimits,
    SandboxManager,
    SandboxType,
    UnknownSandboxError,
)


def _empty_tar() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w"):
        pass
    buf.seek(0)
    return buf.read()


class TestEnvironmentSanitisation:

    def test_local_env_no_secret_leak(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SECRET_KEY", "super_secret")
        env = sanitized_subprocess_env(str(tmp_path))
        assert "SECRET_KEY" not in env


_SECRET = "sk-do-not-leak-abcdef123456"


class TestSecretsNeverEscapeTheBackend:
    """Asserting on a hand-built object that never held a secret proves
    nothing. These configure a real backend WITH a credential and then check
    everything that leaves it."""

    def _e2b_backend(self):
        from app.agent_loop_lib.sandbox.coding.e2b import E2BCodingSandbox

        sandbox = E2BCodingSandbox(
            api_key=_SECRET,
            context=SandboxContext(org_id="org-1", conversation_id="conv-1"),
        )
        # `ref` needs an id, which E2B only has after provision.
        sandbox._sandbox_id = "e2b-sandbox-123"
        return sandbox

    def test_ref_of_a_credentialed_backend_carries_no_key(self):
        assert _SECRET not in self._e2b_backend().ref.model_dump_json()

    def test_provider_tags_carry_no_key(self):
        metadata = self._e2b_backend()._provider_metadata()
        assert _SECRET not in str(metadata)
        assert metadata["org_id"] == "org-1"

    def test_factory_config_repr_masks_the_key(self):
        from app.agent_loop_lib.sandbox.coding.factories.e2b import (
            E2BCodingSandboxFactory,
        )

        cfg = E2BCodingSandboxFactory.config_model(api_key=_SECRET)
        assert _SECRET not in repr(cfg)
        assert _SECRET not in cfg.model_dump_json()
        assert _SECRET not in str(cfg.model_dump())

    def test_key_is_not_logged_during_provision(self, caplog):
        """A key that reaches the log file has leaked to anyone with log
        access, which is a far larger audience than the config."""
        import logging

        from app.agent_loop_lib.sandbox.coding.factories.e2b import (
            E2BCodingSandboxFactory,
        )

        factory = E2BCodingSandboxFactory(
            config=E2BCodingSandboxFactory.config_model(api_key=_SECRET),
        )
        with caplog.at_level(logging.DEBUG):
            backend = factory.create(SandboxContext(org_id="org-1"))
            _ = backend.capabilities
            _ = factory.capabilities()
        assert _SECRET not in caplog.text

    def test_settings_never_carry_the_key(self, monkeypatch):
        """`SandboxSettings` is dumped and logged freely and has no
        `SecretStr` to hide behind, so the key must never enter it."""
        import asyncio

        from app.agent_loop_lib.sandbox.coding.settings import (
            EnvSandboxSettingsLoader,
        )

        monkeypatch.setenv("SANDBOX_MODE", "e2b")
        monkeypatch.setenv("E2B_API_KEY", _SECRET)
        settings = asyncio.run(EnvSandboxSettingsLoader().load(SandboxContext()))
        assert _SECRET not in settings.model_dump_json()


class TestSandboxRefNoCredentials:

    def test_sandbox_ref_no_credentials(self):
        ref = SandboxRef(
            backend="local",
            sandbox_id="test-id",
            created_at=0.0,
            metadata={"region": "us"},
        )
        dump = ref.model_dump_json()
        assert "api_key" not in dump
        assert "secret" not in dump.lower()


class TestPackageSpecInjection:

    @pytest.mark.parametrize(
        "spec,language",
        [
            ('"; rm -rf /"', "typescript"),
            ("git+https://evil.com/repo", "typescript"),
            ("-flag", "typescript"),
            ("../escape", "typescript"),
            ('"; rm -rf /"', "python"),
            ("git+https://evil.com/repo", "python"),
            ("-flag", "python"),
            ("../escape", "python"),
        ],
    )
    def test_package_spec_injection_rejected(self, spec: str, language: str):
        assert validate_package_spec(spec, language) is False


class TestPathTraversal:

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "malicious_path",
        [
            "../escape.txt",
            "/etc/passwd",
            "output/../../escape.txt",
        ],
    )
    async def test_path_traversal_rejected_on_local(self, tmp_path, malicious_path: str):
        """Local only. Traversal is asserted against EVERY backend by the
        contract suite (`contract/test_backend_contract.py`); the old name
        here claimed a coverage this test never had."""
        sb = LocalCodingSandbox(working_dir=str(tmp_path / "traversal_test"))
        await sb.provision()
        try:
            with pytest.raises(ValueError):
                await sb.upload_file(malicious_path, b"bad")
        finally:
            await sb.destroy()


class TestSandboxContextNoSecrets:

    def test_sandbox_context_never_contains_secrets(self):
        field_names = set(SandboxContext.model_fields.keys())
        secret_keywords = {"api_key", "secret", "token", "password", "credential"}
        leaked = field_names & secret_keywords
        assert leaked == set(), f"SandboxContext exposes secret-like fields: {leaked}"


class TestDockerContainerIsolation:
    """Asserted on the kwargs handed to the daemon, because that is what
    actually decides the container's isolation — the config field only
    records an intent."""

    def _run_kwargs(self, tmp_path, *, allow_network: bool, request_network: bool):
        import asyncio

        from app.agent_loop_lib.sandbox.coding.base import CodeRequest
        from app.agent_loop_lib.sandbox.coding.docker import DockerCodingSandbox

        captured: list[dict] = []
        client = MagicMock()

        def _create(**kwargs):
            captured.append(kwargs)
            container = MagicMock()
            container.wait.return_value = {"StatusCode": 0}
            container.logs.side_effect = lambda **kw: b""
            container.get_archive.side_effect = lambda path: (iter([_empty_tar()]), {})
            return container

        client.containers.create.side_effect = _create

        provider = MagicMock()
        provider.client = client
        provider.ensure_image = AsyncMock(return_value=True)
        provider.ensure_egress_network = AsyncMock(return_value="egress")

        async def _run_blocking(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        provider.run_blocking = _run_blocking

        sandbox = DockerCodingSandbox(
            working_dir=str(tmp_path / "iso"),
            allow_network=allow_network,
            provider=provider,
        )

        async def _go():
            await sandbox.provision()
            await sandbox.execute(CodeRequest(
                code="print(1)", language="python", allow_network=request_network,
            ))
            await sandbox.destroy()

        asyncio.run(_go())
        assert captured, "no container was created"
        return captured[0]

    def test_offline_run_gets_no_network(self, tmp_path):
        kwargs = self._run_kwargs(tmp_path, allow_network=False, request_network=False)
        assert kwargs.get("network_mode") == "none"
        assert kwargs.get("network_disabled") is True

    def test_request_cannot_widen_what_the_backend_allows(self, tmp_path):
        """`allow_network` on a request is a narrowing hint, never a grant —
        otherwise generated code could opt itself back onto the network an
        operator disabled."""
        kwargs = self._run_kwargs(tmp_path, allow_network=False, request_network=True)
        assert kwargs.get("network_mode") == "none"

    def test_no_host_filesystem_is_mounted(self, tmp_path):
        """A bind mount would hand generated code the host filesystem; files
        move in and out by tar archive instead."""
        kwargs = self._run_kwargs(tmp_path, allow_network=False, request_network=False)
        assert not kwargs.get("volumes")
        assert not kwargs.get("binds")
        assert not kwargs.get("mounts")

    def test_resource_limits_are_applied(self, tmp_path):
        kwargs = self._run_kwargs(tmp_path, allow_network=False, request_network=False)
        assert kwargs.get("mem_limit")
        assert kwargs.get("nano_cpus")


class TestCrossRequestIsolation:

    @pytest.mark.asyncio
    async def test_cross_request_sandbox_isolation(self, tmp_path):

        class _FakeBackend:
            def __init__(self):
                self.sandbox_id = f"fake-{id(self)}"

            async def provision(self):
                return None

            async def destroy(self):
                pass

        m1 = SandboxManager()
        m1.register_backend_factory(
            SandboxType.CODING, _FakeBackend, limits=SandboxLimits()
        )
        m2 = SandboxManager()
        m2.register_backend_factory(
            SandboxType.CODING, _FakeBackend, limits=SandboxLimits()
        )

        sid1, _ = await m1.get_or_create(SandboxType.CODING)
        with pytest.raises(UnknownSandboxError):
            m2.get(SandboxType.CODING, sid1)
