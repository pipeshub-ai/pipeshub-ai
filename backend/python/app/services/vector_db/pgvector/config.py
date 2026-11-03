from __future__ import annotations

import logging
import math
import os
import re
from dataclasses import dataclass, field

from app.core.db import DbConfig
from app.services.vector_db.pgvector.tuning import Tuning, parse_flag

_LOGIN = re.compile(r"^pipeshub_(node|connectors|indexing|query)_login$")


def _service_of(dsn: str) -> str:
    """The unit of work switches to the role of the service this login belongs to (``pipeshub_<svc>_login``)."""
    user = re.match(r"^[a-z0-9+]+://([^:@/]+)", dsn)
    m = _LOGIN.match(user.group(1)) if user else None
    return m.group(1) if m else "query"


@dataclass(frozen=True)
class PgVectorConfig:
    dsn: str
    # Optional credential of the jobs role: creating or dropping a collection.
    # Without it those calls fail with the command that performs them.
    jobs_dsn: str = ""
    # The service whose role the units of work take. Default: the one the login in ``dsn`` belongs to.
    service: str = ""
    pool_max: int = 10
    # Refuse a pgvector below 0.8.2 instead of degrading.
    strict: bool = False
    # pgbouncer_txn (default), azure_pgbouncer, pgbouncer_session or none: see app.core.db.DbConfig.
    pooler: str = "pgbouncer_txn"
    # Write units assert the deployment-profile epoch read at start (needs platform.deployment_profile).
    fence: bool = True
    # own_leaf (default): an org's leaf is created before its first vector write, by the placement worker; none: no placement
    # policy (rows stay in the shared tree: tests, or a deployment whose own policy decides).
    placement: str = "own_leaf"
    # Seconds a first write waits for the placement worker.
    placement_wait: float = 30.0
    # Seconds a catalog lookup (collection id, an org's spaces) is reused.
    catalog_ttl: float = 5.0
    # Search tiers, admission, timeouts and the recall sampler: see tuning.py (each value has a default and a fallback).
    search: Tuning = field(default_factory=Tuning)

    @classmethod
    def from_dict(cls, data: dict | None) -> PgVectorConfig:
        data = data or {}
        # The process's own PGVECTOR_DSN (the service's login, set by process_monitor.sh) wins over a `dsn` in the config store, which
        # would otherwise put every service back on one login.
        env_dsn = os.getenv("PGVECTOR_DSN") or ""
        if env_dsn and data.get("dsn") and data["dsn"] != env_dsn:
            logging.getLogger(__name__).warning(
                "pgvector: ignoring 'dsn' under /services/pgvector; this process's PGVECTOR_DSN (its own login) is used"
            )
        dsn = env_dsn or data.get("dsn") or ""
        # The jobs login comes from the process's environment only: every process can read the config store, so a value there would
        # hand the jobs credential to a process that was started without it.
        jobs_dsn = os.getenv("PGVECTOR_JOBS_DSN") or ""
        if data.get("jobs_dsn"):
            logging.getLogger(__name__).warning(
                "pgvector: ignoring 'jobs_dsn' under /services/pgvector; the jobs login is read from PGVECTOR_JOBS_DSN only"
            )
        if not dsn:
            raise ValueError(
                "pgvector configuration not found "
                "(expected 'dsn' under /services/pgvector or PGVECTOR_DSN)"
            )
        def pick(key: str, default: object) -> object:
            """A stored null means "not set" (the default), never False or 0."""
            value = data.get(key)
            return default if value is None else value

        placement = str(pick("placement", os.getenv("PGVECTOR_PLACEMENT") or "own_leaf"))
        if placement not in ("own_leaf", "none"):
            raise ValueError(f"pgvector placement must be 'own_leaf' or 'none', not {placement!r}")
        def number(key: str, default: float) -> float:
            raw = pick(key, default)
            try:
                value = float(raw)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                raise ValueError(f"pgvector {key} must be a number of seconds, not {raw!r}") from None
            return value

        placement_wait, catalog_ttl = number("placement_wait", 30.0), number("catalog_ttl", 5.0)
        if not math.isfinite(placement_wait) or placement_wait <= 0:
            raise ValueError(f"pgvector placement_wait must be a positive number of seconds, not {placement_wait!r}")
        if not math.isfinite(catalog_ttl) or catalog_ttl < 0:
            raise ValueError(f"pgvector catalog_ttl must be zero or a positive number of seconds, not {catalog_ttl!r}")
        raw_pool = pick("pool_max", pick("max_size", 10))
        try:
            pool_max = int(raw_pool)  # type: ignore[call-overload]
        except (TypeError, ValueError):
            raise ValueError(f"pgvector pool_max must be a whole number of at least 1, not {raw_pool!r}") from None
        if pool_max < 1:
            raise ValueError(f"pgvector pool_max must be a whole number of at least 1, not {raw_pool!r}")
        return cls(
            dsn=dsn,
            jobs_dsn=jobs_dsn,
            service=str(pick("service", os.getenv("PGVECTOR_SERVICE") or _service_of(dsn))),
            pool_max=pool_max,
            strict=parse_flag("strict (PGVECTOR_STRICT)", pick("strict", os.getenv("PGVECTOR_STRICT")), default=False),
            pooler=str(pick("pooler", os.getenv("PGVECTOR_POOLER") or "pgbouncer_txn")),
            fence=parse_flag("fence (PGVECTOR_FENCE)", pick("fence", os.getenv("PGVECTOR_FENCE")), default=True),
            placement=placement,
            placement_wait=placement_wait,
            catalog_ttl=catalog_ttl,
            search=Tuning.from_dict(data.get("search") if isinstance(data.get("search"), dict) else None),
        )

    def to_db_config(self) -> DbConfig:
        return DbConfig(
            service=self.service or _service_of(self.dsn),
            dsn=self.dsn,
            jobs_dsn=self.jobs_dsn,
            pooler=self.pooler,  # type: ignore[arg-type]
            pool_max=self.pool_max,
            strict=True,        # the kernel's own floors (PostgreSQL, pg_trgm, pgvector); self.strict is the stricter pgvector floor
            fence=self.fence,
            want_extensions=("vector",),
        )
