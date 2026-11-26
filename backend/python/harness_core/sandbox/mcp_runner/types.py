"""Value types of MCP runners (D23): a pinned STDIO MCP server in a broker sandbox, keyed per owner.

Tenants, servers and owners are opaque strings, so any host (PipesHub, Loom) keys
runners by its own tenancy. Secret values travel only in ``RunnerSecrets``, whose
``repr`` never shows them, and only as far as the runner's process environment.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, override

from harness_core.sandbox.mcp_runner.bridge_program import READY as BRIDGE_READY
from harness_core.sandbox.types import Resources, RuntimeMode

if TYPE_CHECKING:
    from collections.abc import Mapping

# Where the bridge listens inside the runner; reached only through the broker's ``connect``.
BRIDGE_PORT = 7700
BRIDGE_DIR = "/opt/pxh-mcp"
BRIDGE_PATH = f"{BRIDGE_DIR}/bridge.py"
# Label every runner sandbox carries; the sweeper's backstop reap filters on it.
RUNNER_LABEL = "pipeshub.mcpRunner"
# The process pool a runner belongs to, by a name that outlives a restart (its host and service): a restarted pool
# stops what the earlier one left (R36COVER-FU-4).
POOL_LABEL = "pipeshub.mcpRunnerPool"
DEFAULT_HTTP_PORTS: tuple[int, ...] = (80,)


@dataclass(frozen=True, slots=True)
class RunnerKey:
    """One runner per (tenant, server, credential owner): never shared across owners."""

    tenant: str
    server_id: str
    owner: str

    def digest(self) -> str:
        raw = json.dumps([self.tenant, self.server_id, self.owner]).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class RunnerEgressHost:
    """A host the server may reach through the egress proxy; ``upstream`` is a trusted deployment override."""

    host: str
    ports: tuple[int, ...] = DEFAULT_HTTP_PORTS
    upstream: str | None = None


@dataclass(frozen=True, slots=True)
class MCPRunnerSpec:
    """Everything that names a runner's program; never a secret value (only the env *names*)."""

    server_id: str
    image: str
    command: str
    args: tuple[str, ...] = ()
    env_names: frozenset[str] = frozenset()
    egress: tuple[RunnerEgressHost, ...] = ()
    isolation_floor: RuntimeMode = RuntimeMode.DOCKER_HARDENED
    resources: Resources = field(default_factory=lambda: Resources(cpus=0.5, memory_mb=512, pids=128, scratch_mb=64))

    def __post_init__(self) -> None:
        object.__setattr__(self, "args", tuple(self.args))
        object.__setattr__(self, "env_names", frozenset(self.env_names))
        object.__setattr__(self, "egress", tuple(self.egress))

    def digest(self) -> str:
        body = {
            "server": self.server_id,
            "image": self.image,
            "command": self.command,
            "args": list(self.args),
            "env": sorted(self.env_names),
            "egress": [[h.host, list(h.ports), h.upstream] for h in self.egress],
            "floor": self.isolation_floor.value,
            "resources": [self.resources.cpus, self.resources.memory_mb, self.resources.pids, self.resources.scratch_mb],
        }
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()[:32]


class RunnerSecrets:
    """Secret environment values for one runner; compared and hashed by the runner, never shown."""

    __slots__ = ("_values",)

    def __init__(self, values: Mapping[str, str] | None = None) -> None:
        self._values = dict(values or {})

    def names(self) -> frozenset[str]:
        return frozenset(self._values)

    def values(self) -> dict[str, str]:
        return dict(self._values)

    def __bool__(self) -> bool:
        return bool(self._values)

    @override
    def __repr__(self) -> str:
        return f"RunnerSecrets(names={sorted(self._values)})"

    @override
    def __str__(self) -> str:
        return self.__repr__()


@dataclass(frozen=True, slots=True)
class RunnerRequest:
    """One ask for a session. ``principal`` owns the secrets; it must be ``key.owner``."""

    key: RunnerKey
    spec: MCPRunnerSpec
    principal: str
    secrets: RunnerSecrets = field(default_factory=RunnerSecrets)
    # Correlation ids for the runner's logs and sandbox labels (org, run, request); never secrets.
    labels: Mapping[str, str] = field(default_factory=lambda: dict[str, str]())

    def __post_init__(self) -> None:
        object.__setattr__(self, "labels", dict(self.labels))


@dataclass(frozen=True, slots=True)
class RunnerInfo:
    runner_id: str
    key: RunnerKey
    spec_digest: str
    sandbox_id: str | None
    sessions: int
    started_at: float
    last_used: float


@dataclass(frozen=True, slots=True)
class RunnerConfig:
    idle_ttl_s: float = 900.0
    max_age_s: float = 6 * 3600.0
    # Past ``max_age_s + reap_grace_s`` no live pool keeps a runner: an older runner sandbox is an orphan.
    reap_grace_s: float = 3600.0
    ready_timeout_s: float = 30.0
    # Labels on every runner sandbox (a deployment's, or a test's own namespace).
    labels: Mapping[str, str] = field(default_factory=lambda: dict[str, str]())
    # Runners one org keeps in this pool (R36COVER-FU-4). Runners are host sandboxes, so they count against the
    # broker's install ceiling (C2.19) like any other; this cap keeps one org's servers from taking all of it, and a
    # new runner past it replaces that org's least recently used idle one.
    max_per_tenant: int = 10

    def __post_init__(self) -> None:
        object.__setattr__(self, "labels", dict(self.labels))
        if self.idle_ttl_s <= 0 or self.max_age_s <= 0 or self.reap_grace_s < 0 or self.ready_timeout_s <= 0:
            raise ValueError("runner timeouts must be positive")
        if self.max_per_tenant <= 0:
            raise ValueError("max_per_tenant must be positive")


__all__ = [
    "BRIDGE_DIR",
    "BRIDGE_PATH",
    "BRIDGE_PORT",
    "BRIDGE_READY",
    "DEFAULT_HTTP_PORTS",
    "POOL_LABEL",
    "RUNNER_LABEL",
    "MCPRunnerSpec",
    "RunnerConfig",
    "RunnerEgressHost",
    "RunnerInfo",
    "RunnerKey",
    "RunnerRequest",
    "RunnerSecrets",
]
