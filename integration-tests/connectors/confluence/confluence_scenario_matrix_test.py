# pyright: ignore-file

"""Confluence Cloud in the shared scenario matrix (``connectors/scenario_matrix.py``).

Items are root-level pages this module creates in the static IT space
(``CONFLUENCE_TEST_SPACE_KEY``) and deletes again at teardown. The connector's
incremental sync finds changes through Confluence's content search ordered by
``lastModified``, and that index lags the write API, so every source action
here waits until the change is visible through the same search before the
harness syncs. That lag is why the older mutation cases were parked in
``confluence_mutation_cases.py``; the matrix bounds each wait instead.

The filter scenario uses the connector's own ``page_ids`` filter with
``not_in`` for the one page it excludes.
"""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio

from app.sources.external.confluence.confluence import (  # type: ignore[import-not-found]
    ConfluenceDataSource,
)
from connectors.confluence.confluence_v1_test_utils import (
    check_page_in_v1_search_bool,
    check_page_title_bool,
    check_version_equals_bool,
    get_confluence_page_version_number_v1,
    wait_until_confluence_condition,
)
from connectors.scenario_matrix import (
    FILTER_KEEPS_EXCLUDED_ITEM,
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

logger = logging.getLogger("confluence-matrix")

# Bounded, unlike the suite's 30-minute settle ceiling: a page that search has not
# indexed in ten minutes is a Confluence incident, not something to wait out.
_SOURCE_VISIBLE_TIMEOUT_SEC = int(os.getenv("CONFLUENCE_MATRIX_VISIBLE_TIMEOUT_SEC", "600"))
_POLL_SEC = 15


def _storage(text: str) -> dict[str, Any]:
    return {"representation": "storage", "value": f"<p>{text}</p>"}


class ConfluenceAdapter(ScenarioAdapter):
    source = "Confluence"

    def __init__(
        self, *, confluence: ConfluenceDataSource, space_id: str, space_key: str, **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self.confluence = confluence
        self.space_id = space_id
        self.space_key = space_key
        self.created: list[str] = []

    async def _wait(self, check: Any, description: str) -> None:
        await wait_until_confluence_condition(
            check_fn=check, description=description,
            timeout=_SOURCE_VISIBLE_TIMEOUT_SEC, poll_interval=_POLL_SEC,
        )

    async def create_item(self, role: Role, text: str, token: str) -> SourceItem:
        title = f"PipesHub IT matrix {role.value} {token}"
        resp = await self.confluence.create_page(
            root_level=True,
            body={"spaceId": self.space_id, "status": "current", "title": title,
                  "body": _storage(text)},
        )
        assert resp.status in (200, 201), f"create_page {title!r}: HTTP {resp.status}"
        page_id = str(resp.json()["id"])
        self.created.append(page_id)
        await self._wait(
            lambda: check_page_in_v1_search_bool(self.confluence, self.space_key, page_id),
            f"page {page_id} visible in content search",
        )
        return SourceItem(role=role, key=page_id, record_name=title, text=text, token=token,
                          external_id=page_id)

    async def _version(self, page_id: str) -> int:
        return int(await get_confluence_page_version_number_v1(self.confluence, page_id))

    async def update_content(self, item: SourceItem, text: str, token: str) -> SourceItem:
        version = await self._version(item.key) + 1
        resp = await self.confluence.update_page(
            id=int(item.key),
            body={"id": item.key, "status": "current", "title": item.record_name,
                  "body": _storage(text), "version": {"number": version}},
        )
        assert resp.status == 200, f"update_page {item.key}: HTTP {resp.status}"
        await self._wait(
            lambda: check_version_equals_bool(self.confluence, item.key, version),
            f"page {item.key} at version {version}",
        )
        return SourceItem(role=item.role, key=item.key, record_name=item.record_name, text=text,
                          token=token, external_id=item.external_id, extra=dict(item.extra))

    async def update_metadata(self, item: SourceItem) -> SourceItem:
        title = f"PipesHub IT matrix renamed {item.token}"
        resp = await self.confluence.update_page_title(
            id=int(item.key), body={"status": "current", "title": title},
        )
        assert resp.status == 200, f"update_page_title {item.key}: HTTP {resp.status}"
        await self._wait(
            lambda: check_page_title_bool(self.confluence, item.key, title),
            f"page {item.key} titled {title!r}",
        )
        return SourceItem(role=item.role, key=item.key, record_name=title, text=item.text,
                          token=item.token, external_id=item.external_id, extra=dict(item.extra))

    async def delete_item(self, item: SourceItem) -> None:
        resp = await self.confluence.delete_page(id=int(item.key))
        assert resp.status in (200, 204, 404), f"delete_page {item.key}: HTTP {resp.status}"

    async def exclusion_filter(self, excluded: SourceItem, kept: list[SourceItem]) -> dict[str, Any]:
        return {
            "sync": {
                "values": {
                    "space_keys": {"operator": "in", "type": "list", "value": [self.space_key]},
                    "page_ids": {"operator": "not_in", "type": "list", "value": [excluded.key]},
                }
            }
        }

    async def cleanup(self) -> None:
        for page_id in self.created:
            try:
                await self.confluence.delete_page(id=int(page_id))
            except Exception as e:  # noqa: BLE001 - one stuck page must not strand the rest
                logger.warning("TEARDOWN: could not delete page %s: %s", page_id, e)


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def scenario_adapter(
    confluence_datasource: ConfluenceDataSource,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[ConfluenceAdapter, None]:
    space_key = (os.getenv("CONFLUENCE_TEST_SPACE_KEY") or "").strip()
    if not space_key:
        source_unavailable(
            "No Confluence space is named for this suite to write its pages to.",
            secrets=["CONFLUENCE_TEST_SPACE_KEY"],
        )
    resp = await confluence_datasource.get_spaces(keys=[space_key])
    spaces = resp.json().get("results") or []
    if not spaces:
        pytest.fail(f"Confluence space {space_key!r} not found")
    space_id = str(spaces[0]["id"])

    instance = pipeshub_client.create_connector(
        connector_type="Confluence",
        instance_name=f"confluence-matrix-{uuid.uuid4().hex[:8]}",
        scope="team",
        config={
            "auth": {
                "authType": "API_TOKEN",
                "baseUrl": os.getenv("CONFLUENCE_TEST_BASE_URL"),
                "email": os.getenv("CONFLUENCE_TEST_EMAIL"),
                "apiToken": os.getenv("CONFLUENCE_TEST_API_TOKEN"),
            },
            "filters": {
                "sync": {
                    "values": {
                        "space_keys": {"operator": "in", "type": "list", "value": [space_key]}
                    }
                }
            },
        },
        auth_type="API_TOKEN",
    )
    connector_id = instance.connector_id
    assert connector_id, "Connector must have a valid ID"
    adapter = ConfluenceAdapter(
        confluence=confluence_datasource,
        space_id=space_id,
        space_key=space_key,
        client=pipeshub_client,
        graph=graph_provider,
        connector_id=connector_id,
    )
    try:
        pipeshub_client.toggle_sync(connector_id, enable=True)
        await wait_for_sync_completion(pipeshub_client, graph_provider, connector_id, timeout=300)
        yield adapter
    finally:
        await adapter.cleanup()
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
@pytest.mark.confluence
class TestConfluenceScenarioMatrix(ConnectorScenarioMatrix):
    SOURCE = "Confluence"
    UNSUPPORTED = {
        Action.CHANGE_PERMISSION.value: (
            "the CI site has one Confluence account (CONFLUENCE_TEST_EMAIL); a page "
            "restriction needs a second Confluence user whose email is also a PipesHub user"
        ),
    }
    KNOWN_BUGS = {
        "filter_change": FILTER_KEEPS_EXCLUDED_ITEM,
        "incr_delete": (
            "A page deleted in Confluence is never removed: the connector finds changes "
            "with a lastModified content search (sources/atlassian/confluence_cloud/"
            "connector.py, _sync_content), which does not return deleted or trashed "
            "pages, and nothing in the connector calls on_record_deleted, so the record "
            "and its vectors stay searchable."
        ),
    }
