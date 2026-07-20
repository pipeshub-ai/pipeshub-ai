from __future__ import annotations

import logging
import os
import shutil
import tempfile
import uuid

from app.agent_loop_lib.sandbox.base import SandboxInfo
from app.agent_loop_lib.sandbox.coding.base import (
    CodeRequest,
    CodeResult,
    CodingLanguage,
    CodingSandboxBackend,
    ErrorAnalysis,
    ErrorCategory,
    InstallResult,
)
from app.agent_loop_lib.sandbox.coding.cleanup import (
    register_sandbox_dir,
    unregister_sandbox_dir,
)
from app.agent_loop_lib.sandbox.coding.environment import EnvironmentManager
from app.agent_loop_lib.sandbox.coding.executor import CodeExecutor, ExecutionLimits
from app.agent_loop_lib.sandbox.coding.reflection import ReflectionEngine

logger = logging.getLogger(__name__)

"""`LocalCodingSandbox`: the local/dev implementation of `CodingSandboxBackend`
— composes `EnvironmentManager` (npm/venv + package installs),
`CodeExecutor` (confined subprocess execution), and `ReflectionEngine`
(error categorization) around one isolated temp working directory.

FOR LOCAL DEVELOPMENT — no remote isolation beyond this process's kernel
confinement (Seatbelt/bwrap) + rlimits. Production-grade multi-tenant
isolation should use a remote backend (E2B/Daytona/AIO) implementing the
same `CodingSandboxBackend` ABC — see that module's docstring.
"""

__all__ = ["LocalCodingSandbox"]

_LISTING_IGNORED_DIRS = {"node_modules", ".venv", "__pycache__", ".git"}


class LocalCodingSandbox(CodingSandboxBackend):
    def __init__(
        self,
        *,
        working_dir: str | None = None,
        allow_network_on_install: bool = True,
        typecheck_typescript: bool = True,
        package_allowlist: list[str] | None = None,
        package_denylist: list[str] | None = None,
        limits: ExecutionLimits | None = None,
    ) -> None:
        self._sandbox_id = str(uuid.uuid4())
        # `os.path.realpath` matters here: on macOS `tempfile.gettempdir()`
        # returns a path under the `/var` -> `/private/var` symlink, and
        # Seatbelt's `subpath` matching operates on the resolved path — an
        # unresolved working_dir would cause every confined write inside it
        # to be denied with a spurious EPERM.
        #
        # The directory name is deliberately SHORT (not the full UUID) —
        # `HOME`/`TMPDIR` are pointed at this directory (see
        # `sanitized_subprocess_env`), and tools like `tsx` create Unix
        # domain sockets under `TMPDIR` for internal IPC; `sockaddr_un` has
        # a ~104-byte path limit, and a long tempdir-plus-UUID path
        # combined with the tool's own subdirectory can exceed it, causing
        # a confusing `EINVAL` from `listen()` that looks unrelated to path
        # length. `sandbox_id` (the full UUID, used for external addressing)
        # is unaffected — only the on-disk directory name is shortened.
        short_suffix = self._sandbox_id.replace("-", "")[:10]
        self._working_dir = os.path.realpath(
            working_dir or os.path.join(tempfile.gettempdir(), f"alcs-{short_suffix}")
        )
        self._environment = EnvironmentManager(
            self._working_dir,
            allow_network_on_install=allow_network_on_install,
            package_allowlist=package_allowlist,
            package_denylist=package_denylist,
        )
        self._executor = CodeExecutor(
            self._working_dir,
            self._environment,
            typecheck_typescript=typecheck_typescript,
            limits=limits,
        )
        self._reflection = ReflectionEngine()
        self._provisioned = False

    @property
    def sandbox_id(self) -> str:
        return self._sandbox_id

    @property
    def working_dir(self) -> str:
        return self._working_dir

    async def provision(self) -> SandboxInfo:
        os.makedirs(self._working_dir, exist_ok=True)
        register_sandbox_dir(self._working_dir)
        self._provisioned = True
        return SandboxInfo(
            sandbox_id=self._sandbox_id,
            status="ready",
            metadata={"working_dir": self._working_dir, "backend": "local"},
        )

    async def execute(self, request: CodeRequest) -> CodeResult:
        if not self._provisioned:
            await self.provision()

        _all_files: list[str] = []
        for dirpath, _dirnames, filenames in os.walk(self._working_dir):
            _dirnames[:] = [d for d in _dirnames if d not in _LISTING_IGNORED_DIRS]
            for fname in filenames:
                _all_files.append(os.path.relpath(os.path.join(dirpath, fname), self._working_dir))
        logger.info(
            "LocalCodingSandbox.execute: sandbox=%s language=%s packages=%s "
            "working_dir=%s existing_files=%d (%s) code_len=%d",
            self._sandbox_id, request.language, request.packages,
            self._working_dir, len(_all_files), sorted(_all_files)[:20],
            len(request.code),
        )

        if request.packages:
            install_result = await self._environment.install_packages(request.packages, request.language)
            if not install_result.success:
                logger.warning(
                    "LocalCodingSandbox.execute: package install FAILED — "
                    "packages=%s stderr=%.500s",
                    request.packages, install_result.stderr,
                )
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

        result = await self._executor.execute(request)
        logger.info(
            "LocalCodingSandbox.execute: exit_code=%d artifacts=%s "
            "duration_ms=%.1f stderr_len=%d stderr_preview=%.500s",
            result.exit_code, result.artifacts,
            result.duration_ms, len(result.stderr) if result.stderr else 0,
            result.stderr[:500] if result.stderr else "",
        )
        if not result.success:
            result = result.model_copy(update={"error_analysis": self._reflection.analyze(result)})
        return result

    async def install_packages(self, packages: list[str], language: CodingLanguage) -> InstallResult:
        if not self._provisioned:
            await self.provision()
        return await self._environment.install_packages(packages, language)

    async def upload_file(self, path: str, content: bytes) -> None:
        if not self._provisioned:
            await self.provision()
        full_path = self._resolve_path(path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "wb") as f:
            f.write(content)
        logger.info(
            "LocalCodingSandbox.upload_file: path=%s full_path=%s size=%d sandbox=%s",
            path, full_path, len(content), self._sandbox_id,
        )

    async def download_file(self, path: str) -> bytes:
        full_path = self._resolve_path(path)
        with open(full_path, "rb") as f:
            return f.read()

    async def list_files(self) -> list[str]:
        results: list[str] = []
        for dirpath, dirnames, filenames in os.walk(self._working_dir):
            dirnames[:] = [d for d in dirnames if d not in _LISTING_IGNORED_DIRS]
            for fname in filenames:
                results.append(os.path.relpath(os.path.join(dirpath, fname), self._working_dir))
        return sorted(results)

    async def destroy(self) -> None:
        shutil.rmtree(self._working_dir, ignore_errors=True)
        unregister_sandbox_dir(self._working_dir)
        self._provisioned = False

    def _resolve_path(self, path: str) -> str:
        """Resolve `path` (relative to the sandbox working dir) and reject
        any traversal outside it — the same boundary `read_sandbox_file`
        relies on."""
        root = os.path.realpath(self._working_dir)
        full_path = os.path.realpath(os.path.join(root, path))
        if full_path != root and not full_path.startswith(root + os.sep):
            raise ValueError(f"path {path!r} escapes the sandbox working directory")
        return full_path
