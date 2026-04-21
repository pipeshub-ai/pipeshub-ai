"""DockerExecutor -- container-based sandbox for production/docker deployments.

Creates ephemeral Docker containers per execution with resource limits,
network isolation, and a mounted output volume for artifact collection.

Uses ``put_archive`` / ``get_archive`` to transfer source files and
output artifacts between the host process and the sandbox container.
This avoids Docker volume mounts, which break in Docker-in-Docker
(sibling container) deployments where the host path does not exist on
the Docker daemon's host filesystem.
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

from app.sandbox.base_executor import BaseExecutor
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


class DockerExecutor(BaseExecutor):
    """Execute code inside ephemeral Docker containers."""

    def __init__(
        self,
        *,
        memory_limit_mb: int = DEFAULT_MEMORY_LIMIT_MB,
        cpu_limit: float = DEFAULT_CPU_LIMIT,
        network_disabled: bool = True,
    ) -> None:
        self.memory_limit_mb = memory_limit_mb
        self.cpu_limit = cpu_limit
        self.network_disabled = network_disabled
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
            cmd = self._build_command(code, lang, src_dir, packages)
            container_env = {**(env or {}), "OUTPUT_DIR": "/output"}

            result = await self._run_container(
                image=SANDBOX_IMAGE,
                command=cmd,
                work_dir=work_dir,
                src_dir=src_dir,
                output_dir=output_dir,
                env=container_env,
                timeout=timeout_seconds,
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
        packages: list[str] | None,
    ) -> list[str]:
        """Write source file and return the container command."""
        safe_packages = validate_packages(packages)
        parts: list[str] = []

        if language == SandboxLanguage.PYTHON:
            script = os.path.join(src_dir, "main.py")
            with open(script, "w") as f:
                f.write(code)
            if safe_packages:
                parts.append(f"pip install --quiet {' '.join(safe_packages)} &&")
            parts.append("python3 /src/main.py")

        elif language == SandboxLanguage.TYPESCRIPT:
            script = os.path.join(src_dir, "main.ts")
            with open(script, "w") as f:
                f.write(code)
            if safe_packages:
                parts.append(f"npm install --save {' '.join(safe_packages)} &&")
            parts.append("npx tsx /src/main.ts")

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
    ) -> ExecutionResult:
        """Create, start, wait, and remove a Docker container.

        Source files are injected via ``put_archive`` and output artifacts
        are extracted via ``get_archive`` — no host-path volume mounts are
        used, so this works in Docker-in-Docker (sibling container) setups.
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
            logger.info("Pulling sandbox image %s ...", image)
            try:
                client.images.pull(image)
            except Exception as pull_err:
                return ExecutionResult(
                    success=False,
                    error=f"Failed to pull image {image}: {pull_err}",
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
                network_disabled=self.network_disabled,
                read_only=False,
                tmpfs={"/tmp": "size=100M"},
                detach=True,
            )

            # Inject source files and create output directory inside the
            # container (avoids host-path volume mounts entirely).
            container.put_archive("/src", _tar_directory(src_dir))
            container.put_archive("/", _tar_empty_dir("output", mode=0o777))

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


def _extract_container_dir(container: object, container_path: str, local_dir: str) -> None:
    """Pull a directory from a container via ``get_archive`` and extract to *local_dir*."""
    try:
        bits, _ = container.get_archive(container_path)
        tar_stream = io.BytesIO(b"".join(bits))
        resolved_root = os.path.realpath(local_dir)
        with tarfile.open(fileobj=tar_stream) as tar:
            prefix = os.path.basename(container_path.rstrip("/")) + "/"
            for member in tar.getmembers():
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
