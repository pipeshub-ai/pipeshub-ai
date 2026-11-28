"""Jira Cloud: a group list (GET /rest/api/3/group/bulk) that answers 400 or 404 was not read, so a full sync unlinks no group.

Jira documents 400 as "the request is not valid" and no 404 for the list
(https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-groups/). Taken as "no groups", every group the full
sync did not see was unlinked, and what named them (a comment restricted to one) stayed closed until its issue changed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.unit.connectors.sources.test_jira_cloud_connector import (
    _make_connector,
    _make_mock_response,
)

if TYPE_CHECKING:
    from app.connectors.sources.atlassian.jira_cloud.connector import JiraConnector


def _connector(status) -> JiraConnector:
    connector = _make_connector()
    connector.data_source = MagicMock()
    connector._call_with_retry = AsyncMock(return_value=_make_mock_response(status))
    connector.notify = AsyncMock()
    connector._kept_unread_access = False
    connector._kept_after_refusal = []
    return connector


@pytest.mark.parametrize("status", [400, 404])
async def test_a_group_list_refused_as_a_bad_request_leaves_the_sync_incomplete(status) -> None:
    connector = _connector(status)
    groups, failed = await connector._fetch_groups()
    assert (groups, failed) == ([], True)
    assert connector.full_sync_coverage().complete is False


@pytest.mark.parametrize(("status", "complete"), [(403, True), (500, False), (429, False)])
async def test_the_other_statuses_are_as_before(status, complete) -> None:
    connector = _connector(status)
    await connector._fetch_groups()
    assert connector.full_sync_coverage().complete is complete
