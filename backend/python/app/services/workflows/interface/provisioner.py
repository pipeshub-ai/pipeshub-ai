"""ISandboxSessionProvisioner port (D6)."""
from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel


class StageItem(BaseModel):
    path: str      # relative path inside the sandbox workspace
    content: bytes


class SessionSpec(BaseModel):
    run_id: str
    org_id: str
    workflow_version_id: str
    source_bundle: bytes
    trigger_payload: dict[str, Any]
    sdk_version: str = "0.1.0"
    skill_resources: list[StageItem] = []
    artifacts: list[StageItem] = []
    is_dry_run: bool = False


class SandboxSession(BaseModel):
    session_id: str
    sandbox_root: str   # absolute path inside the sandbox


class ISandboxSessionProvisioner(Protocol):
    async def provision(self, spec: SessionSpec) -> SandboxSession: ...
    async def stage(self, session: SandboxSession, items: list[StageItem]) -> None: ...
    async def teardown(self, session: SandboxSession) -> None: ...
