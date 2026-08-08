from __future__ import annotations

import asyncio
import datetime
import json
import re
import sqlite3
import uuid
from typing import Any

from app.agent_loop_lib.modules.providers.memory.base import (
    MemoryProvider,
    MemoryResult,
    MemoryScope,
)

"""SQLite+FTS5 memory (Phase 5) — Hermes' pattern: no vector DB, no
embeddings API dependency, just a real full-text index (BM25-ranked) over
a durable on-disk SQLite file. A single `sqlite3.Connection`, all calls
run on a worker thread via `asyncio.to_thread`, serialized behind one
`asyncio.Lock` (stdlib's driver is synchronous) — implemented directly
rather than through a generic full-table-scan-and-filter-in-Python
key/value abstraction, since that can't express FTS5 ranked search —
memory recall benefits from real relevance scoring `InMemoryProvider`'s
substring match can't provide.
"""

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def _fts_query(raw: str) -> str | None:
    """Turn arbitrary user text into a safe FTS5 MATCH expression: each
    word becomes a quoted phrase (sidesteps FTS5 operator syntax like `-`,
    `:`, unbalanced quotes) OR'd together, so a query matches any row
    containing at least one of the query's words. Returns None for a query
    with no word characters (nothing to search for)."""
    words = _WORD_RE.findall(raw)
    if not words:
        return None
    escaped = [w.replace('"', '""') for w in words]
    return " OR ".join(f'"{w}"' for w in escaped)


class SQLiteMemoryProvider(MemoryProvider):
    """Durable, full-text-searchable `MemoryProvider`. `path=":memory:"`
    (the default) gives a private in-process SQLite DB — useful for tests
    that want real FTS5 ranking without a file on disk; pass a real path
    for durability across process restarts."""

    def __init__(self, path: str = ":memory:") -> None:
        self._path = path
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()
        self._ready = False

    async def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = await asyncio.to_thread(
                sqlite3.connect, self._path, check_same_thread=False,
            )
        if not self._ready:
            def _init() -> None:
                assert self._conn is not None
                self._conn.execute(
                    "CREATE TABLE IF NOT EXISTS memories ("
                    " id TEXT PRIMARY KEY,"
                    " content TEXT NOT NULL,"
                    " metadata TEXT NOT NULL,"
                    " agent_id TEXT,"
                    " user_id TEXT,"
                    " session_id TEXT,"
                    " team_id TEXT,"
                    " created_at TEXT NOT NULL"
                    ")"
                )
                self._conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts "
                    "USING fts5(memory_id UNINDEXED, content)"
                )
                self._conn.commit()
            await asyncio.to_thread(_init)
            self._ready = True
        return self._conn

    async def add(
        self,
        content: str,
        metadata: dict[str, Any] | None = None,
        scope: MemoryScope | None = None,
    ) -> str:
        memory_id = str(uuid.uuid4())
        metadata = metadata or {}
        scope = scope or MemoryScope()
        created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

        async with self._lock:
            conn = await self._connect()

            def _add() -> None:
                conn.execute(
                    "INSERT INTO memories "
                    "(id, content, metadata, agent_id, user_id, session_id, team_id, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        memory_id, content, json.dumps(metadata),
                        scope.agent_id, scope.user_id, scope.session_id, scope.team_id,
                        created_at,
                    ),
                )
                conn.execute(
                    "INSERT INTO memories_fts (memory_id, content) VALUES (?, ?)",
                    (memory_id, content),
                )
                conn.commit()

            await asyncio.to_thread(_add)
        return memory_id

    async def search(
        self,
        query: str,
        top_k: int = 10,
        scope: MemoryScope | None = None,
    ) -> list[MemoryResult]:
        fts_query = _fts_query(query)
        if fts_query is None:
            return []

        async with self._lock:
            conn = await self._connect()

            def _search() -> list[Any]:
                try:
                    # Over-fetch (5x) before scope-filtering in Python,
                    # same tradeoff StorageBackend.query() makes — scope
                    # filters aren't part of the FTS5 index.
                    return conn.execute(
                        "SELECT m.id, m.content, m.metadata, m.agent_id, m.user_id, "
                        "m.session_id, m.team_id, bm25(memories_fts) AS rank "
                        "FROM memories_fts JOIN memories m ON m.id = memories_fts.memory_id "
                        "WHERE memories_fts MATCH ? ORDER BY rank LIMIT ?",
                        (fts_query, top_k * 5),
                    ).fetchall()
                except sqlite3.OperationalError:
                    return []

            rows = await asyncio.to_thread(_search)

        results: list[MemoryResult] = []
        for mid, content, metadata_json, agent_id, user_id, session_id, team_id, rank in rows:
            if scope is not None:
                stored_scope = MemoryScope(
                    agent_id=agent_id, user_id=user_id, session_id=session_id, team_id=team_id,
                )
                if not _scope_matches(stored_scope, scope):
                    continue
            # bm25() returns lower-is-better; invert to a friendlier
            # higher-is-better score so callers don't need to know FTS5's
            # sign convention.
            results.append(MemoryResult(
                id=mid, content=content, metadata=json.loads(metadata_json),
                score=-float(rank),
            ))
            if len(results) >= top_k:
                break
        return results

    async def get(self, memory_id: str) -> MemoryResult | None:
        async with self._lock:
            conn = await self._connect()

            def _get() -> Any:
                return conn.execute(
                    "SELECT id, content, metadata FROM memories WHERE id = ?", (memory_id,),
                ).fetchone()

            row = await asyncio.to_thread(_get)
        if row is None:
            return None
        mid, content, metadata_json = row
        return MemoryResult(id=mid, content=content, metadata=json.loads(metadata_json), score=1.0)

    async def delete(self, memory_id: str) -> None:
        async with self._lock:
            conn = await self._connect()

            def _delete() -> None:
                conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
                conn.execute("DELETE FROM memories_fts WHERE memory_id = ?", (memory_id,))
                conn.commit()

            await asyncio.to_thread(_delete)

    async def clear(self, scope: MemoryScope | None = None) -> None:
        async with self._lock:
            conn = await self._connect()

            if scope is None:
                def _clear_all() -> None:
                    conn.execute("DELETE FROM memories")
                    conn.execute("DELETE FROM memories_fts")
                    conn.commit()
                await asyncio.to_thread(_clear_all)
                return

            def _matching_ids() -> list[str]:
                rows = conn.execute(
                    "SELECT id, agent_id, user_id, session_id, team_id FROM memories",
                ).fetchall()
                ids = []
                for mid, agent_id, user_id, session_id, team_id in rows:
                    stored = MemoryScope(
                        agent_id=agent_id, user_id=user_id, session_id=session_id, team_id=team_id,
                    )
                    if _scope_matches(stored, scope):
                        ids.append(mid)
                return ids

            ids = await asyncio.to_thread(_matching_ids)

            def _delete_ids() -> None:
                for mid in ids:
                    conn.execute("DELETE FROM memories WHERE id = ?", (mid,))
                    conn.execute("DELETE FROM memories_fts WHERE memory_id = ?", (mid,))
                conn.commit()

            await asyncio.to_thread(_delete_ids)

    async def close(self) -> None:
        async with self._lock:
            if self._conn is not None:
                await asyncio.to_thread(self._conn.close)
                self._conn = None
                self._ready = False


def _scope_matches(stored: MemoryScope, query: MemoryScope) -> bool:
    if query.agent_id is not None and stored.agent_id != query.agent_id:
        return False
    if query.user_id is not None and stored.user_id != query.user_id:
        return False
    if query.session_id is not None and stored.session_id != query.session_id:
        return False
    if query.team_id is not None and stored.team_id != query.team_id:
        return False
    return True
