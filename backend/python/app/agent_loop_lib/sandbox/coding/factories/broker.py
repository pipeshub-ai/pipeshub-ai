"""``BrokerCodingSandboxFactory``: ``run_code`` through the sandbox broker process (P5.1).

Takes the Docker backend's options (image, limits, registries) and adds the
broker runtime mode. This process needs no Docker socket; it reaches the
broker over its HTTP API with the worker credential.
"""

from __future__ import annotations

import os
import uuid
from typing import Literal

from app.agent_loop_lib.sandbox.coding.base import (
    CodingSandboxBackend,
    SandboxCapabilities,
    SandboxContext,
)
from app.agent_loop_lib.sandbox.coding.factories.docker import DockerFactoryConfig
from app.agent_loop_lib.sandbox.coding.registry import (
    BackendHealth,
    SandboxBackendFactory,
)

__all__ = ["BrokerCodingSandboxFactory"]


class BrokerFactoryConfig(DockerFactoryConfig):
    runtime_mode: Literal["docker", "gvisor"] = "docker"


class BrokerCodingSandboxFactory(SandboxBackendFactory):
    backend_name = "broker"
    config_model = BrokerFactoryConfig

    def is_installed(self) -> bool:
        try:
            import aiohttp  # noqa: F401
            return True
        except ImportError:
            return False

    async def health_check(self) -> BackendHealth:
        # Opening the client reads configuration and calls the broker; that
        # happens on first use, so a health sweep stays local and cheap.
        return BackendHealth(available=True, reason="broker client opens on first use")

    def create(self, ctx: SandboxContext) -> CodingSandboxBackend:
        from app.agent_loop_lib.sandbox.coding.broker import BrokerCodingSandbox
        from app.harness.sandbox.client import process_broker_client
        from harness_core.sandbox.types import RuntimeMode

        cfg: BrokerFactoryConfig = self._config  # type: ignore[assignment]
        shared = self._shared
        root = cfg.working_dir_root
        working_dir = os.path.join(root, f"alcs-broker-{uuid.uuid4().hex[:10]}") if root is not None else None
        return BrokerCodingSandbox(
            broker=process_broker_client.get,
            runtime_mode=RuntimeMode(cfg.runtime_mode),
            image=cfg.image,
            working_dir=working_dir,
            memory_limit_mb=cfg.memory_limit_mb,
            cpu_limit=cfg.cpu_limit,
            pids_limit=cfg.pids_limit,
            pip_index_url=cfg.pip_index_url,
            npm_registry=cfg.npm_registry,
            package_allowlist=getattr(shared, "package_allowlist", None),
            package_denylist=getattr(shared, "package_denylist", None) or [],
            image_node_modules=cfg.image_node_modules,
            allow_network=cfg.allow_network,
            context=ctx,
        )

    def capabilities(self) -> SandboxCapabilities:
        from app.agent_loop_lib.sandbox.coding.broker import BrokerCodingSandbox

        cfg: BrokerFactoryConfig = self._config  # type: ignore[assignment]
        return BrokerCodingSandbox.describe_capabilities(allow_network=cfg.allow_network)
