from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from app.agent_loop_lib.core.exceptions import AgentLoopError
from app.agent_loop_lib.sandbox.base import SandboxInfo

"""Coding sandbox (local-coding-sandbox feature): a `SandboxProvider`-adjacent
but deliberately SEPARATE interface for multi-language code generation with
package management (TypeScript-first, Python where a library makes it the
better choice).

Kept as its own ABC rather than an extension of `sandbox/base.py`'s
`SandboxProvider` — Interface Segregation: `SandboxProvider.run(code,
language)` is a bare "run this code" contract that `LocalSandbox`/
`run_shell` already implement and depend on; folding environment
management (npm/venv install, package tracking, artifact detection) into
it would force every existing consumer to grow methods they don't need.

Intended implementations (see docs/roadmap for the sandbox taxonomy):
    - LocalCodingSandbox — subprocess + npm/venv, for local development
    - E2BCodingSandbox — https://e2b.dev (cloud micro-VMs)
    - DaytonaCodingSandbox — https://daytona.io (dev containers)
    - AIOCodingSandbox — any OCI-compatible all-in-one sandbox API
"""

__all__ = [
    "CodeRequest",
    "CodeResult",
    "InstallResult",
    "ErrorCategory",
    "ErrorAnalysis",
    "CodingSandboxBackend",
    "CodingLanguage",
    "CodingSandboxError",
    "EnvironmentSetupError",
]

CodingLanguage = Literal["typescript", "python"]


class CodingSandboxError(AgentLoopError):
    """Base for coding-sandbox infrastructure failures (as opposed to
    code-level failures, which are represented as data on `CodeResult`/
    `InstallResult` — see their docstrings)."""


class EnvironmentSetupError(CodingSandboxError):
    """Raised when foundational environment setup (npm init, venv creation)
    fails — distinct from a normal package install failure, which is
    reported as `InstallResult(success=False, ...)` instead of raised."""


class ErrorCategory(str, Enum):
    """Coarse classification of a failed run, used to decide whether the
    agent should retry and what to fix — see `ReflectionEngine`."""

    SYNTAX = "syntax"
    TYPE = "type"
    RUNTIME = "runtime"
    IMPORT = "import"
    TIMEOUT = "timeout"
    PERMISSION = "permission"
    UNKNOWN = "unknown"


class ErrorAnalysis(BaseModel):
    """Structured, retry-friendly summary of a failed `execute()` — this is
    what makes reflection/self-correction possible: the agent gets a
    category + actionable suggestion instead of a raw stack trace to
    re-parse itself."""

    category: ErrorCategory
    message: str
    file: str | None = None
    line: int | None = None
    column: int | None = None
    suggestion: str | None = None
    stack_trace: str | None = None
    is_retryable: bool = True


class CodeRequest(BaseModel):
    """One `execute()` invocation. `packages`, when given, are ensured
    installed (idempotently) before the code runs — the auto-ensure path,
    distinct from the explicit `install_packages` tool/method."""

    code: str
    language: CodingLanguage = "typescript"
    timeout: float = 30.0
    packages: list[str] = Field(default_factory=list)
    allow_network: bool = False
    entry_file: str | None = None


class CodeResult(BaseModel):
    """Uniform result envelope for a code run.

    Error-propagation contract (see `CodingSandboxBackend`): a failed run
    (nonzero exit, exception, timeout) is represented HERE — `exit_code`
    non-zero and `error_analysis` populated — not as a raised exception.
    Callers (the `run_code` tool) surface this as `ToolResult(success=True,
    data=...)` so the model sees the failure as data it can reflect on and
    retry, matching the existing `db_sandbox` soft-error pattern. Only
    infrastructure failures (missing runtime, unknown sandbox, sandbox
    destroyed mid-call) should raise.
    """

    stdout: str
    stderr: str
    exit_code: int
    language: str
    duration_ms: float
    error_analysis: ErrorAnalysis | None = None
    artifacts: list[str] = Field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.exit_code == 0


class InstallResult(BaseModel):
    success: bool
    installed: list[str] = Field(default_factory=list)
    stdout: str = ""
    stderr: str = ""


class CodingSandboxBackend(ABC):
    """One sandbox instance = one isolated working directory that can host
    BOTH a Node environment (`node_modules/`) and a Python virtualenv
    (`.venv/`), created lazily on first use per language. Installed
    packages are tracked per language so re-requesting an already-installed
    package is a cheap no-op (see `CodeRequest.packages` auto-ensure).

    State contract: only the FILESYSTEM persists between `execute()` calls
    on the same instance — interpreter state (variables, imports, open
    handles) does NOT persist. This is deliberate: it's what `LocalCodingSandbox`
    can actually guarantee, and backends that offer richer semantics (e.g.
    E2B's stateful Jupyter-style contexts) must not expose them through
    this interface, so swapping backends never silently changes behavior
    (Liskov substitution).

    Usage (context manager, matching `SandboxProvider`):
        async with LocalCodingSandbox(cfg) as sb:
            result = await sb.execute(CodeRequest(code="console.log(1+1)"))
    """

    @property
    @abstractmethod
    def sandbox_id(self) -> str:
        """Stable identifier for this sandbox instance, used by
        `SandboxManager` for `(SandboxType, sandbox_id) -> backend` tracking
        and returned to callers so they can reuse the same sandbox across
        calls. For local backends this is a locally-generated UUID; for
        remote backends (E2B, Daytona) it MUST be the provider's own
        server-assigned id so `SandboxManager` never desyncs from the
        remote's notion of identity (reconnect/billing/inspection all key
        off of it). Formalized on the ABC (rather than left to duck typing)
        so every backend is required to expose it — `SandboxManager` no
        longer needs to fall back to inventing an id for backends that
        forget to set one.

        Contract: must be readable before `provision()` is called for
        locally-generated ids (see `LocalCodingSandbox`, which generates it
        in `__init__`), but backends that only receive a server-assigned id
        from `provision()` (e.g. `E2BCodingSandbox`) may raise until
        `provision()` has run — `SandboxManager.get_or_create()` always
        reads this only AFTER awaiting `provision()`, so both styles work.
        """
        ...

    @abstractmethod
    async def provision(self) -> SandboxInfo:
        """Create the sandbox's working directory. Must be called (or used
        via the async context manager) before `execute()`."""
        ...

    @abstractmethod
    async def execute(self, request: CodeRequest) -> CodeResult:
        """Run `request.code` and return its result. Never raises for
        code-level failures — see `CodeResult`'s error-propagation contract
        docstring. May raise for infrastructure failures (runtime missing,
        sandbox already destroyed)."""
        ...

    @abstractmethod
    async def install_packages(self, packages: list[str], language: CodingLanguage) -> InstallResult:
        """Ensure `packages` are installed for `language`. Idempotent —
        already-installed packages are skipped."""
        ...

    @abstractmethod
    async def upload_file(self, path: str, content: bytes) -> None:
        """Write a file into the sandbox's working directory. `path` is
        relative to the sandbox dir; implementations must reject any path
        that escapes it (traversal)."""
        ...

    @abstractmethod
    async def download_file(self, path: str) -> bytes:
        """Read a file from the sandbox's working directory. Same
        traversal restriction as `upload_file`."""
        ...

    @abstractmethod
    async def list_files(self) -> list[str]:
        """List file paths (relative to the sandbox dir) currently present."""
        ...

    @abstractmethod
    async def destroy(self) -> None:
        """Tear down the sandbox and release all resources (temp dir,
        subprocess handles, remote billing for cloud backends)."""
        ...

    async def __aenter__(self) -> "CodingSandboxBackend":
        await self.provision()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.destroy()
