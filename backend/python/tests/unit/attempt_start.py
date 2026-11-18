"""A graph-provider double's ``write_attempt_start``: the write the attempt's status fields make, through the double's own
``update_node``, so a test that watches the status writes sees an attempt start as the status write it is."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock


def attempt_start_via(provider: Any) -> AsyncMock:  # noqa: ANN401
    async def write(record_id: str, fields: dict[str, Any], now: int | None = None, transaction: str | None = None) -> Any:  # noqa: ANN401
        return await provider.update_node(record_id, "records", fields)

    return AsyncMock(side_effect=write)
