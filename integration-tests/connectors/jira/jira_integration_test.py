# pyright: ignore-file

"""
Jira Connector – Integration Tests
==================================

Test cases:
  TC-SYNC-001    — Full sync + graph validation
  TC-INCR-001    — Incremental sync (create new issues)
  TC-UPDATE-001  — Summary change detection (edit existing issue)
  TC-SUBTASK-001 — Sub-task creates a parent-child edge
"""

import logging
import sys
import uuid
from pathlib import Path
from typing import Any, Dict

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.sources.external.jira.jira import JiraDataSource  # type: ignore[import-not-found]  # noqa: E402
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]  # noqa: E402
from helper.graph_provider import GraphProviderProtocol  # noqa: E402
from helper.graph_provider_utils import wait_until_graph_condition  # noqa: E402

logger = logging.getLogger("jira-lifecycle-test")


def _adf(text: str) -> Dict[str, Any]:
    """Build a minimal Atlassian Document Format paragraph block."""
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": text}],
            }
        ],
    }


@pytest.mark.integration
@pytest.mark.jira
@pytest.mark.asyncio(loop_scope="session")
class TestJiraConnector:
    """Integration tests for the Jira connector sync pipeline."""

    # ---------------------------------------------------------------------
    # TC-SYNC-001 — Full sync + graph validation
    # ---------------------------------------------------------------------
    @pytest.mark.order(1)
    async def test_tc_sync_001_full_sync_graph_validation(
        self,
        jira_connector: Dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        connector_id = jira_connector["connector_id"]
        uploaded = jira_connector["uploaded_count"]
        full_count = jira_connector["full_sync_count"]

        await graph_provider.assert_min_records(connector_id, uploaded)

        await graph_provider.assert_record_groups_and_edges(
            connector_id,
            min_groups=1,
            min_record_edges=full_count,
        )

        await graph_provider.assert_app_record_group_edges(connector_id, min_edges=1)
        await graph_provider.assert_no_orphan_records(connector_id)

        summary = await graph_provider.graph_summary(connector_id)
        logger.info(
            "TC-SYNC-001 passed: connector=%s, summary=%s",
            connector_id, summary,
        )

    # ---------------------------------------------------------------------
    # TC-INCR-001 — Incremental sync via JQL `updated > <ts>`
    # ---------------------------------------------------------------------
    @pytest.mark.order(2)
    async def test_tc_incr_001_incremental_sync_new_issues(
        self,
        jira_connector: Dict[str, Any],
        jira_datasource: JiraDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        connector_id = jira_connector["connector_id"]
        project_key = jira_connector["project_key"]
        before_count = await graph_provider.count_records(connector_id)

        title_1 = f"IncrTestAlpha-{uuid.uuid4().hex[:8]}"
        title_2 = f"IncrTestBeta-{uuid.uuid4().hex[:8]}"

        for title in (title_1, title_2):
            resp = await jira_datasource.create_issue(
                fields={
                    "project": {"key": project_key},
                    "summary": title,
                    "issuetype": {"name": "Task"},
                    "description": _adf("Incremental sync test issue."),
                }
            )
            assert resp.status in (200, 201), f"Failed to create '{title}': HTTP {resp.status}"
            new_key = resp.json().get("key")
            assert new_key, f"create_issue response missing key for '{title}'"
            # No per-issue tracking needed — project teardown deletes everything.

        # Brief wait for Jira to register issue timestamps before our JQL re-fetch.
        pipeshub_client.wait(5)
        pipeshub_client.toggle_sync(connector_id, enable=False)
        pipeshub_client.wait(3)
        pipeshub_client.toggle_sync(connector_id, enable=True)

        async def _incr_ok() -> bool:
            return await graph_provider.count_records(connector_id) >= before_count + 2

        await wait_until_graph_condition(
            connector_id,
            check=_incr_ok,
            timeout=240,
            poll_interval=10,
            description="incremental sync (new issues)",
        )

        after_count = await graph_provider.count_records(connector_id)
        assert after_count >= before_count + 2, (
            f"Expected at least 2 new records; before={before_count}, after={after_count}"
        )
        logger.info(
            "TC-INCR-001 passed: %d -> %d records (added 2 issues)",
            before_count, after_count,
        )

    # ---------------------------------------------------------------------
    # TC-UPDATE-001 — Summary change detection (source_updated_at diff)
    # ---------------------------------------------------------------------
    @pytest.mark.order(3)
    async def test_tc_update_001_summary_change_detection(
        self,
        jira_connector: Dict[str, Any],
        jira_datasource: JiraDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        connector_id = jira_connector["connector_id"]
        target_key = jira_connector["seed_issue_keys"][0]
        new_summary = f"UPDATED-{uuid.uuid4().hex[:8]}"

        before_count = await graph_provider.count_records(connector_id)

        resp = await jira_datasource.edit_issue(
            issueIdOrKey=target_key,
            fields={"summary": new_summary},
        )
        assert resp.status in (200, 204), f"edit_issue failed: HTTP {resp.status}"

        pipeshub_client.wait(5)
        pipeshub_client.toggle_sync(connector_id, enable=False)
        pipeshub_client.wait(3)
        pipeshub_client.toggle_sync(connector_id, enable=True)

        # Updates don't change record count — verify by record name appearing.
        async def _renamed_ok() -> bool:
            return await graph_provider.record_path_or_name_contains(
                connector_id, new_summary
            )

        await wait_until_graph_condition(
            connector_id,
            check=_renamed_ok,
            timeout=240,
            poll_interval=10,
            description="summary update detection",
        )

        after_count = await graph_provider.count_records(connector_id)
        assert after_count == before_count, (
            f"Update should not change record count; before={before_count}, after={after_count}"
        )
        logger.info(
            "TC-UPDATE-001 passed: issue %s renamed to '%s', count stable at %d",
            target_key, new_summary, after_count,
        )

    # ---------------------------------------------------------------------
    # TC-SUBTASK-001 — Sub-task creates a parent-child edge
    # ---------------------------------------------------------------------
    @pytest.mark.order(4)
    async def test_tc_subtask_001_parent_child_edge_created(
        self,
        jira_connector: Dict[str, Any],
        jira_datasource: JiraDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        connector_id = jira_connector["connector_id"]
        project_key = jira_connector["project_key"]
        parent_key = jira_connector["seed_issue_keys"][2]
        subtask_type = jira_connector["subtask_issuetype_name"]
        subtask_summary = f"SubTaskTest-{uuid.uuid4().hex[:8]}"

        before_count = await graph_provider.count_records(connector_id)
        before_parent_edges = await graph_provider.count_parent_child_edges(connector_id)

        resp = await jira_datasource.create_issue(
            fields={
                "project": {"key": project_key},
                "parent": {"key": parent_key},
                "summary": subtask_summary,
                "issuetype": {"name": subtask_type},
                "description": _adf("Sub-task for parent-child integration test."),
            }
        )
        if resp.status not in (200, 201):
            pytest.skip(
                f"Sub-task creation rejected (HTTP {resp.status}, type='{subtask_type}'). "
                "Workspace may not have sub-tasks enabled or the issuetype name differs."
            )
        subtask_key = resp.json().get("key")
        assert subtask_key, "Sub-task create response missing key"
        # No per-issue tracking — project teardown cascades.

        pipeshub_client.wait(5)
        pipeshub_client.toggle_sync(connector_id, enable=False)
        pipeshub_client.wait(3)
        pipeshub_client.toggle_sync(connector_id, enable=True)

        async def _subtask_ok() -> bool:
            has_name = await graph_provider.record_path_or_name_contains(
                connector_id, subtask_summary
            )
            edges_after = await graph_provider.count_parent_child_edges(connector_id)
            return has_name and edges_after >= before_parent_edges + 1

        await wait_until_graph_condition(
            connector_id,
            check=_subtask_ok,
            timeout=240,
            poll_interval=10,
            description="sub-task parent-child indexing",
        )

        after_count = await graph_provider.count_records(connector_id)
        after_parent_edges = await graph_provider.count_parent_child_edges(connector_id)
        assert after_count >= before_count + 1, (
            f"Expected sub-task as a new record; before={before_count}, after={after_count}"
        )
        assert after_parent_edges >= before_parent_edges + 1, (
            f"Expected new parent-child edge for sub-task; "
            f"before={before_parent_edges}, after={after_parent_edges}"
        )
        logger.info(
            "TC-SUBTASK-001 passed: sub-task %s under parent %s, parent-child edges %d -> %d",
            subtask_key, parent_key, before_parent_edges, after_parent_edges,
        )
