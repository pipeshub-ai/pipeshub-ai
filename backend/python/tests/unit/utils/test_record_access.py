"""Unit tests for `caller_can_read_virtual_record`."""

import logging
from unittest.mock import AsyncMock

from app.utils.record_access import caller_can_read_virtual_record

LOGGER = logging.getLogger("test")


def _graph(*, record_ids: list[str] | None = None, allowed: bool = True) -> AsyncMock:
    graph = AsyncMock()
    graph.get_records_by_virtual_record_id.return_value = record_ids if record_ids is not None else ["rec-1"]
    graph.check_record_access_with_details.return_value = {"id": "rec-1"} if allowed else None
    return graph


class TestCallerCanReadVirtualRecord:
    async def test_grants_when_any_owning_record_is_readable(self) -> None:
        graph = _graph()

        assert await caller_can_read_virtual_record(
            graph, user_id="user-1", org_id="org-1", virtual_record_id="vrid-1", logger=LOGGER,
        )

        graph.check_record_access_with_details.assert_awaited_once_with("user-1", "org-1", "rec-1")

    async def test_denies_when_the_access_check_returns_nothing(self) -> None:
        graph = _graph(allowed=False)

        assert not await caller_can_read_virtual_record(
            graph, user_id="user-1", org_id="org-1", virtual_record_id="vrid-1", logger=LOGGER,
        )

    async def test_denies_without_a_user(self) -> None:
        graph = _graph()

        assert not await caller_can_read_virtual_record(
            graph, user_id="", org_id="org-1", virtual_record_id="vrid-1", logger=LOGGER,
        )
        graph.get_records_by_virtual_record_id.assert_not_called()

    async def test_denies_when_no_record_owns_the_virtual_id(self) -> None:
        graph = _graph(record_ids=[])

        assert not await caller_can_read_virtual_record(
            graph, user_id="user-1", org_id="org-1", virtual_record_id="vrid-1", logger=LOGGER,
        )
        graph.check_record_access_with_details.assert_not_called()

    async def test_denies_when_the_lookup_fails(self) -> None:
        graph = _graph()
        graph.get_records_by_virtual_record_id.side_effect = RuntimeError("graph down")

        assert not await caller_can_read_virtual_record(
            graph, user_id="user-1", org_id="org-1", virtual_record_id="vrid-1", logger=LOGGER,
        )
        graph.check_record_access_with_details.assert_not_called()

    async def test_service_account_reads_an_org_granted_record(self) -> None:
        graph = _graph(allowed=False)
        graph.get_edge.return_value = {"type": "ORGANIZATION", "role": "READER"}

        assert await caller_can_read_virtual_record(
            graph, user_id="sa-1", org_id="org-1", virtual_record_id="vrid-1", logger=LOGGER,
            is_service_account=True,
        )

    async def test_service_account_cannot_read_a_private_record(self) -> None:
        graph = _graph(allowed=False)
        graph.get_edge.return_value = None

        assert not await caller_can_read_virtual_record(
            graph, user_id="sa-1", org_id="org-1", virtual_record_id="vrid-1", logger=LOGGER,
            is_service_account=True,
        )

    async def test_org_edge_does_not_grant_a_regular_user(self) -> None:
        graph = _graph(allowed=False)
        graph.get_edge.return_value = {"type": "ORGANIZATION", "role": "READER"}

        assert not await caller_can_read_virtual_record(
            graph, user_id="user-1", org_id="org-1", virtual_record_id="vrid-1", logger=LOGGER,
        )
        graph.get_edge.assert_not_called()
