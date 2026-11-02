"""The integration stack: bring it up, wait for health, seed, and always tear down."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import secrets
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import urlsplit

COMPOSE_FILE = "deployment/docker-compose/docker-compose.integration.{graph}.yml"
STACK_DIR = "integration-tests/stack"
APP_SERVICE = "pipeshub-ai"
LOCKFILES = ["*package-lock.json", "*uv.lock", "*pyproject.toml"]
_BASE_ARG = re.compile(r"^\s*(\w+_IMAGE):\s*\$\{\1:-([^}]+)\}\s*$", re.M)
_SERVICE_LINE = re.compile(r"^  ([\w.-]+):\s*$")
_PORT_LINE = re.compile(r"""^\s+-\s+["']?(?:([\d.]+):)?(\d+):(\d+)(?:/(\w+))?["']?\s*$""")


def ports_from_config(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Published host ports per service, from `docker compose config --format json`."""
    return [
        {"service": name, "host_ip": p.get("host_ip") or "", "published": int(p["published"]), "target": int(p["target"]), "protocol": p.get("protocol") or "tcp"}
        for name, svc in sorted((config.get("services") or {}).items())
        for p in svc.get("ports") or []
        if str(p.get("published", "")).isdigit()
    ]


def ports_from_text(text: str) -> list[dict[str, Any]]:
    """The same from a compose file's short-syntax port lines, for a host without docker; every profile counts."""
    out: list[dict[str, Any]] = []
    service = None
    for line in text.splitlines():
        if m := _SERVICE_LINE.match(line):
            service = m.group(1)
        elif (m := _PORT_LINE.match(line)) and service:
            out.append({"service": service, "host_ip": m.group(1) or "", "published": int(m.group(2)), "target": int(m.group(3)), "protocol": m.group(4) or "tcp"})
    return out


def base_image_args(compose_text: str) -> dict[str, str]:
    """The build args the compose file takes from the environment, with their effective refs."""
    return {n: os.environ.get(n) or d for n, d in _BASE_ARG.findall(compose_text)}


def repo_digest(ref: str, repo_digests: list[str]) -> str | None:
    if "@sha256:" in ref:
        return ref
    name = ref.rsplit(":", 1)[0] if ":" in ref.rsplit("/", 1)[-1] else ref
    return next((d for d in repo_digests if d.split("@", 1)[0] == name), None)


def file_hashes(root: Path, patterns: list[str]) -> dict[str, str]:
    listed = subprocess.run(["git", "ls-files", "-z", "--", *patterns], cwd=root, capture_output=True, text=True, check=True, timeout=60).stdout
    return {f: hashlib.sha256((root / f).read_bytes()).hexdigest() for f in sorted(filter(None, listed.split("\0"))) if (root / f).is_file()}


def image_tag(head: str | None) -> str:
    return f"pipeshub-ai:qa-{(head or 'local')[:12]}"


def project_name(head: str | None, run_id: str, graph: str, variant: str = "") -> str:
    """One compose project per commit, run, stack variant and graph DB, so concurrent runs never share a stack.
    run_id already carries the variant; a fixed QA_COMPOSE_PROJECT does not, so it gets it here."""
    fixed = os.environ.get("QA_COMPOSE_PROJECT")
    prefix = f"{fixed}-{variant}" if fixed and variant else fixed or f"qa-{(head or 'local')[:12]}-{run_id}"
    return re.sub(r"[^a-z0-9_-]", "-", f"{prefix}-{graph}".lower())


def run_credentials(env: Mapping[str, str]) -> dict[str, str]:
    """A fresh admin per managed stack. The prefix meets the product's password rule
    (an upper, a lower, a digit and a symbol); the whole stays well under bcrypt's 72 bytes."""
    return {
        "PIPESHUB_TEST_USER_EMAIL": env.get("PIPESHUB_TEST_USER_EMAIL") or "qa-admin@example.com",
        "PIPESHUB_TEST_USER_PASSWORD": "Qa1!" + secrets.token_urlsafe(18),
    }


def _load(root: Path, name: str) -> ModuleType:
    path = root / STACK_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"qa_stack_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


@dataclass
class Stack:
    root: Path
    mode: str  # managed | external | none
    graph_db: str
    broker: str
    compose_profiles: list[str]
    project: str
    base_url: str
    log_dir: Path
    build: bool = False
    up: bool = False
    events: list[str] = field(default_factory=list)
    extra_env: dict[str, str] = field(default_factory=dict)
    overrides: list[str] = field(default_factory=list)
    environment: dict[str, Any] = field(default_factory=dict)
    port_map: dict[int, int] = field(default_factory=dict)
    _published: list[dict[str, Any]] | None = field(default=None, repr=False)

    @property
    def compose_file(self) -> str:
        return COMPOSE_FILE.format(graph=self.graph_db)

    def env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update({
            "COMPOSE_PROFILES": ",".join(self.compose_profiles),
            "MESSAGE_BROKER": self.broker,
            "PIPESHUB_BASE_URL": self.base_url,
            "TEST_GRAPH_DB_TYPE": self.graph_db,
        })
        env.update(self.extra_env)
        return env

    def compose(self, *args: str) -> list[str]:
        # Overrides let a shared host remap the CI file's fixed host ports without editing it.
        extra = [f for f in os.environ.get("QA_COMPOSE_OVERRIDES", "").split(os.pathsep) if f]
        files = [x for f in [self.compose_file, *extra, *self.overrides] for x in ("-f", f.replace("{graph}", self.graph_db))]
        return ["docker", "compose", "-p", self.project, *files, *args]

    def describe(self) -> list[str]:
        if self.mode == "none":
            return ["no stack"]
        lines = [f"stack mode {self.mode}, graph DB {self.graph_db}, broker {self.broker}, profiles {self.compose_profiles or '-'}"]
        if self.mode == "managed":
            lines.append(f"image {image_tag(self.extra_env.get('GIT_COMMIT'))}" + (" (built here)" if self.build else ""))
            if self.build:
                lines.append(" ".join(self.compose("build")))
            lines.append(" ".join(self.compose("up", "-d")))
        lines.append(f"wait for {self.base_url}/api/v1/health and /api/v1/health/services")
        lines.append(f"seed org at {self.base_url}")
        if self.mode == "managed":
            lines.append(" ".join(self.compose("down", "-v")) + "   (always)")
        return lines

    def _run(self, cmd: list[str], log: str, timeout: int = 1800) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        with (self.log_dir / log).open("a", encoding="utf-8") as fh:
            fh.write("$ " + " ".join(cmd) + "\n")
            fh.flush()
            subprocess.run(cmd, cwd=self.root, env=self.env(), stdout=fh, stderr=subprocess.STDOUT, check=True, timeout=timeout)

    def pin_bases(self) -> None:
        """Build from the digests the base tags point at now; a base with none keeps its tag and is recorded as unpinned."""
        bases: dict[str, str] = {}
        unpinned: list[str] = []
        try:
            compose_text = (self.root / self.compose_file).read_text(encoding="utf-8")
        except OSError:
            compose_text = ""  # no compose file means no base build args, not a crashed run
        for name, ref in base_image_args(compose_text).items():
            digest = None
            try:
                if "@sha256:" not in ref:
                    subprocess.run(["docker", "pull", ref], capture_output=True, timeout=300)  # offline or local-only is fine
                out = subprocess.run(["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", ref], capture_output=True, text=True, timeout=60)
                digest = repo_digest(ref, json.loads(out.stdout or "[]") or []) if out.returncode == 0 else None
            except (ValueError, OSError, subprocess.SubprocessError):
                digest = None
            if digest:
                self.extra_env[name] = digest
                bases[name] = digest
            else:
                unpinned.append(ref)
        dockerfile = (self.root / "Dockerfile").read_text(encoding="utf-8") if (self.root / "Dockerfile").is_file() else ""
        stages: set[str] = set()
        for line in dockerfile.splitlines():
            parts = line.split()
            if not parts or parts[0].upper() != "FROM":
                continue
            ref = next((p for p in parts[1:] if not p.startswith("-")), "")
            if ref and "$" not in ref and ref not in stages and ref not in unpinned:
                unpinned.append(ref)
            if len(parts) > 2 and parts[-2].upper() == "AS":
                stages.add(parts[-1])
        self.environment.update({"base_images": bases, "unpinned": unpinned})

    def published(self) -> list[dict[str, Any]]:
        """Host ports the compose files publish before any remap: base, QA_COMPOSE_OVERRIDES and hook files."""
        if self._published is None:
            try:
                out = subprocess.run(self.compose("config", "--format", "json"), cwd=self.root, env=self.env(), capture_output=True, text=True, timeout=120)
                self._published = ports_from_config(json.loads(out.stdout)) if out.returncode == 0 else None
            except (OSError, subprocess.SubprocessError, ValueError):
                self._published = None
            if self._published is None:
                # shortcut: without docker only the base file's short-syntax ports are read; enough for the CI file.
                try:
                    self._published = ports_from_text((self.root / self.compose_file).read_text(encoding="utf-8"))
                except OSError:
                    self._published = []
        return self._published

    def host_ports(self) -> list[int]:
        return sorted({self.port_map.get(p["published"], p["published"]) for p in self.published()})

    def remap(self, offset: int, work_dir: Path) -> None:
        """Move every published host port by offset, and point the tests at the moved ports."""
        ports = self.published()
        self.port_map = {p["published"]: p["published"] + offset for p in ports}
        bad = sorted(p for p, q in self.port_map.items() if not 0 < q < 65536)
        if bad:
            raise RuntimeError(f"offset {offset} moves ports {bad} out of range")
        hook_file = self.root / STACK_DIR / "ports.py"
        if not hook_file.is_file():
            raise RuntimeError(f"{STACK_DIR}/ports.py is missing, so the tests cannot find remapped ports")
        # JSON cannot carry the !override tag; without it compose appends to a service's ports instead of replacing them.
        lines = ["services:"]
        for svc in sorted({p["service"] for p in ports}):
            lines += [f"  {svc}:", "    ports: !override"]
            lines += [f'      - "{p["host_ip"] + ":" if p["host_ip"] else ""}{self.port_map[p["published"]]}:{p["target"]}/{p["protocol"]}"' for p in ports if p["service"] == svc]
        work_dir.mkdir(parents=True, exist_ok=True)
        path = work_dir / f"ports-{self.graph_db}.yml"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.overrides.append(str(path))
        url = urlsplit(self.base_url)
        if url.hostname in ("localhost", "127.0.0.1") and url.port in self.port_map:
            self.base_url = url._replace(netloc=f"{url.hostname}:{self.port_map[url.port]}").geturl()
        asked: set[int] = set()

        def port(default: int) -> int:
            asked.add(default)
            return self.port_map.get(default, default)
        self.extra_env.update(_load(self.root, "ports").env(port, self.env()))
        unread = sorted(set(self.port_map) - asked - {url.port})
        self.events.append(f"ports moved by {offset}" + (f"; no test setting names {unread}" if unread else ""))

    def record_environment(self) -> None:
        """Image id, lockfile and compose hashes; best effort, never fails the run."""
        tag = image_tag(self.extra_env.get("GIT_COMMIT"))
        try:
            ident = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", tag], capture_output=True, text=True, timeout=60)
            digest = ident.stdout.strip() if ident.returncode == 0 and ident.stdout.strip() else None
            self.environment.update({
                "image": tag, "image_digest": digest,
                "lockfiles": file_hashes(self.root, LOCKFILES),
                "compose": {self.compose_file: hashlib.sha256((self.root / self.compose_file).read_bytes()).hexdigest()},
            })
        except (OSError, subprocess.SubprocessError) as exc:
            self.events.append(f"environment not recorded: {exc}")

    def start(self, health_timeout: float = 300) -> None:
        if self.mode == "none":
            return
        if self.mode == "managed":
            if self.build:
                self.pin_bases()
                self._run(self.compose("build"), f"stack-{self.graph_db}.log", timeout=3600)
            self.up = True
            self._run(self.compose("up", "-d"), f"stack-{self.graph_db}.log")
            self.record_environment()
            self.events.append("compose up")
        pending = _load(self.root, "health").wait(self.base_url, health_timeout)
        if pending:
            raise RuntimeError(f"stack unhealthy after {health_timeout:.0f}s: {', '.join(pending)}")
        self.events.append("healthy")
        email = os.environ.get("PIPESHUB_TEST_USER_EMAIL")
        password = os.environ.get("PIPESHUB_TEST_USER_PASSWORD")
        if not email or not password:
            raise RuntimeError("PIPESHUB_TEST_USER_EMAIL / PIPESHUB_TEST_USER_PASSWORD are not set; cannot seed")
        self.events.append("seed: " + _load(self.root, "seed").seed(self.base_url, email, password))

    def collect_logs(self) -> None:
        if self.mode == "managed" and self.up:
            try:
                self._run(self.compose("logs", "--no-color"), f"compose-{self.graph_db}.log", timeout=300)
            except (subprocess.SubprocessError, OSError) as exc:
                self.events.append(f"log collection failed: {exc}")

    def stop(self) -> None:
        if self.mode == "managed" and self.up:
            try:
                self._run(self.compose("down", "-v", "--remove-orphans"), f"stack-{self.graph_db}.log", timeout=600)
                self.events.append("compose down")
            except (subprocess.SubprocessError, OSError) as exc:
                self.events.append(f"teardown failed: {exc}")
                print(f"qa: teardown failed: {exc}", file=sys.stderr)
            self.up = False
