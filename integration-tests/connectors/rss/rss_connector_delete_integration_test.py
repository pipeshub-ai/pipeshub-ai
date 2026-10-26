# pyright: ignore-file

"""
RSS Connector – Deletion Guard Integration Tests
================================================

Deleting an RSS connector, against the guards the platform puts around it.
Each test makes a connector of its own, since each one deletes it.

Test cases:
  TC-DEL-001 — Only an admin or the creator can delete; a second delete while one runs is refused
  TC-DEL-002 — Delete is refused while an agent uses the connector as knowledge, naming the agent
  TC-DEL-003 — A delete during a running sync stops it, and the sync writes nothing afterwards
"""

import asyncio
import logging
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
import requests

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from connector_lifecycle import create_connector_and_await_sync  # noqa: E402
from connectors.rss.conftest import NEWS_FEED, NEWS_ITEMS  # noqa: E402  # type: ignore[import-not-found]
from helper.clients.agents_client import AgentsClient  # noqa: E402
from helper.graph_provider import GraphProviderProtocol  # noqa: E402
from helper.graph_provider_utils import wait_until_graph_condition  # noqa: E402
from helper.second_user import SecondUser, second_user  # noqa: E402, F401 - fixture
from helper.web_fixtures import WebFixtures  # noqa: E402
from pipeshub_client import PipeshubClient  # noqa: E402  # type: ignore[import-not-found]

logger = logging.getLogger("rss-delete-test")

CLEANUP_TIMEOUT = 300
# The delete runs in the background: the route answers 202 once it has started.
DELETE_ACCEPTED = (200, 202)


async def _new_connector(
    pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol, rss_fixtures: WebFixtures
) -> str:
    rss_fixtures.reset()
    state: dict[str, Any] = {}
    await create_connector_and_await_sync(
        pipeshub_client, graph_provider, state,
        connector_type="RSS", connector_name=f"rss-delete-{uuid.uuid4().hex[:8]}",
        connector_config={"sync": {"feed_urls": rss_fixtures.url_for_connector(NEWS_FEED),
                                   "max_articles_per_feed": 3, "fetch_full_content": True}},
        expected_records=3, scope="team",
    )
    return state["connector_id"]


def _delete(pipeshub_client: PipeshubClient, connector_id: str) -> requests.Response:
    return pipeshub_client.request("DELETE", f"/api/v1/connectors/{connector_id}")


async def _delete_and_wait_clean(
    pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol, connector_id: str
) -> None:
    resp = _delete(pipeshub_client, connector_id)
    # 409: a delete the test started is still running; 404: it already finished.
    assert resp.status_code in (*DELETE_ACCEPTED, 404, 409), f"delete: {resp.status_code} {resp.text[:300]}"
    await graph_provider.assert_all_records_cleaned(connector_id, timeout=CLEANUP_TIMEOUT)


@pytest.mark.integration
@pytest.mark.rss
@pytest.mark.asyncio(loop_scope="session")
class TestRSSConnectorDeletion:

    @pytest.mark.order(1)
    async def test_tc_del_001_only_admin_deletes_and_a_second_delete_is_refused(
        self,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        rss_fixtures: WebFixtures,
        second_user: SecondUser,  # noqa: F811
    ) -> None:
        """TC-DEL-001 (checklist 7.9)."""
        connector_id = await _new_connector(pipeshub_client, graph_provider, rss_fixtures)
        before = await graph_provider.count_records(connector_id)
        try:
            refused = requests.delete(
                f"{pipeshub_client.base_url}/api/v1/connectors/{connector_id}",
                headers=second_user.headers, timeout=60,
            )
            assert refused.status_code in (403, 404), (
                f"TC-DEL-001: a member who did not create the team connector deleted it: "
                f"{refused.status_code} {refused.text[:300]}"
            )
            assert await graph_provider.count_records(connector_id) == before

            first = _delete(pipeshub_client, connector_id)
            second = _delete(pipeshub_client, connector_id)
            assert first.status_code in DELETE_ACCEPTED, f"TC-DEL-001: admin delete: {first.status_code} {first.text[:300]}"
            # 404 only if the first delete already finished; while it runs the answer is 409.
            assert second.status_code in (409, 404), (
                f"TC-DEL-001: a second delete while one runs: {second.status_code} {second.text[:300]}"
            )
            logger.info("TC-DEL-001: second delete answered %s: %s", second.status_code, second.text[:200])
        finally:
            await _delete_and_wait_clean(pipeshub_client, graph_provider, connector_id)

    @pytest.mark.order(2)
    async def test_tc_del_002_delete_is_refused_while_an_agent_uses_the_connector(
        self,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        rss_fixtures: WebFixtures,
        agents_client: AgentsClient,
    ) -> None:
        """TC-DEL-002 (checklist 7.8)."""
        connector_id = await _new_connector(pipeshub_client, graph_provider, rss_fixtures)
        agent_name = f"it-rss-agent-{uuid.uuid4().hex[:6]}"
        agent_key = None
        try:
            # No models: the agent falls back to the org's default LLM, which this test never calls.
            resp = agents_client.create_agent(
                name=agent_name,
                description="Answers from the RSS feed.",
                systemPrompt="Answer from the attached knowledge.",
                knowledge=[{"connectorId": connector_id, "filters": {}}],
            )
            assert resp.status_code in (200, 201), f"creating the agent: {resp.status_code} {resp.text[:500]}"
            agent_key = (resp.json().get("agent") or {}).get("_key")
            assert agent_key, f"agent create returned no key: {resp.text[:300]}"
            refused = _delete(pipeshub_client, connector_id)
            assert refused.status_code == 409, (
                f"TC-DEL-002: a connector an agent uses was deleted: {refused.status_code} {refused.text[:300]}"
            )
            assert agent_name in refused.text, f"TC-DEL-002: the refusal does not name the agent: {refused.text[:300]}"
            assert await graph_provider.count_records(connector_id) >= 3
        finally:
            if agent_key:
                deleted = agents_client.delete_agent(agent_key)
                assert deleted.status_code in (200, 204, 404), f"deleting the agent: {deleted.status_code}"
            resp = _delete(pipeshub_client, connector_id)
            assert resp.status_code in (*DELETE_ACCEPTED, 404), f"delete after the agent: {resp.status_code}"

            # The deleted agent's AgentKnowledge node keeps the connector id, so the
            # all-nodes check cannot pass here; the connector's own data must still go.
            async def _connector_data_gone() -> bool:
                return (
                    await graph_provider.count_records(connector_id) == 0
                    and await graph_provider.count_record_groups(connector_id) == 0
                )

            await wait_until_graph_condition(
                connector_id, check=_connector_data_gone, timeout=CLEANUP_TIMEOUT, poll_interval=10,
                description="the connector's records and groups to be deleted",
            )

    @pytest.mark.order(3)
    async def test_tc_del_003_delete_during_a_sync_stops_it(
        self,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        rss_fixtures: WebFixtures,
    ) -> None:
        """TC-DEL-003 (checklist 7.7)."""
        connector_id = await _new_connector(pipeshub_client, graph_provider, rss_fixtures)
        slow_paths = [item.article_path for item in NEWS_ITEMS[:3]]
        for path in slow_paths:
            rss_fixtures.add_fault(path, delay=20)
        try:
            pipeshub_client.resync_connector(connector_id, full_sync=True)

            async def _reading_articles() -> bool:
                return rss_fixtures.fault_hits(slow_paths[0]) >= 1

            await wait_until_graph_condition(
                connector_id, check=_reading_articles, timeout=120, poll_interval=2,
                description="the sync to reach the slow article pages",
            )
            resp = _delete(pipeshub_client, connector_id)
            assert resp.status_code in DELETE_ACCEPTED, f"TC-DEL-003: delete during sync: {resp.status_code} {resp.text[:300]}"
            shown = pipeshub_client.request("GET", f"/api/v1/connectors/{connector_id}")
            assert shown.status_code == 404 or "DELETING" in shown.text, (
                f"TC-DEL-003: right after the delete the connector is not shown as deleting: {shown.text[:300]}"
            )

            await graph_provider.assert_all_records_cleaned(connector_id, timeout=CLEANUP_TIMEOUT)
            # The stopped sync must not write once the delete has cleaned up.
            await asyncio.sleep(45)
            assert await graph_provider.count_records(connector_id) == 0, (
                "TC-DEL-003: records came back after the connector was deleted mid-sync"
            )
        finally:
            for path in slow_paths:
                rss_fixtures.remove_fault(path)
            resp = _delete(pipeshub_client, connector_id)
            if resp.status_code not in (*DELETE_ACCEPTED, 404, 409):
                logger.warning("TC-DEL-003 teardown delete: %s %s", resp.status_code, resp.text[:200])
