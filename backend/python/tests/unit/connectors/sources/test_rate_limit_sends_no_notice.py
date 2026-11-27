"""A rate limit is not something the connector's owner can fix, so it is not reported as one."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.core.base.connector.failure_notices import SyncRunState
from app.connectors.sources.github_teams.users import UsersSync
from app.connectors.sources.gitlab.connector import GitLabConnector
from tests.unit.connectors.sources.test_github_teams.conftest import make_mock_connector

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(("rate_limited", "noted"), [(True, False), (False, True)])
async def test_a_member_listing_refused_by_a_rate_limit_asks_for_no_scope(*, rate_limited: bool, noted: bool) -> None:
    c = make_mock_connector()
    c.run_state = SyncRunState()
    refused = MagicMock(success=False, data=None, error="403 Forbidden", status_code=403, is_rate_limited=rate_limited)
    c.runtime.ds_call = AsyncMock(return_value=refused)
    await UsersSync(c)._discover_principals(["acme"])
    assert bool(c.run_state.problems) is noted


def test_a_gitlab_read_cut_off_by_a_rate_limit_marks_the_run() -> None:
    c = object.__new__(GitLabConnector)
    c.logger = logging.getLogger("test.gitlab")
    c.run_state = SyncRunState()
    c.sync_problems = []
    c.note_failed_read("issues of group/project", MagicMock(data=None, status_code=429, error="429 Too Many Requests"))
    assert c.run_state.rate_limited is True
    assert c.sync_problems
