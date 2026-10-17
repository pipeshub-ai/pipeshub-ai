from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from enum import Enum
from typing import ClassVar, Literal

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
    "OUTPUT_DIR_NAME",
    "normalize_sandbox_path",
    "IsolationLevel",
    "SandboxCapabilities",
    "SandboxContext",
    "SandboxRef",
    "ExecutionEvent",
]

CodingLanguage = Literal["typescript", "python"]

# The deliverable directory, as a path relative to the sandbox working dir.
OUTPUT_DIR_NAME = "output"

# How the same directory is spelled inside a run: `$OUTPUT_DIR` in the
# environment (`environment.py::sanitized_subprocess_env`, DockerCodingSandbox's
# container env), which resolves to `/output` in the container.
_OUTPUT_DIR_ALIASES = ("${OUTPUT_DIR}", "$OUTPUT_DIR", "/output")


def normalize_sandbox_path(path: str) -> str:
    """Rewrite the ways a model spells the deliverable directory into the
    sandbox-relative form every backend's `_resolve_path` expects.

    `run_code`'s contract tells the model to write deliverables to
    `$OUTPUT_DIR`, so it naturally reaches for that same spelling when
    naming the file to a path-taking tool. Nothing expands a tool argument
    though, so `read_sandbox_file(path="$OUTPUT_DIR/report.pdf")` looks for
    a directory literally named `$OUTPUT_DIR` and reports "No such file"
    for a file that is right there.

    Only these known aliases are rewritten: any other absolute path stays
    absolute so `_resolve_path` still rejects it as an escape rather than
    silently reinterpreting it as sandbox-relative.
    """
    cleaned = path.strip()
    for alias in _OUTPUT_DIR_ALIASES:
        if cleaned == alias:
            return OUTPUT_DIR_NAME
        if cleaned.startswith(alias + "/"):
            return f"{OUTPUT_DIR_NAME}/{cleaned[len(alias) + 1:].lstrip('/')}"
    return cleaned


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


class IsolationLevel(str, Enum):
    """How strongly a sandbox is isolated from the host.

    `HOST` is a process boundary only — rlimits and, where available,
    Seatbelt/bubblewrap. There is no network namespace, so code run at this
    level reaches whatever the host can reach and no configuration can
    prevent it. Treat `HOST` as suitable for development, not for running
    untrusted code.
    """

    HOST = "host"
    CONTAINER = "container"
    MICROVM = "microvm"


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


class SandboxCapabilities(BaseModel):
    """Declares what a sandbox backend supports, queried at runtime."""

    isolation: IsolationLevel
    supported_languages: list[CodingLanguage] = ["typescript", "python"]
    # Whether code in this sandbox can reach the network. This describes
    # what the sandbox DOES, not what it was asked for: a `HOST` sandbox
    # has the host's network and no way to take it away, so it reports
    # True regardless of configuration. Only a backend that can actually
    # enforce a denial (its own netns) may report False.
    supports_network: bool = False
    supports_package_install: bool = True
    supports_streaming: bool = False
    supports_reconnect: bool = False
    is_metered: bool = False
    max_timeout_s: float | None = None
    persistent_filesystem: bool = True


class SandboxContext(BaseModel):
    """Who/what this sandbox serves.

    Used for provider tags, audit logs and the governor's per-org fairness
    cap.  Never contains secrets; never shown to the model.
    """

    org_id: str | None = None
    user_id: str | None = None
    conversation_id: str | None = None
    request_id: str | None = None


class SandboxRef(BaseModel):
    """Serializable identity: enough to reconnect, nothing live, no credentials.

    `backend` is the REGISTRY KEY (`"e2b"`, `"daytona"`), not a class name —
    reconnecting means `registry.get(ref.backend).reconnect(ref)`, so a
    class name here would make the ref unusable for the one thing it
    exists to do.

    `created_at`/`expires_at` are wall-clock (`time.time()`) because a ref
    outlives the process that made it; a monotonic clock is meaningless
    once deserialised elsewhere.
    """

    backend: str
    sandbox_id: str
    created_at: float
    expires_at: float | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class ExecutionEvent(BaseModel):
    """A single event emitted during streamed code execution."""

    kind: Literal["stdout", "stderr", "artifact", "result"]
    text: str | None = None
    result: CodeResult | None = None


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

    # Registry key for this backend ("local", "docker", "e2b", ...). Used
    # by `ref` so a serialised handle can be routed back to the factory
    # that can reconnect it.
    backend_name: ClassVar[str] = "unknown"

    # Wall-clock provision time, set by `provision()`. See `ref`.
    _created_at: float | None = None

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

    @property
    @abstractmethod
    def capabilities(self) -> SandboxCapabilities:
        """What this backend supports — every backend must declare."""
        ...

    @property
    def ref(self) -> SandboxRef:
        """Serializable handle for this sandbox instance.

        `created_at` is captured at provision, not read here: a property
        that returns `time.time()` reports the moment it was *called*, so
        every ref would claim to be brand new and no TTL computed from it
        could ever expire.
        """
        return SandboxRef(
            backend=self.backend_name,
            sandbox_id=self.sandbox_id,
            created_at=self._created_at if self._created_at is not None else time.time(),
            expires_at=self.expires_at,
        )

    @property
    def expires_at(self) -> float | None:
        """Wall-clock time the provider will reclaim this sandbox, when it
        sets a TTL of its own. `None` for backends with no provider-side
        expiry (local, docker) — those are reclaimed by the manager."""
        return None

    async def execute_stream(self, request: CodeRequest) -> AsyncIterator[ExecutionEvent]:
        """Yield execution events incrementally.

        The default implementation calls `execute()` and emits discrete
        stdout / stderr / result events.  Backends whose
        ``capabilities.supports_streaming`` is ``True`` should override
        with a real incremental implementation.
        """
        result = await self.execute(request)
        if result.stdout:
            yield ExecutionEvent(kind="stdout", text=result.stdout)
        if result.stderr:
            yield ExecutionEvent(kind="stderr", text=result.stderr)
        yield ExecutionEvent(kind="result", result=result)

    async def __aenter__(self) -> "CodingSandboxBackend":
        await self.provision()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.destroy()
