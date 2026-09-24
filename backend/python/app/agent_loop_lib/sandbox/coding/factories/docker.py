"""``DockerCodingSandboxFactory``: factory for the Docker container
``DockerCodingSandbox`` backend — two-phase container execution."""

from __future__ import annotations

import uuid
import os

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

__all__ = ["DockerCodingSandboxFactory"]


class DockerFactoryConfig(BaseModel):
    """Factory-owned config for the Docker backend. See
    ``LocalFactoryConfig`` for why extras are forbidden."""

    model_config = ConfigDict(extra="forbid")

    image: str = "agent-loop-sandbox:latest"
    memory_limit_mb: int = 512
    cpu_limit: float = 0.5
    egress_network: str = "sandbox_egress"
    pip_index_url: str = "https://pypi.org/simple"
    npm_registry: str = "https://registry.npmjs.org"
    working_dir_root: str | None = None
    image_node_modules: str | None = None
    allow_network: bool = False


class DockerCodingSandboxFactory(SandboxBackendFactory):
    backend_name = "docker"
    config_model = DockerFactoryConfig

    def is_installed(self) -> bool:
        try:
            import docker  # noqa: F401
            return True
        except ImportError:
            return False

    async def health_check(self) -> BackendHealth:
        try:
            from app.agent_loop_lib.sandbox.coding.docker_client import (
                get_default_provider,
            )
            provider = get_default_provider()
            reachable = await provider.ping()
            if not reachable:
                return BackendHealth(available=False, reason="docker daemon unreachable")
            cfg: DockerFactoryConfig = self._config  # type: ignore[assignment]
            image_present = await provider.ensure_image(cfg.image)
            if not image_present:
                return BackendHealth(
                    available=True,
                    reason=f"image {cfg.image!r} not found locally (will pull on first use)",
                )
            return BackendHealth(available=True)
        except Exception as exc:
            return BackendHealth(available=False, reason=f"{type(exc).__name__}: {exc}")

    async def warmup(self) -> None:
        from app.agent_loop_lib.sandbox.coding.docker_client import (
            get_default_provider,
        )
        provider = get_default_provider()
        cfg: DockerFactoryConfig = self._config  # type: ignore[assignment]
        present = await provider.ensure_image(cfg.image)
        if not present:
            await provider.pull_image(cfg.image)
        await provider.ensure_egress_network(cfg.egress_network)

    def create(self, ctx: SandboxContext) -> CodingSandboxBackend:
        from app.agent_loop_lib.sandbox.coding.docker import DockerCodingSandbox

        cfg: DockerFactoryConfig = self._config  # type: ignore[assignment]
        shared = self._shared

        root = cfg.working_dir_root
        working_dir = (
            os.path.join(root, f"alcs-docker-{uuid.uuid4().hex[:10]}")
            if root is not None else None
        )

        allowlist = getattr(shared, "package_allowlist", None)
        denylist = getattr(shared, "package_denylist", None) or []

        return DockerCodingSandbox(
            image=cfg.image,
            working_dir=working_dir,
            memory_limit_mb=cfg.memory_limit_mb,
            cpu_limit=cfg.cpu_limit,
            egress_network=cfg.egress_network,
            pip_index_url=cfg.pip_index_url,
            npm_registry=cfg.npm_registry,
            package_allowlist=allowlist,
            package_denylist=denylist,
            image_node_modules=cfg.image_node_modules,
            allow_network=cfg.allow_network,
            context=ctx,
        )

    def capabilities(self) -> SandboxCapabilities:
        from app.agent_loop_lib.sandbox.coding.docker import DockerCodingSandbox

        cfg: DockerFactoryConfig = self._config  # type: ignore[assignment]
        return DockerCodingSandbox.describe_capabilities(
            allow_network=cfg.allow_network,
        )
