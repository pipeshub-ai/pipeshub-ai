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
  qdrant, neo4j, ...) are unreachable by service name. A bridge does not
  filter by address, so the install container first installs the egress
  firewall (``agent_loop_lib/sandbox/coding/egress_firewall.py``) that
  rejects private, link-local/metadata and bridge addresses, then drops to
  the unprivileged ``sandbox`` user; ``SANDBOX_EGRESS_ALLOW_CIDRS`` admits
  an internal package mirror. If the firewall can't be installed the
  install fails rather than running unfiltered.
- The install container writes deps to ``/deps`` (Python ``pip install
  --target``) or ``/install/node_modules`` (``npm install --prefix``).
  Those deps are tarred via ``get_archive`` and injected into the run
  container, which stays fully offline.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import os
import shutil
import tarfile
import tempfile
import time
from uuid import uuid4

from app.agent_loop_lib.sandbox.coding.credentials import (
    ProxiedRegistry,
    Registry,
    credentialed_needs_broker,
    database,
    registry,
)
from app.agent_loop_lib.sandbox.coding.egress_firewall import (
    CONTAINER_HARDENING,
    create_sandbox_container,
    ensure_egress_network_sync,
    firewall_unavailable,
    firewalled_container_kwargs,
    limited,
    new_firewall_token,
    parse_cidrs,
)
from app.harness.sandbox.docker_client import proxied_docker_client
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
from app.sandbox.roots import DOCKER, ensure_private_dir, sandbox_root
from harness_core.archive_reader import ArchiveRefused, spool_chunks

logger = logging.getLogger(__name__)

def _sandbox_root() -> str:
    """The running user's own folder, never a shared name another user could own or plant (R13-ROOT). Read at each
    use, as the artifact reader reads it, so a temp folder that moves after import moves both."""
    return sandbox_root(DOCKER)

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
        self.egress_allow_cidrs = parse_cidrs(os.environ.get("SANDBOX_EGRESS_ALLOW_CIDRS"))
        _ = ensure_private_dir(_sandbox_root())

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
        work_dir = os.path.join(_sandbox_root(), execution_id)
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

            refused = credentialed_database(lang, env)
            if refused is not None:
                return ExecutionResult(success=False, error=refused, execution_time_ms=_now_ms() - start_ms)
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

    def _build_command(self, code: str, language: SandboxLanguage, src_dir: str) -> list[str]:
        return write_run_command(code, language, src_dir)

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
            import docker  # noqa: F401 - whether docker-py is installed
            from docker.errors import (
                ImageNotFound,  # noqa: F401 (re-exported for tests)
            )
        except ImportError as exc:
            raise RuntimeError(
                "docker Python package is not installed. Install with: pip install docker"
            ) from exc

        client = proxied_docker_client()
        try:
            egress_cidrs = ensure_egress_network_sync(
                client, self.egress_network, {"pipeshub.sandbox": "egress"},
            )
            network_name = self.egress_network

            found = registry_for(language)
            if found is not None and found.credential is not None:
                raise RuntimeError(f"Package install refused: {credentialed_needs_broker('package registry')}")
            cmd, extract_path, mount_point = install_command(packages, language)

            # Fail closed: pip builds sdists and npm runs lifecycle scripts,
            # so an unfiltered install would hand package code the host,
            # private ranges and cloud metadata.
            if not egress_cidrs:
                raise RuntimeError(
                    "Package install refused: the egress network's subnet could not be "
                    "read, so the egress firewall cannot be installed"
                )
            token = new_firewall_token()
            command_kwargs = firewalled_container_kwargs(
                cmd, network_cidrs=egress_cidrs, allowed_cidrs=self.egress_allow_cidrs, token=token,
            )
            deps_tar = self._install_once(
                client, network_name, language, extract_path, timeout, command_kwargs, token,
            )
            return deps_tar, mount_point
        finally:
            try:
                client.close()
            except Exception:
                pass

    def _install_once(
        self,
        client: object,
        network_name: str,
        language: SandboxLanguage,
        extract_path: str,
        timeout: int,
        command_kwargs: dict,
        token: str,
    ) -> bytes:
        """Deps tar from one firewalled install container."""
        container = create_sandbox_container(
            client,
            image=SANDBOX_IMAGE,
            environment={},
            **limited(self.memory_limit_mb * 1024 * 1024),
            nano_cpus=int(self.cpu_limit * 1e9),
            network=network_name,
            network_disabled=False,
            detach=True,
            **command_kwargs,
        )
        try:
            container.start()
            exit_info = container.wait(timeout=timeout + 30)
            exit_code = exit_info.get("StatusCode", -1)
            if exit_code != 0:
                stderr = container.logs(stdout=False, stderr=True).decode(errors="replace")
                stdout = container.logs(stdout=True, stderr=False).decode(errors="replace")
                if firewall_unavailable(exit_code, stderr, token):
                    raise RuntimeError(
                        f"Package install refused: sandbox image {SANDBOX_IMAGE} cannot install "
                        "the egress firewall, and packages are never installed on an unfiltered "
                        f"network; use a sandbox image that includes iptables. {stderr[:300]}"
                    )
                raise RuntimeError(
                    f"Package install failed (exit {exit_code}). "
                    f"stderr: {stderr[:500]} stdout: {stdout[:500]}"
                )
            return _get_archive_bytes(container, extract_path)
        finally:
            try:
                container.remove(force=True, v=True)
            except Exception:
                pass

    def _ensure_egress_network(self, client: object) -> str:
        """Create (or re-use) the dedicated egress network and return its name.

        The network is a user-defined bridge, NOT the compose project's
        default network, with inter-container traffic off.
        """
        ensure_egress_network_sync(client, self.egress_network, {"pipeshub.sandbox": "egress"})
        return self.egress_network

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
            import docker  # noqa: F401 - whether docker-py is installed
            from docker.errors import ImageNotFound
        except ImportError:
            return ExecutionResult(
                success=False,
                error="docker Python package is not installed. Install with: pip install docker",
            )

        client = proxied_docker_client()

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

            container = create_sandbox_container(
                client,
                image=image,
                command=command,
                environment=env,
                working_dir="/src",
                **limited(mem_bytes),
                nano_cpus=nano_cpus,
                network_mode="none",
                network_disabled=self.network_disabled,
                tmpfs={"/tmp": "size=100M"},
                detach=True,
                **CONTAINER_HARDENING,
            )

            # The root is read-only: /src, /output and the deps target are the container's own volumes
            # (``limited``), so the files go straight in (no host-path volume mounts).
            container.put_archive("/src", _tar_directory(src_dir))
            if deps_tar and deps_target:
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
            except asyncio.CancelledError:
                # Stop the container, then let the cancellation through (it is not a timeout).
                with contextlib.suppress(Exception):
                    container.kill()
                raise
            except asyncio.TimeoutError:
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
        except Exception:
            if container:
                try:
                    container.kill()
                except Exception:
                    pass
            raise
        finally:
            if container:
                try:
                    container.remove(force=True, v=True)
                except Exception:
                    pass
            try:
                client.close()
            except Exception:
                pass

    @staticmethod
    def cleanup_execution(execution_id: str) -> None:
        path = os.path.join(_sandbox_root(), execution_id)
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)

    @staticmethod
    def get_sandbox_root() -> str:
        return _sandbox_root()


def credentialed_database(language: SandboxLanguage, env: dict[str, str] | None) -> str | None:
    """Why a PostgreSQL run cannot happen here: its password is added by the egress proxy, which this executor has
    not (``None``: nothing to refuse)."""
    url = (env or {}).get("DATABASE_URL")
    if language != SandboxLanguage.POSTGRESQL or not url or database(url).password is None:
        return None
    return credentialed_needs_broker("database")


def write_run_command(code: str, language: SandboxLanguage, src_dir: str) -> list[str]:
    """Write *code* as the source file under *src_dir* (seen as ``/src``) and return the run command.

    Shared by every executor that runs the image's own toolchain, so a run is
    the same command wherever it happens.
    """
    if language == SandboxLanguage.PYTHON:
        name, run = "main.py", "python3 /src/main.py"
    elif language == SandboxLanguage.TYPESCRIPT:
        name, run = "main.ts", "npx tsx /src/main.ts"
    elif language == SandboxLanguage.SQLITE:
        name, run = "query.sql", "sqlite3 -header -csv /tmp/sandbox.db < /src/query.sql"
    elif language == SandboxLanguage.POSTGRESQL:
        name, run = "query.sql", 'psql "$DATABASE_URL" -f /src/query.sql --csv'
    else:
        raise ValueError(f"Unsupported language: {language}")
    with open(os.path.join(src_dir, name), "w") as f:
        f.write(code)
    return ["sh", "-c", run]


def registry_for(language: SandboxLanguage) -> Registry | None:
    """The package registry an install of *language* uses, split from any credential it carries."""
    if language == SandboxLanguage.PYTHON:
        return registry(_DEFAULT_PIP_INDEX_URL)
    if language == SandboxLanguage.TYPESCRIPT:
        return registry(_DEFAULT_NPM_REGISTRY)
    return None


def install_command(
    packages: list[str], language: SandboxLanguage, proxied: ProxiedRegistry | None = None
) -> tuple[list[str], str, str]:
    """``(command, path the deps land in, where the run sees them)`` for validated *packages*; ``proxied``: the
    registry is reached through the egress proxy, which adds its credential."""
    if language == SandboxLanguage.PYTHON:
        index = proxied.pip_url() if proxied is not None else registry(_DEFAULT_PIP_INDEX_URL).url
        command = [
            "sh", "-c",
            f"pip install --quiet --no-cache-dir --target /deps "
            f"--index-url {index} "
            + " ".join(packages),
        ]
        return (proxied.command(command) if proxied else command), "/deps", "/deps"
    if language == SandboxLanguage.TYPESCRIPT:
        auth = f"{proxied.npm_auth_flag()} " if proxied is not None else ""
        command = [
            "sh", "-c",
            f"mkdir -p /install && cd /install && "
            f"npm install --prefix /install --no-save --loglevel=error "
            f"--registry={registry(_DEFAULT_NPM_REGISTRY).url} {auth}"
            + " ".join(packages),
        ]
        return (proxied.command(command) if proxied else command), "/install/node_modules", "/node_modules"
    raise ValueError(f"Cannot install packages for language: {language}")


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




def _get_archive_bytes(container: object, path: str) -> bytes:
    """Stream ``container.get_archive(path)`` into a bytes blob."""
    bits, _ = container.get_archive(path)
    return b"".join(bits)


# Keep up to 16 MiB of tar data in memory; anything larger spills to disk.
# This avoids OOM on large artifact directories while still being fast for
# the common small-output case.
_TAR_SPOOL_MAX_SIZE = 16 * 1024 * 1024
# The sandbox fills the folder it hands back: its archive is bounded as the broker bounds one (MAX_ARCHIVE_BYTES).
MAX_EXTRACT_BYTES = 4 * 1024**3


def _extract_container_dir(container: object, container_path: str, local_dir: str) -> None:
    """Pull a directory from a container via ``get_archive`` and extract to *local_dir*.

    The archive is streamed chunk-by-chunk into a ``SpooledTemporaryFile`` so
    small archives stay in memory while large ones transparently spill to
    disk. This prevents high memory consumption / OOM on large outputs.

    Only regular files are extracted. A symlink (or hardlink/device) member is
    never a legitimate artifact and, left on the host, would be followed by the
    next run's readers and copy host files back into the sandbox (SB-5), so
    non-regular members are dropped and ``filter="data"`` sanitises the rest.
    """
    try:
        bits, _ = container.get_archive(container_path)
        with tempfile.SpooledTemporaryFile(max_size=_TAR_SPOOL_MAX_SIZE) as tar_stream:
            try:
                spool_chunks(bits, tar_stream, max_bytes=MAX_EXTRACT_BYTES)
            except ArchiveRefused:
                logger.warning("Not extracting %s: its archive is over %d bytes", container_path, MAX_EXTRACT_BYTES)
                return
            resolved_root = os.path.realpath(local_dir)
            with tarfile.open(fileobj=tar_stream, mode="r|*") as tar:
                prefix = os.path.basename(container_path.rstrip("/")) + "/"
                for member in tar:
                    if member.isdir():
                        continue
                    if not member.isfile():
                        logger.warning(
                            "Skipping non-regular tar member %r (type %r) from %s",
                            member.name, member.type, container_path,
                        )
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
                    tar.extract(member, local_dir, filter="data")
    except Exception:
        logger.debug("No output artifacts to extract from container %s", container_path)
