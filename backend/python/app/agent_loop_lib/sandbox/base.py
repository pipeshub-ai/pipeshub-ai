from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


class ExecResult(BaseModel):
    stdout: str
    stderr: str
    exit_code: int


class SandboxInfo(BaseModel):
    """Metadata about a provisioned sandbox instance."""

    sandbox_id: str
    status: str = "ready"        # "provisioning" | "ready" | "destroyed"
    metadata: dict[str, Any] = Field(default_factory=dict)


class SandboxProvider(ABC):
    """Abstract code execution sandbox with full lifecycle management.

    Intended implementations (not local subprocess):
    - E2B        — https://e2b.dev  (cloud micro-VMs, Python/JS/bash)
    - Daytona    — https://daytona.io (dev containers, full workspace)
    - Modal      — https://modal.com (serverless GPU/CPU containers)
    - AIO Sandbox — any OCI-compatible sandbox API
    - LocalSandbox — subprocess-based, for development only

    Usage (context manager):
        async with E2BSandbox(api_key=...) as sb:
            result = await sb.run("print('hello')")
    """

    @abstractmethod
    async def provision(self) -> SandboxInfo:
        """Create and start the sandbox. Must be called before run().
        For cloud backends this allocates a remote environment."""
        ...

    @abstractmethod
    async def run(
        self,
        code: str,
        language: str = "python",
        timeout: float = 30.0,
    ) -> ExecResult: ...

    @abstractmethod
    async def upload_file(self, path: str, content: bytes) -> None:
        """Upload a file to the sandbox filesystem."""
        ...

    @abstractmethod
    async def download_file(self, path: str) -> bytes:
        """Download a file from the sandbox filesystem."""
        ...

    @abstractmethod
    async def destroy(self) -> None:
        """Tear down sandbox and release all resources (network, billing, etc.)."""
        ...

    async def __aenter__(self) -> "SandboxProvider":
        await self.provision()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.destroy()
