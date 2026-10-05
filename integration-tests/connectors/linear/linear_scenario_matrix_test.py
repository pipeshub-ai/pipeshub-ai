# pyright: ignore-file

"""Linear in the shared scenario matrix (``connectors/scenario_matrix.py``).

Items are issues this run creates in the mutation team (the secondary team in
``LINEAR_TEST_TEAM_IDS`` when there is one, see ``pick_mutation_team``), titled in
the ``LinearIT-<run_id>-<Kind>-<hex>`` form the suite's sweep owns, with the
scenario text in the description. The connector is scoped to that team with
``team_ids``. Deleting trashes the issue; the connector's trashed-issue pass
(``_sync_deleted_issues``) is what removes the record.

Search runs as the admin that created the connector: Linear registers the
API-token account as the source identity the creator authenticated as.
"""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio

from app.sources.external.linear.linear import LinearDataSource  # type: ignore[import-not-found]
from connectors.linear.constants import artifact_title
from connectors.linear.linear_test_utils import (
    _api_call_with_retry,
    check_issue_exists_bool,
    delete_artifact_issue,
    linear_artifacts,
    pick_mutation_team,
    reap_own_artifacts,
    wait_until_linear_condition,
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

logger = logging.getLogger("linear-matrix")


class LinearAdapter(ScenarioAdapter):
    source = "Linear"

    def __init__(self, *, linear: LinearDataSource, team_id: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.linear = linear
        self.team_id = team_id

    async def create_item(self, role: Role, text: str, token: str) -> SourceItem:
        title = artifact_title("Matrix")
        resp = await _api_call_with_retry(
            self.linear.issueCreate,
            input={"teamId": self.team_id, "title": title, "description": text},
            context=f"matrix issueCreate {role.value}",
        )
        issue = ((resp.data or {}).get("issueCreate") or {}).get("issue") or {}
        issue_id, identifier = issue.get("id"), issue.get("identifier")
        assert issue_id and identifier, f"issueCreate returned no id/identifier: {issue}"
        linear_artifacts.register(issue_id, title)
        await wait_until_linear_condition(
            check_fn=lambda: check_issue_exists_bool(self.linear, issue_id),
            description=f"matrix issue {identifier} fetchable", timeout=120,
        )
        return SourceItem(
            role=role, key=issue_id, record_name=f"[{identifier}] {title}", text=text,
            token=token, external_id=issue_id, extra={"identifier": identifier},
        )

    async def _update(self, item: SourceItem, fields: dict[str, Any], what: str) -> None:
        await _api_call_with_retry(
            self.linear.issueUpdate, id=item.key, input=fields,
            context=f"matrix issueUpdate {what} {item.extra['identifier']}",
        )

    async def update_content(self, item: SourceItem, text: str, token: str) -> SourceItem:
        await self._update(item, {"description": text}, "description")
        return SourceItem(role=item.role, key=item.key, record_name=item.record_name, text=text,
                          token=token, external_id=item.external_id, extra=dict(item.extra))

    async def update_metadata(self, item: SourceItem) -> SourceItem:
        title = artifact_title("Renamed")
        await self._update(item, {"title": title}, "title")
        return SourceItem(role=item.role, key=item.key,
                          record_name=f"[{item.extra['identifier']}] {title}", text=item.text,
                          token=item.token, external_id=item.external_id, extra=dict(item.extra))

    async def delete_item(self, item: SourceItem) -> None:
        assert await delete_artifact_issue(
            self.linear, issue_id=item.key, context="matrix delete",
        ), f"Linear never confirmed {item.extra['identifier']} trashed"


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def scenario_adapter(
    linear_datasource: LinearDataSource,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[LinearAdapter, None]:
    team_ids = [t.strip() for t in os.getenv("LINEAR_TEST_TEAM_IDS", "").split(",") if t.strip()]
    if not team_ids:
        source_unavailable(
            "No Linear teams are named for this suite to write its issues to.",
            secrets=["LINEAR_TEST_TEAM_IDS"],
        )
    team_id = pick_mutation_team(team_ids)

    instance = pipeshub_client.create_connector(
        connector_type="Linear",
        instance_name=f"linear-matrix-{uuid.uuid4().hex[:8]}",
        scope="team",
        config={
            "auth": {"authType": "API_TOKEN", "apiToken": os.getenv("LINEAR_TEST_API_TOKEN")},
            "filters": {
                "sync": {
                    "values": {"team_ids": {"operator": "in", "type": "list", "value": [team_id]}}
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
        yield LinearAdapter(
            linear=linear_datasource,
            team_id=team_id,
            client=pipeshub_client,
            graph=graph_provider,
            connector_id=connector_id,
        )
    finally:
        try:
            await reap_own_artifacts(linear_datasource, [team_id])
        except Exception as e:  # noqa: BLE001 - a later run's age-gated sweep catches leaks
            logger.warning("TEARDOWN: issue reap failed: %s", e)
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
@pytest.mark.linear
class TestLinearScenarioMatrix(ConnectorScenarioMatrix):
    SOURCE = "Linear"
    UNSUPPORTED = {
        Action.CHANGE_PERMISSION.value: (
            "Linear has no per-issue share: an issue is visible to its team (the connector "
            "maps team privacy to the team record group), and CI has a single Linear account"
        ),
        Action.SET_FILTER.value: (
            "the connector's sync filters are team ids and created/modified dates; the matrix "
            "issues share one mutation team, and a date cut that drops one of them also drops "
            "the issue the later indexing scenario creates"
        ),
    }
