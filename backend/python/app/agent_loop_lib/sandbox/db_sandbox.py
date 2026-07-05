from __future__ import annotations

import asyncio
import re
import sqlite3

from app.agent_loop_lib.core.exceptions import AgentLoopError

"""DB sandbox (Phase 3 taxonomy) — scoped SQL access with readonly/allowlist
modes, over stdlib `sqlite3` (no new dependency; a Postgres/MySQL-backed
implementation can satisfy the same duck-typed `query()` interface later).
"""


class DBSandboxError(AgentLoopError):
    """A query violated the sandbox's readonly or table-allowlist policy."""


_TABLE_NAME_RE = re.compile(
    r"\b(?:from|join|into|update)\s+([\"'`\[]?)([a-zA-Z_][a-zA-Z0-9_]*)\1",
    re.IGNORECASE,
)


class SqliteDBSandbox:
    """`mode="readonly"` rejects any statement other than a top-level
    SELECT; `table_allowlist`, when given, additionally rejects statements
    that reference a table outside it. Table detection is a best-effort
    keyword scan (not a full SQL parser) — sufficient to prevent accidental
    cross-table access from agent-authored queries, not adversarial SQL
    written to evade it; pair with least-privilege DB file permissions for
    real defense in depth.
    """

    def __init__(
        self,
        db_path: str = ":memory:",
        mode: str = "readonly",
        table_allowlist: list[str] | None = None,
    ) -> None:
        if mode not in ("readonly", "readwrite"):
            raise ValueError(f"Unknown DB sandbox mode: {mode!r}. Supported: 'readonly', 'readwrite'")
        self._db_path = db_path
        self._mode = mode
        self._table_allowlist = set(table_allowlist) if table_allowlist else None

    @property
    def mode(self) -> str:
        return self._mode

    async def query(self, sql: str, params: list | None = None) -> list[dict]:
        self._check_policy(sql)
        return await asyncio.to_thread(self._execute_sync, sql, params or [])

    def _check_policy(self, sql: str) -> None:
        stripped = sql.strip().lower()
        first_word = stripped.split(None, 1)[0] if stripped else ""
        if self._mode == "readonly" and first_word != "select":
            raise DBSandboxError(
                f"DB sandbox is readonly — statement type {first_word!r} is not allowed"
            )
        if self._table_allowlist is not None:
            referenced = {m.group(2).lower() for m in _TABLE_NAME_RE.finditer(sql)}
            allowed = {t.lower() for t in self._table_allowlist}
            disallowed = referenced - allowed
            if disallowed:
                raise DBSandboxError(
                    f"Query references table(s) outside the allowlist: {sorted(disallowed)}"
                )

    def _execute_sync(self, sql: str, params: list) -> list[dict]:
        conn = sqlite3.connect(self._db_path)
        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(sql, params)
            if cursor.description is None:
                conn.commit()
                return [{"rows_affected": cursor.rowcount}]
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()
