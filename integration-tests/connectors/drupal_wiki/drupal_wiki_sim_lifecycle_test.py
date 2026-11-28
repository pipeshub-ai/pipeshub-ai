# pyright: ignore-file

"""
Drupal Wiki on the simulator – turning the connector off and on, deleting it, running two
=========================================================================================

  TC-DWSIM-LIFE-001  Disabled and enabled again: the records are the same ones (same ids, versions), nothing is
                     duplicated, and a change made while it was off is picked up
  TC-DWSIM-LIFE-002  Deleting the connector leaves nothing of it in the graph
  TC-DWSIM-LIFE-003  Two connectors on one wiki hold each their own records; deleting one leaves the other whole
  TC-DWSIM-LIFE-004  A sync requested while one runs is refused (HTTP 409), not run twice at once: nothing is duplicated
                     and the result matches the wiki
"""

import pytest
from pipeshub_client import PipeshubClientError
from connectors.drupal_wiki.sim_world import DrupalWorld, succeeded


@pytest.mark.integration
@pytest.mark.drupal_wiki_sim
@pytest.mark.asyncio(loop_scope="session")
class TestDrupalWikiSimLifecycle:

    async def test_tc_dwsim_life_001_disable_and_enable(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start())
        before = {r["externalRecordId"]: (r["id"], r["version"]) for r in await w.records(parents=False)}
        w.client.toggle_sync(w.connector_id, enable=False)
        await w._wait_idle()
        w.mutate(("page_create", {"space": 1, "title": "Written while the connector was off"}))
        assert succeeded(await w.enable())
        after = {r["externalRecordId"]: (r["id"], r["version"]) for r in await w.records(parents=False)}
        assert {k: v for k, v in after.items() if k in before} == before, "records changed identity or version"
        assert len(after) == len(before) + 1
        await w.assert_matches_source("after the connector was enabled again")

    async def test_tc_dwsim_life_002_delete_leaves_nothing(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start())
        connector_id = w.connector_id
        w.client.toggle_sync(connector_id, enable=False)
        await w._wait_idle()
        w.client.delete_connector(connector_id)
        w.connector_id = None
        await w.graph.assert_connector_graph_fully_cleaned(connector_id, timeout=240)

    async def test_tc_dwsim_life_003_two_connectors_on_one_wiki(self, drupal_world: DrupalWorld,
                                                                 drupal_wiki_sim_people, pipeshub_client,
                                                                 graph_provider) -> None:
        first = drupal_world
        assert succeeded(await first.start())
        second = DrupalWorld(first.sim, pipeshub_client, graph_provider, drupal_wiki_sim_people)
        try:
            assert succeeded(await second.start(reset=False))
            await first.assert_matches_source("the first connector, with a second on the same wiki")
            await second.assert_matches_source("the second connector")
            keys_first = {r["id"] for r in await first.records(parents=False)}
            keys_second = {r["id"] for r in await second.records(parents=False)}
            assert keys_first.isdisjoint(keys_second), "two connectors share records"
            first.mutate(("page_edit", {"id": 104, "body_extra": "<p>both see it</p>"}))
            assert succeeded(await first.sync()) and succeeded(await second.sync())
            await first.assert_matches_source("the first, after an edit")
            await second.assert_matches_source("the second, after an edit")
            second.client.toggle_sync(second.connector_id, enable=False)
            await second._wait_idle()
            second.client.delete_connector(second.connector_id)
            second.connector_id = None
            await first.assert_matches_source("the first, after the second was deleted")
        finally:
            second.remove_connector()

    async def test_tc_dwsim_life_004_sync_requested_while_one_runs(self, drupal_world: DrupalWorld) -> None:
        w = drupal_world
        assert succeeded(await w.start())
        w.mutate(("page_edit", {"id": 104, "body_extra": "<p>one</p>"}))
        before = w.connector().get("lastSyncFinishedAtTimestamp") or 0
        answers = []
        for _ in range(3):
            try:
                w.client.resync_connector(w.connector_id, full_sync=False, connector_name="Drupal Wiki")
                answers.append("started")
            except PipeshubClientError as exc:
                assert "409" in str(exc), f"a sync requested while one runs failed in another way: {exc}"
                answers.append("refused")
        assert answers[0] == "started", answers
        await w._wait_idle()
        assert (w.connector().get("lastSyncFinishedAtTimestamp") or 0) > before
        await w.assert_matches_source("after three syncs were requested together")
        assert len(await w.records(parents=False)) == len({r["externalRecordId"] for r in await w.records(parents=False)})
