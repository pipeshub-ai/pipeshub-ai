# pyright: ignore-file

"""Jira Cloud in the shared scenario matrix (``connectors/scenario_matrix.py``).

Items are tickets this run creates in the mutation project (the secondary IT
project when the account can create and delete there, see
``pick_mutation_project``), each with the ``PHIT-<run_id>-Matrix-<hex>`` summary
the suite's artifact sweep owns, and the scenario text in its description. The
connector is scoped to that one project with ``project_keys``.

Search runs as the admin that created the connector: Jira registers the API-token
account as the source identity the creator authenticated as, so the creator
reaches what that account can browse.
"""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import AsyncGenerator
from typing import Any, Optional

import pytest
import pytest_asyncio

from app.sources.external.jira.jira import JiraDataSource  # type: ignore[import-not-found]
from connectors.jira.constants import artifact_summary
from connectors.jira.jira_test_utils import (
    can_delete_issues_in,
    check_issue_exists_bool,
    jira_api_call_with_retry,
    jira_artifacts,
    pick_mutation_project,
    reap_own_artifacts,
    wait_until_jira_condition,
)
from connectors.scenario_matrix import (
    Action,
    ConnectorScenarioMatrix,
    Role,
    ScenarioAdapter,
    SourceItem,
)
from helper.graph_provider import GraphProviderProtocol
from helper.graph_provider_utils import wait_for_sync_completion
from helper.source_credentials import source_unavailable
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

logger = logging.getLogger("jira-matrix")


def _adf(text: str) -> dict[str, Any]:
    return {
        "type": "doc",
        "version": 1,
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
    }


class JiraAdapter(ScenarioAdapter):
    source = "Jira"

    def __init__(self, *, jira: JiraDataSource, project_key: str, issue_type: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.jira = jira
        self.project_key = project_key
        self.issue_type = issue_type

    async def create_item(self, role: Role, text: str, token: str) -> SourceItem:
        summary = artifact_summary("Matrix")
        resp = await jira_api_call_with_retry(
            self.jira.create_issue,
            fields={
                "project": {"key": self.project_key},
                "summary": summary,
                "issuetype": {"name": self.issue_type},
                "description": _adf(text),
            },
            context=f"matrix create_issue {role.value}",
            # A lost 201 must not become a duplicate ticket.
            retry_server_errors=False,
        )
        assert resp.status in (200, 201), f"create_issue {summary!r}: HTTP {resp.status}"
        data = resp.json()
        issue_id, issue_key = str(data["id"]), data["key"]
        jira_artifacts.register(issue_id, issue_key)
        await wait_until_jira_condition(
            check_fn=lambda: check_issue_exists_bool(self.jira, issue_key),
            description=f"matrix issue {issue_key} fetchable", timeout=120,
        )
        return SourceItem(
            role=role, key=issue_key, record_name=f"[{issue_key}] {summary}", text=text,
            token=token, external_id=issue_id, extra={"summary": summary},
        )

    async def _edit(self, item: SourceItem, fields: dict[str, Any], what: str) -> None:
        resp = await jira_api_call_with_retry(
            self.jira.edit_issue, issueIdOrKey=item.key, fields=fields,
            context=f"matrix {what} {item.key}", retry_server_errors=True,
        )
        assert resp.status in (200, 204), f"edit_issue {item.key} ({what}): HTTP {resp.status}"

    async def update_content(self, item: SourceItem, text: str, token: str) -> SourceItem:
        await self._edit(item, {"description": _adf(text)}, "description edit")
        return SourceItem(role=item.role, key=item.key, record_name=item.record_name, text=text,
                          token=token, external_id=item.external_id, extra=dict(item.extra))

    async def update_metadata(self, item: SourceItem) -> SourceItem:
        summary = artifact_summary("Renamed")
        await self._edit(item, {"summary": summary}, "summary edit")
        return SourceItem(role=item.role, key=item.key, record_name=f"[{item.key}] {summary}",
                          text=item.text, token=item.token, external_id=item.external_id,
                          extra={**item.extra, "summary": summary})

    async def delete_item(self, item: SourceItem) -> None:
        resp = await jira_api_call_with_retry(
            self.jira.delete_issue, issueIdOrKey=item.external_id,
            context=f"matrix delete_issue {item.key}", retry_server_errors=True,
        )
        assert resp.status in (200, 202, 204, 404), f"delete_issue {item.key}: HTTP {resp.status}"
        jira_artifacts.release(item.external_id or "")


async def _default_issue_type(jira: JiraDataSource, project_key: str) -> Optional[str]:
    resp = await jira_api_call_with_retry(
        jira.get_create_issue_meta, projectKeys=[project_key], expand="projects.issuetypes",
        context=f"createmeta {project_key}",
    )
    if resp.status != 200:
        return None
    for project in (resp.json() or {}).get("projects") or []:
        names = [str(t.get("name")) for t in project.get("issuetypes") or [] if not t.get("subtask")]
        for preferred in ("Task", "Story", "Bug"):
            if preferred in names:
                return preferred
        if names:
            return names[0]
    return None


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def scenario_adapter(
    jira_datasource: JiraDataSource,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[JiraAdapter, None]:
    project_keys = [k.strip() for k in os.getenv("JIRA_TEST_PROJECT_KEYS", "").split(",") if k.strip()]
    if not project_keys:
        source_unavailable(
            "No Jira projects are named for this suite to write its tickets to.",
            secrets=["JIRA_TEST_PROJECT_KEYS"],
        )
    issue_types = {k: await _default_issue_type(jira_datasource, k) for k in project_keys}
    can_delete = {k: await can_delete_issues_in(jira_datasource, k) for k in project_keys}
    project_key, issue_type = pick_mutation_project(project_keys, issue_types, can_delete)
    if not can_delete.get(project_key):
        pytest.fail(
            f"The IT account cannot delete issues in {project_key!r}; the matrix would leak "
            "tickets and could not test deletion."
        )

    instance = pipeshub_client.create_connector(
        connector_type="Jira",
        instance_name=f"jira-matrix-{uuid.uuid4().hex[:8]}",
        scope="team",
        config={
            "auth": {
                "authType": "API_TOKEN",
                "baseUrl": (os.getenv("JIRA_TEST_BASE_URL") or "").rstrip("/"),
                "email": os.getenv("JIRA_TEST_EMAIL"),
                "apiToken": os.getenv("JIRA_TEST_API_TOKEN"),
            },
            "filters": {
                "sync": {
                    "values": {
                        "project_keys": {"operator": "in", "type": "list", "value": [project_key]}
                    }
                }
            },
        },
        auth_type="API_TOKEN",
    )
    connector_id = instance.connector_id
    assert connector_id, "Connector must have a valid ID"
    try:
        pipeshub_client.toggle_sync(connector_id, enable=True)
        await wait_for_sync_completion(pipeshub_client, graph_provider, connector_id, timeout=300)
        yield JiraAdapter(
            jira=jira_datasource,
            project_key=project_key,
            issue_type=issue_type,
            client=pipeshub_client,
            graph=graph_provider,
            connector_id=connector_id,
        )
    finally:
        try:
            await reap_own_artifacts(jira_datasource, [project_key])
        except Exception as e:  # noqa: BLE001 - a later run's age-gated sweep catches leaks
            logger.warning("TEARDOWN: ticket reap failed: %s", e)
        try:
            pipeshub_client.toggle_sync(connector_id, enable=False)
            pipeshub_client.delete_connector(connector_id)
            pipeshub_client.wait(25)
            await graph_provider.assert_all_records_cleaned(
                connector_id, timeout=int(os.getenv("INTEGRATION_GRAPH_CLEANUP_TIMEOUT", "300")),
            )
        except Exception as e:  # noqa: BLE001 - teardown must not mask the test result
            logger.warning("TEARDOWN: delete/clean failed for %s: %s", connector_id, e)


@pytest.mark.integration
@pytest.mark.jira
class TestJiraScenarioMatrix(ConnectorScenarioMatrix):
    SOURCE = "Jira"
    UNSUPPORTED = {
        Action.CHANGE_PERMISSION.value: (
            "Jira has no per-issue share: tickets inherit their project's browse "
            "permission (the connector writes no direct PERMISSION edges, see TC-SYNC-001), "
            "and the CI site has a single Jira account"
        ),
        Action.SET_FILTER.value: (
            "the connector's sync filters are project keys and created/modified dates; the "
            "matrix tickets share one mutation project and are created within the same "
            "minute (JQL dates truncate to the minute), so no filter leaves out exactly one "
            "of them. Project narrowing is covered by TC-FILTER-002"
        ),
    }
