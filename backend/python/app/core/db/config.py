"""Runtime configuration. Nothing here is read unless a feature builds a ``KernelDB``."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Literal

from app.core.db.errors import DbConfigError

Pooler = Literal["pgbouncer_txn", "none", "azure_pgbouncer", "pgbouncer_session"]
_POOLERS = ("pgbouncer_txn", "none", "azure_pgbouncer", "pgbouncer_session")
_SERVICE = re.compile(r"[a-z][a-z0-9_]{0,30}")


@dataclass(frozen=True, slots=True)
class TimeoutClass:
    statement_ms: int
    lock_ms: int
    work_mem_kb: int | None = None


# Defaults by service class. An org limit can lower them, never raise them.
TIMEOUT_CLASSES: dict[str, TimeoutClass] = {
    "query": TimeoutClass(5_000, 2_000),
    "api": TimeoutClass(15_000, 2_000),
    "indexing": TimeoutClass(60_000, 5_000),
    "jobs": TimeoutClass(15 * 60_000, 5_000),
}
_CLASS_OF_SERVICE = {"query": "query", "indexing": "indexing", "connectors": "indexing"}


@dataclass(frozen=True, slots=True)
class DbConfig:
    service: str  # logical service; the UoW switches to role ``pipeshub_<service>``
    dsn: str = field(repr=False)
    jobs_dsn: str = field(default="", repr=False)  # only processes that run jobs get one
    pooler: Pooler = "pgbouncer_txn"
    pool_max: int = 10
    jobs_pool_max: int = 3
    pool_idle_seconds: float = 300.0  # an idle connection older than this is closed (asyncpg's own default)
    pool_min: int = 1  # connections a pool keeps open when idle; 0 lets an idle process hold none
    strict: bool = True
    fence: bool = True  # write UoWs assert the deployment-profile epoch read at start
    want_extensions: tuple[str, ...] = ()
    app_version: str = "dev"
    slow_statement_ms: int = 500

    def __post_init__(self) -> None:
        if not _SERVICE.fullmatch(self.service):
            raise DbConfigError(f"invalid service name {self.service!r}")
        if not self.dsn:
            raise DbConfigError("no database DSN configured")
        if self.pool_max < 1 or self.jobs_pool_max < 1 or self.pool_idle_seconds < 0 or self.pool_min < 0:
            raise DbConfigError("pool sizes must be at least 1, the idle time and the minimum not negative")
        if self.pooler not in _POOLERS:
            raise DbConfigError(f"pooler must be one of {_POOLERS}, got {self.pooler!r}")

    @property
    def role(self) -> str:
        return f"pipeshub_{self.service}"

    @property
    def timeout_class(self) -> str:
        return _CLASS_OF_SERVICE.get(self.service, "api")

    @property
    def statement_cache_size(self) -> int:
        # Only a direct connection may cache prepared statements. Behind any pooler the cache is off: a pooler that
        # does not track them (max_prepared_statements = 0, e.g. managed ones) breaks only under load, not at start-up.
        # Measured cost of turning the cache off: about 45% throughput (a revisit signal for psycopg 3).
        return 100 if self.pooler == "none" else 0

    @classmethod
    def from_env(cls, service: str) -> DbConfig:
        env = os.environ
        return cls(
            service=service,
            dsn=env.get("PG_CORE_URL", ""),
            jobs_dsn=env.get("PG_JOBS_URL", ""),
            pooler=(env.get("PG_POOLER") or "pgbouncer_txn").strip(),  # type: ignore[arg-type]
            strict=env.get("PG_STRICT", "true").strip().lower() not in ("0", "false", "no"),
            pool_max=int(env.get("PG_POOL_MAX") or 10),
            jobs_pool_max=int(env.get("PG_JOBS_POOL_MAX") or 3),
            pool_idle_seconds=float(env.get("PG_POOL_IDLE_SECONDS") or 300),
            pool_min=int(env.get("PG_POOL_MIN") or 1),
        )
