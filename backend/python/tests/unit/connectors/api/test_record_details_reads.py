"""H4 DM-2: the details route of a record the user reads themselves does not read the record a second time.

``check_record_access_with_details`` already answers the record (with its ``connectorId``); the route read the same
document again only to find the connector for the demo-data check. The answer is unchanged.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.connectors.api import router
from tests.support.record_access import InMemoryRecordAccess


def _request() -> MagicMock:
    request = MagicMock()
    request.state.user = {"orgId": "o1", "userId": "u1"}
    request.app.container.logger.return_value = MagicMock()
    request.app.container.config_service.return_value = MagicMock()
    return request


def _access() -> InMemoryRecordAccess:
    return InMemoryRecordAccess().readable("u1", "o1", "r1", recordName="policy.pdf", connectorId="c1")


def _graph(access: InMemoryRecordAccess) -> MagicMock:
    graph = MagicMock()
    access.serve(graph)
    graph.get_document = AsyncMock(side_effect=AssertionError("the route read the record again"))
    return graph


async def test_dm2_own_access_answers_the_details_without_a_second_record_read() -> None:
    access = _access()
    hidden = AsyncMock(return_value=False)
    with patch.object(router, "is_hidden_demo_record", hidden):
        details = await router.get_record_by_id("r1", _request(), graph_provider=_graph(access))
    assert details == {**await access.check_record_access_with_details("u1", "o1", "r1"), "stageStates": []}
    assert hidden.await_args is not None and hidden.await_args.args[-1] == "c1"


async def test_dm2_a_switched_off_demo_record_is_still_not_found() -> None:
    with patch.object(router, "is_hidden_demo_record", AsyncMock(return_value=True)), pytest.raises(
        HTTPException
    ) as refused:
        await router.get_record_by_id("r1", _request(), graph_provider=_graph(_access()))
    assert refused.value.status_code == 404
