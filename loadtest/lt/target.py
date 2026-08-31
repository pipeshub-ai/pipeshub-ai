"""Locating the stack under test and talking to its container.

Both supported deployments (the prebuilt `docker-compose.yml` image and the
hot-reload dev compose) run every service as sibling processes inside a single
container, so per-container CPU/memory says nothing about connector-vs-indexing.
Everything here is therefore process-oriented, not container-oriented.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

AGENT = Path(__file__).with_name("agent.py")

# Sibling containers worth sampling; the app container is sampled per-process
# by the in-container agent instead.
STATEFUL_CONTAINERS = ("arango", "neo4j", "qdrant", "redis", "mongodb", "kafka-1", "etcd")


class TargetError(RuntimeError):
    pass


@dataclass
class Target:
    base_url: str
    container: str
    docker: str = "docker"
    extra_containers: list[str] = field(default_factory=list)

    def run(self, args: list[str], *, timeout: int = 60, check: bool = True) -> str:
        proc = subprocess.run(
            [self.docker, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if check and proc.returncode != 0:
            raise TargetError(
                f"docker {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}"
            )
        return proc.stdout

    def exec(self, args: list[str], *, privileged: bool = False, timeout: int = 60, check: bool = True) -> str:
        flags = ["exec"]
        if privileged:
            flags.append("--privileged")
        return self.run([*flags, self.container, *args], timeout=timeout, check=check)

    def copy_out(self, remote: str, local: Path) -> None:
        local.parent.mkdir(parents=True, exist_ok=True)
        self.run(["cp", f"{self.container}:{remote}", str(local)])

    def running_containers(self) -> list[str]:
        out = self.run(["ps", "--format", "{{.Names}}"])
        return [line.strip() for line in out.splitlines() if line.strip()]

    def stateful_containers(self) -> list[str]:
        running = set(self.running_containers())
        found = [c for c in STATEFUL_CONTAINERS if c in running]
        return found + [c for c in self.extra_containers if c in running and c not in found]

    def discover_processes(self) -> dict[int, str]:
        """pid -> service label, as seen from inside the app container."""
        script = AGENT.read_text(encoding="utf-8")
        proc = subprocess.run(
            [self.docker, "exec", "-i", self.container, "python3", "-", "1", "0"],
            input=script,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            raise TargetError(f"process discovery failed: {proc.stderr.strip()}")
        for line in proc.stdout.splitlines():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if payload.get("type") == "discovery":
                return {int(k): v for k, v in payload["pids"].items()}
        raise TargetError("process discovery produced no result")

    def reset_backend_timing(self) -> None:
        self.exec(["rm", "-f", "/tmp/lt-backend-timing.jsonl"], check=False)

    def collect_backend_timing(self, dest: Path) -> bool:
        try:
            self.copy_out("/tmp/lt-backend-timing.jsonl", dest)
            return dest.exists() and dest.stat().st_size > 0
        except TargetError:
            return False

    def count_log_matches(self, pattern: str, since_seconds: int) -> int:
        """Occurrences of `pattern` in the app container's log over the window.

        Used to spot upstream throttling: when a SaaS source rate-limits, the
        connector's CPU flattens out and that looks exactly like saturation.
        The distinction matters, so it is measured rather than assumed.
        """
        try:
            out = self.run(
                ["logs", "--since", f"{max(int(since_seconds), 1)}s", self.container],
                timeout=120,
                check=False,
            )
        except Exception:
            return -1
        import re

        return len(re.findall(pattern, out, flags=re.IGNORECASE))

    def preflight(self) -> list[str]:
        """Return a list of problems; empty means good to go."""
        problems: list[str] = []
        if shutil.which(self.docker) is None:
            return [f"`{self.docker}` not found on PATH"]
        try:
            running = self.running_containers()
        except TargetError as exc:
            return [f"cannot talk to the docker daemon: {exc}"]
        if self.container not in running:
            problems.append(
                f"container `{self.container}` is not running (running: {', '.join(running) or 'none'})"
            )
            return problems
        try:
            found = self.discover_processes()
        except TargetError as exc:
            problems.append(str(exc))
            return problems
        if "connector" not in found.values():
            problems.append(
                "no connector service process found inside the container "
                "(looked for a process whose cmdline contains app.connectors_serve "
                "or app.connectors_main)"
            )
        return problems
