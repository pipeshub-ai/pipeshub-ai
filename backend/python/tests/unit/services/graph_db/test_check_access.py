"""`check_access` — the one batch access check behind every
permission decision: node ids, virtual record ids, or both in one call.

The rules themselves (which nodes the user may access) live in each backend's
``_kh_v3_accessible_rows`` and are tested against a real graph, on both, in
`tests/integration/graph_permissions/test_provider_v3_access_check.py`. These
tests hold what `check_access` adds around that query: which rows answer which
question, the citation filters, the choice of one record per virtual record id,
and failure that raises instead of denying.
"""

from unittest.mock import MagicMock

import pytest

from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
from app.services.graph_db.interface.graph_db_provider import (
    AccessCheck,
    IGraphDBProvider,
)
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


def _provider(rows=(), *, fail=False) -> tuple[Neo4jProvider, dict]:
    provider = Neo4jProvider.__new__(Neo4jProvider)
    provider.logger = MagicMock()
    calls: dict = {}

    async def _rows(user_key, org_id, node_ids, vrids, *, access, transaction) -> list:
        calls.update(user_key=user_key, org_id=org_id, node_ids=set(node_ids), vrids=set(vrids), access=access)
        if fail:
            raise RuntimeError("graph down")
        return list(rows)

    provider._kh_v3_accessible_rows = _rows
    return provider, calls


def _row(record_id, vrid=None, *, status="COMPLETED", connector="app-1") -> dict:
    return {"id": record_id, "vrid": vrid, "indexingStatus": status, "connectorId": connector}


class TestNodeIds:
    @pytest.mark.asyncio
    async def test_only_the_asked_nodes_that_came_back_are_accessible(self) -> None:
        provider, calls = _provider([_row("n1")])
        result = await provider.check_access("user-key", "org-1", node_ids=["n1", "n2", "n1"])
        assert result == AccessCheck(node_ids=frozenset({"n1"}), node_ids_in_scope=frozenset({"n1"}))
        assert calls["node_ids"] == {"n1", "n2"} and calls["vrids"] == set()

    @pytest.mark.asyncio
    async def test_the_citation_filters_never_narrow_node_ids(self) -> None:
        provider, _ = _provider([_row("n1", "v1", status="QUEUED", connector="app-out")])
        result = await provider.check_access(
            "user-key", "org-1", node_ids=["n1"], indexed_only=True, connector_ids=frozenset({"app-in"}),
        )
        assert result.node_ids == frozenset({"n1"})


class TestVirtualRecordIds:
    @pytest.mark.asyncio
    async def test_one_record_per_vrid_is_the_smallest_id_whatever_the_row_order(self) -> None:
        rows = [_row("r-b", "v1"), _row("r-a", "v1")]
        forward = (await _provider(rows)[0].check_access("k", "o", virtual_record_ids=["v1"])).records_by_vrid
        backward = (await _provider(rows[::-1])[0].check_access("k", "o", virtual_record_ids=["v1"])).records_by_vrid
        assert forward == backward == {"v1": "r-a"}

    @pytest.mark.asyncio
    async def test_indexed_only_cites_only_finished_records(self) -> None:
        provider, _ = _provider([_row("r1", "v1", status="QUEUED"), _row("r2", "v2")])
        result = await provider.check_access("k", "o", virtual_record_ids=["v1", "v2"], indexed_only=True)
        assert result.records_by_vrid == {"v2": "r2"}

    @pytest.mark.asyncio
    async def test_without_indexed_only_an_unfinished_record_is_cited(self) -> None:
        """Chat attachments: the user may open what is still indexing."""
        provider, _ = _provider([_row("r1", "v1", status="QUEUED")])
        assert (await provider.check_access("k", "o", virtual_record_ids=["v1"])).records_by_vrid == {"v1": "r1"}

    @pytest.mark.asyncio
    async def test_the_cited_record_must_be_in_scope(self) -> None:
        provider, _ = _provider([_row("r1", "v1", connector="app-out"), _row("r2", "v1", connector="app-in")])
        result = await provider.check_access(
            "k", "o", virtual_record_ids=["v1"], connector_ids=frozenset({"app-in"}),
        )
        assert result.records_by_vrid == {"v1": "r2"}

    @pytest.mark.asyncio
    async def test_an_empty_scope_cites_nothing_without_querying(self) -> None:
        provider, calls = _provider([_row("r1", "v1")])
        result = await provider.check_access("k", "o", virtual_record_ids=["v1"], connector_ids=frozenset())
        assert result == AccessCheck() and not calls


class _Inside:
    """A scope that admits the rows of one record group."""

    def __init__(self, group_id: str) -> None:
        self.group_id = group_id

    def admits(self, row) -> bool:
        return self.group_id in (row.get("groupIds") or ())


class TestScopes:
    @pytest.mark.asyncio
    async def test_only_a_copy_every_scope_admits_is_cited(self) -> None:
        rows = [
            {**_row("r1-outside", "v1"), "groupIds": ["g-other"]},
            {**_row("r2-inside", "v1"), "groupIds": ["g-sel", "g-allowed"]},
            {**_row("r3-half", "v1"), "groupIds": ["g-sel"]},
        ]
        provider, _ = _provider(rows)
        result = await provider.check_access(
            "user-key", "org-1", virtual_record_ids=["v1"],
            scopes=[_Inside("g-sel"), _Inside("g-allowed")],
        )
        assert result.records_by_vrid == {"v1": "r2-inside"}

    @pytest.mark.asyncio
    async def test_no_copy_inside_the_scope_cites_nothing(self) -> None:
        provider, _ = _provider([{**_row("r1", "v1"), "groupIds": ["g-other"]}])
        result = await provider.check_access(
            "user-key", "org-1", virtual_record_ids=["v1"], scopes=[_Inside("g-sel")],
        )
        assert result.records_by_vrid == {}

    @pytest.mark.asyncio
    async def test_a_scope_never_narrows_node_ids(self) -> None:
        """The same check tells whether the selected nodes are readable at all."""
        provider, _ = _provider([{**_row("n1"), "groupIds": []}])
        result = await provider.check_access(
            "user-key", "org-1", node_ids=["n1"], scopes=[_Inside("g-sel")],
        )
        assert result.node_ids == frozenset({"n1"})
        assert result.node_ids_in_scope == frozenset()

    @pytest.mark.asyncio
    async def test_the_asked_nodes_inside_every_scope_are_reported_apart(self) -> None:
        rows = [{**_row("in"), "groupIds": ["g-sel"]}, {**_row("out"), "groupIds": ["g-other"]}]
        provider, _ = _provider(rows)
        result = await provider.check_access(
            "user-key", "org-1", node_ids=["in", "out"], scopes=[_Inside("g-sel")],
        )
        assert result.node_ids == frozenset({"in", "out"})
        assert result.node_ids_in_scope == frozenset({"in"})


class TestBothInOneCall:
    @pytest.mark.asyncio
    async def test_each_row_answers_only_the_question_it_was_asked_for(self) -> None:
        """A node asked by id may carry a vrid nobody asked about, and a record
        found through a vrid is not an asked node."""
        rows = [_row("n1", "v-unasked"), _row("r1", "v1")]
        provider, calls = _provider(rows)
        result = await provider.check_access("k", "o", node_ids=["n1"], virtual_record_ids=["v1"])
        assert result == AccessCheck(
            node_ids=frozenset({"n1"}), records_by_vrid={"v1": "r1"}, node_ids_in_scope=frozenset({"n1"}),
        )
        assert calls["node_ids"] == {"n1"} and calls["vrids"] == {"v1"}


class TestIdentityAndFailure:
    @pytest.mark.asyncio
    async def test_no_identity_is_nothing_accessible_without_querying(self) -> None:
        provider, calls = _provider([_row("n1")])
        assert await provider.check_access("", "o", node_ids=["n1"]) == AccessCheck()
        assert not calls

    @pytest.mark.asyncio
    async def test_a_given_access_context_stands_in_for_the_user(self) -> None:
        """Browse admission passes the request's access context and no key."""
        access = {"grantee_ids": ["u"], "gated_app_ids": ["app-1"], "by_connector": {}}
        provider, calls = _provider([_row("n1")])
        result = await provider.check_access("", "o", node_ids=["n1"], access=access)
        assert result.node_ids == frozenset({"n1"}) and calls["access"] is access

    @pytest.mark.asyncio
    async def test_nothing_asked_is_nothing_accessible_without_querying(self) -> None:
        provider, calls = _provider([_row("n1")])
        assert await provider.check_access("k", "o") == AccessCheck()
        assert not calls

    @pytest.mark.asyncio
    async def test_a_graph_failure_raises_instead_of_denying(self) -> None:
        provider, _ = _provider(fail=True)
        with pytest.raises(PermissionVerificationUnavailableError):
            await provider.check_access("k", "o", node_ids=["n1"])


class TestInterface:
    @pytest.mark.asyncio
    async def test_a_backend_without_the_batch_check_fails_loudly(self) -> None:
        """Callers fall back to their old behaviour on NotImplementedError, so a
        backend without rows must raise it, never answer empty or report an
        outage."""
        provider = MagicMock(spec=IGraphDBProvider)
        provider.logger = MagicMock()
        provider._kh_v3_accessible_rows = lambda *a, **k: IGraphDBProvider._kh_v3_accessible_rows(None, *a, **k)
        with pytest.raises(NotImplementedError):
            await IGraphDBProvider.check_access(provider, "k", "o", node_ids=["n1"])

    def test_neither_provider_is_left_abstract(self) -> None:
        from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

        for cls in (ArangoHTTPProvider, Neo4jProvider):
            assert not getattr(cls, "__abstractmethods__", frozenset()), f"{cls.__name__} is abstract"

    def test_one_check_serves_both_backends(self) -> None:
        """The wrapper is shared; each backend supplies only its rows."""
        from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

        for provider in (Neo4jProvider, ArangoHTTPProvider):
            assert "check_access" not in vars(provider), provider
            assert "_kh_v3_accessible_rows" in vars(provider), provider

    def test_the_old_entry_points_are_gone(self) -> None:
        for name in ("filter_accessible_nodes", "resolve_accessible_virtual_record_ids"):
            assert not hasattr(IGraphDBProvider, name) and not hasattr(Neo4jProvider, name), name


def _failing(backend: str):
    """A provider of ``backend`` whose check cannot run, and whose linked-records
    query finds one link."""
    from unittest.mock import AsyncMock

    from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

    cls = Neo4jProvider if backend == "neo4j" else ArangoHTTPProvider
    provider = cls.__new__(cls)
    provider.logger = MagicMock()
    provider.client, provider.http_client = MagicMock(), MagicMock()
    provider.client.execute_query = AsyncMock(return_value=[{"item": {"id": "r2"}}])
    provider.http_client.execute_aql = AsyncMock(return_value=[{"id": "r2"}])
    provider.check_access = AsyncMock(side_effect=PermissionVerificationUnavailableError("graph down"))
    return provider


@pytest.mark.parametrize("backend", ["neo4j", "arango"])
class TestAFailedCheckIsNotReportedAsAbsence:
    """Node access and linked records answer "not found" and "no links" for
    what the check refuses; a check that could not run must not read as that."""

    async def test_node_access(self, backend) -> None:
        with pytest.raises(PermissionVerificationUnavailableError):
            await _failing(backend).get_knowledge_hub_node_access("r1", "u", "org-1", folder_mime_types=[])

    async def test_linked_records(self, backend) -> None:
        with pytest.raises(PermissionVerificationUnavailableError):
            await _failing(backend).get_linked_records("r1", "org-1", "u", ["BLOCKS"])
