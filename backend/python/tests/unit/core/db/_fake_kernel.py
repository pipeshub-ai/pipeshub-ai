"""A KernelDB stand-in for test_query_log_unit: its frames play app.core.db's, so the caller walk skips them."""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from app.core.db import DbConfig, DbTimeout, KernelDB
from app.core.db.uow import Tx

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class TxnDb(KernelDB):
    """``uow`` yields a Tx on ``con``; leaving it records a failed COMMIT the way ``KernelDB._open`` does."""

    def __init__(self, con: object, *, commit_fails: bool = False) -> None:
        super().__init__(DbConfig(service="connectors", dsn="postgresql://x@h/db"))
        self.con, self.commit_fails = con, commit_fails

    def uow(self, **_: object) -> object:
        @asynccontextmanager
        async def cm() -> AsyncIterator[Tx]:
            tx = Tx(self.con, self, klass="api", idempotent=False, request_id="unit-req")  # type: ignore[arg-type]
            yield tx
            if self.commit_fails:
                tx._record("commit", None, time.perf_counter(), DbTimeout("commit timed out"))

        return cm()
