"""The collection catalog and the per-org embedding spaces, through the vetted catalog functions (migration ``retrieval_0001``).

Reads run as the service role in a read unit of work and are cached for ``catalog_ttl`` seconds. Writes (a new collection, an
org's first space, dropping a collection) are jobs paths: they need the jobs credential (``PgVectorConfig.jobs_dsn``) and name
the operator command when it is missing.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import asyncpg

from app.core import org_context
from app.core.db import DbConfigError
from app.services.vector_db.pgvector.errors import (
    CollectionAlreadyExistsError,
    CollectionNotFoundError,
    PgVectorError,
)
from app.services.vector_db.pgvector.scope import catalog_scope, org_scope

if TYPE_CHECKING:
    from app.core.db import KernelDB, Tx


@dataclass(frozen=True)
class CollectionRow:
    collection_id: int
    name: str
    sparse_enabled: bool
    sparse_idf: bool
    lexical_version: str
    status: str


@dataclass(frozen=True)
class OrgSpace:
    space_id: int
    role: str  # active | shadow | retiring
    model: str
    dim: int
    metric: str
    # The model that first wrote to a nameless space (descriptor model UNKNOWN_MODEL); None while no stamped write has named it.
    named_by: str | None = None

    @property
    def effective_model(self) -> str:
        return self.named_by or self.model


def _collection(name: str, r: asyncpg.Record) -> CollectionRow:
    return CollectionRow(r["collection_id"], name, r["sparse_enabled"], r["sparse_idf"], r["lexical_version"], r["status"])


class Catalog:
    def __init__(self, db: KernelDB, ttl: float) -> None:
        self._db = db
        self._ttl = ttl
        self._collections: dict[str, tuple[float, CollectionRow]] = {}
        self._spaces: dict[str, tuple[float, list[OrgSpace]]] = {}

    def forget(self, name: str | None = None, org: str | None = None) -> None:
        if name is not None:
            self._collections.pop(name, None)
        if org is not None:
            self._spaces.pop(org, None)

    # ---- reads --------------------------------------------------------------------------------------------------
    async def collection(self, name: str) -> CollectionRow | None:
        hit = self._collections.get(name)
        if hit and hit[0] > time.monotonic():
            return hit[1]

        async def read(tx: Tx) -> Any:  # noqa: ANN401
            return await tx.fetchrow(
                "/* pipeshub:pgvector.collection_get */ SELECT * FROM retrieval.collection_get($1)", name
            )

        with catalog_scope():
            row = await self._db.run(read, custom_plan=True)
        if row is None:
            self._collections.pop(name, None)
            return None
        found = _collection(name, row)
        self._collections[name] = (time.monotonic() + self._ttl, found)
        return found

    async def require_collection(self, name: str) -> CollectionRow:
        found = await self.collection(name)
        if found is None:
            raise CollectionNotFoundError(name)
        return found

    async def collections(self) -> list[CollectionRow]:
        out: list[CollectionRow] = []
        after = 0
        while True:
            async def read(tx: Tx, after: int = after) -> Any:  # noqa: ANN401
                return await tx.fetch(
                    "/* pipeshub:pgvector.collection_list */ SELECT * FROM retrieval.collection_list($1, 1000)", after
                )

            with catalog_scope():
                rows = await self._db.run(read, custom_plan=True)
            out += [_collection(r["name"], r) for r in rows]
            if len(rows) < 1000:
                return out
            after = rows[-1]["collection_id"]

    async def org_spaces(self, org: str) -> list[OrgSpace]:
        hit = self._spaces.get(org)
        if hit and hit[0] > time.monotonic():
            return hit[1]

        async def read(tx: Tx) -> Any:  # noqa: ANN401
            rows = await tx.fetch("/* pipeshub:pgvector.org_space_list */ SELECT * FROM retrieval.org_space_list()")
            named = await tx.fetch("/* pipeshub:pgvector.org_space_named_by */ SELECT * FROM retrieval.org_space_named_by()")
            return rows, {r["space_id"]: r["named_by"] for r in named}

        with org_scope(org):
            rows, named_by = await self._db.run(read, custom_plan=True)
        spaces = [OrgSpace(r["space_id"], r["space_role"], r["model"], r["dim"], r["metric"], named_by.get(r["space_id"])) for r in rows]
        self._spaces[org] = (time.monotonic() + self._ttl, spaces)
        return spaces

    async def active_space(self, org: str) -> OrgSpace | None:
        return next((s for s in await self.org_spaces(org) if s.role == "active"), None)

    async def stamp(self, name: str) -> str | None:
        async def read(tx: Tx) -> Any:  # noqa: ANN401
            return await tx.fetchval("/* pipeshub:pgvector.collection_stamp_get */ SELECT stamp FROM retrieval.collection_stamp_get($1)", name)

        with catalog_scope():
            return await self._db.run(read, custom_plan=True)

    async def set_stamp(self, name: str, stamp: str) -> str:
        """Sets the stamp when the collection has none; answers the one in force (a vetted function the indexing role may call)."""
        async def write(tx: Tx) -> Any:  # noqa: ANN401
            return await tx.fetchval(
                "/* pipeshub:pgvector.collection_stamp_set */ SELECT stamp FROM retrieval.collection_stamp_set($1, $2)", name, stamp
            )

        with catalog_scope():
            found = await self._db.run(write, write=True, idempotent=True, custom_plan=True)
        if found is None:
            raise CollectionNotFoundError(name)
        return found

    async def orgs_of_vrids(self, vrids: list[str]) -> dict[str, set[str]]:
        """Global lookup (it names the orgs of any vrid it is given) and callable by the indexing role only; at most 100 per call."""
        found: dict[str, set[str]] = {}
        todo = [vrids[i:i + 100] for i in range(0, len(vrids), 100)]
        while todo:
            batch = todo.pop()

            async def read(tx: Tx, batch: list[str] = batch) -> Any:  # noqa: ANN401
                return await tx.fetch("/* pipeshub:pgvector.vrid_orgs */ SELECT vrid, org_id FROM retrieval.vrid_orgs($1::text[])", batch)

            try:
                with catalog_scope():
                    rows = await self._db.run(read, write=True, custom_plan=True)
            except asyncpg.ProgramLimitExceededError:
                # The function refuses an answer of more than 1000 rows instead of cutting it: halve the batch and go on.
                if len(batch) == 1:
                    raise
                half = len(batch) // 2
                todo += [batch[:half], batch[half:]]
                continue
            for r in rows:
                # org-safe: this is a column of the vetted global lookup's answer (the orgs that hold the vrid), not an org taken from a
                # claim or payload to act for; the guard baseline counts it (tests/unit/core/org_context_baseline.json)
                found.setdefault(r["vrid"], set()).add(r["org_id"])
        return found

    # ---- jobs paths ----------------------------------------------------------------------------------------------
    async def _jobs(self, what: str, fn):  # noqa: ANN001, ANN202
        try:
            with org_context.system("pgvector.catalog"):
                return await self._db.run(fn, jobs=True, write=True, custom_plan=True)
        except DbConfigError as e:
            raise PgVectorError(
                f"{what} needs the jobs credential: set PGVECTOR_JOBS_DSN in this process, "
                "or run the operator step (python -m db.maintain, see backend/db/README.md)"
            ) from e

    async def create_collection(self, name: str, *, sparse_enabled: bool, sparse_idf: bool, lexical_version: str = "unset") -> int:
        async def write(tx: Tx) -> Any:  # noqa: ANN401
            return await tx.fetchval(
                "/* pipeshub:pgvector.collection_create */ SELECT new_id FROM retrieval.collection_create($1, $2, $3, $4)",
                name, sparse_enabled, sparse_idf, lexical_version,
            )

        try:
            created = await self._jobs("creating a collection", write)
        except asyncpg.UniqueViolationError as e:
            raise CollectionAlreadyExistsError(name) from e
        self.forget(name)
        return created

    async def mark_deleting(self, name: str) -> bool:
        async def write(tx: Tx) -> Any:  # noqa: ANN401
            return await tx.fetchval(
                "/* pipeshub:pgvector.collection_mark_deleting */ SELECT gone_id FROM retrieval.collection_mark_deleting($1)", name
            )

        gone = await self._jobs("deleting a collection", write)
        self.forget(name)
        return gone is not None

    async def replace_empty_space(self, org: str, model: str, dim: int, metric: str = "cosine") -> bool:
        """Replace the org's active space by ``(model, dim, metric)`` while it holds no point (a vetted function the indexing
        role may call, on a write that fits no space of the org). False when a point exists or a write landed meanwhile."""
        async def write(tx: Tx) -> Any:  # noqa: ANN401
            return await tx.fetchval(
                "/* pipeshub:pgvector.org_space_replace_empty */ SELECT replaced FROM retrieval.org_space_replace_empty($1, $2, $3)",
                model, dim, metric,
            )

        with org_scope(org):
            replaced = await self._db.run(write, write=True, idempotent=True, custom_plan=True)
        self.forget(org=org)
        return bool(replaced)

    async def name_space(self, org: str, stamp: str) -> str:
        """Name the org's active space after ``stamp`` when it is nameless and still unnamed (a vetted function the indexing role may
        call); answers the name in force, which is another model's when a different stamped write came first."""
        async def write(tx: Tx) -> Any:  # noqa: ANN401
            return await tx.fetchval("/* pipeshub:pgvector.org_space_name */ SELECT named_by FROM retrieval.org_space_name($1)", stamp)

        with org_scope(org):
            named = await self._db.run(write, write=True, idempotent=True, custom_plan=True)
        self.forget(org=org)
        return named

    async def activate_space(self, org: str, model: str, dim: int, metric: str = "cosine") -> OrgSpace:
        """The org's active space; if it has none yet, the space for ``(model, dim, metric)`` becomes it. A vetted function the
        indexing role may call, so no jobs credential is needed in the request path."""
        async def write(tx: Tx) -> Any:  # noqa: ANN401
            return await tx.fetchval(
                "/* pipeshub:pgvector.org_space_adopt */ SELECT active_id FROM retrieval.org_space_adopt($1, $2, $3)", model, dim, metric
            )

        with org_scope(org):      # a concurrent first write of the org is settled inside the function: both get the one space
            await self._db.run(write, write=True, idempotent=True, custom_plan=True)
        self.forget(org=org)
        space = await self.active_space(org)
        if space is None:
            raise PgVectorError("the organisation has no active embedding space after adoption")
        return space
