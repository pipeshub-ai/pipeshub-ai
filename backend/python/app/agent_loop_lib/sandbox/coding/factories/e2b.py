"""``E2BCodingSandboxFactory``: factory for the E2B cloud micro-VM
``E2BCodingSandbox`` backend — billed per sandbox-second."""

from __future__ import annotations

import os

from pydantic import BaseModel, ConfigDict, SecretStr

from app.agent_loop_lib.sandbox.coding.base import (
    CodingSandboxBackend,
    SandboxCapabilities,
    SandboxContext,
    SandboxRef,
)
from app.agent_loop_lib.sandbox.coding.registry import (
    BackendHealth,
    SandboxBackendFactory,
)

__all__ = ["E2BCodingSandboxFactory"]


class E2BFactoryConfig(BaseModel):
    """Factory-owned config for the E2B backend.  ``api_key`` is
    ``SecretStr`` so it never appears in ``SandboxInfo.metadata``,
    ``SandboxRef``, tool output, or logs."""

    model_config = ConfigDict(extra="forbid")

    api_key: SecretStr | None = None
    template: str = "base"
    e2b_timeout: int = 300
    allow_internet_access: bool = True


class E2BCodingSandboxFactory(SandboxBackendFactory):
    backend_name = "e2b"
    config_model = E2BFactoryConfig

    def is_installed(self) -> bool:
        try:
            import e2b_code_interpreter  # noqa: F401
            return True
        except ImportError:
            return False

    async def health_check(self) -> BackendHealth:
        if not self.is_installed():
            return BackendHealth(
                available=False,
                reason="e2b_code_interpreter not installed (pip install agent-loop[e2b])",
            )
        if not self._resolved_api_key():
            return BackendHealth(available=False, reason="E2B_API_KEY missing")
        return BackendHealth(available=True)

    def create(self, ctx: SandboxContext) -> CodingSandboxBackend:
        from app.agent_loop_lib.sandbox.coding.e2b import E2BCodingSandbox

        cfg: E2BFactoryConfig = self._config  # type: ignore[assignment]
        shared = self._shared

        allowlist = getattr(shared, "package_allowlist", None)
        denylist = getattr(shared, "package_denylist", None) or []

        return E2BCodingSandbox(
            api_key=self._resolved_api_key(),
            template=cfg.template,
            e2b_timeout=self._effective_ttl_s(),
            allow_internet_access=cfg.allow_internet_access,
            package_allowlist=allowlist,
            package_denylist=denylist,
            context=ctx,
        )

    async def reconnect(self, ref: SandboxRef) -> CodingSandboxBackend:
        from e2b_code_interpreter import AsyncSandbox

        from app.agent_loop_lib.sandbox.coding.e2b import E2BCodingSandbox

        cfg: E2BFactoryConfig = self._config  # type: ignore[assignment]
        sbx = await AsyncSandbox.connect(
            ref.sandbox_id, api_key=self._resolved_api_key(),
        )
        return E2BCodingSandbox.attach(
            sbx,
            sandbox_id=ref.sandbox_id,
            api_key=self._resolved_api_key(),
            template=cfg.template,
            e2b_timeout=self._effective_ttl_s(),
            allow_internet_access=cfg.allow_internet_access,
            # The provider's clock, not ours: E2B reclaims relative to when
            # it created the VM, so the ref's timestamp is what makes
            # `expires_at` honest after a reconnect.
            created_at=ref.created_at,
        )

    def capabilities(self) -> SandboxCapabilities:
        from app.agent_loop_lib.sandbox.coding.e2b import E2BCodingSandbox

        cfg: E2BFactoryConfig = self._config  # type: ignore[assignment]
        return E2BCodingSandbox.describe_capabilities(
            allow_internet_access=cfg.allow_internet_access,
            e2b_timeout=self._effective_ttl_s(),
        )

    def _resolved_api_key(self) -> str | None:
        """Config first, then the env var.

        The key is deliberately absent from `SandboxSettings`: that model is
        logged and dumped freely, and a `SecretStr` only protects a value
        once it has been validated into this config — a raw key routed
        through `backend_options` would already have leaked by then.
        """
        cfg: E2BFactoryConfig = self._config  # type: ignore[assignment]
        if cfg.api_key is not None:
            return cfg.api_key.get_secret_value()
        return os.environ.get("E2B_API_KEY")

    def _effective_ttl_s(self) -> int:
        """Provider-side TTL, never longer than the manager's own lifetime
        cap.

        E2B bills per sandbox-second and kills the VM at its own `timeout`.
        If that timeout outlived `max_lifetime_s`, a process that died
        between provisioning and teardown would leave a VM billing with
        nothing left to reap it — so the shorter of the two wins.
        """
        cfg: E2BFactoryConfig = self._config  # type: ignore[assignment]
        max_lifetime = getattr(self._shared, "max_lifetime_s", None)
        if max_lifetime is None:
            return cfg.e2b_timeout
        return int(min(cfg.e2b_timeout, max_lifetime))
