from __future__ import annotations

import asyncio
import io
import logging
import os
import shutil
import tarfile
import tempfile
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
    ErrorAnalysis,
    ErrorCategory,
    InstallResult,
)
from app.agent_loop_lib.sandbox.coding.cleanup import (
    register_sandbox_dir,
    unregister_sandbox_dir,
)
from app.agent_loop_lib.sandbox.coding.reflection import ReflectionEngine
from app.agent_loop_lib.sandbox.coding.validation import (
    canonical_package_key,
    matches_package_set,
    package_name,
    validate_package_spec,
)

"""`DockerCodingSandbox`: a `CodingSandboxBackend` implementation that runs
code inside ephemeral Docker containers instead of as a host subprocess
(`LocalCodingSandbox`) or a remote micro-VM (`E2BCodingSandbox`).

Two-phase execution, ported from PipesHub's own stateless
``app/sandbox/docker_executor.py`` (this backend adopts that module's proven
security model, not just its shape):

- By default, user code runs in its own container with ``network_mode="none"``
  AND ``network_disabled=True`` — no routable networking stack at all. When
  the backend is constructed with ``allow_network=True`` AND the individual
  ``CodeRequest.allow_network`` is also set, the run container instead joins
  the SAME dedicated egress bridge used for package installs (below) —
  real internet access for the sandboxed code, but still never the caller's
  default Docker network, so compose sibling services stay unreachable.
- Packages are installed in a SEPARATE, short-lived container attached to a
  dedicated egress network (outbound internet for pip/npm only) — never the
  caller's default Docker network, so this backend can be dropped into a
  compose deployment without exposing sibling services to sandboxed code.
- No host-path volume mounts anywhere: every file transfer in or out of a
  container goes through ``put_archive``/``get_archive``, so this also works
  under Docker-in-Docker (sibling container) deployments where the host path
  doesn't exist on the daemon's own filesystem.

Persistence model (the state contract `CodingSandboxBackend` requires):
installed dependencies and files written to the sandbox's working directory
persist on the HOST, across `execute()`/`install_packages()` calls on the
same instance, and are re-archived into a fresh container on every call —
including files that only ever reached the HOST side via `upload_file()`
(staged `input_artifacts`, skill resources) rather than a prior `execute()`,
which `_collect_working_dir_inputs()`/`_tar_files()` fold into the same
`/src` archive the entry file gets, so they're visible at the program's cwd
under their staged relative path, not just present on the host and
invisible to the container; interpreter state never persists, since every
call is a brand new container.

This module has no PipesHub-specific imports or literals — image name,
egress network, and registry URLs are all constructor arguments with
neutral defaults, supplied by the adapter layer that wires this backend up.
"""

__all__ = ["DockerCodingSandbox"]

logger = logging.getLogger(__name__)

_ENTRY_FILES: dict[CodingLanguage, str] = {"typescript": "main.ts", "python": "main.py"}
# `npx tsx` (not the local sandbox's bootstrap-and-run-a-binary approach):
# the sandbox image is expected to ship a global Node/npx/tsx toolchain, so
# there is no per-sandbox TypeScript runtime bootstrap step here — matching
# the existing `docker_executor.py`'s behavior exactly.
_RUN_COMMANDS: dict[CodingLanguage, str] = {
    "typescript": "npx tsx {file}",
    "python": "python3 {file}",
}
_LISTING_IGNORED_DIRS = {"node_modules", "deps_python", "__pycache__", ".git"}
# Top-level working-dir subdirectories that are archived into the container
# through their OWN dedicated `put_archive` call (`_src` -> `/src`'s entry
# file, `output` -> `/output`, `deps_python`/`node_modules` -> `/deps` /
# `/node_modules`) — never re-included in the generic "everything else in
# the working dir" sweep below, or they'd be double-archived / land at the
# wrong container path.
_RESERVED_WORKING_DIR_SUBDIRS = frozenset({"_src", "output", "deps_python", "node_modules"})

# Keep up to 16 MiB of tar data in memory; anything larger spills to disk —
# avoids OOM on large artifact/dependency directories while staying fast for
# the common small-output case.
_TAR_SPOOL_MAX_SIZE = 16 * 1024 * 1024


class DockerCodingSandbox(CodingSandboxBackend):
    """One sandbox instance = one host working directory + a Docker image
    used to run every `execute()`/`install_packages()` call in a fresh,
    isolated container."""

    def __init__(
        self,
        *,
        image: str = "agent-loop-sandbox:latest",
        working_dir: str | None = None,
        memory_limit_mb: int = 512,
        cpu_limit: float = 0.5,
        egress_network: str = "sandbox_egress",
        network_disabled: bool = True,
        allow_network: bool = False,
        pip_index_url: str = "https://pypi.org/simple",
        npm_registry: str = "https://registry.npmjs.org",
        package_allowlist: list[str] | None = None,
        package_denylist: list[str] | None = None,
        image_node_modules: str | None = None,
    ) -> None:
        self._sandbox_id = str(uuid.uuid4())
        short_suffix = self._sandbox_id.replace("-", "")[:10]
        self._working_dir = os.path.realpath(
            working_dir or os.path.join(tempfile.gettempdir(), f"alcs-docker-{short_suffix}")
        )
        self._image = image
        self._memory_limit_mb = memory_limit_mb
        self._cpu_limit = cpu_limit
        self._egress_network = egress_network
        self._network_disabled = network_disabled
        self._allow_network = allow_network
        self._pip_index_url = pip_index_url
        self._npm_registry = npm_registry
        self._allowlist = set(package_allowlist) if package_allowlist else None
        self._denylist = set(package_denylist or [])
        self._image_node_modules = image_node_modules
        self._installed: dict[str, set[str]] = {"typescript": set(), "python": set()}
        self._reflection = ReflectionEngine()
        self._provisioned = False

    # ------------------------------------------------------------------
    # CodingSandboxBackend contract
    # ------------------------------------------------------------------

    @property
    def sandbox_id(self) -> str:
        return self._sandbox_id

    @property
    def working_dir(self) -> str:
        return self._working_dir

    async def provision(self) -> SandboxInfo:
        if not self._provisioned:
            os.makedirs(self._output_dir, exist_ok=True)
            register_sandbox_dir(self._working_dir)
            self._provisioned = True
        return SandboxInfo(
            sandbox_id=self._sandbox_id,
            status="ready",
            metadata={"backend": "docker", "image": self._image, "working_dir": self._working_dir},
        )

    async def execute(self, request: CodeRequest) -> CodeResult:
        if not self._provisioned:
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
                        suggestion=(
                            "Fix the package spec, or call install_packages directly to see "
                            "the full installer output."
                        ),
                        is_retryable=True,
                    ),
                )

        start = time.monotonic()
        entry = request.entry_file or _ENTRY_FILES[request.language]
        src_dir = os.path.join(self._working_dir, "_src")
        shutil.rmtree(src_dir, ignore_errors=True)
        os.makedirs(src_dir, exist_ok=True)
        with open(os.path.join(src_dir, entry), "w", encoding="utf-8") as f:
            f.write(request.code)

        run_cmd = ["sh", "-c", _RUN_COMMANDS[request.language].format(file=entry)]

        # Everything `upload_file()` has ever staged into the working dir
        # (input artifacts from `input_artifacts`, skill resources, files
        # promoted from a PRIOR run's cwd — see `_promote_src_artifacts`)
        # lived on the HOST only until now: the run container previously
        # got just the entry file, never this. Read once, up front, so the
        # exact same bytes are both what gets archived into `/src` below
        # AND the baseline `_promote_src_artifacts` diffs against after the
        # run, instead of re-reading (and risking a second, possibly
        # racy) read off disk.
        staged_inputs = _collect_working_dir_inputs(self._working_dir)
        logger.info(
            "DockerCodingSandbox.execute: sandbox=%s language=%s packages=%s "
            "entry=%s working_dir=%s staged_inputs=%d (%s) "
            "total_staged_bytes=%d allow_network=%s image=%s",
            self._sandbox_id, request.language, request.packages,
            entry, self._working_dir, len(staged_inputs),
            sorted(staged_inputs.keys()),
            sum(len(v) for v in staged_inputs.values()),
            self._allow_network and request.allow_network,
            self._image,
        )

        network_enabled = self._allow_network and request.allow_network
        try:
            exit_code, stdout, stderr = await asyncio.wait_for(
                asyncio.to_thread(
                    self._run_container_sync,
                    run_cmd, src_dir, request.timeout, network_enabled, staged_inputs,
                ),
                timeout=request.timeout + 30,
            )
        except (asyncio.TimeoutError, asyncio.CancelledError):
            duration_ms = (time.monotonic() - start) * 1000
            result = CodeResult(
                stdout="",
                stderr=f"Execution timed out after {request.timeout}s",
                exit_code=-1,
                language=request.language,
                duration_ms=duration_ms,
            )
            return result.model_copy(update={"error_analysis": self._reflection.analyze(result)})
        except CodingSandboxError:
            raise
        except Exception as exc:
            logger.exception("DockerCodingSandbox.execute failed")
            duration_ms = (time.monotonic() - start) * 1000
            return CodeResult(
                stdout="", stderr=str(exc), exit_code=-1,
                language=request.language, duration_ms=duration_ms,
            )

        duration_ms = (time.monotonic() - start) * 1000
        artifacts = self._list_output_artifacts()
        artifacts.extend(self._promote_src_artifacts(src_dir, entry, staged_inputs))
        artifacts.sort()
        result = CodeResult(
            stdout=stdout, stderr=stderr, exit_code=exit_code,
            language=request.language, duration_ms=duration_ms, artifacts=artifacts,
        )
        if not result.success:
            result = result.model_copy(update={"error_analysis": self._reflection.analyze(result)})
        return result

    async def install_packages(self, packages: list[str], language: CodingLanguage) -> InstallResult:
        if not self._provisioned:
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

        try:
            success, stdout, stderr = await asyncio.to_thread(
                self._install_packages_sync, to_install, language,
            )
        except CodingSandboxError:
            raise
        except Exception as exc:
            logger.exception("DockerCodingSandbox.install_packages failed")
            return InstallResult(success=False, stderr=str(exc))

        if success:
            for spec in to_install:
                self._installed[language].add(canonical_package_key(package_name(spec, language), language))
        return InstallResult(
            success=success,
            installed=[package_name(s, language) for s in to_install] if success else [],
            stdout=stdout,
            stderr=stderr,
        )

    async def upload_file(self, path: str, content: bytes) -> None:
        if not self._provisioned:
            await self.provision()
        full_path = self._resolve_path(path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "wb") as f:
            f.write(content)
        logger.info(
            "DockerCodingSandbox.upload_file: path=%s full_path=%s size=%d sandbox=%s",
            path, full_path, len(content), self._sandbox_id,
        )

    async def download_file(self, path: str) -> bytes:
        full_path = self._resolve_path(path)
        with open(full_path, "rb") as f:
            return f.read()

    async def list_files(self) -> list[str]:
        results: list[str] = []
        for dirpath, dirnames, filenames in os.walk(self._working_dir):
            dirnames[:] = [
                d for d in dirnames
                if d not in _LISTING_IGNORED_DIRS and not d.startswith("_src")
            ]
            for fname in filenames:
                results.append(os.path.relpath(os.path.join(dirpath, fname), self._working_dir))
        return sorted(results)

    async def destroy(self) -> None:
        shutil.rmtree(self._working_dir, ignore_errors=True)
        unregister_sandbox_dir(self._working_dir)
        self._provisioned = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @property
    def _output_dir(self) -> str:
        return os.path.join(self._working_dir, "output")

    @property
    def _deps_python_dir(self) -> str:
        return os.path.join(self._working_dir, "deps_python")

    @property
    def _deps_node_dir(self) -> str:
        return os.path.join(self._working_dir, "node_modules")

    def _resolve_path(self, path: str) -> str:
        """Reject any `path` that escapes the sandbox working directory —
        same traversal boundary as `LocalCodingSandbox`/`E2BCodingSandbox`."""
        root = os.path.realpath(self._working_dir)
        full_path = os.path.realpath(os.path.join(root, path))
        if full_path != root and not full_path.startswith(root + os.sep):
            raise ValueError(f"path {path!r} escapes the sandbox working directory")
        return full_path

    def _promote_src_artifacts(
        self, src_dir: str, entry: str, staged_inputs: dict[str, bytes],
    ) -> list[str]:
        """Move files the program wrote to its container cwd (/src, mirrored
        back into `src_dir` after the run) up into the sandbox working dir,
        and report them as artifacts.

        Models overwhelmingly write output files to their cwd, not to
        $OUTPUT_DIR — the local backend captures those via its mtime diff,
        but Docker only extracted /output, silently discarding every
        cwd-written file with the container. This restores parity.

        `staged_inputs` is the same `{relative_path: content}` map that was
        archived INTO `/src` before the run (see `execute()`) — every
        artifact the model staged via `input_artifacts` lands back here
        byte-identical unless the program actually rewrote it, since the
        mirror-back is a merge, not a diff. Skip re-"promoting" (and
        re-reporting as a freshly produced artifact) any file whose content
        still matches its staged baseline; a program that genuinely
        modified a staged input (same path, different bytes) still gets
        that change picked up and reported.
        """
        promoted: list[str] = []
        skipped_unchanged: list[str] = []
        for dirpath, _dirnames, filenames in os.walk(src_dir):
            for fname in filenames:
                full = os.path.join(dirpath, fname)
                rel = os.path.relpath(full, src_dir)
                if rel == entry:
                    continue
                baseline = staged_inputs.get(rel)
                if baseline is not None:
                    with open(full, "rb") as fh:
                        current = fh.read()
                    if current == baseline:
                        skipped_unchanged.append(rel)
                        continue
                dest = os.path.join(self._working_dir, rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.move(full, dest)
                promoted.append(rel)
        logger.info(
            "_promote_src_artifacts: promoted=%s skipped_unchanged=%s "
            "staged_baselines=%d entry=%s",
            promoted, skipped_unchanged,
            len(staged_inputs), entry,
        )
        return promoted

    def _list_output_artifacts(self) -> list[str]:
        results: list[str] = []
        if not os.path.isdir(self._output_dir):
            logger.info("_list_output_artifacts: output_dir %s does not exist", self._output_dir)
            return results
        for dirpath, _dirnames, filenames in os.walk(self._output_dir):
            for fname in filenames:
                full = os.path.join(dirpath, fname)
                results.append(os.path.relpath(full, self._working_dir))
        logger.info("_list_output_artifacts: found %d file(s): %s", len(results), sorted(results))
        return sorted(results)

    # -- blocking docker-py calls: only ever invoked via asyncio.to_thread --

    def _run_container_sync(
        self,
        command: list[str],
        src_dir: str,
        timeout: float,
        network_enabled: bool,
        staged_inputs: dict[str, bytes],
    ) -> tuple[int, str, str]:
        try:
            import docker
            from docker.errors import ImageNotFound
        except ImportError as exc:
            raise CodingSandboxError(
                "docker Python package is not installed. Install with: pip install docker"
            ) from exc

        client = docker.from_env()
        container = None
        try:
            try:
                client.images.get(self._image)
            except ImageNotFound:
                logger.info("Sandbox image %s not found locally, attempting pull ...", self._image)
                try:
                    client.images.pull(self._image)
                except Exception as pull_err:
                    raise CodingSandboxError(
                        f"Sandbox image {self._image!r} is not available locally and could not "
                        f"be pulled: {pull_err}"
                    ) from pull_err

            has_py_deps = os.path.isdir(self._deps_python_dir) and bool(os.listdir(self._deps_python_dir))
            has_node_deps = os.path.isdir(self._deps_node_dir) and bool(os.listdir(self._deps_node_dir))
            env = {"OUTPUT_DIR": "/output"}
            if has_py_deps:
                env["PYTHONPATH"] = "/deps"
            node_paths: list[str] = []
            if has_node_deps:
                node_paths.append("/node_modules")
            if self._image_node_modules:
                node_paths.append(self._image_node_modules)
            if node_paths:
                env["NODE_PATH"] = ":".join(node_paths)

            mem_bytes = self._memory_limit_mb * 1024 * 1024
            nano_cpus = int(self._cpu_limit * 1e9)
            container_kwargs: dict[str, Any] = {
                "image": self._image,
                "command": command,
                "environment": env,
                "working_dir": "/src",
                "mem_limit": mem_bytes,
                "nano_cpus": nano_cpus,
                "tmpfs": {"/tmp": "size=100M"},
                "detach": True,
            }
            if network_enabled:
                # Same dedicated egress bridge the install phase uses (below)
                # — real internet access for the run container, but never
                # the caller's default Docker network, so compose sibling
                # services (mongo/arango/redis/...) stay unreachable by name.
                container_kwargs["network"] = self._ensure_egress_network(client)
                container_kwargs["network_disabled"] = False
            else:
                container_kwargs["network_mode"] = "none"
                container_kwargs["network_disabled"] = self._network_disabled
            container = client.containers.create(**container_kwargs)
            logger.info(
                "_run_container_sync: container created id=%.12s image=%s "
                "network_enabled=%s mem_limit=%dMB cpu=%.1f env=%s",
                container.id, self._image, network_enabled,
                self._memory_limit_mb, self._cpu_limit, env,
            )

            container.put_archive("/", _tar_empty_dir("src", mode=0o777))
            if staged_inputs:
                tar_data = _tar_files(staged_inputs)
                logger.info(
                    "_run_container_sync: put_archive staged_inputs into /src — "
                    "%d file(s), tar_size=%d bytes, paths=%s",
                    len(staged_inputs), len(tar_data),
                    sorted(staged_inputs.keys()),
                )
                container.put_archive("/src", tar_data)
            else:
                logger.info("_run_container_sync: no staged_inputs to archive")
            container.put_archive("/src", _tar_directory(src_dir))
            container.put_archive("/", _tar_empty_dir("output", mode=0o777))
            if has_py_deps:
                container.put_archive("/", _tar_empty_dir("deps", mode=0o755))
                container.put_archive("/deps", _tar_directory(self._deps_python_dir))
            if has_node_deps:
                container.put_archive("/", _tar_empty_dir("node_modules", mode=0o755))
                container.put_archive("/node_modules", _tar_directory(self._deps_node_dir))

            logger.info(
                "_run_container_sync: starting container %.12s "
                "(has_py_deps=%s has_node_deps=%s)",
                container.id, has_py_deps, has_node_deps,
            )
            container.start()

            timed_out = False
            try:
                exit_info = container.wait(timeout=timeout)
                exit_code = exit_info.get("StatusCode", -1)
            except Exception:
                timed_out = True
                exit_code = -1
                try:
                    container.kill()
                except Exception:
                    pass

            stdout = container.logs(stdout=True, stderr=False).decode(errors="replace")
            stderr = container.logs(stdout=False, stderr=True).decode(errors="replace")
            if timed_out:
                stderr = f"Execution timed out after {timeout}s\n{stderr}".strip()

            logger.info(
                "_run_container_sync: container %.12s finished exit_code=%d "
                "timed_out=%s stdout_len=%d stderr_len=%d stderr_preview=%.500s",
                container.id, exit_code, timed_out,
                len(stdout), len(stderr),
                stderr[:500] if stderr else "",
            )

            os.makedirs(self._output_dir, exist_ok=True)
            _extract_container_dir(container, "/output", self._output_dir)
            _extract_container_dir(container, "/src", src_dir)
            return exit_code, stdout, stderr
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass
            try:
                client.close()
            except Exception:
                pass

    def _install_packages_sync(
        self, to_install: list[str], language: CodingLanguage,
    ) -> tuple[bool, str, str]:
        try:
            import docker
        except ImportError as exc:
            raise CodingSandboxError(
                "docker Python package is not installed. Install with: pip install docker"
            ) from exc

        client = docker.from_env()
        try:
            network_name = self._ensure_egress_network(client)

            if language == "python":
                cmd = [
                    "sh", "-c",
                    "pip install --quiet --no-cache-dir --target /deps "
                    f"--index-url {self._pip_index_url} " + " ".join(to_install),
                ]
                extract_path = "/deps"
                host_target = self._deps_python_dir
            else:
                cmd = [
                    "sh", "-c",
                    "mkdir -p /install && npm install --prefix /install --no-save "
                    f"--loglevel=error --registry={self._npm_registry} "
                    + " ".join(to_install),
                ]
                extract_path = "/install/node_modules"
                host_target = self._deps_node_dir

            mem_bytes = self._memory_limit_mb * 1024 * 1024
            nano_cpus = int(self._cpu_limit * 1e9)
            logger.info(
                "_install_packages_sync: language=%s to_install=%s image=%s "
                "network=%s host_target=%s",
                language, to_install, self._image, network_name, host_target,
            )
            container = client.containers.create(
                image=self._image,
                command=cmd,
                environment={},
                mem_limit=mem_bytes,
                nano_cpus=nano_cpus,
                network=network_name,
                network_disabled=False,
                detach=True,
            )
            try:
                if language == "python":
                    container.put_archive("/", _tar_empty_dir("deps", mode=0o777))
                else:
                    container.put_archive("/", _tar_empty_dir("install", mode=0o777))
                container.start()
                exit_info = container.wait(timeout=300)
                exit_code = exit_info.get("StatusCode", -1)
                stdout = container.logs(stdout=True, stderr=False).decode(errors="replace")
                stderr = container.logs(stdout=False, stderr=True).decode(errors="replace")
                logger.info(
                    "_install_packages_sync: container %.12s exit_code=%d "
                    "stdout_len=%d stderr_preview=%.500s",
                    container.id, exit_code, len(stdout),
                    stderr[:500] if stderr else "",
                )
                if exit_code != 0:
                    logger.warning(
                        "_install_packages_sync: install FAILED for %s — "
                        "exit_code=%d stderr=%.1000s",
                        to_install, exit_code, stderr,
                    )
                    return False, stdout, stderr
                os.makedirs(host_target, exist_ok=True)
                _extract_container_dir(container, extract_path, host_target)
                return True, stdout, stderr
            finally:
                try:
                    container.remove(force=True)
                except Exception:
                    pass
        finally:
            try:
                client.close()
            except Exception:
                pass

    def _ensure_egress_network(self, client: object) -> str:
        """Create (or reuse) the dedicated install-phase network and return
        its name. A user-defined bridge, never the caller's default Docker
        network, so sibling services on a compose deployment stay
        unreachable from the install container."""
        name = self._egress_network
        try:
            existing = client.networks.list(names=[name])
            if existing:
                return name
            client.networks.create(
                name=name,
                driver="bridge",
                internal=False,
                labels={"agent_loop.sandbox": "egress"},
                check_duplicate=True,
            )
            logger.info("Created dedicated sandbox egress network: %s", name)
        except Exception as exc:
            # Another process may have raced us to create the network; try
            # once more to list and fall through if that worked.
            logger.debug("egress network creation raised %s; re-checking", exc)
            try:
                if client.networks.list(names=[name]):
                    return name
            except Exception:
                pass
            raise
        return name


# ------------------------------------------------------------------
# Tar helpers for put_archive / get_archive — ported from
# app/sandbox/docker_executor.py (generic, no PipesHub-specific naming).
# ------------------------------------------------------------------

def _tar_directory(src_dir: str) -> bytes:
    """In-memory tar of every entry directly under `src_dir` (flat, no
    wrapping directory entry)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for fname in os.listdir(src_dir):
            tar.add(os.path.join(src_dir, fname), arcname=fname)
    buf.seek(0)
    return buf.read()


def _collect_working_dir_inputs(working_dir: str) -> dict[str, bytes]:
    """Every file under `working_dir` EXCEPT the reserved, separately-
    archived subtrees (`_RESERVED_WORKING_DIR_SUBDIRS`) — i.e. whatever
    `upload_file()` has staged there across this sandbox's lifetime
    (`input_artifacts` bytes, skill resources, ...) plus any file a PRIOR
    run promoted from its own cwd (see `_promote_src_artifacts`). Returned
    as an explicit `{relative_path: content}` map, read fully into memory,
    so the caller can both build a tar from it AND diff against it later
    without a second disk read.
    """
    files: dict[str, bytes] = {}
    for dirpath, dirnames, filenames in os.walk(working_dir):
        if dirpath == working_dir:
            dirnames[:] = [d for d in dirnames if d not in _RESERVED_WORKING_DIR_SUBDIRS]
        for fname in filenames:
            full = os.path.join(dirpath, fname)
            rel = os.path.relpath(full, working_dir)
            with open(full, "rb") as fh:
                files[rel] = fh.read()
    return files


def _tar_files(files: dict[str, bytes]) -> bytes:
    """In-memory tar built from an explicit `{relative_path: content}`
    map — unlike `_tar_directory` (which tars a real directory's flat
    listing), this preserves whatever nested structure the paths encode
    (e.g. `input/artifacts/foo.png`) without touching disk beyond what the
    caller already read."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for rel_path, content in files.items():
            info = tarfile.TarInfo(name=rel_path)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    buf.seek(0)
    return buf.read()


def _tar_empty_dir(name: str, *, mode: int = 0o755) -> bytes:
    """In-memory tar containing a single empty directory entry."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name=name)
        info.type = tarfile.DIRTYPE
        info.mode = mode
        tar.addfile(info)
    buf.seek(0)
    return buf.read()


def _extract_container_dir(container: object, container_path: str, local_dir: str) -> None:
    """Pull a directory from a container via `get_archive` and extract it
    into `local_dir`, merging with (not clearing) whatever's already there.

    Streamed chunk-by-chunk into a `SpooledTemporaryFile` so small archives
    stay in memory while large ones transparently spill to disk. Any tar
    member whose resolved path would land outside `local_dir` is skipped —
    the same path-traversal guard `docker_executor.py` uses.
    """
    try:
        bits, _ = container.get_archive(container_path)
        with tempfile.SpooledTemporaryFile(max_size=_TAR_SPOOL_MAX_SIZE) as tar_stream:
            for chunk in bits:
                if chunk:
                    tar_stream.write(chunk)
            tar_stream.seek(0)
            resolved_root = os.path.realpath(local_dir)
            with tarfile.open(fileobj=tar_stream, mode="r|*") as tar:
                prefix = os.path.basename(container_path.rstrip("/")) + "/"
                for member in tar:
                    if member.isdir():
                        continue
                    if member.name.startswith(prefix):
                        member.name = member.name[len(prefix):]
                    if not member.name:
                        continue
                    target = os.path.realpath(os.path.join(local_dir, member.name))
                    if not target.startswith(resolved_root + os.sep) and target != resolved_root:
                        logger.warning(
                            "Skipping tar member with path traversal: %s -> %s",
                            member.name, target,
                        )
                        continue
                    tar.extract(member, local_dir)
    except Exception:
        logger.debug("No output artifacts to extract from container %s", container_path)
