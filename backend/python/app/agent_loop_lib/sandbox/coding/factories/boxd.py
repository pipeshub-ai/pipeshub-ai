"""``BoxdCodingSandboxFactory``: factory for the ``BoxdCodingSandbox``
backend, one isolated boxd machine per sandbox, billed while it runs."""

from __future__ import annotations

import logging
import os
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

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

__all__ = ["BoxdCodingSandboxFactory", "BoxdFactoryConfig"]

logger = logging.getLogger(__name__)

# Once per process: the factory is built for every chat.
_warned_no_snapshot = False


def _warn_no_snapshot() -> None:
    """`run_code` tells the model which packages are installed; without a
    snapshot a new machine has none of them."""
    global _warned_no_snapshot
    if not _warned_no_snapshot:
        _warned_no_snapshot = True
        logger.warning("boxd_sandbox.no_snapshot")

# vCPU and memory of the machine sizes boxd offers.
_SIZES: dict[str, tuple[int, str]] = {"1x4": (1, "4G"), "2x8": (2, "8G"), "4x16": (4, "16G"), "8x32": (8, "32G")}


class BoxdFactoryConfig(BaseModel):
    """Factory-owned config for the boxd backend. ``api_key`` is
    ``SecretStr`` so it never appears in ``SandboxInfo.metadata``,
    ``SandboxRef``, tool output, or logs. ``org`` and ``region`` fall back to
    ``BOXD_ORG`` and ``BOXD_REGION``, ``snapshot`` to ``BOXD_SNAPSHOT``;
    ``size`` unset is the organisation's default machine size."""

    model_config = ConfigDict(extra="forbid")

    api_key: SecretStr | None = None
    org: str | None = None
    region: str | None = None
    size: Literal["1x4", "2x8", "4x16", "8x32"] | None = None
    snapshot: str | None = None
    boxd_timeout: int = Field(default=1800, ge=60)
    allow_network: bool = False
    name_prefix: str = Field(default="pipeshub-sandbox", pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    # Added to every machine, for cost attribution in boxd. The sandbox's own
    # labels (app, org-id, user-id, ...) win on a clash.
    labels: dict[str, str] = Field(default_factory=dict)


class BoxdCodingSandboxFactory(SandboxBackendFactory):
    backend_name = "boxd"
    config_model = BoxdFactoryConfig

    def is_installed(self) -> bool:
        try:
            import boxd  # noqa: F401
            return True
        except ImportError:
            return False

    async def health_check(self) -> BackendHealth:
        if not self.is_installed():
            return BackendHealth(available=False, reason="boxd SDK not installed (pip install boxd)")
        if not self._resolved_api_key():
            return BackendHealth(available=False, reason="BOXD_API_KEY missing")
        return BackendHealth(available=True)

    def create(self, ctx: SandboxContext) -> CodingSandboxBackend:
        from app.agent_loop_lib.sandbox.coding.boxd import BoxdCodingSandbox

        kwargs = self._sandbox_kwargs()
        if kwargs["snapshot"] is None:
            _warn_no_snapshot()
        return BoxdCodingSandbox(context=ctx, **kwargs)

    async def reconnect(self, ref: SandboxRef) -> CodingSandboxBackend:
        from app.agent_loop_lib.sandbox.coding.boxd import BoxdCodingSandbox

        sandbox = BoxdCodingSandbox.attach(ref.sandbox_id, created_at=ref.created_at, **self._sandbox_kwargs())
        # Fails here, with the machine named as gone, rather than at the
        # first run.
        await sandbox.ensure_alive()
        return sandbox

    def capabilities(self) -> SandboxCapabilities:
        from app.agent_loop_lib.sandbox.coding.boxd import BoxdCodingSandbox

        cfg: BoxdFactoryConfig = self._config  # type: ignore[assignment]
        return BoxdCodingSandbox.describe_capabilities(
            allow_network=cfg.allow_network,
            lifetime_s=self._effective_ttl_s(),
        )

    def _sandbox_kwargs(self) -> dict[str, Any]:
        cfg: BoxdFactoryConfig = self._config  # type: ignore[assignment]
        vcpu, memory = _SIZES[cfg.size] if cfg.size else (None, None)
        return {
            "api_key": self._resolved_api_key(),
            "org": cfg.org or os.environ.get("BOXD_ORG") or None,
            "region": cfg.region or os.environ.get("BOXD_REGION") or None,
            "vcpu": vcpu,
            "memory": memory,
            "snapshot": cfg.snapshot or os.environ.get("BOXD_SNAPSHOT") or None,
            "lifetime_s": self._effective_ttl_s(),
            "allow_network": cfg.allow_network,
            "name_prefix": cfg.name_prefix,
            "labels": dict(cfg.labels),
            "package_allowlist": getattr(self._shared, "package_allowlist", None),
            "package_denylist": getattr(self._shared, "package_denylist", None) or [],
        }

    def _resolved_api_key(self) -> str | None:
        """Config first, then the env var. Kept out of `SandboxSettings` for
        the reason `E2BCodingSandboxFactory._resolved_api_key` gives."""
        cfg: BoxdFactoryConfig = self._config  # type: ignore[assignment]
        if cfg.api_key is not None:
            return cfg.api_key.get_secret_value()
        return os.environ.get("BOXD_API_KEY") or None

    def _effective_ttl_s(self) -> int:
        """The machine's provider-side lifetime, never longer than the
        manager's own cap, so a process that died before teardown cannot
        leave a machine running longer than a sandbox may live."""
        cfg: BoxdFactoryConfig = self._config  # type: ignore[assignment]
        max_lifetime = getattr(self._shared, "max_lifetime_s", None)
        if max_lifetime is None:
            return cfg.boxd_timeout
        return max(60, int(min(cfg.boxd_timeout, max_lifetime)))
