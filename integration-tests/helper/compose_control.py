"""Restart, stop and poke the services of the running integration stack.

The resilience tests break a dependency on purpose while the product is busy,
so they need the same ``docker compose`` handle the workflow used to start the
stack. By default that is the integration compose file for the graph backend
under test, run from its own directory, which is what the workflow does:

    RESILIENCE_COMPOSE_FILE     compose file (default: the integration file for
                                TEST_GRAPH_DB_TYPE under deployment/docker-compose)
    RESILIENCE_COMPOSE_PROJECT  compose project name, when the stack was started
                                with ``-p`` (default: compose's own, the file's folder)

Anything that stops the handle from working (no docker CLI, no daemon, a
different stack, the service not running) is a :class:`ComposeUnavailable`
with the reason, which the tests turn into a skip.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_DIR = REPO_ROOT / "deployment" / "docker-compose"


def graph_service() -> str:
    """The graph database's compose service in the stack for TEST_GRAPH_DB_TYPE."""
    return {"neo4j": "neo4j", "arango": "arango"}[os.getenv("TEST_GRAPH_DB_TYPE", "neo4j").lower()]


class ComposeUnavailable(RuntimeError):
    """The stack cannot be controlled from here; the message says why."""


@dataclass(frozen=True)
class ComposeStack:
    compose_file: Path
    project: str | None = None
    command_timeout: float = 180

    @classmethod
    def from_env(cls) -> "ComposeStack":
        raw = os.getenv("RESILIENCE_COMPOSE_FILE")
        graph = os.getenv("TEST_GRAPH_DB_TYPE", "neo4j").lower()
        compose_file = Path(raw) if raw else COMPOSE_DIR / f"docker-compose.integration.{graph}.yml"
        if not compose_file.is_file():
            raise ComposeUnavailable(f"compose file {compose_file} does not exist (set RESILIENCE_COMPOSE_FILE)")
        if shutil.which("docker") is None:
            raise ComposeUnavailable("the docker CLI is not on PATH")
        return cls(compose_file.resolve(), os.getenv("RESILIENCE_COMPOSE_PROJECT") or None)

    def _base(self) -> list[str]:
        command = ["docker", "compose", "-f", str(self.compose_file)]
        if self.project:
            command += ["-p", self.project]
        return command

    def _run(self, args: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        command = [*self._base(), *args]
        try:
            result = subprocess.run(  # noqa: S603 - fixed argv, no shell
                command,
                cwd=self.compose_file.parent,
                capture_output=True,
                text=True,
                timeout=self.command_timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ComposeUnavailable(f"`{' '.join(command)}` did not run: {exc}") from exc
        if check and result.returncode != 0:
            raise ComposeUnavailable(
                f"`{' '.join(command)}` exited {result.returncode}: {(result.stderr or result.stdout).strip()}"
            )
        return result

    def running_services(self) -> set[str]:
        output = self._run(["ps", "--status", "running", "--services"]).stdout
        return {line.strip() for line in output.splitlines() if line.strip()}

    def require(self, *services: str) -> None:
        """Raise unless every one of ``services`` is running in this stack."""
        missing = set(services) - self.running_services()
        if missing:
            raise ComposeUnavailable(
                f"service(s) {sorted(missing)} are not running in the stack of {self.compose_file}"
                + (f" (project {self.project})" if self.project else "")
            )

    def restart(self, service: str) -> None:
        self._run(["restart", service])

    def stop(self, service: str) -> None:
        """Stop ``service`` and confirm it is down, so a caller knows the outage has really begun."""
        self._run(["stop", service])
        if service in self.running_services():
            raise ComposeUnavailable(f"{service} was stopped but still reports running")

    def start(self, service: str) -> None:
        self._run(["start", service])

    def exec(self, service: str, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return self._run(["exec", "-T", service, *command])

    def container_id(self, service: str) -> str:
        container = self._run(["ps", "-q", service]).stdout.strip()
        if not container:
            raise ComposeUnavailable(f"service {service} has no container")
        return container.splitlines()[0]

    def health(self, service: str) -> str:
        """``healthy``, ``unhealthy`` or ``starting``; ``running`` for a service with no health check."""
        result = subprocess.run(  # noqa: S603, S607 - fixed argv, no shell
            ["docker", "inspect", "--format", "{{json .State}}", self.container_id(service)],
            capture_output=True, text=True, timeout=self.command_timeout, check=False,
        )
        if result.returncode != 0:
            return "missing"
        state = json.loads(result.stdout)
        health = state.get("Health") or {}
        return health.get("Status") or state.get("Status", "unknown")

    def wait_ready(
        self,
        service: str,
        *,
        probe: Sequence[str] | None = None,
        timeout: float = 180,
        interval: float = 2,
    ) -> None:
        """Block until ``service`` passes its health check or, lacking one, ``probe`` run inside it.

        A container with no health check reads "running" as soon as its process
        starts, before it accepts connections, so "running" alone never counts.
        """
        deadline = time.monotonic() + timeout
        status = "unknown"
        while time.monotonic() < deadline:
            status = self.health(service)
            if status == "healthy":
                return
            if status == "running":
                if probe is None:
                    raise ComposeUnavailable(
                        f"{service} has no health check; pass a probe command that proves it is serving"
                    )
                if self._run(["exec", "-T", service, *probe], check=False).returncode == 0:
                    return
                status = "running, probe failing"
            time.sleep(interval)
        raise TimeoutError(f"{service} was still {status!r} {timeout:.0f}s after the fault")
