from __future__ import annotations

import asyncio
import logging
import posixpath
import shlex
import time
import uuid
from typing import Any

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
    IsolationLevel,
    SandboxCapabilities,
    SandboxContext,
    normalize_sandbox_path,
    validate_entry_file,
)
from app.agent_loop_lib.sandbox.coding.output_limits import cap_text
from app.agent_loop_lib.sandbox.coding.reflection import ReflectionEngine
from app.agent_loop_lib.sandbox.coding.validation import (
    canonical_package_key,
    matches_package_set,
    package_name,
    validate_package_spec,
)

"""`BoxdCodingSandbox`: a `CodingSandboxBackend` on a boxd machine
(https://boxd.sh/?ref=pipeshub-ai), a full Ubuntu VM, through the optional
`boxd` Python SDK (`pip install boxd`).

Each sandbox is one machine, created `isolated` (no private network, no
credentials inside it) and deleted by `destroy()`. Its `delete_after` timer is
the sandbox lifetime, so a machine whose process died before teardown is still
deleted. Like the E2B backend, every run is a new process (`machines.exec`),
so only the filesystem persists between calls.

With network off, the machine's egress allowlist is `none` for its whole
life, installs included: generated code runs with root in its own VM, so no
window may open the network after it has run. Packages then come from a
snapshot (`snapshot`, built by `app/agents/agent_loop/boxd_snapshot.py` with
the packages `run_code` says are installed, and `tsx`), and asking for one
that is already there succeeds without the network.
"""

__all__ = ["BoxdCodingSandbox"]

logger = logging.getLogger(__name__)

_WORKING_DIR = "/home/boxd/sandbox"
_VENV_DIR = f"{_WORKING_DIR}/.venv"
_ENTRY_FILES: dict[CodingLanguage, str] = {"typescript": "main.ts", "python": "main.py"}
_RUN_COMMANDS: dict[CodingLanguage, str] = {
    "typescript": "./node_modules/.bin/tsx {file}",
    "python": ".venv/bin/python {file}",
}
_LISTING_IGNORED_DIRS = ("node_modules", ".venv", "__pycache__", ".git")
_RUN_ENV: dict[str, str] = {"OUTPUT_DIR": f"{_WORKING_DIR}/output", "WORKSPACE_DIR": f"{_WORKING_DIR}/workspace"}
_MAKE_RUN_DIRS = 'mkdir -p "$OUTPUT_DIR" "$WORKSPACE_DIR" && '

_EGRESS_NONE = ["none"]
_EGRESS_PROBE_HOST = "pypi.org"
_EGRESS_SETTLE_S = 10.0
_EGRESS_POLL_S = 0.25

# coreutils `timeout` exits 124 when it ended the command, and 128 + 9 when
# the command ignored the first signal and was killed after `-k`.
_TIMEOUT_EXITS = (124, 137)
# The exec call outlives the in-machine `timeout`, so the remote process is
# always ended by the machine and never left running after the call returns.
_EXEC_GRACE_S = 15.0
_KILL_AFTER_S = 5
_SETUP_TIMEOUT_S = 120.0
_INSTALL_TIMEOUT_S = 600.0
_LABEL_VALUE_MAX = 63


class BoxdCodingSandbox(CodingSandboxBackend):
    backend_name = "boxd"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        org: str | None = None,
        region: str | None = None,
        vcpu: int | None = None,
        memory: str | None = None,
        snapshot: str | None = None,
        lifetime_s: int = 1800,
        allow_network: bool = False,
        name_prefix: str = "pipeshub-sandbox",
        labels: dict[str, str] | None = None,
        package_allowlist: list[str] | None = None,
        package_denylist: list[str] | None = None,
        context: SandboxContext | None = None,
        client: Any | None = None,
    ) -> None:
        self._api_key = api_key
        self._org = org
        self._region = region
        self._vcpu = vcpu
        self._memory = memory
        self._snapshot = snapshot
        self._lifetime_s = lifetime_s
        self._allow_network = allow_network
        self._name_prefix = name_prefix
        self._extra_labels = dict(labels or {})
        self._allowlist = set(package_allowlist) if package_allowlist else None
        self._denylist = set(package_denylist or [])
        self._context = context
        self._client = client
        self._owns_client = client is None
        self._installed: dict[str, set[str]] = {"typescript": set(), "python": set()}
        self._node_initialized = False
        self._python_initialized = False
        self._reflection = ReflectionEngine()
        self._machine_id: str | None = None
        self._created_at: float | None = None
        # One call at a time per sandbox: two runs would overwrite each
        # other's entry file and mix up their artifacts.
        self._lock = asyncio.Lock()

    @classmethod
    def attach(
        cls,
        machine_id: str,
        *,
        created_at: float | None = None,
        **kwargs: Any,
    ) -> "BoxdCodingSandbox":
        """Wrap an existing machine (the reconnect path).

        `created_at` comes from `SandboxRef.created_at`: the machine's
        lifetime counts from when it was created, so stamping "now" would
        report an `expires_at` later than the real one.
        """
        sandbox = cls(**kwargs)
        sandbox._machine_id = machine_id
        sandbox._created_at = created_at if created_at is not None else time.time()
        return sandbox

    async def ensure_alive(self) -> None:
        """Make sure the machine still exists and can run commands: a stopped
        machine is started, a paused one resumed, a hibernated one woken. A
        deleted one is a `CodingSandboxError`."""
        machines = self._sdk().machines
        try:
            machine = await machines.get(self.sandbox_id, region=self._region)
            status = getattr(machine, "status", None)
            if status == "stopped":
                await machines.start(self.sandbox_id, region=self._region)
            elif status == "suspended":
                await machines.resume(self.sandbox_id, region=self._region)
            elif status == "hibernated":
                await machines.wake(self.sandbox_id, region=self._region)
            await machines.wait_until_ready(self.sandbox_id, region=self._region)
        except Exception as e:
            raise _infra_error("reconnect to the boxd machine", e) from e

    @property
    def sandbox_id(self) -> str:
        if self._machine_id is None:
            raise CodingSandboxError("BoxdCodingSandbox has not been provisioned yet; call provision() first.")
        return self._machine_id

    @property
    def expires_at(self) -> float | None:
        if self._created_at is None:
            return None
        return self._created_at + self._lifetime_s

    @classmethod
    def describe_capabilities(cls, *, allow_network: bool = False, lifetime_s: int = 1800) -> SandboxCapabilities:
        """See `LocalCodingSandbox.describe_capabilities`."""
        return SandboxCapabilities(
            isolation=IsolationLevel.MICROVM,
            supports_network=allow_network,
            supports_streaming=False,
            supports_reconnect=True,
            is_metered=True,
            max_timeout_s=float(lifetime_s),
            persistent_filesystem=True,
        )

    @property
    def capabilities(self) -> SandboxCapabilities:
        return self.describe_capabilities(allow_network=self._allow_network, lifetime_s=self._lifetime_s)

    def _info(self) -> SandboxInfo:
        return SandboxInfo(sandbox_id=self.sandbox_id, status="ready", metadata={"backend": "boxd"})

    def _labels(self) -> dict[str, str]:
        """Labels on the machine, so whose it is can be read from boxd alone
        after the process that created it is gone. Never secrets."""
        labels = {**self._extra_labels, "app": "pipeshub", "pipeshub-sandbox": "coding"}
        ctx = self._context
        if ctx is not None:
            for key, value in (
                ("org-id", ctx.org_id),
                ("user-id", ctx.user_id),
                ("conversation-id", ctx.conversation_id),
                ("run-id", ctx.run_id),
            ):
                if value:
                    labels[key] = str(value)[:_LABEL_VALUE_MAX]
        return labels

    def _sdk(self) -> Any:
        if self._client is None:
            if not self._api_key:
                # Never the ambient credential of the host PipesHub runs on.
                raise CodingSandboxError("BOXD_API_KEY is not set, so the boxd coding sandbox cannot start.")
            try:
                from boxd import AsyncBoxd
            except ImportError as e:
                raise CodingSandboxError(
                    "The boxd SDK is not installed; install it with `pip install boxd` "
                    "to use the boxd coding sandbox backend."
                ) from e
            self._client = AsyncBoxd(api_key=self._api_key, region=self._region)
            self._owns_client = True
        return self._client

    async def provision(self) -> SandboxInfo:
        if self._machine_id is not None:
            return self._info()
        client = self._sdk()
        name = f"{self._name_prefix}-{uuid.uuid4().hex[:10]}"
        try:
            machine = await client.machines.create(
                name,
                from_snapshot=self._snapshot,
                org=self._org,
                isolated=True,
                labels=self._labels(),
                vcpu=self._vcpu,
                memory=self._memory,
                auto_suspend_timeout=0,
                delete_after=self._lifetime_s,
                region=self._region,
            )
        except Exception as e:
            if _is_not_found(e):
                raise CodingSandboxError(
                    "Could not create a boxd machine: its snapshot, organisation or region was not found. "
                    "Check BOXD_SNAPSHOT, BOXD_ORG and BOXD_REGION."
                ) from e
            raise _infra_error("create a boxd machine", e) from e
        self._machine_id = machine.id
        self._created_at = time.time()
        logger.info("boxd_sandbox.created", extra={"machine_id": machine.id, "lifetime_s": self._lifetime_s})
        try:
            await client.machines.wait_until_ready(machine.id, region=self._region)
            if not self._allow_network:
                await self._set_egress(_EGRESS_NONE)
                await self._wait_egress_closed()
            await self._run_command(f"mkdir -p {_WORKING_DIR}", timeout=60.0, cwd="/home/boxd")
        except BaseException as e:
            try:
                await self.destroy()
            except Exception:
                # Its `delete_after` timer removes it; the failure that matters is the one raised below.
                logger.warning("boxd_sandbox.cleanup_failed", extra={"machine_id": machine.id})
            if isinstance(e, Exception) and not isinstance(e, CodingSandboxError):
                raise _infra_error("start the boxd machine", e) from e
            raise
        return self._info()

    async def execute(self, request: CodeRequest) -> CodeResult:
        async with self._lock:
            return await self._execute(request)

    async def _execute(self, request: CodeRequest) -> CodeResult:
        if self._machine_id is None:
            await self.provision()

        if request.packages:
            install_result = await self._install(request.packages, request.language)
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
        else:
            await self._ensure_python_env()

        before = set(await self.list_files())

        entry_file = validate_entry_file(request.entry_file or _ENTRY_FILES[request.language])
        await self._upload(f"{_WORKING_DIR}/{entry_file}", request.code.encode())

        run_cmd = _MAKE_RUN_DIRS + _RUN_COMMANDS[request.language].format(file=shlex.quote(entry_file))
        start = time.monotonic()
        stdout, stderr, exit_code = await self._run_command(run_cmd, timeout=request.timeout, env=_RUN_ENV)
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
        `EnvironmentManager.install_packages`, installing with a command in
        the machine. With network off, only packages already in the machine
        (from the snapshot) succeed."""
        async with self._lock:
            return await self._install(packages, language)

    async def _install(self, packages: list[str], language: CodingLanguage) -> InstallResult:
        if self._machine_id is None:
            await self.provision()
        if not packages:
            return InstallResult(success=True, installed=[])

        to_install: list[str] = []
        for spec in packages:
            name = package_name(spec, language)
            if not validate_package_spec(spec, language):
                return InstallResult(success=False, stderr=f"invalid or unsafe package spec: {spec!r}")
            if self._denylist and matches_package_set(name, self._denylist, language):
                return InstallResult(success=False, stderr=f"package {name!r} is denylisted")
            if self._allowlist is not None and not matches_package_set(name, self._allowlist, language):
                return InstallResult(success=False, stderr=f"package {name!r} is not in the configured allowlist")
            if canonical_package_key(name, language) not in self._installed[language]:
                to_install.append(spec)

        if not to_install:
            return InstallResult(success=True, installed=[])

        if language == "typescript":
            await self._ensure_node_project()
        else:
            await self._ensure_python_env()

        if not self._allow_network:
            return await self._use_installed(to_install, language)

        if language == "typescript":
            cmd = "npm install --ignore-scripts --no-audit --no-fund " + _shell_words(to_install)
        else:
            cmd = ".venv/bin/pip install --no-cache-dir --only-binary=:all: " + _shell_words(to_install)

        stdout, stderr, exit_code = await self._run_command(cmd, timeout=min(_INSTALL_TIMEOUT_S, self._lifetime_s))
        success = exit_code == 0
        if success:
            for spec in to_install:
                self._installed[language].add(canonical_package_key(package_name(spec, language), language))
        return InstallResult(
            success=success,
            installed=[package_name(s, language) for s in to_install] if success else [],
            stdout=stdout,
            stderr=stderr,
        )

    async def _use_installed(self, specs: list[str], language: CodingLanguage) -> InstallResult:
        """The install with network off: the packages the machine already
        has (from its snapshot) succeed, any other is refused by name."""
        names = [package_name(spec, language) for spec in specs]
        if language == "typescript":
            checks = [f"test -f node_modules/{shlex.quote(name)}/package.json" for name in names]
        else:
            checks = [
                ".venv/bin/python -c 'import importlib.metadata as m, sys; m.version(sys.argv[1])' "
                + shlex.quote(name)
                for name in names
            ]
        missing = []
        for name, check in zip(names, checks, strict=True):
            _, _, exit_code = await self._run_command(f"{check} 2>/dev/null", timeout=60.0)
            if exit_code != 0:
                missing.append(name)
        if missing:
            return InstallResult(
                success=False,
                stderr=(
                    f"Not installed, and package installs need the network, which is off for this sandbox: "
                    f"{', '.join(missing)}. Use the packages that are installed, or the standard library."
                ),
            )
        for name in names:
            self._installed[language].add(canonical_package_key(name, language))
        return InstallResult(success=True, installed=[])

    async def upload_file(self, path: str, content: bytes) -> None:
        if self._machine_id is None:
            await self.provision()
        await self._upload(self._resolve_path(path), content)

    async def download_file(self, path: str) -> bytes:
        if self._machine_id is None:
            await self.provision()
        full_path = self._resolve_path(path)
        try:
            return bytes(await self._sdk().machines.files.download(self.sandbox_id, full_path, region=self._region))
        except Exception as e:
            if _is_missing_file(e) or (_is_not_found(e) and not await self._is_file(full_path)):
                raise FileNotFoundError(f"No such file in the sandbox: {path}") from e
            raise _infra_error("read a file from the boxd machine", e) from e

    async def _is_file(self, full_path: str) -> bool:
        """Whether `full_path` is a file. A download of a missing file and a
        call on a deleted machine both answer not found; this tells them
        apart, and raises when the machine itself is gone."""
        result = await self._exec(f"test -f {shlex.quote(full_path)}", timeout=30.0)
        return result.exit_code == 0

    async def read_file_head(self, path: str, max_bytes: int) -> tuple[bytes, bool]:
        """At most ``max_bytes`` of a file, read in the machine so a large
        file is never sent whole."""
        if self._machine_id is None:
            await self.provision()
        max_bytes = max(0, int(max_bytes))
        full_path = self._resolve_path(path)
        result = await self._exec(
            f"test -f {shlex.quote(full_path)} && head -c {max_bytes + 1} -- {shlex.quote(full_path)}",
            timeout=60.0, encoding="buffer",
        )
        if result.exit_code != 0:
            raise FileNotFoundError(f"No such file in the sandbox: {path}")
        data = bytes(result.stdout or b"")
        return data[:max_bytes], len(data) > max_bytes

    async def file_size(self, path: str) -> int | None:
        if self._machine_id is None:
            await self.provision()
        full_path = self._resolve_path(path)
        try:
            result = await self._exec(f"stat -c %s -- {shlex.quote(full_path)}", timeout=30.0)
        except CodingSandboxError:
            return None
        if result.exit_code != 0:
            return None
        try:
            return int((result.stdout or "").strip())
        except ValueError:
            return None

    async def list_files(self) -> list[str]:
        if self._machine_id is None:
            await self.provision()
        prune = " -o ".join(f"-name {shlex.quote(d)}" for d in _LISTING_IGNORED_DIRS)
        cmd = f"cd {_WORKING_DIR} && find . -mindepth 1 -type d \\( {prune} \\) -prune -o -type f -print0"
        result = await self._exec(cmd, timeout=60.0, encoding="buffer")
        if result.exit_code != 0:
            raise CodingSandboxError("could not list the files in the boxd machine")
        raw = bytes(result.stdout or b"")
        paths = (p.decode("utf-8", "replace") for p in raw.split(b"\0") if p)
        return sorted(p[2:] if p.startswith("./") else p for p in paths)

    async def destroy(self) -> None:
        machine_id = self._machine_id
        self._machine_id = None
        if machine_id is None:
            await self._close_client()
            return
        try:
            await self._sdk().machines.delete(machine_id, region=self._region)
            logger.info("boxd_sandbox.deleted", extra={"machine_id": machine_id})
        except Exception as e:
            if not _is_not_found(e):
                # Its `delete_after` timer still removes it.
                raise _infra_error("delete the boxd machine", e) from e
        finally:
            await self._close_client()

    async def _close_client(self) -> None:
        if self._owns_client and self._client is not None:
            client, self._client = self._client, None
            close = getattr(client, "close", None)
            if close is not None:
                try:
                    await close()
                except Exception:
                    pass

    async def _upload(self, full_path: str, content: bytes) -> None:
        parent = posixpath.dirname(full_path)
        if parent and parent != _WORKING_DIR:
            await self._run_command(f"mkdir -p {shlex.quote(parent)}", timeout=30.0)
        try:
            await self._sdk().machines.files.upload(self.sandbox_id, full_path, content, region=self._region)
        except Exception as e:
            raise _infra_error("write a file to the boxd machine", e) from e

    async def _ensure_typescript_runtime(self) -> None:
        if self._node_initialized:
            return
        await self._ensure_node_project()
        # A machine made from a prepared snapshot already has the runtime.
        _, _, has_tsx = await self._run_command("test -x node_modules/.bin/tsx", timeout=30.0)
        if has_tsx == 0:
            self._node_initialized = True
            return
        if not self._allow_network:
            raise EnvironmentSetupError(
                "TypeScript needs tsx, which this machine does not have and cannot install with the network "
                "off. Start sandboxes from a snapshot that has it (BOXD_SNAPSHOT), or use Python."
            )
        install_result = await self._install(["tsx", "typescript"], "typescript")
        if not install_result.success:
            raise EnvironmentSetupError(
                f"failed to install TypeScript runtime (tsx/typescript): {install_result.stderr}"
            )
        self._node_initialized = True

    async def _ensure_node_project(self) -> None:
        _, stderr, exit_code = await self._run_command(
            "test -f package.json || npm init -y >/dev/null", timeout=60.0,
        )
        if exit_code != 0:
            raise EnvironmentSetupError(f"npm init failed: {stderr}")

    async def _ensure_python_env(self) -> None:
        """A virtualenv in the working directory: Ubuntu's system Python
        refuses `pip install` outside one."""
        if self._python_initialized:
            return
        _, stderr, exit_code = await self._run_command(
            f"test -x {_VENV_DIR}/bin/python || python3 -m venv {_VENV_DIR}", timeout=_SETUP_TIMEOUT_S,
        )
        if exit_code != 0:
            raise EnvironmentSetupError(f"could not create the Python environment: {stderr}")
        self._python_initialized = True

    async def _set_egress(self, entries: list[str]) -> None:
        try:
            await self._sdk().machines.set_egress_allow(self.sandbox_id, entries, region=self._region)
        except Exception as e:
            raise _infra_error("set the boxd machine's egress allowlist", e) from e

    async def _wait_egress_closed(self) -> None:
        """Returns once the machine can no longer resolve a registry host.
        An allowlist change takes a moment to apply, and no run may start
        before it has."""
        deadline = time.monotonic() + _EGRESS_SETTLE_S
        probe = f"getent hosts {_EGRESS_PROBE_HOST} >/dev/null"
        while True:
            result = await self._exec(probe, timeout=30.0)
            if result.exit_code != 0:
                return
            if time.monotonic() >= deadline:
                raise CodingSandboxError("the boxd machine's network could not be closed")
            await asyncio.sleep(_EGRESS_POLL_S)

    async def _run_command(
        self, cmd: str, *, timeout: float, env: dict[str, str] | None = None, cwd: str = _WORKING_DIR,
    ) -> tuple[str, str, int]:
        """Run `cmd` under coreutils `timeout` and return `(stdout, stderr,
        exit_code)`. A run that hits its timeout is exit code -1, like the
        other backends; only infrastructure failures raise."""
        seconds = max(1, int(round(timeout)))
        wrapped = f"timeout -k {_KILL_AFTER_S} {seconds} sh -c {shlex.quote(cmd)}"
        start = time.monotonic()
        try:
            result = await self._exec(wrapped, timeout=seconds + _KILL_AFTER_S + _EXEC_GRACE_S, env=env, cwd=cwd)
        except _ExecTimedOut:
            return "", f"Execution timed out after {seconds}s", -1
        stdout, stderr = cap_text(result.stdout or ""), cap_text(result.stderr or "")
        if result.exit_code in _TIMEOUT_EXITS and time.monotonic() - start >= seconds:
            return stdout, (stderr + f"\nExecution timed out after {seconds}s").lstrip(), -1
        return stdout, stderr, result.exit_code

    async def _exec(
        self,
        cmd: str,
        *,
        timeout: float,
        env: dict[str, str] | None = None,
        cwd: str = _WORKING_DIR,
        encoding: str = "utf-8",
    ) -> Any:
        try:
            return await self._sdk().machines.exec(
                self.sandbox_id, cmd, env=env, cwd=cwd, timeout=timeout, encoding=encoding, region=self._region,
            )
        except Exception as e:
            if _is_exec_timeout(e):
                raise _ExecTimedOut() from e
            raise _infra_error("run a command in the boxd machine", e) from e

    def _resolve_path(self, path: str) -> str:
        """Resolve `path` (relative to `_WORKING_DIR`) and reject any
        traversal outside it, the same boundary as `LocalCodingSandbox`."""
        full_path = posixpath.normpath(posixpath.join(_WORKING_DIR, normalize_sandbox_path(path)))
        if full_path != _WORKING_DIR and not full_path.startswith(_WORKING_DIR + "/"):
            raise ValueError(f"path {path!r} escapes the sandbox working directory")
        return full_path


class _ExecTimedOut(CodingSandboxError):
    """The exec call itself ran out of time. A run reads it as a timeout;
    anywhere else it is an infrastructure failure."""

    def __init__(self) -> None:
        super().__init__("A command in the boxd machine did not answer in time.")


def _is_not_found(exc: BaseException) -> bool:
    try:
        from boxd.errors import NotFoundError
    except ImportError:
        return False
    return isinstance(exc, NotFoundError)


def _is_exec_timeout(exc: BaseException) -> bool:
    """The SDK ends an exec past its `timeout` with a `BoxdError` whose only
    mark is its message; a connection timeout says something else."""
    try:
        from boxd.errors import BoxdError
    except ImportError:
        return False
    return type(exc) is BoxdError and str(exc).startswith("exec timed out")


def _is_missing_file(exc: BaseException) -> bool:
    try:
        from boxd.errors import MachineFileNotFoundError
    except ImportError:
        return False
    return isinstance(exc, MachineFileNotFoundError)


def _infra_error(action: str, exc: BaseException) -> CodingSandboxError:
    """A `CodingSandboxError` that names what failed and the error's kind.
    The provider's own message stays out: it reaches tool results."""
    try:
        from boxd.errors import (
            AuthenticationError,
            NotFoundError,
            PermissionDeniedError,
            RateLimitError,
        )
    except ImportError:
        return CodingSandboxError(f"Could not {action}.")
    if isinstance(exc, AuthenticationError):
        return CodingSandboxError(f"Could not {action}: boxd refused the API key. Check BOXD_API_KEY.")
    if isinstance(exc, PermissionDeniedError):
        return CodingSandboxError(f"Could not {action}: the boxd API key may not do this in its organisation.")
    if isinstance(exc, RateLimitError):
        return CodingSandboxError(f"Could not {action}: boxd is rate limiting requests; try again shortly.")
    if isinstance(exc, NotFoundError):
        return CodingSandboxError(f"Could not {action}: the boxd machine no longer exists.")
    return CodingSandboxError(f"Could not {action} ({type(exc).__name__}).")


def _shell_words(specs: list[str]) -> str:
    """Package specs as literal ``sh`` words: ``numpy>=1.26`` would
    otherwise be read as a redirection, and ``lodash@4.*`` as a glob."""
    return " ".join(shlex.quote(spec) for spec in specs)
