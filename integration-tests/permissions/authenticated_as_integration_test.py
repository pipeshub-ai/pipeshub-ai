# pyright: ignore-file

"""
authenticatedAs — Integration Tests (seeded graph, both backends)
==================================================================

A connector creator whose PipesHub email differs from the account the connector
authenticated with is joined to that account by an ``authenticatedAs`` edge. Every
permission query then counts both accounts as principals, scoped to that one connector.

The suite runs against whichever graph database ``TEST_GRAPH_DB_TYPE`` selects, so the
two hand-written query dialects are held to the same expectations. The fixture graph is
built in ``conftest.py``:

  app-linked  reached ONLY through the link (the creator has no app relation of their own)
              rg-src-stronger  creator READER, source OWNER
              rg-own-stronger  creator OWNER,  source READER
              rg-src-only      source OWNER only
  app-own     the creator's own connector, no link           rg-own    creator WRITER
  app-other   NOT linked; only the source account has access rg-other  source OWNER

  order 1  TC-AA-LINK-001      — one edge per connector: upsert, repoint, remove
  order 2  TC-AA-APP-001       — the app list reaches a connector the link alone grants
  order 3  TC-AA-ROLE-001      — the stronger of the two accounts wins, both directions
  order 4  TC-AA-SCOPE-001     — a link never reaches into another connector
  order 5  TC-AA-KH-001        — browsing shows records only the source account can see
  order 6  TC-AA-SEARCH-001    — all-records search returns own and linked together
  order 7  TC-AA-SEARCH-002    — scoped search and the connector filter stay scoped
  order 8  TC-AA-RETRIEVAL-001 — chat retrieval covers the linked connector
  order 9  TC-AA-RECORD-001    — opening one record: allowed, denied, and the role shown
  order 10 TC-AA-REINDEX-001   — both reindex gates: the checkers AND the worker listing
  order 11 TC-AA-PROC-001      — the connector-side hook writes the stub and the link
  order 12 TC-AA-NEG-001       — a user with no link is unaffected
"""

from __future__ import annotations

import logging

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors

logger = logging.getLogger(__name__)

# The provider and the seed are session-scoped. Without pinning the tests to the same
# loop, pytest-asyncio gives each one a fresh loop, so the Neo4j client builds (and then
# cannot close) one driver per test.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.authenticated_as,
    pytest.mark.asyncio(loop_scope="session"),
]


async def _links_of(provider, user_key: str) -> dict[str, str]:
    """Connector id -> source user key, read straight off the edge.

    The providers deliberately have no link reader any more: every permission query
    resolves the link inside its own AQL/Cypher, so the edge itself is the only
    backend-agnostic way to assert on it.
    """
    users = CollectionNames.USERS.value
    edges = await provider.get_edges_from_node(f"{users}/{user_key}", CollectionNames.AUTHENTICATED_AS.value)
    return {
        e["connectorId"]: (e.get("_to") or e.get("to_id") or "").split("/")[-1]
        for e in (edges or []) if e.get("connectorId")
    }


def _ids(result: dict) -> set[str]:
    return {n["id"] for n in (result or {}).get("nodes", [])}


async def _children(provider, parent_id: str, parent_type: str, user_key: str, org: str) -> set[str]:
    return _ids(await provider.get_knowledge_hub_children(
        parent_id, parent_type, org, user_key, 0, 100, "name", "ASC"
    ))


async def _search(provider, org: str, user_key: str, **kwargs) -> dict:
    return await provider.get_knowledge_hub_search(org, user_key, 0, 100, "name", "ASC", **kwargs)


class TestAuthenticatedAs:
    """Both accounts' permissions, for one connector only."""

    @pytest.mark.order(1)
    async def test_link_lifecycle(self, graph_provider, seeded_graph) -> None:
        """TC-AA-LINK-001: exactly one link per connector, and removal is reported."""
        creator, source, stranger = seeded_graph["creator"], seeded_graph["source"], seeded_graph["stranger"]
        app, org = seeded_graph["app_linked"], seeded_graph["org"]

        assert await _links_of(graph_provider, creator) == {app: source}

        # Re-running a sync must not duplicate it
        await graph_provider.upsert_authenticated_as(creator, source, app, org)
        assert await _links_of(graph_provider, creator) == {app: source}

        # Someone else re-authenticating repoints it rather than adding a second
        await graph_provider.upsert_authenticated_as(stranger, source, app, org)
        assert await _links_of(graph_provider, creator) == {}
        assert await _links_of(graph_provider, stranger) == {app: source}

        assert await graph_provider.remove_authenticated_as(app) is True
        assert await graph_provider.remove_authenticated_as(app) is False, (
            "a no-op removal must report False, or every sync would drop the cache"
        )
        await graph_provider.upsert_authenticated_as(creator, source, app, org)

    @pytest.mark.order(2)
    async def test_app_list_includes_the_linked_connector(self, graph_provider, seeded_graph) -> None:
        """TC-AA-APP-001: the creator has no app relation of their own to app-linked."""
        creator, org = seeded_graph["creator"], seeded_graph["org"]

        app_ids = await graph_provider.get_user_app_ids(creator)
        assert seeded_graph["app_linked"] in app_ids
        assert app_ids.count(seeded_graph["app_linked"]) == 1, "no duplicate from the link"
        assert {seeded_graph["app_own"], seeded_graph["app_other"]} <= set(app_ids)

        app_keys = {a.get("_key") or a.get("id") for a in await graph_provider.get_user_apps(creator)}
        assert seeded_graph["app_linked"] in app_keys

        node = await graph_provider.get_knowledge_hub_node_access(seeded_graph["app_linked"], creator, org, [])
        assert node and node.get("userRole"), "the app node itself must open, not only its children"

    @pytest.mark.order(3)
    async def test_the_stronger_account_wins(self, graph_provider, seeded_graph) -> None:
        """TC-AA-ROLE-001: max(own, linked), whichever side holds it."""
        creator, org = seeded_graph["creator"], seeded_graph["org"]

        async def role(rg: str) -> str | None:
            result = await graph_provider.get_knowledge_hub_context_permissions(creator, org, rg, None, "recordGroup")
            return (result or {}).get("role")

        assert await role("it-aa-rg-src-stronger") == "OWNER", "source OWNER beats the creator's own READER"
        assert await role("it-aa-rg-own-stronger") == "OWNER", "the creator's own OWNER survives the merge"
        assert await role("it-aa-rg-src-only") == "OWNER"
        assert await role("it-aa-rg-own") == "WRITER", "an unlinked connector is untouched"

    @pytest.mark.order(4)
    async def test_a_link_never_reaches_another_connector(self, graph_provider, seeded_graph) -> None:
        """TC-AA-SCOPE-001: the source account is OWNER in app-other, the creator must not be."""
        creator, org = seeded_graph["creator"], seeded_graph["org"]

        assert await _children(graph_provider, seeded_graph["app_other"], "app", creator, org) == set()
        assert await _children(graph_provider, "it-aa-rg-other", "recordGroup", creator, org) == set()
        assert await graph_provider.get_knowledge_hub_node_access("it-aa-rec-other", creator, org, []) is None

        allowed = await graph_provider._check_record_group_permissions("it-aa-rg-other", creator, org)
        assert allowed.get("allowed") is False

    @pytest.mark.order(5)
    async def test_browsing_shows_source_only_records(self, graph_provider, seeded_graph) -> None:
        """TC-AA-KH-001: the tree under a linked connector."""
        creator, org = seeded_graph["creator"], seeded_graph["org"]

        groups = await _children(graph_provider, seeded_graph["app_linked"], "app", creator, org)
        assert {"it-aa-rg-src-stronger", "it-aa-rg-own-stronger", "it-aa-rg-src-only"} <= groups

        children = await _children(graph_provider, "it-aa-rg-src-only", "recordGroup", creator, org)
        assert children == {"it-aa-rec-src-only"}

    @pytest.mark.order(6)
    async def test_all_records_search_returns_both_accounts(self, graph_provider, seeded_graph) -> None:
        """TC-AA-SEARCH-001: the creator's own hits must not hide the linked ones."""
        creator, org = seeded_graph["creator"], seeded_graph["org"]

        found = _ids(await _search(graph_provider, org, creator))
        assert {"it-aa-rec-src-only", "it-aa-rec-src-stronger"} <= found, "records only the source account can see"
        assert {"it-aa-rec-own", "it-aa-rec-own-stronger"} <= found, "the creator's own records, still there"
        assert "it-aa-rec-other" not in found and "it-aa-rg-other" not in found

    @pytest.mark.order(7)
    async def test_scoped_search_and_filters(self, graph_provider, seeded_graph) -> None:
        """TC-AA-SEARCH-002: scoping and the connector filter survive the principals change."""
        creator, org = seeded_graph["creator"], seeded_graph["org"]

        inside = _ids(await _search(graph_provider, org, creator,
                                    parent_id=seeded_graph["app_linked"], parent_type="app"))
        assert "it-aa-rec-src-only" in inside
        assert "it-aa-rec-own" not in inside, "a scoped search must not leak the other connector in"

        filtered = _ids(await _search(graph_provider, org, creator, connector_ids=[seeded_graph["app_own"]]))
        assert "it-aa-rec-own" in filtered
        assert "it-aa-rec-src-only" not in filtered

        page = await _search(graph_provider, org, creator, connector_ids=[seeded_graph["app_linked"]])
        assert page["total"] == len(page["nodes"]), "the total must match what the same query returns"

    @pytest.mark.order(8)
    async def test_chat_retrieval_covers_the_linked_connector(self, graph_provider, seeded_graph) -> None:
        """TC-AA-RETRIEVAL-001: the gate every chat answer passes through."""
        org, uid = seeded_graph["org"], seeded_graph["creator_uid"]

        everything = await graph_provider.get_accessible_virtual_record_ids(uid, org)
        assert {"v-it-aa-rec-src-only", "v-it-aa-rec-own"} <= set(everything)
        assert "v-it-aa-rec-other" not in everything

        linked_only = await graph_provider.get_accessible_virtual_record_ids(
            uid, org, {"apps": [seeded_graph["app_linked"]]})
        assert "v-it-aa-rec-src-only" in linked_only
        assert "v-it-aa-rec-own" not in linked_only

        other_only = await graph_provider.get_accessible_virtual_record_ids(
            uid, org, {"apps": [seeded_graph["app_other"]]})
        assert other_only == {}

    @pytest.mark.order(9)
    async def test_opening_one_record(self, graph_provider, seeded_graph) -> None:
        """TC-AA-RECORD-001: the record page, preview, download and citations all use this."""
        org, uid = seeded_graph["org"], seeded_graph["creator_uid"]

        allowed = await graph_provider.check_record_access_with_details(uid, org, "it-aa-rec-src-only")
        assert allowed is not None

        best = await graph_provider.check_record_access_with_details(uid, org, "it-aa-rec-own-stronger")
        roles = [p.get("relationship") for p in (best or {}).get("permissions", [])]
        assert "OWNER" in roles, f"the creator's own OWNER must be reported, got {roles}"

        assert await graph_provider.check_record_access_with_details(uid, org, "it-aa-rec-other") is None

    @pytest.mark.order(10)
    async def test_both_reindex_gates(self, graph_provider, seeded_graph) -> None:
        """TC-AA-REINDEX-001: passing the permission check alone once queued 0 records."""
        creator, org = seeded_graph["creator"], seeded_graph["org"]
        app = seeded_graph["app_linked"]

        group_check = await graph_provider._check_record_group_permissions("it-aa-rg-src-only", creator, org)
        assert group_check.get("allowed") is True
        record_check = await graph_provider._check_record_permissions("it-aa-rec-src-only", creator)
        assert record_check.get("permission")

        listed = await graph_provider.get_records_by_record_group(
            "it-aa-rg-src-only", app, org, 100, creator, limit=100)
        assert [r.id for r in listed] == ["it-aa-rec-src-only"], "the worker must see the records, not just be allowed"

    @pytest.mark.order(11)
    async def test_the_connector_side_hook(self, graph_provider, seeded_graph, config_service) -> None:
        """TC-AA-PROC-001: what a sync actually calls, including the inactive stub."""
        from app.connectors.core.base.data_processor.data_source_entities_processor import (
            DataSourceEntitiesProcessor,
        )
        from app.connectors.core.base.data_store.graph_data_store import GraphDataStore

        org, app = seeded_graph["org"], seeded_graph["app_own"]
        processor = DataSourceEntitiesProcessor(logger, GraphDataStore(logger, graph_provider), config_service)
        processor.org_id = org

        new_email, new_source_id = "it-aa-fresh@source.test", "it-aa-src-99"
        try:
            await processor.link_authenticator_to_source_user(
                app, seeded_graph["creator_uid"], new_email, new_source_id, Connectors.GITLAB)

            stub = await graph_provider.get_user_by_email(new_email)
            assert stub is not None, "a source account with no PipesHub login gets an inactive stub"
            assert stub.is_active is False
            assert stub.user_id == new_source_id, "the stub carries the source id, not the email"

            assert (await _links_of(graph_provider, seeded_graph["creator"])).get(app) == stub.id

            # A source account whose email matches the authenticating user drops the link
            await processor.link_authenticator_to_source_user(
                app, seeded_graph["creator_uid"], f"{seeded_graph['creator']}@pipeshub.test",
                "it-aa-src-self", Connectors.GITLAB)
            assert app not in await _links_of(graph_provider, seeded_graph["creator"])
        finally:
            await graph_provider.remove_authenticated_as(app)
            stub = await graph_provider.get_user_by_email(new_email)
            if stub:
                # batch_upsert_app_users also wires the stub to the org and the app
                users, apps = CollectionNames.USERS.value, CollectionNames.APPS.value
                await graph_provider.delete_edge(
                    stub.id, users, org, CollectionNames.ORGS.value, CollectionNames.BELONGS_TO.value)
                await graph_provider.delete_edge(
                    stub.id, users, app, apps, CollectionNames.USER_APP_RELATION.value)
                await graph_provider.delete_nodes([stub.id], users)

    @pytest.mark.order(12)
    async def test_a_user_with_no_link_is_unaffected(self, graph_provider, seeded_graph) -> None:
        """TC-AA-NEG-001: the guard against this change leaking access to everyone else."""
        org, stranger = seeded_graph["org"], seeded_graph["stranger"]

        assert await _links_of(graph_provider, stranger) == {}
        assert _ids(await _search(graph_provider, org, stranger)) == set()
        assert await graph_provider.get_accessible_virtual_record_ids(seeded_graph["stranger_uid"], org) == {}
        assert await graph_provider.check_record_access_with_details(
            seeded_graph["stranger_uid"], org, "it-aa-rec-src-only") is None

        # The source account's own view does not change because someone links to it
        source_ids = await graph_provider.get_accessible_virtual_record_ids(seeded_graph["source_uid"], org)
        assert {"v-it-aa-rec-src-only", "v-it-aa-rec-other"} <= set(source_ids)
