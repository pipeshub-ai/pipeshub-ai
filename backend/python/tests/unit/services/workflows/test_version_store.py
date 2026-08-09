"""Version storage has to be immutable and correctly ordered, because both
"which code does the next scheduled run execute" and "what does rollback roll
back to" are answered by `get_latest`/`list_for_workflow`.

The shared fakes sort and paginate the way the real databases do -- ordering
in the database, then slicing -- so a store that re-sorts client-side (which
only reorders the page the database already picked) fails here. They also
enforce Neo4j's property-shape rule on every write; see
`tests/unit/services/graph_db/test_backend_portability` for the round-trip
suite that covers payload encoding.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.workflows.adapters.graph.version_store import (
    ArangoWorkflowVersionStore,
    GraphWorkflowVersionStore,
    _from_doc,
    _to_node,
)
from app.services.workflows.domain.errors import WorkflowVersionConflictError
from app.services.workflows.domain.models import ArtifactRef, WorkflowVersion
from tests.unit.services.graph_provider_fakes import (
    GRAPH_PROVIDER_FAKES,
    ArangoSemanticsGraphProvider,
    BaseFakeGraphProvider,
    Neo4jSemanticsGraphProvider,
)


def _version(version_id: str, *, version_number: int = 0, org_id: str = "org-1") -> WorkflowVersion:
    return WorkflowVersion(
        version_id=version_id,
        version_number=version_number,
        workflow_id="wf-1",
        org_id=org_id,
        bundle_ref=ArtifactRef(artifact_id=f"art-{version_id}"),
        content_hash="h",
        created_by_user_id="u-1",
    )


@pytest.fixture(params=GRAPH_PROVIDER_FAKES, ids=lambda cls: cls.__name__)
def store(
    request: pytest.FixtureRequest,
) -> tuple[ArangoWorkflowVersionStore, BaseFakeGraphProvider]:
    provider = request.param()
    return ArangoWorkflowVersionStore(provider), provider


class TestImmutability:
    @pytest.mark.asyncio
    async def test_saving_the_same_version_id_twice_is_a_conflict(self, store) -> None:
        version_store, _ = store
        await version_store.save(_version("ver-1"))

        with pytest.raises(WorkflowVersionConflictError):
            await version_store.save(_version("ver-1"))

    @pytest.mark.asyncio
    async def test_version_numbers_are_assigned_monotonically(self, store) -> None:
        version_store, _ = store
        first = await version_store.save(_version("ver-1"))
        second = await version_store.save(_version("ver-2"))
        third = await version_store.save(_version("ver-3"))

        assert [first.version_number, second.version_number, third.version_number] == [1, 2, 3]


class TestOrdering:
    @pytest.mark.asyncio
    async def test_get_latest_returns_the_newest_not_the_oldest(self, store) -> None:
        version_store, _ = store
        for i in range(1, 26):
            await version_store.save(_version(f"ver-{i}"))

        latest = await version_store.get_latest("wf-1", "org-1")

        assert latest is not None
        assert latest.version_number == 25
        assert latest.version_id == "ver-25"

    @pytest.mark.asyncio
    async def test_listing_shows_the_newest_page_past_the_default_limit(self, store) -> None:
        """Regression: with 25 versions and a 20-row page, an ASC sort returns
        v1-v20 and the newest five are invisible in the UI."""
        version_store, _ = store
        for i in range(1, 26):
            await version_store.save(_version(f"ver-{i}"))

        page = await version_store.list_for_workflow("wf-1", "org-1")

        assert [v.version_number for v in page] == list(range(25, 5, -1))

    @pytest.mark.asyncio
    async def test_offset_walks_backwards_through_history(self, store) -> None:
        version_store, _ = store
        for i in range(1, 26):
            await version_store.save(_version(f"ver-{i}"))

        page = await version_store.list_for_workflow("wf-1", "org-1", limit=5, offset=5)

        assert [v.version_number for v in page] == [20, 19, 18, 17, 16]

    @pytest.mark.asyncio
    async def test_no_versions_yields_no_latest(self, store) -> None:
        version_store, _ = store
        assert await version_store.get_latest("wf-1", "org-1") is None


class TestOrgIsolation:
    @pytest.mark.asyncio
    async def test_get_refuses_another_orgs_version(self, store) -> None:
        version_store, _ = store
        await version_store.save(_version("ver-1", org_id="org-OTHER"))

        assert await version_store.get("ver-1", "org-1") is None
        assert await version_store.get("ver-1", "org-OTHER") is not None

    @pytest.mark.asyncio
    async def test_listing_excludes_other_orgs(self, store) -> None:
        version_store, _ = store
        await version_store.save(_version("ver-mine"))
        await version_store.save(_version("ver-theirs", org_id="org-OTHER"))

        page = await version_store.list_for_workflow("wf-1", "org-1")

        assert [v.version_id for v in page] == ["ver-mine"]

    @pytest.mark.asyncio
    async def test_delete_refuses_another_orgs_version(self, store) -> None:
        version_store, provider = store
        await version_store.save(_version("ver-1", org_id="org-OTHER"))

        assert await version_store.delete("ver-1", "org-1") is False
        assert await provider.get_document("ver-1", "workflowVersions") is not None


class TestErrorPropagation:
    """BUG-1: `list_for_workflow` used to swallow graph-level failures and
    return `[]`, indistinguishable from "no versions exist." It must now
    propagate so `WorkflowService` can turn it into a 503."""

    @pytest.mark.asyncio
    async def test_list_for_workflow_raises_on_graph_error(self) -> None:
        provider = AsyncMock()
        provider.get_documents_paginated.side_effect = RuntimeError("graph unreachable")
        version_store = GraphWorkflowVersionStore(provider)

        with pytest.raises(RuntimeError, match="graph unreachable"):
            await version_store.list_for_workflow("wf-1", "org-1")

        # The whole point of the fix: raise_on_error=True must reach the
        # provider so it re-raises instead of swallowing the failure itself.
        _, kwargs = provider.get_documents_paginated.call_args
        assert kwargs["raise_on_error"] is True

    @pytest.mark.asyncio
    async def test_get_latest_also_raises_since_it_delegates_to_list(self) -> None:
        provider = AsyncMock()
        provider.get_documents_paginated.side_effect = RuntimeError("graph unreachable")
        version_store = GraphWorkflowVersionStore(provider)

        with pytest.raises(RuntimeError):
            await version_store.get_latest("wf-1", "org-1")


class TestDocumentRoundtrip:
    """Both backends must decode exactly what `_to_node` encoded, including
    each backend's own identity attributes (`_key`/`_id`/`_rev` on Arango,
    none on Neo4j) and JSON-encoded nested fields."""

    def _version_with_ir(self) -> WorkflowVersion:
        from app.services.workflows.domain.models import IREdge, IRNode, WorkflowIR

        return WorkflowVersion(
            version_id="ver-1",
            version_number=3,
            workflow_id="wf-1",
            org_id="org-1",
            bundle_ref=ArtifactRef(artifact_id="art-1", version="1"),
            tool_pins={"jira__search_issues": "jira__search_issues"},
            agent_pins={"agent-1"},
            ir=WorkflowIR(
                nodes=[IRNode(node_id="n1", kind="workflow", label="wf")],
                edges=[IREdge(from_node="n1", to_node="n1")],
                entry_node_id="n1",
            ),
            content_hash="abc123",
            created_by_user_id="u-1",
        )

    def test_roundtrip_through_arango_shape(self) -> None:
        version = self._version_with_ir()
        node = _to_node(version)
        stored = ArangoSemanticsGraphProvider()._store(node)
        doc = ArangoSemanticsGraphProvider()._read(stored)
        assert {"_key", "_id", "_rev"} <= doc.keys()

        restored = _from_doc(doc)
        assert restored.version_id == version.version_id
        assert restored.bundle_ref == version.bundle_ref
        assert restored.tool_pins == version.tool_pins
        assert restored.agent_pins == version.agent_pins
        assert restored.ir.model_dump() == version.ir.model_dump()

    def test_roundtrip_through_neo4j_shape(self) -> None:
        version = self._version_with_ir()
        node = _to_node(version)
        stored = Neo4jSemanticsGraphProvider()._store(node)
        doc = Neo4jSemanticsGraphProvider()._read(stored)
        assert "_key" not in doc and "_rev" not in doc

        restored = _from_doc(doc)
        assert restored.version_id == version.version_id
        assert restored.bundle_ref == version.bundle_ref
        assert restored.tool_pins == version.tool_pins
        assert restored.agent_pins == version.agent_pins
        assert restored.ir.model_dump() == version.ir.model_dump()

    def test_from_doc_logs_corrupt_bundle_ref(self, caplog: pytest.LogCaptureFixture) -> None:
        """BUG-3: a corrupt `bundle_ref` must not disappear silently -- the
        raw value has to make it into the log so the corruption is
        diagnosable instead of surfacing only as a later 404 on source."""
        doc = {
            "id": "ver-1",
            "version_id": "ver-1",
            "workflow_id": "wf-1",
            "org_id": "org-1",
            "bundle_ref": "not-json{{{",
            "content_hash": "h",
            "created_by_user_id": "u-1",
        }
        with caplog.at_level("WARNING"):
            restored = _from_doc(doc)

        assert restored.bundle_ref is None
        assert any("not-json" in record.getMessage() for record in caplog.records)
