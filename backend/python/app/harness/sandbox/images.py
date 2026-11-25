"""The sandbox images a deployment names, read the same way by the broker (its allowlist) and its callers."""

from __future__ import annotations

import os

SANDBOX_IMAGE_ENV = "SANDBOX_DOCKER_IMAGE"
MCP_RUNNER_IMAGE_ENV = "MCP_RUNNER_IMAGE"
DEFAULT_SANDBOX_IMAGE = "pipeshub/sandbox:latest"


def sandbox_image() -> str:
    return (os.getenv(SANDBOX_IMAGE_ENV) or "").strip() or DEFAULT_SANDBOX_IMAGE


def mcp_runner_image() -> str:
    """STDIO MCP servers' runner image (P5.10); the sandbox image unless the operator names one."""
    return (os.getenv(MCP_RUNNER_IMAGE_ENV) or "").strip() or sandbox_image()


__all__ = ["DEFAULT_SANDBOX_IMAGE", "MCP_RUNNER_IMAGE_ENV", "SANDBOX_IMAGE_ENV", "mcp_runner_image", "sandbox_image"]
