"""``LocalCodingSandboxFactory``: factory for the local/dev
``LocalCodingSandbox`` backend — subprocess + npm/venv on the host."""

from __future__ import annotations

import os
import shutil
import uuid

from pydantic import BaseModel, ConfigDict

from app.agent_loop_lib.sandbox.coding.base import (
    CodingSandboxBackend,
    SandboxCapabilities,
    SandboxContext,
)
from app.agent_loop_lib.sandbox.coding.registry import (
    BackendHealth,
    SandboxBackendFactory,
)

__all__ = ["LocalCodingSandboxFactory"]


class LocalFactoryConfig(BaseModel):
    """Factory-owned config for the local backend.

    ``extra="forbid"``: these keys are projected from
    ``CodingSandboxConfig.effective_backend_options()``, and pydantic's
    default would silently drop a key that drifted out of sync — which is
    exactly the failure this config plumbing exists to prevent.
    """

    model_config = ConfigDict(extra="forbid")

    working_dir_root: str | None = None
    typecheck_typescript: bool = True
    max_memory_bytes: int = 1536 * 1024 * 1024
    max_cpu_seconds: int = 30
    max_file_size_bytes: int = 50 * 1024 * 1024
    max_processes: int = 2048


class LocalCodingSandboxFactory(SandboxBackendFactory):
    backend_name = "local"
    config_model = LocalFactoryConfig

    def is_installed(self) -> bool:
        return True

    async def health_check(self) -> BackendHealth:
        node_ok = shutil.which("node") is not None
        python_ok = shutil.which("python3") is not None
        if not node_ok and not python_ok:
            return BackendHealth(available=False, reason="neither node nor python3 found on PATH")
        return BackendHealth(available=True)

    def create(self, ctx: SandboxContext) -> CodingSandboxBackend:
        from app.agent_loop_lib.sandbox.coding.executor import ExecutionLimits
        from app.agent_loop_lib.sandbox.coding.local import LocalCodingSandbox

        cfg: LocalFactoryConfig = self._config  # type: ignore[assignment]
        shared = self._shared

        root = cfg.working_dir_root
        working_dir = (
            os.path.join(root, f"alcs-{uuid.uuid4().hex[:10]}")
            if root is not None else None
        )

        allowlist = getattr(shared, "package_allowlist", None)
        denylist = getattr(shared, "package_denylist", None) or []
        allow_network_on_install = getattr(shared, "allow_network_on_install", True)

        return LocalCodingSandbox(
            working_dir=working_dir,
            allow_network_on_install=allow_network_on_install,
            typecheck_typescript=cfg.typecheck_typescript,
            package_allowlist=allowlist,
            package_denylist=denylist,
            limits=ExecutionLimits(
                max_memory_bytes=cfg.max_memory_bytes,
                max_cpu_seconds=cfg.max_cpu_seconds,
                max_file_size_bytes=cfg.max_file_size_bytes,
                max_processes=cfg.max_processes,
            ),
            context=ctx,
        )

    def capabilities(self) -> SandboxCapabilities:
        from app.agent_loop_lib.sandbox.coding.local import LocalCodingSandbox

        return LocalCodingSandbox.describe_capabilities()
