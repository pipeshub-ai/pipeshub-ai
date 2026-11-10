import asyncio
from decimal import Decimal
from typing import Any

import pytest

from app.sources.client.snowflake.snowflake import SnowflakeResponse
from app.sources.external.snowflake.statements import (
    SnowflakeColumn,
    SnowflakeFetchError,
    SnowflakeResult,
    SnowflakeStatementRunner,
    decode_value,
)

ROW_TYPE = [
    {"name": "ID", "type": "fixed", "scale": 0},
    {"name": "NAME", "type": "text"},
]


def ok(status: int = 200, **body: Any) -> SnowflakeResponse:  # noqa: ANN401
    return SnowflakeResponse(success=True, data=body, status_code=status)


def result_body(rows, partitions: int = 1, handle: str = "h1") -> dict:
    return {
        "statementHandle": handle,
        "resultSetMetaData": {
            "rowType": ROW_TYPE,
            "partitionInfo": [{"rowCount": 1}] * partitions,
        },
        "data": rows,
    }


def failure(status: int = 422, sql_state: str | None = "42S02", message: str = "no such table") -> SnowflakeResponse:
    return SnowflakeResponse(
        success=False, status_code=status, sql_state=sql_state, error=message,
        data={"message": message, "sqlState": sql_state},
    )


class FakeDataSource:
    def __init__(self, post, statuses: list[SnowflakeResponse] | None = None) -> None:
        # One response for every POST, or a list consumed in order.
        self.posts = list(post) if isinstance(post, list) else None
        self.post = post
        self.statuses = list(statuses or [])
        self.execute_calls: list[dict] = []
        self.status_calls: list[tuple[str, int | None]] = []
        self.cancelled: list[str] = []

    async def execute_sql(self, **kwargs) -> SnowflakeResponse:
        self.execute_calls.append(kwargs)
        return self.posts.pop(0) if self.posts is not None else self.post

    async def get_statement_status(self, handle: str, partition: int | None = None) -> SnowflakeResponse:
        self.status_calls.append((handle, partition))
        return self.statuses.pop(0)

    async def cancel_statement(self, handle: str) -> SnowflakeResponse:
        self.cancelled.append(handle)
        return ok()


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def runner_for(ds: FakeDataSource, clock: FakeClock | None = None, **kwargs) -> SnowflakeStatementRunner:
    clock = clock or FakeClock()
    return SnowflakeStatementRunner(
        ds, warehouse="WH", sleep=clock.sleep, clock=clock, **kwargs,
    )


class TestRun:
    async def test_single_partition_needs_no_status_call(self) -> None:
        ds = FakeDataSource(ok(**result_body([["1", "a"]])))
        result = await runner_for(ds).run("SELECT 1")
        assert result.rows == [[1, "a"]]
        assert ds.status_calls == []

    async def test_every_partition_is_read_in_order(self) -> None:
        ds = FakeDataSource(
            ok(**result_body([["1", "a"]], partitions=3)),
            statuses=[ok(data=[["2", "b"]]), ok(data=[["3", "c"]])],
        )
        result = await runner_for(ds).run("SELECT 1")
        assert result.rows == [[1, "a"], [2, "b"], [3, "c"]]
        assert ds.status_calls == [("h1", 1), ("h1", 2)]

    async def test_a_202_is_polled_until_the_result_arrives(self) -> None:
        ds = FakeDataSource(
            ok(202, statementHandle="h1", code="333334"),
            statuses=[ok(202, statementHandle="h1"), ok(**result_body([["7", "x"]], partitions=2)), ok(data=[["8", "y"]])],
        )
        result = await runner_for(ds).run("SELECT 1")
        assert result.rows == [[7, "x"], [8, "y"]]
        assert ds.status_calls == [("h1", None), ("h1", None), ("h1", 1)]

    async def test_a_202_that_fails_raises_with_the_sqlstate(self) -> None:
        ds = FakeDataSource(ok(202, statementHandle="h1"), statuses=[failure()])
        with pytest.raises(SnowflakeFetchError) as excinfo:
            await runner_for(ds).run("SELECT 1")
        assert excinfo.value.sqlstate == "42S02"
        assert excinfo.value.status_code is None

    async def test_past_the_deadline_the_statement_is_cancelled(self) -> None:
        clock = FakeClock()
        ds = FakeDataSource(ok(202, statementHandle="h1"), statuses=[ok(202)] * 100)
        with pytest.raises(SnowflakeFetchError, match="timed out") as excinfo:
            await runner_for(ds, clock).run("SELECT 1", timeout_s=10)
        assert ds.cancelled == ["h1"]
        # Class 57 maps to 503 in to_snowflake_stream_error.
        assert excinfo.value.sqlstate == "57014"
        assert clock.now >= 10

    async def test_a_202_without_a_handle_raises(self) -> None:
        ds = FakeDataSource(ok(202))
        with pytest.raises(SnowflakeFetchError, match="no handle"):
            await runner_for(ds).run("SELECT 1")

    async def test_a_refused_post_raises_with_the_sqlstate_only(self) -> None:
        ds = FakeDataSource([failure()])
        with pytest.raises(SnowflakeFetchError) as excinfo:
            await runner_for(ds).run("SELECT 1")
        assert excinfo.value.sqlstate == "42S02"
        assert excinfo.value.status_code is None
        assert "no such table" in str(excinfo.value)

    async def test_a_poll_with_no_answer_is_asked_again(self) -> None:
        ds = FakeDataSource(
            ok(202, statementHandle="h1"),
            statuses=[SnowflakeResponse(success=False, error="reset"), ok(**result_body([["1", "a"]]))],
        )
        result = await runner_for(ds).run("SELECT 1")
        assert result.rows == [[1, "a"]]
        assert ds.cancelled == []

    async def test_a_poll_that_keeps_failing_cancels_the_statement(self) -> None:
        ds = FakeDataSource(
            ok(202, statementHandle="h1"),
            statuses=[SnowflakeResponse(success=False, error="reset")] * 3,
        )
        with pytest.raises(SnowflakeFetchError):
            await runner_for(ds).run("SELECT 1")
        assert ds.cancelled == ["h1"]

    async def test_a_finished_statement_is_not_cancelled(self) -> None:
        ds = FakeDataSource(ok(**result_body([["1", "a"]])))
        await runner_for(ds).run("SELECT 1")
        assert ds.cancelled == []

    async def test_a_post_with_no_answer_is_resubmitted(self) -> None:
        ds = FakeDataSource([SnowflakeResponse(success=False, error="reset"), ok(**result_body([]))])
        await runner_for(ds).run("SELECT 1")
        first, second = ds.execute_calls
        assert first["request_id"] == second["request_id"] and second["retry"] is True

    async def test_a_transport_failure_raises_without_a_status(self) -> None:
        ds = FakeDataSource(SnowflakeResponse(success=False, error="connect timeout"))
        with pytest.raises(SnowflakeFetchError) as excinfo:
            await runner_for(ds).run("SELECT 1")
        assert excinfo.value.status_code is None
        assert excinfo.value.sqlstate is None

    async def test_a_failed_partition_raises_instead_of_truncating(self) -> None:
        ds = FakeDataSource(
            ok(**result_body([["1", "a"]], partitions=2)),
            statuses=[failure(status=404, sql_state=None, message="gone")],
        )
        with pytest.raises(SnowflakeFetchError) as excinfo:
            await runner_for(ds).run("SELECT 1")
        assert excinfo.value.status_code == 404

    async def test_a_busy_server_is_asked_again_for_a_partition(self) -> None:
        ds = FakeDataSource(
            ok(**result_body([["1", "a"]], partitions=2)),
            statuses=[failure(status=503, sql_state=None), ok(data=[["2", "b"]])],
        )
        result = await runner_for(ds).run("SELECT 1")
        assert result.rows == [[1, "a"], [2, "b"]]

    async def test_context_reaches_the_api(self) -> None:
        ds = FakeDataSource(ok(**result_body([])))
        await runner_for(ds, role="READER").run("SELECT 1", database="DB", schema="S", timeout_s=30)
        (call,) = ds.execute_calls
        assert call["request_id"]
        assert {k: v for k, v in call.items() if k != "request_id"} == {
            "statement": "SELECT 1", "database": "DB", "schema": "S",
            "warehouse": "WH", "role": "READER", "timeout": 30, "retry": False,
            "parameters": {"MULTI_STATEMENT_COUNT": "1"},
        }

    async def test_session_parameters_are_sent_and_one_statement_is_always_stated(self) -> None:
        ds = FakeDataSource(ok(**result_body([])))
        await runner_for(ds, session_parameters={"QUERY_TAG": "pipeshub:relations"}).run("SELECT 1")
        assert ds.execute_calls[0]["parameters"] == {"MULTI_STATEMENT_COUNT": "1", "QUERY_TAG": "pipeshub:relations"}

    async def test_slots_bound_the_statements_in_flight(self) -> None:
        in_flight = peak = 0

        class SlowDataSource(FakeDataSource):
            async def execute_sql(self, **kwargs) -> object:
                nonlocal in_flight, peak
                in_flight += 1
                peak = max(peak, in_flight)
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                in_flight -= 1
                return ok(**result_body([]))

        slots = asyncio.Semaphore(2)
        runner = runner_for(SlowDataSource([]), slots=slots)
        await asyncio.gather(*(runner.run("SELECT 1") for _ in range(6)))
        assert peak == 2

    async def test_a_zero_timeout_is_never_sent(self) -> None:
        ds = FakeDataSource(ok(**result_body([])))
        await runner_for(ds).run("SELECT 1", timeout_s=0)
        assert ds.execute_calls[0]["timeout"] == 1

    async def test_a_busy_server_gets_the_same_post_again_as_a_retry(self) -> None:
        ds = FakeDataSource([failure(status=429, sql_state=None), ok(**result_body([["1", "a"]]))])
        result = await runner_for(ds).run("SELECT 1")
        assert result.rows == [[1, "a"]]
        first, second = ds.execute_calls
        assert first["request_id"] == second["request_id"]
        assert (first["retry"], second["retry"]) == (False, True)

    async def test_a_post_that_keeps_failing_gives_up(self) -> None:
        ds = FakeDataSource([failure(status=503, sql_state=None)] * 3)
        with pytest.raises(SnowflakeFetchError) as excinfo:
            await runner_for(ds).run("SELECT 1")
        assert excinfo.value.status_code == 503
        assert len(ds.execute_calls) == 3

    async def test_a_statement_error_is_not_retried(self) -> None:
        ds = FakeDataSource([failure()])
        with pytest.raises(SnowflakeFetchError):
            await runner_for(ds).run("SELECT 1")
        assert len(ds.execute_calls) == 1

    async def test_a_cancelled_caller_cancels_the_statement(self) -> None:
        clock = FakeClock()
        ds = FakeDataSource(ok(202, statementHandle="h1"), statuses=[ok(202)] * 100)
        started = asyncio.Event()

        async def sleep(seconds) -> None:
            started.set()
            await asyncio.sleep(3600)

        runner = SnowflakeStatementRunner(ds, warehouse="WH", sleep=sleep, clock=clock)
        task = asyncio.create_task(runner.run("SELECT 1"))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert ds.cancelled == ["h1"]

    async def test_max_rows_stops_reading_partitions(self) -> None:
        body = result_body([["1", "a"], ["2", "b"]], partitions=3)
        body["resultSetMetaData"]["numRows"] = 6
        ds = FakeDataSource(ok(**body), statuses=[ok(data=[["3", "c"], ["4", "d"]])])
        result = await runner_for(ds).run("SELECT 1", max_rows=3)
        assert result.rows == [[1, "a"], [2, "b"], [3, "c"]]
        assert result.truncated is True
        assert ds.status_calls == [("h1", 1)]

    async def test_max_bytes_stops_reading_partitions(self) -> None:
        body = result_body([["1", "a"]], partitions=3)
        body["resultSetMetaData"]["partitionInfo"] = [
            {"rowCount": 1, "uncompressedSize": 600},
            {"rowCount": 1, "uncompressedSize": 600},
            {"rowCount": 1, "uncompressedSize": 600},
        ]
        ds = FakeDataSource(ok(**body), statuses=[ok(data=[["2", "b"]])])
        result = await runner_for(ds).run("SELECT 1", max_bytes=1000)
        assert result.rows == [[1, "a"], [2, "b"]]
        assert result.truncated is True
        assert ds.status_calls == [("h1", 1)]

    async def test_the_first_partition_is_read_whatever_its_size(self) -> None:
        body = result_body([["1", "a"]])
        body["resultSetMetaData"]["partitionInfo"] = [{"rowCount": 1, "uncompressedSize": 10_000}]
        result = await runner_for(FakeDataSource(ok(**body))).run("SELECT 1", max_bytes=10)
        assert result.rows == [[1, "a"]] and result.truncated is False

    async def test_a_result_within_max_rows_is_not_truncated(self) -> None:
        body = result_body([["1", "a"]])
        body["resultSetMetaData"]["numRows"] = 1
        ds = FakeDataSource(ok(**body))
        result = await runner_for(ds).run("SELECT 1", max_rows=5)
        assert result.truncated is False

    async def test_polling_interval_doubles_and_caps(self) -> None:
        clock = FakeClock()
        ds = FakeDataSource(
            ok(202, statementHandle="h1"),
            statuses=[ok(202)] * 5 + [ok(**result_body([]))],
        )
        await runner_for(ds, clock, poll_interval_s=1, max_poll_interval_s=4).run("SELECT 1", timeout_s=100)
        assert clock.sleeps == [1, 2, 4, 4, 4, 4]

    async def test_the_rate_limiter_is_entered_for_every_call(self) -> None:
        entered = []

        class Limiter:
            async def __aenter__(self) -> None:
                entered.append(1)

            async def __aexit__(self, *exc: object) -> bool:
                return False

        ds = FakeDataSource(
            ok(202, statementHandle="h1"),
            statuses=[ok(**result_body([["1", "a"]], partitions=2)), ok(data=[["2", "b"]])],
        )
        await runner_for(ds, rate_limiter=Limiter()).run("SELECT 1")
        assert len(entered) == 3


class TestResult:
    def test_as_dicts_keys_by_column_name(self) -> None:
        result = SnowflakeResult(
            columns=[SnowflakeColumn("A", "text"), SnowflakeColumn("B", "text")],
            rows=[["1", "2"]],
        )
        assert result.as_dicts() == [{"A": "1", "B": "2"}]
        assert result.column_names == ["A", "B"]


class TestDecodeValue:
    @pytest.mark.parametrize(
        ("raw", "column", "expected"),
        [
            ("42", SnowflakeColumn("c", "fixed", 0), 42),
            ("42.0", SnowflakeColumn("c", "fixed", None), 42),
            ("3.14", SnowflakeColumn("c", "fixed", 2), Decimal("3.14")),
            ("1.5", SnowflakeColumn("c", "real"), 1.5),
            ("true", SnowflakeColumn("c", "boolean"), True),
            ("false", SnowflakeColumn("c", "boolean"), False),
            ("18262", SnowflakeColumn("c", "date"), "2020-01-01"),
            ("-1", SnowflakeColumn("c", "date"), "1969-12-31"),
            ("82919.000000000", SnowflakeColumn("c", "time"), "23:01:59"),
            ("3600.500000000", SnowflakeColumn("c", "time"), "01:00:00.500000"),
            ("1577836800.000000000", SnowflakeColumn("c", "timestamp_ntz"), "2020-01-01T00:00:00"),
            ("1577836800.000000000", SnowflakeColumn("c", "timestamp_ltz"), "2020-01-01T00:00:00+00:00"),
            ("1616173619.000000000 1500", SnowflakeColumn("c", "timestamp_tz"), "2021-03-19T18:06:59+01:00"),
            ("1616173619.000000000 1380", SnowflakeColumn("c", "timestamp_tz"), "2021-03-19T16:06:59-01:00"),
            ("hello", SnowflakeColumn("c", "text"), "hello"),
            ('{"a": 1}', SnowflakeColumn("c", "variant"), '{"a": 1}'),
            ("x", SnowflakeColumn("c", "something_new"), "x"),
            (None, SnowflakeColumn("c", "fixed", 0), None),
            ("not a number", SnowflakeColumn("c", "fixed", 0), "not a number"),
        ],
    )
    def test_decodes_by_type(self, raw, column, expected) -> None:
        assert decode_value(raw, column) == expected
