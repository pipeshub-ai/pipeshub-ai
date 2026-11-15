"""GitLab and GitHub tell a reindex which records their current indexing filters exclude."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.core.registry.filters import Filter, FilterCollection
from app.connectors.sources.github_teams.connector import GitHubTeamsConnector
from app.connectors.sources.gitlab.connector import GitLabConnector

pytestmark = pytest.mark.anyio

MERGE_REQUEST = SimpleNamespace(id="mr", record_type="PULL_REQUEST", external_record_id="ext-mr")
MR_ATTACHMENT = SimpleNamespace(id="mr-att", record_type="FILE", parent_external_record_id="ext-mr")
ISSUE = SimpleNamespace(id="issue", record_type="TICKET", external_record_id="ext-issue")


def _filters(**values: bool) -> FilterCollection:
    return FilterCollection(filters=[
        Filter(key=key, value=value, type="boolean", operator="is") for key, value in values.items()
    ])


CONNECTORS = pytest.mark.parametrize(
    ("connector_cls", "module"),
    [
        pytest.param(GitLabConnector, "app.connectors.sources.gitlab.connector", id="gitlab"),
        pytest.param(GitHubTeamsConnector, "app.connectors.sources.github_teams.connector", id="github"),
    ],
)


@CONNECTORS
async def test_reads_the_saved_filters_not_the_ones_from_the_last_sync(connector_cls, module) -> None:
    c = MagicMock()
    c.connector_id = "conn-1"
    c.config_service = AsyncMock()
    # Loaded by the last sync, before merge requests were switched off.
    c.indexing_filters = _filters(merge_requests=True)
    c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=MERGE_REQUEST)
    saved = (FilterCollection(), _filters(merge_requests=False))

    with patch(f"{module}.load_connector_filters_strict", AsyncMock(return_value=saved)) as load:
        excluded = await connector_cls.records_excluded_by_indexing_filters(
            c, [MERGE_REQUEST, MR_ATTACHMENT, ISSUE],
        )

    assert excluded == {"mr", "mr-att"}
    assert load.await_args.args[2] == "conn-1"
    c.data_entities_processor.get_record_by_external_id.assert_awaited_once_with("conn-1", "ext-mr")


@CONNECTORS
async def test_a_failed_filter_read_stops_the_reindex(connector_cls, module) -> None:
    # Empty filters would read as "every type on" and index what the admin switched off.
    c = MagicMock()
    c.connector_id = "conn-1"
    c.config_service = AsyncMock()
    c.config_service.get_config.side_effect = RuntimeError("config store unreachable")

    with pytest.raises(RuntimeError, match="config store unreachable"):
        await connector_cls.records_excluded_by_indexing_filters(c, [MERGE_REQUEST])
