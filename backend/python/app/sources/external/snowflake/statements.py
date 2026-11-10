"""Run one SQL statement through the Snowflake SQL API and read all of its result.

``SnowflakeDataSource.execute_sql`` returns the first reply as is. That reply
can be a 202 with only a statement handle (the API waits 45 s before giving
one), and holds only the first partition of a large result. ``run`` polls the
handle, reads every partition and decodes the API's strings into Python
values, so callers never see a silently cut-off result.

API reference: https://docs.snowflake.com/en/developer-guide/sql-api/reference
Value encodings: https://docs.snowflake.com/en/developer-guide/sql-api/handling-responses
"""

import asyncio
import contextlib
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from app.sources.client.snowflake.snowflake import SnowflakeResponse
from app.sources.external.snowflake.snowflake_ import SnowflakeDataSource

DEFAULT_STATEMENT_TIMEOUT_S = 60
# Snowflake cancels a statement at its timeout and answers 422; polling a little
# longer lets that answer arrive instead of a client-side guess.
_POLL_GRACE_S = 15
_STILL_RUNNING = 202
# Busy or restarting server, or no answer at all (None: the request never got
# one). A status poll is a read; a POST is resubmitted under the same request
# id, which Snowflake runs at most once.
_RETRYABLE_STATUSES = frozenset({None, 429, 500, 502, 503, 504})
_ATTEMPTS = 3
# Query canceled: to_snowflake_stream_error maps class 57 to 503.
_TIMED_OUT_SQLSTATE = "57014"

_EPOCH_DATE = date(1970, 1, 1)
_EPOCH = datetime(1970, 1, 1)  # noqa: DTZ001 - TIMESTAMP_NTZ values carry no zone
# TIMESTAMP_TZ offsets are sent as minutes + 1440, so they are never negative.
_TZ_OFFSET_BIAS_MINUTES = 1440


class SnowflakeFetchError(Exception):
    """A statement Snowflake refused, carrying what it refused with.

    ``status_code`` and ``sqlstate`` are the attribute names ``to_stream_error``
    and ``to_sql_stream_error`` read, and only one of them is ever set: the SQL
    API answers *every* statement-level failure with HTTP 422, so where a
    SQLSTATE exists the status is noise and must not reach ``map_source_status``.
    """

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        sqlstate: str | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.sqlstate = sqlstate
        self.code = code

    @classmethod
    def from_response(cls, response: SnowflakeResponse, context: str) -> "SnowflakeFetchError":
        """Build from a failed envelope, preferring its SQLSTATE; Snowflake's message and code when it gave them."""
        if response.sql_state:
            return cls(response.error or context, sqlstate=response.sql_state, code=response.code)
        message = f"{context}: {response.error}" if response.error else context
        return cls(message, status_code=response.status_code, code=response.code)


@dataclass(frozen=True)
class SnowflakeColumn:
    name: str
    type: str
    scale: int | None = None


@dataclass
class SnowflakeResult:
    columns: list[SnowflakeColumn]
    rows: list[list[Any]]
    # More rows existed than ``max_rows`` let through.
    truncated: bool = False

    @property
    def column_names(self) -> list[str]:
        return [column.name for column in self.columns]

    def as_dicts(self) -> list[dict[str, Any]]:
        names = self.column_names
        return [dict(zip(names, row)) for row in self.rows]


def decode_value(raw: Any, column: SnowflakeColumn) -> Any:  # noqa: ANN401
    """The Python value for one cell the API sent as a string.

    A value that does not parse as its type is returned unchanged rather than
    failing the whole result.
    """
    if raw is None or not isinstance(raw, str):
        return raw
    kind = column.type.lower()
    try:
        if kind == "fixed":
            number = Decimal(raw)
            return int(number) if not column.scale else number
        if kind == "real":
            return float(raw)
        if kind == "boolean":
            return raw.lower() == "true"
        if kind == "date":
            return (_EPOCH_DATE + timedelta(days=int(raw))).isoformat()
        if kind == "time":
            return _time_of_day(Decimal(raw))
        if kind == "timestamp_ntz":
            return _from_epoch(Decimal(raw)).isoformat()
        if kind == "timestamp_ltz":
            return _from_epoch(Decimal(raw)).replace(tzinfo=UTC).isoformat()
        if kind == "timestamp_tz":
            seconds, _, offset = raw.partition(" ")
            zone = timezone(timedelta(minutes=int(offset) - _TZ_OFFSET_BIAS_MINUTES))
            utc = _from_epoch(Decimal(seconds)).replace(tzinfo=UTC)
            return utc.astimezone(zone).isoformat()
    except (ArithmeticError, InvalidOperation, ValueError, OverflowError):
        return raw
    return raw


def _partition_size(info: Any) -> int:  # noqa: ANN401
    size = info.get("uncompressedSize") if isinstance(info, dict) else None
    return size if isinstance(size, int) else 0


def _from_epoch(seconds: Decimal) -> datetime:
    # timedelta keeps microseconds and handles dates before 1970.
    return _EPOCH + timedelta(microseconds=int(seconds * 1_000_000))


def _time_of_day(seconds: Decimal) -> str:
    return (datetime.min + timedelta(microseconds=int(seconds * 1_000_000))).time().isoformat()


class SnowflakeStatementRunner:
    def __init__(
        self,
        data_source: SnowflakeDataSource,
        *,
        warehouse: str | None,
        role: str | None = None,
        rate_limiter: AbstractAsyncContextManager[Any] | None = None,
        slots: asyncio.Semaphore | None = None,
        session_parameters: dict[str, str] | None = None,
        poll_interval_s: float = 0.5,
        max_poll_interval_s: float = 5.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.data_source = data_source
        self.warehouse = warehouse
        self.role = role
        self._rate_limiter = rate_limiter
        # Held while a statement runs, queued time included: bounds the
        # statements in flight on the warehouse, not only the requests.
        self._slots = slots
        # One statement per request is the API's default; stated so a changed
        # account default cannot let a second statement through.
        self._session_parameters = {"MULTI_STATEMENT_COUNT": "1", **(session_parameters or {})}
        self._poll_interval_s = poll_interval_s
        self._max_poll_interval_s = max_poll_interval_s
        self._sleep = sleep
        self._clock = clock

    async def run(
        self,
        statement: str,
        *,
        database: str | None = None,
        schema: str | None = None,
        timeout_s: int = DEFAULT_STATEMENT_TIMEOUT_S,
        max_rows: int | None = None,
        max_bytes: int | None = None,
    ) -> SnowflakeResult:
        """Run ``statement`` and return its rows, all of them or the first ``max_rows``.

        ``max_bytes`` stops reading result partitions once the ones read hold
        that much (by Snowflake's uncompressed size); the first is always read.

        Raises SnowflakeFetchError when Snowflake refuses the statement, a
        partition cannot be read, or no answer comes within ``timeout_s``. If
        the calling task is cancelled, the statement is cancelled in Snowflake
        too, so the warehouse does not keep running for nobody.
        """
        if self._slots is None:
            return await self._run(statement, database, schema, timeout_s, max_rows, max_bytes)
        async with self._slots:
            return await self._run(statement, database, schema, timeout_s, max_rows, max_bytes)

    async def _run(
        self,
        statement: str,
        database: str | None,
        schema: str | None,
        timeout_s: int,
        max_rows: int | None,
        max_bytes: int | None,
    ) -> SnowflakeResult:
        # A timeout of 0 means Snowflake's maximum, a week.
        timeout_s = max(1, int(timeout_s))
        deadline = self._clock() + timeout_s + _POLL_GRACE_S
        response = await self._submit(statement, database, schema, timeout_s)
        body = response.data if isinstance(response.data, dict) else {}
        handle = body.get("statementHandle") or response.statement_handle
        try:
            if response.status_code == _STILL_RUNNING:
                body = await self._wait_for_result(handle, deadline)
            return await self._read_result(body, handle, max_rows, max_bytes)
        except BaseException:
            # Cancelled, timed out, or a poll or partition read failed: the
            # statement may still be running, and a warehouse bills for it.
            if handle:
                await asyncio.shield(self._cancel(handle))
            raise

    async def _submit(
        self, statement: str, database: str | None, schema: str | None, timeout_s: int,
    ) -> SnowflakeResponse:
        request_id = str(uuid.uuid4())
        attempt = 0
        while True:
            attempt += 1
            async with self._limit():
                response = await self.data_source.execute_sql(
                    statement=statement,
                    database=database,
                    schema=schema,
                    warehouse=self.warehouse,
                    role=self.role,
                    timeout=timeout_s,
                    parameters=self._session_parameters,
                    request_id=request_id,
                    retry=attempt > 1,
                )
            if response.success:
                return response
            if response.status_code not in _RETRYABLE_STATUSES or attempt == _ATTEMPTS:
                raise SnowflakeFetchError.from_response(response, "Snowflake refused the statement")
            await self._sleep(attempt)

    async def _read_result(
        self, body: dict[str, Any], handle: str | None, max_rows: int | None, max_bytes: int | None = None,
    ) -> SnowflakeResult:
        meta = body.get("resultSetMetaData")
        if not isinstance(meta, dict):
            # Every finished statement has it, an empty result too. Without it the
            # reply is not a result (an empty body from a proxy, say), and reading
            # it as no rows would make a schema look empty.
            raise SnowflakeFetchError("Snowflake sent a finished statement without its result metadata")
        columns = [
            SnowflakeColumn(
                name=column.get("name", f"col_{i}"),
                type=column.get("type", "text"),
                scale=column.get("scale"),
            )
            for i, column in enumerate(meta.get("rowType") or [])
        ]
        raw_rows: list[list[Any]] = list(body.get("data") or [])
        # Later partitions come without metadata; the first reply's rowType describes them.
        partitions = meta.get("partitionInfo") or []
        bytes_read = _partition_size(partitions[0]) if partitions else 0
        stopped_on_bytes = False
        for partition in range(1, len(partitions)):
            if max_rows is not None and len(raw_rows) >= max_rows:
                break
            if max_bytes is not None and bytes_read >= max_bytes:
                stopped_on_bytes = True
                break
            partition_body = await self._get_status(handle, partition=partition)
            raw_rows.extend(partition_body.get("data") or [])
            bytes_read += _partition_size(partitions[partition])

        total = meta.get("numRows")
        truncated = stopped_on_bytes or (max_rows is not None and (
            len(raw_rows) > max_rows or (isinstance(total, int) and total > max_rows)
        ))
        if max_rows is not None:
            raw_rows = raw_rows[:max_rows]
        rows = [
            [decode_value(value, column) for value, column in zip(row, columns)]
            for row in raw_rows
        ]
        return SnowflakeResult(columns=columns, rows=rows, truncated=truncated)

    async def _wait_for_result(self, handle: str | None, deadline: float) -> dict[str, Any]:
        if not handle:
            raise SnowflakeFetchError("Snowflake accepted the statement but sent no handle to follow")
        interval = self._poll_interval_s
        while True:
            if self._clock() >= deadline:
                raise SnowflakeFetchError("The Snowflake statement timed out", sqlstate=_TIMED_OUT_SQLSTATE)
            await self._sleep(interval)
            interval = min(interval * 2, self._max_poll_interval_s)
            response = await self._status_response(handle)
            if response.status_code != _STILL_RUNNING:
                return self._body_of(response, "Snowflake refused the statement")

    async def _get_status(self, handle: str | None, *, partition: int) -> dict[str, Any]:
        if not handle:
            raise SnowflakeFetchError("Snowflake sent a partitioned result with no handle to read it by")
        response = await self._status_response(handle, partition=partition)
        return self._body_of(response, f"Could not read part {partition + 1} of the Snowflake result")

    async def _status_response(self, handle: str, *, partition: int | None = None) -> SnowflakeResponse:
        for attempt in range(1, _ATTEMPTS + 1):
            async with self._limit():
                response = await self.data_source.get_statement_status(handle, partition=partition)
            if response.status_code not in _RETRYABLE_STATUSES or attempt == _ATTEMPTS:
                return response
            await self._sleep(attempt)
        return response

    @staticmethod
    def _body_of(response: SnowflakeResponse, context: str) -> dict[str, Any]:
        if not response.success:
            raise SnowflakeFetchError.from_response(response, context)
        return response.data if isinstance(response.data, dict) else {}

    async def _cancel(self, handle: str) -> None:
        with contextlib.suppress(Exception):
            async with self._limit():
                await self.data_source.cancel_statement(handle)

    def _limit(self) -> AbstractAsyncContextManager[Any]:
        return self._rate_limiter if self._rate_limiter is not None else contextlib.nullcontext()
