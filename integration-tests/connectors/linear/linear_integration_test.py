# pyright: ignore-file

"""
Linear Connector – Integration Tests
=====================================

Test cases:
  TC-SYNC-001  — Full sync + graph validation (smoke)
  TC-INCR-001  — Incremental sync (create new issues)
"""

import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.sources.external.linear.linear import (  # type: ignore[import-not-found]  # noqa: E402
    LinearDataSource,
)
from helper.graph_provider import GraphProviderProtocol  # noqa: E402
from helper.graph_provider_utils import wait_for_sync_completion  # noqa: E402
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]  # noqa: E402
from connectors.linear.linear_test_utils import (  # noqa: E402
    check_issue_exists_bool,
    wait_until_linear_condition,
)

logger = logging.getLogger("linear-lifecycle-test")


def _restart_sync(pipeshub_client: PipeshubClient, connector_id: str) -> None:
    """Disable then re-enable the connector to trigger a fresh incremental sync.

    The trailing wait is critical: after ``enable=True`` the connector takes a
    moment to transition from IDLE → SYNCING. Without it, ``wait_for_sync_completion``
    can poll status while it's still IDLE and return immediately — sync never ran.
    """
    pipeshub_client.toggle_sync(connector_id, enable=False)
    pipeshub_client.wait(5)
    pipeshub_client.toggle_sync(connector_id, enable=True)
    pipeshub_client.wait(8)


pytestmark = [
    pytest.mark.integration,
    pytest.mark.linear,
    pytest.mark.asyncio(loop_scope="session"),
]


class TestLinearConnector:
    """Sync-pipeline tests: full sync smoke + incremental sync."""

    # ---------------------------------------------------------------------
    # TC-SYNC-001 — Full sync + graph validation
    # ---------------------------------------------------------------------
    @pytest.mark.order(1)
    async def test_tc_sync_001_full_sync_graph_validation(
        self,
        linear_connector: Dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-SYNC-001: validate the graph after the fixture's initial sync.

        Smoke check on structural soundness — the seeded Linear issues turned
        into Records, the team / project shows up as a RecordGroup, every
        Record has a BELONGS_TO edge, and the App→RecordGroup edge exists.
        Specific record properties are NOT validated here; that belongs in a
        future TC-LN-004-style test.
        """
        connector_id = linear_connector["connector_id"]
        uploaded = linear_connector["uploaded_count"]
        full_count = linear_connector["full_sync_count"]

        await graph_provider.assert_min_records(connector_id, uploaded)
        await graph_provider.assert_record_groups_and_edges(
            connector_id, min_groups=1, min_record_edges=full_count,
        )
        await graph_provider.assert_app_record_group_edges(connector_id, min_edges=1)
        await graph_provider.assert_no_orphan_records(connector_id)

        summary = await graph_provider.graph_summary(connector_id)
        perms = await graph_provider.count_permission_edges(connector_id)
        logger.info(
            "TC-SYNC-001 passed: connector=%s, summary=%s, perms=%d, uploaded=%d, full_sync=%d",
            connector_id, summary, perms, uploaded, full_count,
        )

    # ---------------------------------------------------------------------
    # TC-INCR-001 — Incremental sync via re-fetch
    # ---------------------------------------------------------------------
    @pytest.mark.order(2)
    async def test_tc_incr_001_incremental_sync_new_issues(
        self,
        linear_connector: Dict[str, Any],
        linear_datasource: LinearDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-INCR-001: create 2 new issues, restart sync, verify they land in the graph."""
        connector_id = linear_connector["connector_id"]
        team_id = linear_connector["team_id"]
        before_count = await graph_provider.count_records(connector_id)

        # Create 2 ad-hoc issues. Cleanup is handled by the conftest's
        # ``teamDelete`` at teardown — the team-level cascade picks up these
        # issues, so no per-issue tracking is needed here.
        title_a = f"IncrTestAlpha-{uuid.uuid4().hex[:8]}"
        title_b = f"IncrTestBeta-{uuid.uuid4().hex[:8]}"
        new_ids = []
        for title in (title_a, title_b):
            resp = await linear_datasource.issueCreate(
                input={
                    "title": title,
                    "teamId": team_id,
                    "description": "Incremental sync test issue.",
                }
            )
            assert resp.success, f"issueCreate '{title}' failed: {resp.message}"
            issue = ((resp.data or {}).get("issueCreate") or {}).get("issue") or {}
            assert issue.get("id"), f"issueCreate response missing id for '{title}'"
            new_ids.append(str(issue["id"]))

        # Confirm both issues are immediately fetchable from Linear (no
        # search-index lag like Atlassian's enhanced JQL — but poll briefly
        # to handle any transient 5xx / network hiccup).
        await wait_until_linear_condition(
            check_fn=lambda: _all_exist(linear_datasource, new_ids),
            description="TC-INCR-001: both new issues fetchable",
            timeout=60,
            poll_interval=5,
        )

        _restart_sync(pipeshub_client, connector_id)
        after_count = await wait_for_sync_completion(
            pipeshub_client, graph_provider, connector_id,
            min_records=before_count + 2, timeout=300,
        )

        assert after_count >= before_count + 2, (
            f"Expected ≥2 new records; before={before_count}, after={after_count}"
        )

        # Both new issues should be queryable in the graph by externalRecordId.
        for ext_id in new_ids:
            record = await graph_provider.get_record_by_external_id(connector_id, ext_id)
            assert record is not None, (
                f"New Linear issue id={ext_id} not found in graph after sync "
                f"(before_count={before_count}, after_count={after_count})"
            )

        logger.info(
            "TC-INCR-001 passed: %d -> %d records (added 2 issues: %s)",
            before_count, after_count, new_ids,
        )


async def _all_exist(datasource: LinearDataSource, issue_ids: list[str]) -> bool:
    """True when every ``issue_id`` resolves via ``issue(id=...)``."""
    for ext_id in issue_ids:
        if not await check_issue_exists_bool(datasource, ext_id):
            return False
    return True
