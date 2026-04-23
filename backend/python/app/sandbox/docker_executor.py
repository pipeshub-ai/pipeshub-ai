"""DockerExecutor -- container-based sandbox for production/docker deployments.

Creates ephemeral Docker containers per execution with resource limits,
network isolation, and a mounted output volume for artifact collection.

Uses ``put_archive`` / ``get_archive`` to transfer source files and
output artifacts between the host process and the sandbox container.
This avoids Docker volume mounts, which break in Docker-in-Docker
(sibling container) deployments where the host path does not exist on
the Docker daemon's host filesystem.

Security model:

- User code runs in a container with ``network_mode="none"`` AND
  ``network_disabled=True``. It has no routable networking stack at all.
- When the agent requests packages, a SEPARATE short-lived *install*
  container is launched first on a dedicated user-defined bridge network
  (``pipeshub_sandbox_egress`` by default). That network exists solely to
  give pip/npm outbound internet access and is NOT the compose default
  network, so sibling services (mongodb, arangodb, redis, etcd, kafka,
  qdrant, neo4j, ...) are unreachable by service name or IP.
- The install container writes deps to ``/deps`` (Python ``pip install
  --target``) or ``/install/node_modules`` (``npm install --prefix``).
  Those deps are tarred via ``get_archive`` and injected into the run
  container, which stays fully offline.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import shutil
import tarfile
import tempfile
import time
from uuid import uuid4

from app.sandbox.base_executor import BaseExecutor, build_sandbox_env
from app.sandbox.models import (
    DEFAULT_CPU_LIMIT,
    DEFAULT_MEMORY_LIMIT_MB,
    DEFAULT_TIMEOUT_SECONDS,
    SANDBOX_IMAGE,
    ExecutionResult,
    SandboxLanguage,
    validate_packages,
)

logger = logging.getLogger(__name__)

_SANDBOX_ROOT = os.path.join(tempfile.gettempdir(), "pipeshub_sandbox_docker")

#: Name of the Docker network used ONLY for the install phase. Kept
#: separate from the compose project's default network so sibling
#: services (mongodb, arangodb, redis, etc.) are unreachable. Overridable
#: via the ``SANDBOX_EGRESS_NETWORK`` env var.
_DEFAULT_EGRESS_NETWORK = "pipeshub_sandbox_egress"

#: Registries used during the install phase. An operator who wants to
#: point at an internal mirror can override these.
_DEFAULT_PIP_INDEX_URL = os.environ.get("SANDBOX_PIP_INDEX_URL", "https://pypi.org/simple")
_DEFAULT_NPM_REGISTRY = os.environ.get("SANDBOX_NPM_REGISTRY", "https://registry.npmjs.org")


class DockerExecutor(BaseExecutor):
    """Execute code inside ephemeral Docker containers."""

    def __init__(
        self,
        *,
        memory_limit_mb: int = DEFAULT_MEMORY_LIMIT_MB,
        cpu_limit: float = DEFAULT_CPU_LIMIT,
        network_disabled: bool = True,
        egress_network: str | None = None,
    ) -> None:
        self.memory_limit_mb = memory_limit_mb
        self.cpu_limit = cpu_limit
        self.network_disabled = network_disabled
        self.egress_network = (
            egress_network
            or os.environ.get("SANDBOX_EGRESS_NETWORK")
            or _DEFAULT_EGRESS_NETWORK
        )
        os.makedirs(_SANDBOX_ROOT, exist_ok=True)

    async def execute(
        self,
        code: str,
        language: str,
        *,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        packages: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> ExecutionResult:
        execution_id = str(uuid4())
        work_dir = os.path.join(_SANDBOX_ROOT, execution_id)
        output_dir = os.path.join(work_dir, "output")
        src_dir = os.path.join(work_dir, "src")
        os.makedirs(output_dir, exist_ok=True)
        os.makedirs(src_dir, exist_ok=True)

        start_ms = _now_ms()

        try:
            lang = SandboxLanguage(language)
            # Validate packages up-front (shell-injection + allowlist). This
            # raises before we spin up any container.
            safe_packages = validate_packages(packages, language=lang)

            # Install phase (only if packages were requested). Produces a tar
            # of the installed dependencies that will be injected into the
            # offline run container.
            deps_tar: bytes | None = None
            deps_target: str | None = None
            if safe_packages:
                deps_tar, deps_target = await asyncio.to_thread(
                    self._install_dependencies,
                    safe_packages,
                    lang,
                    timeout_seconds,
                )

            cmd = self._build_command(code, lang, src_dir)
            # Pass only the shared allowlist + caller-provided env into the
            # container. Do NOT forward the host's full environ (OPENAI_API_KEY,
            # Arango/Neo4j creds, JWT secrets, etc.) into user-controlled code.
            run_env: dict[str, str] = {**(env or {}), "OUTPUT_DIR": "/output"}
            if deps_target == "/deps":
                run_env["PYTHONPATH"] = "/deps"
            elif deps_target == "/node_modules":
                run_env["NODE_PATH"] = "/node_modules"
            container_env = build_sandbox_env(run_env)

            result = await self._run_container(
                image=SANDBOX_IMAGE,
                command=cmd,
                work_dir=work_dir,
                src_dir=src_dir,
                output_dir=output_dir,
                env=container_env,
                timeout=timeout_seconds,
                deps_tar=deps_tar,
                deps_target=deps_target,
            )
            result.artifacts = self.collect_artifacts(output_dir)
            result.execution_time_ms = _now_ms() - start_ms
            return result

        except asyncio.TimeoutError:
            return ExecutionResult(
                success=False,
                error=f"Execution timed out after {timeout_seconds}s",
                exit_code=-1,
                execution_time_ms=_now_ms() - start_ms,
            )
        except Exception as exc:
            logger.exception("DockerExecutor.execute failed for %s", language)
            return ExecutionResult(
                success=False,
                error=str(exc),
                execution_time_ms=_now_ms() - start_ms,
            )

    def _build_command(
        self,
        code: str,
        language: SandboxLanguage,
        src_dir: str,
    ) -> list[str]:
        """Write source file and return the container command.

        The install step is no longer concatenated into this command; it
        runs in a separate container (see :meth:`_install_dependencies`).
        """
        parts: list[str] = []

        if language == SandboxLanguage.PYTHON:
            script = os.path.join(src_dir, "main.py")
            with open(script, "w") as f:
                f.write(code)
            parts.append("python3 /src/main.py")

        elif language == SandboxLanguage.TYPESCRIPT:
            # Use a `.mts` extension so tsx/esbuild treats the file as ESM.
            # Plain `.ts` falls back to CJS (because there is no
            # package.json with `"type": "module"` in /src), which would
            # reject top-level `await` — pptxgenjs/docx-js examples and
            # our pipeshub-slides helper all rely on it.
            script = os.path.join(src_dir, "main.mts")
            with open(script, "w") as f:
                f.write(code)
            parts.append("npx tsx /src/main.mts")

        elif language == SandboxLanguage.SQLITE:
            sql_file = os.path.join(src_dir, "query.sql")
            with open(sql_file, "w") as f:
                f.write(code)
            parts.append("sqlite3 -header -csv /tmp/sandbox.db < /src/query.sql")

        elif language == SandboxLanguage.POSTGRESQL:
            sql_file = os.path.join(src_dir, "query.sql")
            with open(sql_file, "w") as f:
                f.write(code)
            parts.append("psql $DATABASE_URL -f /src/query.sql --csv")

        return ["sh", "-c", " ".join(parts)]

    # ------------------------------------------------------------------
    # Install phase
    # ------------------------------------------------------------------

    def _install_dependencies(
        self,
        packages: list[str],
        language: SandboxLanguage,
        timeout: int,
    ) -> tuple[bytes, str]:
        """Install *packages* in a short-lived container on the egress network.

        Returns ``(deps_tar_bytes, mount_point)`` where ``mount_point`` is the
        path inside the run container where the tar should be extracted
        (``/deps`` for Python, ``/node_modules`` for TypeScript).
        """
        try:
            import docker
            from docker.errors import ImageNotFound  # noqa: F401 (re-exported for tests)
        except ImportError as exc:
            raise RuntimeError(
                "docker Python package is not installed. Install with: pip install docker"
            ) from exc

        client = docker.from_env()
        try:
            network_name = self._ensure_egress_network(client)

            # Install into a writable location under /tmp. The sandbox image
            # runs as a non-root user whose home isn't on the container root,
            # so directories like /install or /deps at "/" aren't writable.
            # /tmp is world-writable and ephemeral, which is exactly what we
            # want for a throw-away install container.
            if language == SandboxLanguage.PYTHON:
                cmd = [
                    "sh", "-c",
                    f"mkdir -p /tmp/deps && "
                    f"pip install --quiet --no-cache-dir --target /tmp/deps "
                    f"--index-url {_DEFAULT_PIP_INDEX_URL} "
                    + " ".join(packages),
                ]
                extract_path = "/tmp/deps"
                mount_point = "/deps"
            elif language == SandboxLanguage.TYPESCRIPT:
                cmd = [
                    "sh", "-c",
                    f"mkdir -p /tmp/install && cd /tmp/install && "
                    f"npm install --prefix /tmp/install --no-save --loglevel=error "
                    f"--registry={_DEFAULT_NPM_REGISTRY} "
                    + " ".join(packages),
                ]
                extract_path = "/tmp/install/node_modules"
                mount_point = "/node_modules"
            else:
                raise ValueError(f"Cannot install packages for language: {language}")

            mem_bytes = self.memory_limit_mb * 1024 * 1024
            nano_cpus = int(self.cpu_limit * 1e9)

            container = client.containers.create(
                image=SANDBOX_IMAGE,
                command=cmd,
                environment={},
                mem_limit=mem_bytes,
                nano_cpus=nano_cpus,
                network=network_name,
                network_disabled=False,
                detach=True,
            )
            try:
                container.start()
                exit_info = container.wait(timeout=timeout + 30)
                exit_code = exit_info.get("StatusCode", -1)
                if exit_code != 0:
                    stderr = container.logs(stdout=False, stderr=True).decode(errors="replace")
                    stdout = container.logs(stdout=True, stderr=False).decode(errors="replace")
                    raise RuntimeError(
                        f"Package install failed (exit {exit_code}). "
                        f"stderr: {stderr[:500]} stdout: {stdout[:500]}"
                    )
                deps_tar = _get_archive_bytes(container, extract_path)
                return deps_tar, mount_point
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
        """Create (or re-use) the dedicated egress network and return its name.

        The network is a user-defined bridge, NOT the compose project's
        default network, so sibling services cannot be reached from it.
        """
        name = self.egress_network
        try:
            existing = client.networks.list(names=[name])
            if existing:
                return name
            client.networks.create(
                name=name,
                driver="bridge",
                internal=False,
                labels={"pipeshub.sandbox": "egress"},
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
    # Run phase
    # ------------------------------------------------------------------

    async def _run_container(
        self,
        *,
        image: str,
        command: list[str],
        work_dir: str,
        src_dir: str,
        output_dir: str,
        env: dict[str, str],
        timeout: int,
        deps_tar: bytes | None = None,
        deps_target: str | None = None,
    ) -> ExecutionResult:
        """Create, start, wait, and remove a Docker container.

        Source files are injected via ``put_archive`` and output artifacts
        are extracted via ``get_archive`` -- no host-path volume mounts are
        used, so this works in Docker-in-Docker (sibling container) setups.

        The run container is created with ``network_mode="none"`` AND
        ``network_disabled=True``. This combination ensures the container
        has no routable networking stack even in Docker versions that
        ignore ``network_disabled``.
        """
        try:
            import docker
            from docker.errors import ImageNotFound
        except ImportError:
            return ExecutionResult(
                success=False,
                error="docker Python package is not installed. Install with: pip install docker",
            )

        client = docker.from_env()

        try:
            client.images.get(image)
        except ImageNotFound:
            logger.info("Sandbox image %s not found locally, attempting pull ...", image)
            try:
                client.images.pull(image)
            except Exception as pull_err:
                logger.error("Failed to pull sandbox image %s: %s", image, pull_err)
                return ExecutionResult(
                    success=False,
                    error=(
                        f"Sandbox image '{image}' is not available locally and "
                        f"could not be pulled ({pull_err}). This image is not "
                        f"published to a public registry; build it locally with:\n"
                        f"    docker build -t {image} deployment/sandbox\n"
                        f"or set SANDBOX_DOCKER_IMAGE to an image you have pushed "
                        f"to your own registry."
                    ),
                )

        container = None
        try:
            mem_bytes = self.memory_limit_mb * 1024 * 1024
            nano_cpus = int(self.cpu_limit * 1e9)

            container = client.containers.create(
                image=image,
                command=command,
                environment=env,
                working_dir="/src",
                mem_limit=mem_bytes,
                nano_cpus=nano_cpus,
                network_mode="none",
                network_disabled=self.network_disabled,
                read_only=False,
                tmpfs={"/tmp": "size=100M"},
                detach=True,
            )

            # Inject source files and create output directory inside the
            # container (avoids host-path volume mounts entirely).
            container.put_archive("/src", _tar_directory(src_dir))
            container.put_archive("/", _tar_empty_dir("output", mode=0o777))
            if deps_tar and deps_target:
                # Create the target directory then extract the deps tar into
                # it. The tar from ``get_archive`` has a leading directory
                # entry (the basename of the source path), so we put it at
                # the container root and rely on the directory name being
                # correct by construction.
                container.put_archive("/", _tar_empty_dir(deps_target.lstrip("/"), mode=0o755))
                container.put_archive(deps_target, deps_tar)

            container.start()

            def _blocking_wait():
                exit_info = container.wait(timeout=timeout + 30)
                exit_code = exit_info.get("StatusCode", -1)
                stdout = container.logs(stdout=True, stderr=False).decode(errors="replace")
                stderr = container.logs(stdout=False, stderr=True).decode(errors="replace")
                return exit_code, stdout, stderr

            try:
                exit_code, stdout, stderr = await asyncio.wait_for(
                    asyncio.to_thread(_blocking_wait),
                    timeout=timeout + 5,
                )
            except (asyncio.TimeoutError, asyncio.CancelledError):
                logger.warning("Container execution timed out after %ds, killing container", timeout)
                try:
                    container.kill()
                except Exception:
                    pass
                try:
                    stdout = container.logs(stdout=True, stderr=False).decode(errors="replace")
                    stderr = container.logs(stdout=False, stderr=True).decode(errors="replace")
                except Exception:
                    stdout, stderr = "", ""
                return ExecutionResult(
                    success=False,
                    stdout=stdout,
                    stderr=stderr,
                    exit_code=-1,
                    error=f"Execution timed out after {timeout}s",
                )

            # Pull output artifacts from the container into the local output_dir.
            _extract_container_dir(container, "/output", output_dir)

            return ExecutionResult(
                success=exit_code == 0,
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
            )
        except Exception as exc:
            if container:
                try:
                    container.kill()
                except Exception:
                    pass
            raise
        finally:
            if container:
                try:
                    container.remove(force=True)
                except Exception:
                    pass
            try:
                client.close()
            except Exception:
                pass

    @staticmethod
    def cleanup_execution(execution_id: str) -> None:
        path = os.path.join(_SANDBOX_ROOT, execution_id)
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)

    @staticmethod
    def get_sandbox_root() -> str:
        return _SANDBOX_ROOT


def _now_ms() -> int:
    return int(time.time() * 1000)


# ------------------------------------------------------------------
# Tar helpers for put_archive / get_archive
# ------------------------------------------------------------------

def _tar_directory(src_dir: str) -> bytes:
    """Create an in-memory tar of all files in *src_dir* (flat, no directory entry)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for fname in os.listdir(src_dir):
            tar.add(os.path.join(src_dir, fname), arcname=fname)
    buf.seek(0)
    return buf.read()


def _tar_empty_dir(name: str, *, mode: int = 0o755) -> bytes:
    """Create an in-memory tar containing a single empty directory."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name=name)
        info.type = tarfile.DIRTYPE
        info.mode = mode
        tar.addfile(info)
    buf.seek(0)
    return buf.read()


def _get_archive_bytes(container: object, path: str) -> bytes:
    """Stream ``container.get_archive(path)`` into a bytes blob."""
    bits, _ = container.get_archive(path)
    return b"".join(bits)


# Keep up to 16 MiB of tar data in memory; anything larger spills to disk.
# This avoids OOM on large artifact directories while still being fast for
# the common small-output case.
_TAR_SPOOL_MAX_SIZE = 16 * 1024 * 1024


def _extract_container_dir(container: object, container_path: str, local_dir: str) -> None:
    """Pull a directory from a container via ``get_archive`` and extract to *local_dir*.

    The archive is streamed chunk-by-chunk into a ``SpooledTemporaryFile`` so
    small archives stay in memory while large ones transparently spill to
    disk. This prevents high memory consumption / OOM on large outputs.
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
