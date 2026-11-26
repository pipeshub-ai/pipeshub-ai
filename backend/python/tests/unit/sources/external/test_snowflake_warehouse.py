"""connector_runner: a runner on a connector's stored credentials, with the shared limit."""

import asyncio
import threading
from typing import Never
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sources.external.snowflake.warehouse import connector_runner
from app.utils.statement_slots import MAX_STATEMENTS_IN_FLIGHT, statement_slots

CONFIG = {"auth": {"accountIdentifier": "acct", "warehouse": "WH", "patToken": "pat"}}


def _config(value) -> object:
    return patch(
        "app.sources.client.snowflake.snowflake.SnowflakeClient._get_connector_config",
        new_callable=AsyncMock, return_value=value,
    )


async def test_one_limit_per_connector() -> None:
    assert statement_slots("a") is statement_slots("a")
    assert statement_slots("a") is not statement_slots("b")
    assert statement_slots("a")._value == MAX_STATEMENTS_IN_FLIGHT


def test_two_event_loops_each_get_their_own_limit() -> None:
    # Readers can run on more than one event loop in a process.
    async def burst() -> object:
        slots = statement_slots("conn-loops")

        async def hold() -> None:
            async with slots:
                await asyncio.sleep(0.001)

        await asyncio.gather(*(hold() for _ in range(MAX_STATEMENTS_IN_FLIGHT * 3)))
        return slots

    errors: list[BaseException] = []
    other: list = []

    def worker() -> None:
        try:
            other.append(asyncio.run(burst()))
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    mine = asyncio.run(burst())
    assert errors == []
    assert mine is not other[0]


async def test_the_runner_is_tagged_limited_and_its_client_closed() -> None:
    data_source = MagicMock()
    data_source.http.close = AsyncMock()
    with _config(CONFIG), patch(
        "app.sources.external.snowflake.warehouse.SnowflakeDataSource", return_value=data_source,
    ):
        async with connector_runner(
            logger=MagicMock(), config_service=MagicMock(), connector_id="conn-1", query_tag="pipeshub:agent",
        ) as runner:
            assert runner.warehouse == "WH"
            assert runner._session_parameters["QUERY_TAG"] == "pipeshub:agent"
            assert runner._slots is statement_slots("conn-1")
        data_source.http.close.assert_awaited_once()


async def test_the_client_is_closed_when_the_caller_fails() -> Never:
    data_source = MagicMock()
    data_source.http.close = AsyncMock()
    with _config(CONFIG), patch(
        "app.sources.external.snowflake.warehouse.SnowflakeDataSource", return_value=data_source,
    ), pytest.raises(RuntimeError):
        async with connector_runner(
            logger=MagicMock(), config_service=MagicMock(), connector_id="conn-1", query_tag="t",
        ):
            raise RuntimeError("boom")
    data_source.http.close.assert_awaited_once()


async def test_an_incomplete_config_raises_before_a_client_exists() -> None:
    with _config({"auth": {"accountIdentifier": "acct"}}), pytest.raises(ValueError):
        async with connector_runner(
            logger=MagicMock(), config_service=MagicMock(), connector_id="conn-1", query_tag="t",
        ):
            pass


async def test_the_role_reaches_the_runner_and_an_empty_one_sends_none() -> None:
    data_source = MagicMock()
    data_source.http.close = AsyncMock()
    for role, expected in (("LOADER", "LOADER"), ("", None)):
        with _config({"auth": {**CONFIG["auth"], "role": role}}), patch(
            "app.sources.external.snowflake.warehouse.SnowflakeDataSource", return_value=data_source,
        ):
            async with connector_runner(
                logger=MagicMock(), config_service=MagicMock(), connector_id="conn-1", query_tag="t",
            ) as runner:
                assert runner.role == expected
