from __future__ import annotations

import time

from app.agent_loop_lib.sandbox.base import SandboxInfo
from app.agent_loop_lib.sandbox.coding.base import (
    CodeRequest,
    CodeResult,
    CodingLanguage,
    CodingSandboxBackend,
    CodingSandboxError,
    EnvironmentSetupError,
    ErrorAnalysis,
    ErrorCategory,
    InstallResult,
)
from app.agent_loop_lib.sandbox.coding.reflection import ReflectionEngine
from app.agent_loop_lib.sandbox.coding.validation import (
    package_name,
    validate_package_spec,
)

"""`E2BCodingSandbox`: a `CodingSandboxBackend` implementation backed by
https://e2b.dev cloud micro-VMs, via the optional `e2b_code_interpreter`
dependency (`pip install agent-loop[e2b]`). Kept as a thin adapter over
`AsyncSandbox` rather than sharing `LocalCodingSandbox`'s
`EnvironmentManager`/`CodeExecutor` — those are subprocess/kernel-confinement
specific (rlimits, Seatbelt/bwrap, local paths) and have no remote
equivalent; only the *contract* (`CodingSandboxBackend`) and the package
validation rules (`validation.py`) are shared.

Deliberately uses `commands.run()` (process-per-execution), never E2B's
`run_code()` stateful Jupyter-kernel API — see `CodingSandboxBackend`'s
docstring on the "only the filesystem persists" state contract. Using
`run_code()` would let interpreter state leak across calls, silently
changing behavior when an agent swaps from `backend="local"` to
`backend="e2b"` (a Liskov substitution violation).

Every execution/install runs from `_WORKING_DIR` inside the sandbox VM —
since the whole E2B micro-VM is already an isolated, single-tenant
environment (unlike the local backend, which shares the host kernel), a
single fixed working directory is sufficient; there is no need for the
short-UUID-suffixed unique directory trick `LocalCodingSandbox` uses to
dodge Unix-socket path-length limits.
"""

__all__ = ["E2BCodingSandbox"]

_WORKING_DIR = "/home/user"
_ENTRY_FILES: dict[CodingLanguage, str] = {"typescript": "main.ts", "python": "main.py"}
_RUN_COMMANDS: dict[CodingLanguage, str] = {
    # `./node_modules/.bin/tsx` (not `npx tsx`) — npx would try to resolve
    # against its registry/cache first and can attempt a network fetch even
    # when the package is already installed locally; invoking the installed
    # binary directly guarantees no such lookup ever happens.
    "typescript": "./node_modules/.bin/tsx {file}",
    "python": "python3 {file}",
}
_LISTING_IGNORED_DIRS = {"node_modules", ".venv", "__pycache__", ".git"}


class E2BCodingSandbox(CodingSandboxBackend):
    def __init__(
        self,
        *,
        api_key: str | None = None,
        template: str = "base",
        e2b_timeout: int = 300,
        allow_internet_access: bool = True,
        package_allowlist: list[str] | None = None,
        package_denylist: list[str] | None = None,
    ) -> None:
        self._api_key = api_key
        self._template = template
        self._e2b_timeout = e2b_timeout
        self._allow_internet_access = allow_internet_access
        self._allowlist = set(package_allowlist) if package_allowlist else None
        self._denylist = set(package_denylist or [])
        self._installed: dict[str, set[str]] = {"typescript": set(), "python": set()}
        self._node_initialized = False
        self._reflection = ReflectionEngine()
        self._sbx = None
        self._sandbox_id: str | None = None

    @property
    def sandbox_id(self) -> str:
        # Server-assigned — unlike LocalCodingSandbox (which generates its
        # id locally in __init__), this is only known after provision().
        if self._sandbox_id is None:
            raise CodingSandboxError("E2BCodingSandbox has not been provisioned yet — call provision() first.")
        return self._sandbox_id

    async def provision(self) -> SandboxInfo:
        if self._sbx is not None:
            return SandboxInfo(
                sandbox_id=self.sandbox_id, status="ready",
                metadata={"backend": "e2b", "template": self._template},
            )
        try:
            from e2b_code_interpreter import AsyncSandbox
        except ImportError as e:
            raise CodingSandboxError(
                "e2b_code_interpreter is not installed — install it with `pip install agent-loop[e2b]` "
                "to use the E2B coding sandbox backend."
            ) from e

        self._sbx = await AsyncSandbox.create(
            template=self._template,
            timeout=self._e2b_timeout,
            allow_internet_access=self._allow_internet_access,
            api_key=self._api_key,
        )
        self._sandbox_id = self._sbx.sandbox_id
        return SandboxInfo(
            sandbox_id=self._sandbox_id,
            status="ready",
            metadata={"backend": "e2b", "template": self._template},
        )

    async def execute(self, request: CodeRequest) -> CodeResult:
        if self._sbx is None:
            await self.provision()

        if request.packages:
            install_result = await self.install_packages(request.packages, request.language)
            if not install_result.success:
                return CodeResult(
                    stdout="",
                    stderr=install_result.stderr,
                    exit_code=1,
                    language=request.language,
                    duration_ms=0.0,
                    error_analysis=ErrorAnalysis(
                        category=ErrorCategory.IMPORT,
                        message=install_result.stderr or "package installation failed",
                        suggestion="Fix the package spec, or call install_packages directly to see the full installer output.",
                        is_retryable=True,
                    ),
                )

        if request.language == "typescript":
            await self._ensure_typescript_runtime()

        before = set(await self.list_files())

        entry_file = request.entry_file or _ENTRY_FILES[request.language]
        file_path = f"{_WORKING_DIR}/{entry_file}"
        await self._sbx.files.write(file_path, request.code)

        run_cmd = _RUN_COMMANDS[request.language].format(file=entry_file)
        start = time.monotonic()
        stdout, stderr, exit_code = await self._run_command(run_cmd, timeout=request.timeout)
        duration_ms = (time.monotonic() - start) * 1000

        after = set(await self.list_files())
        artifacts = sorted(after - before - {entry_file})

        result = CodeResult(
            stdout=stdout, stderr=stderr, exit_code=exit_code,
            language=request.language, duration_ms=duration_ms,
            artifacts=artifacts,
        )
        if not result.success:
            result = result.model_copy(update={"error_analysis": self._reflection.analyze(result)})
        return result

    async def install_packages(self, packages: list[str], language: CodingLanguage) -> InstallResult:
        """Same validation/allowlist/denylist/idempotency semantics as
        `EnvironmentManager.install_packages` — see that docstring — but
        installing via a remote `commands.run()` instead of a local
        subprocess."""
        if self._sbx is None:
            await self.provision()
        if not packages:
            return InstallResult(success=True, installed=[])

        to_install: list[str] = []
        for spec in packages:
            name = package_name(spec, language)
            if not validate_package_spec(spec, language):
                return InstallResult(success=False, stderr=f"invalid or unsafe package spec: {spec!r}")
            if self._denylist and name in self._denylist:
                return InstallResult(success=False, stderr=f"package {name!r} is denylisted")
            if self._allowlist is not None and name not in self._allowlist:
                return InstallResult(success=False, stderr=f"package {name!r} is not in the configured allowlist")
            if name not in self._installed[language]:
                to_install.append(spec)

        if not to_install:
            return InstallResult(success=True, installed=[])

        if language == "typescript":
            await self._ensure_node_project()
            cmd = "npm install --ignore-scripts --no-audit --no-fund " + " ".join(to_install)
        else:
            cmd = "pip install --no-cache-dir " + " ".join(to_install)

        stdout, stderr, exit_code = await self._run_command(cmd, timeout=float(self._e2b_timeout))
        success = exit_code == 0
        if success:
            for spec in to_install:
                self._installed[language].add(package_name(spec, language))
        return InstallResult(
            success=success,
            installed=[package_name(s, language) for s in to_install] if success else [],
            stdout=stdout,
            stderr=stderr,
        )

    async def upload_file(self, path: str, content: bytes) -> None:
        if self._sbx is None:
            await self.provision()
        full_path = self._resolve_path(path)
        await self._sbx.files.write(full_path, content)

    async def download_file(self, path: str) -> bytes:
        if self._sbx is None:
            await self.provision()
        full_path = self._resolve_path(path)
        content = await self._sbx.files.read(full_path, format="bytes")
        return bytes(content)

    async def list_files(self) -> list[str]:
        if self._sbx is None:
            await self.provision()
        from e2b_code_interpreter import FileType

        results: list[str] = []
        entries = await self._sbx.files.list(_WORKING_DIR, depth=100)
        for entry in entries:
            rel_path = entry.path[len(_WORKING_DIR):].lstrip("/")
            if not rel_path:
                continue
            parts = rel_path.split("/")
            if any(part in _LISTING_IGNORED_DIRS for part in parts[:-1]):
                continue
            if entry.type == FileType.FILE:
                results.append(rel_path)
        return sorted(results)

    async def destroy(self) -> None:
        if self._sbx is not None:
            await self._sbx.kill()
        self._sbx = None

    async def _ensure_typescript_runtime(self) -> None:
        if self._node_initialized:
            return
        await self._ensure_node_project()
        install_result = await self.install_packages(["tsx", "typescript"], "typescript")
        if not install_result.success:
            raise EnvironmentSetupError(
                f"failed to install TypeScript runtime (tsx/typescript): {install_result.stderr}"
            )
        self._node_initialized = True

    async def _ensure_node_project(self) -> None:
        stdout, stderr, exit_code = await self._run_command(
            f"test -f {_WORKING_DIR}/package.json || npm init -y", timeout=60.0,
        )
        if exit_code != 0:
            raise EnvironmentSetupError(f"npm init failed: {stderr}")

    async def _run_command(self, cmd: str, *, timeout: float) -> tuple[str, str, int]:
        """Run `cmd` in the sandbox and normalize both the happy path
        (`CommandResult`) and E2B's non-zero-exit path (`run()` internally
        calls `AsyncCommandHandle.wait()`, which RAISES `CommandExitException`
        instead of returning a result with a non-zero `exit_code`) into one
        `(stdout, stderr, exit_code)` tuple — matching the error-propagation
        contract that only infrastructure failures raise, never code-level
        ones."""
        from e2b_code_interpreter import CommandExitException, TimeoutException

        try:
            result = await self._sbx.commands.run(cmd, cwd=_WORKING_DIR, timeout=timeout)
            return result.stdout, result.stderr, result.exit_code
        except CommandExitException as e:
            return e.stdout, e.stderr, e.exit_code
        except TimeoutException as e:
            return "", str(e) or "Execution timed out", -1

    def _resolve_path(self, path: str) -> str:
        """Resolve `path` (relative to `_WORKING_DIR`) and reject any
        traversal outside it — same boundary as `LocalCodingSandbox`."""
        import posixpath

        full_path = posixpath.normpath(posixpath.join(_WORKING_DIR, path))
        if full_path != _WORKING_DIR and not full_path.startswith(_WORKING_DIR + "/"):
            raise ValueError(f"path {path!r} escapes the sandbox working directory")
        return full_path
