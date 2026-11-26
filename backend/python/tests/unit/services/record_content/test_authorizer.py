"""Unit tests for TieredRecordAuthorizer.

Covers:
- Org mismatch → RecordAccessDeniedError (no I/O)
- A direct permission edge is never read: main's one access check (``check_access``) decides (SYNC4 S8)
- The access check admits → granted; denies → RecordAccessDeniedError
- Service account (ArtifactNotFoundError in resolve_user_key) → reads nothing
- No-external-call on org mismatch (tier 1 short-circuit)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors, OriginTypes
from app.core.org_context import MissingOrgContext
from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
from app.harness.contracts.generated.pipeshub.citation_source import (
    PipeshubLiveSource,
    PipeshubRecordSource,
)
from app.models.entities import Record, RecordType
from app.services.record_content.authorizer import TieredRecordAuthorizer
from app.services.record_content.models import (
    RecordAccessDeniedError,
    RecordContentUnavailableError,
    RecordNotFoundError,
)
from tests.support.record_access import InMemoryRecordAccess


def _make_actor(org_id="org1", user_id="user1"):
    actor = MagicMock()
    actor.org_id = org_id
    actor.user_id = user_id
    return actor


def _make_record(org_id="org1", record_id="rec1"):
    record = MagicMock()
    record.org_id = org_id
    record.id = record_id
    # Support dict-style access fallback
    record.get = lambda key, default=None: {
        "orgId": org_id,
        "_key": record_id,
    }.get(key, default)
    return record


@pytest.fixture
def access() -> InMemoryRecordAccess:
    return InMemoryRecordAccess().user("user1", "org1", key="user/user1").record("rec1", "org1")


@pytest.fixture
def graph(access):
    g = AsyncMock()
    g.get_edge = AsyncMock(return_value=None)
    access.serve(g)
    return g


@pytest.mark.asyncio
async def test_org_mismatch_raises_without_io(graph, access):
    """Tier 1 rejects cross-org records before any I/O."""
    authorizer = TieredRecordAuthorizer(graph)
    actor = _make_actor(org_id="orgA")
    record = _make_record(org_id="orgB")

    with pytest.raises(RecordAccessDeniedError):
        await authorizer.authorize(actor, record)

    graph.get_edge.assert_not_called()
    assert access.asked_about == []


@pytest.mark.asyncio
async def test_a_direct_edge_alone_no_longer_grants(graph, access):
    """A user->record edge used to be accepted on its own, skipping the
    STRICT/RESTRICTED rule and the connector gate. The permission model decides."""
    graph.get_edge.return_value = {"_id": "edges/e1"}
    authorizer = TieredRecordAuthorizer(graph)

    with pytest.raises(RecordAccessDeniedError):
        await authorizer.authorize(_make_actor(), _make_record())

    graph.get_edge.assert_not_called()
    assert access.asked_about == [("user1", "org1", "rec1")]


@pytest.mark.asyncio
async def test_tier3_grants_when_edge_absent(graph, access):
    """Edge absent, tier 3 returns non-None → granted."""
    graph.get_edge.return_value = None
    access.grant("rec1", user="user1")
    authorizer = TieredRecordAuthorizer(graph)
    actor = _make_actor()
    record = _make_record()

    with patch(
        "app.services.artifact_registry.access.AccessPolicy.resolve_user_key",
        new=AsyncMock(return_value="user/user1"),
    ):
        await authorizer.authorize(actor, record)


@pytest.mark.asyncio
async def test_all_tiers_fail_raises(graph):
    """All three tiers fail → RecordAccessDeniedError."""
    graph.get_edge.return_value = None
    authorizer = TieredRecordAuthorizer(graph)
    actor = _make_actor()
    record = _make_record()

    with patch(
        "app.services.artifact_registry.access.AccessPolicy.resolve_user_key",
        new=AsyncMock(return_value="user/user1"),
    ):
        with pytest.raises(RecordAccessDeniedError):
            await authorizer.authorize(actor, record)


@pytest.mark.asyncio
async def test_an_actor_without_a_user_node_reads_nothing(graph, access):
    """When resolve_user_key raises ArtifactNotFoundError (no user node), the
    access check has no identity to decide for: denied, never asked."""
    from app.services.artifact_registry.access import ArtifactNotFoundError

    access.grant("rec1", user="user1")
    authorizer = TieredRecordAuthorizer(graph)

    with patch(
        "app.services.artifact_registry.access.AccessPolicy.resolve_user_key",
        new=AsyncMock(side_effect=ArtifactNotFoundError("no node")),
    ):
        with pytest.raises(RecordAccessDeniedError):
            await authorizer.authorize(_make_actor(), _make_record())

    assert access.asked_about == []


@pytest.mark.asyncio
async def test_service_account_denied_by_tier3(graph):
    """Service account with no user node + tier 3 also denies → error."""
    from app.services.artifact_registry.access import ArtifactNotFoundError

    graph.get_edge.return_value = None
    authorizer = TieredRecordAuthorizer(graph)
    actor = _make_actor()
    record = _make_record()

    with patch(
        "app.services.artifact_registry.access.AccessPolicy.resolve_user_key",
        new=AsyncMock(side_effect=ArtifactNotFoundError("no node")),
    ):
        with pytest.raises(RecordAccessDeniedError):
            await authorizer.authorize(actor, record)


class _Graph:
    """Records the calls `readable` makes; `edges` (a grant on the record itself: an artifact, a chat attachment) and
    `acl` (user, org, record) are the grants the one access check decides by."""

    def __init__(self, records: dict, *, edges: set = frozenset(), acl: set = frozenset(), fail_tier3: bool = False) -> None:
        self.records = records
        self.edges = set(edges)
        self.access = InMemoryRecordAccess()
        for rid, record in records.items():
            self.access.record(rid, record.org_id, isDeleted=getattr(record, "is_deleted", False) is True)
        grants = {*acl, *((key.removeprefix("key-"), records[rid].org_id, rid) for key, rid in edges if rid in records)}
        for user_id, org_id, rid in grants:
            if user_id not in self.access.users:
                self.access.user(user_id, org_id, key=f"key-{user_id}")
            if rid not in self.access.records:
                self.access.record(rid, org_id)
            self.access.grant(rid, user=user_id)
        if fail_tier3:
            self.access.fail_with(ConnectionError("graph down"))
        self.access.serve(self)
        self.batches: list[list[str]] = []
        self.user_lookups = 0

    @property
    def tier3(self) -> list[str]:
        return [rid for _, _, rid in self.access.asked_about]

    async def get_typed_records_batch(self, record_ids: list[str]) -> dict:
        self.batches.append(list(record_ids))
        return {rid: self.records[rid] for rid in record_ids if rid in self.records}

    async def get_user_by_user_id(self, user_id: str, **_: object) -> dict:
        self.user_lookups += 1
        return {"_key": f"key-{user_id}"}

    async def get_edge(self, *, from_id: str, to_id: str, **_: str) -> dict | None:
        return {"_id": "e"} if (from_id, to_id) in self.edges else None


class TestReadable:
    """The batched check behind shared viewers' citations (P1b C-6)."""

    def _records(self, *ids: str, org: str = "org1") -> dict:
        return {rid: _make_record(org_id=org, record_id=rid) for rid in ids}

    @pytest.mark.asyncio
    async def test_mixed_grants_one_batch_one_user_lookup(self) -> None:
        records = {**self._records("edge", "acl", "denied"), **self._records("other-org", org="org2")}
        graph = _Graph(
            records,
            edges={("key-user1", "edge")},
            acl={("user1", "org1", "acl"), ("user1", "org1", "other-org")},
        )
        readable = await TieredRecordAuthorizer(graph).readable(
            _make_actor(), ["edge", "acl", "denied", "other-org", "missing", "edge", ""]
        )
        assert readable == {"edge", "acl"}
        assert graph.batches == [["edge", "acl", "denied", "other-org", "missing"]]
        assert graph.user_lookups == 1
        # Tier 1 settles the other org's record; one access check decides every live id of the org.
        assert sorted(graph.tier3) == ["acl", "denied", "edge"]

    @pytest.mark.asyncio
    async def test_a_trashed_record_is_not_readable_even_with_a_direct_edge(self) -> None:
        """Trashed records keep their edges for restore; the batch and `permits` refuse them as `authorize` does."""
        records = self._records("live", "trashed")
        records["trashed"].is_deleted = True
        graph = _Graph(records, edges={("key-user1", "live"), ("key-user1", "trashed")})
        authorizer = TieredRecordAuthorizer(graph)
        assert await authorizer.readable(_make_actor(), ["live", "trashed"]) == {"live"}
        assert await authorizer.permits(_make_actor(), records["trashed"]) is False
        # Only the live record is ever asked about.
        assert graph.tier3 == ["live"]

    @pytest.mark.asyncio
    async def test_nothing_asked_is_no_io(self) -> None:
        graph = _Graph({})
        assert await TieredRecordAuthorizer(graph).readable(_make_actor(), []) == set()
        assert graph.batches == [] and graph.user_lookups == 0

    @pytest.mark.asyncio
    async def test_an_actor_without_a_user_node_reads_nothing(self) -> None:
        graph = _Graph(self._records("acl"), acl={("user1", "org1", "acl")})
        graph.get_user_by_user_id = AsyncMock(return_value=None)
        assert await TieredRecordAuthorizer(graph).readable(_make_actor(), ["acl"]) == set()

    @pytest.mark.asyncio
    async def test_io_error_propagates_so_callers_fail_closed(self) -> None:
        graph = _Graph(self._records("a"), fail_tier3=True)
        with pytest.raises(ConnectionError):
            await TieredRecordAuthorizer(graph).readable(_make_actor(), ["a"])

    @pytest.mark.asyncio
    async def test_denials_are_not_logged_per_record(self, caplog: pytest.LogCaptureFixture) -> None:
        graph = _Graph(self._records("secret-record"))
        with caplog.at_level("DEBUG"):
            assert await TieredRecordAuthorizer(graph).readable(_make_actor(), ["secret-record"]) == set()
        assert "secret-record" not in caplog.text


class _LiveGraph(_Graph):
    """Also answers which records are indexed copies of live items."""

    def __init__(self, records: dict, *, copies: list[dict] = (), fail_copies: bool = False, **grants: set) -> None:
        super().__init__(records, **grants)
        self.copies = list(copies)
        self.fail_copies = fail_copies
        self.copy_queries: list[tuple[str, list[str], list[str]]] = []

    async def get_records_by_external_ids(self, org_id: str, external_ids: list[str], connector_names: list[str]) -> list[dict]:
        self.copy_queries.append((org_id, list(external_ids), sorted(connector_names)))
        if self.fail_copies:
            raise ConnectionError("graph down")
        return [
            c for c in self.copies
            if c["orgId"] == org_id and c["externalRecordId"] in external_ids and c["connectorName"] in connector_names
        ]


def _copy(record_id: str, external_id: str, connector: str = "DRIVE", org: str = "org1") -> dict:
    return {"id": record_id, "externalRecordId": external_id, "connectorName": connector, "orgId": org}


def _indexed(record_id: str, kind: str = "indexed") -> PipeshubRecordSource:
    return PipeshubRecordSource(kind=kind, record_id=record_id)


def _live(external_id: str, connector: str = "DRIVE") -> PipeshubLiveSource:
    return PipeshubLiveSource(kind="live", connector=connector, connector_id="toolset-1", external_record_id=external_id)


class TestDecide:
    """Typed sources (P1b C-18): records by their ACL, live items by their indexed copy's."""

    def _records(self, *ids: str, org: str = "org1") -> dict:
        return {rid: _make_record(org_id=org, record_id=rid) for rid in ids}

    @pytest.mark.asyncio
    async def test_each_kind_readable_or_denied_a_live_item_without_a_copy_unresolved(self) -> None:
        records = {**self._records("open", "closed", "copy-open", "copy-closed", "att", "art"), **self._records("elsewhere", org="org2")}
        graph = _LiveGraph(
            records,
            copies=[_copy("copy-open", "f-open"), _copy("copy-closed", "f-closed")],
            acl={("user1", "org1", "open"), ("user1", "org1", "copy-open"), ("user1", "org1", "elsewhere")},
            edges={("key-user1", "att"), ("key-user1", "art")},
        )
        sources = [
            _indexed("open"),
            _indexed("closed"),
            _indexed("missing"),
            _indexed("elsewhere"),
            _indexed("att", "attachment"),
            _indexed("art", "generated"),
            _live("f-open"),
            _live("f-closed"),
            _live("f-nowhere"),
        ]
        statuses = await TieredRecordAuthorizer(graph).decide(_make_actor(), sources)
        assert statuses == [
            "readable",
            "denied",
            "denied",  # an absent record reads as one the viewer may not read (QA27-F3)
            "denied",  # another org's record is not the viewer's to know about
            "readable",
            "readable",
            "readable",
            "denied",
            "unresolved",
        ]

    @pytest.mark.asyncio
    async def test_live_items_resolve_in_one_query_then_one_records_batch(self) -> None:
        graph = _LiveGraph(
            self._records("r", "c1", "c2"),
            copies=[_copy("c1", "f1"), _copy("c2", "f2", connector="DRIVE WORKSPACE")],
            acl={("user1", "org1", "c2")},
        )
        statuses = await TieredRecordAuthorizer(graph).decide(
            _make_actor(), [_indexed("r"), _live("f1"), _live("f2"), _live("f3", connector="ONEDRIVE")]
        )
        assert statuses == ["denied", "denied", "readable", "unresolved"]
        # One resolution query for every live item, in the actor's org, over the connector kinds that index them.
        assert graph.copy_queries == [("org1", ["f1", "f2", "f3"], ["DRIVE", "DRIVE WORKSPACE", "ONEDRIVE"])]
        assert graph.batches == [["r", "c1", "c2"]]
        assert graph.user_lookups == 1

    @pytest.mark.asyncio
    async def test_a_live_item_readable_through_any_indexed_copy(self) -> None:
        graph = _LiveGraph(
            self._records("personal", "workspace"),
            copies=[_copy("personal", "f1"), _copy("workspace", "f1", connector="DRIVE WORKSPACE")],
            acl={("user1", "org1", "workspace")},
        )
        assert await TieredRecordAuthorizer(graph).decide(_make_actor(), [_live("f1")]) == ["readable"]

    @pytest.mark.asyncio
    async def test_a_copy_counts_only_for_its_own_connector_kind(self) -> None:
        # Two source systems can use the same external id; one query asks for both kinds.
        graph = _LiveGraph(
            self._records("onedrive-copy"),
            copies=[_copy("onedrive-copy", "item-1", connector="ONEDRIVE")],
            acl={("user1", "org1", "onedrive-copy")},
        )
        statuses = await TieredRecordAuthorizer(graph).decide(
            _make_actor(), [_live("item-1", connector="DRIVE"), _live("item-1", connector="ONEDRIVE")]
        )
        assert statuses == ["unresolved", "readable"]

    @pytest.mark.asyncio
    async def test_no_live_item_no_resolution_query(self) -> None:
        graph = _LiveGraph(self._records("r"), acl={("user1", "org1", "r")})
        assert await TieredRecordAuthorizer(graph).decide(_make_actor(), [_indexed("r")]) == ["readable"]
        assert graph.copy_queries == []

    @pytest.mark.asyncio
    async def test_nothing_asked_is_no_io(self) -> None:
        graph = _LiveGraph({})
        assert await TieredRecordAuthorizer(graph).decide(_make_actor(), []) == []
        assert graph.copy_queries == [] and graph.batches == [] and graph.user_lookups == 0

    @pytest.mark.asyncio
    async def test_a_failed_resolution_propagates_so_callers_fail_closed(self) -> None:
        graph = _LiveGraph(self._records("r"), fail_copies=True)
        with pytest.raises(ConnectionError):
            await TieredRecordAuthorizer(graph).decide(_make_actor(), [_live("f1")])

    @pytest.mark.asyncio
    async def test_nothing_is_logged_per_item(self, caplog: pytest.LogCaptureFixture) -> None:
        graph = _LiveGraph(self._records("secret-copy"), copies=[_copy("secret-copy", "secret-file")])
        with caplog.at_level("DEBUG"):
            await TieredRecordAuthorizer(graph).decide(_make_actor(), [_live("secret-file"), _indexed("secret-record")])
        assert "secret" not in caplog.text


class TestAttachmentContent:
    """A steering message's attachment (P1b C-21): the content it names must be the record's."""

    @pytest.mark.asyncio
    async def test_readable_only_with_the_records_own_virtual_record_id(self) -> None:
        own = _make_record(record_id="att")
        own.virtual_record_id = "vr-att"
        other = _make_record(record_id="other")
        other.virtual_record_id = "vr-other"
        graph = _Graph({"att": own, "other": other}, edges={("key-user1", "att")})
        sources = [
            PipeshubRecordSource(kind="attachment", record_id="att", virtual_record_id="vr-att"),
            PipeshubRecordSource(kind="attachment", record_id="att", virtual_record_id="vr-other"),
            PipeshubRecordSource(kind="attachment", record_id="other", virtual_record_id="vr-other"),
            PipeshubRecordSource(kind="attachment", record_id="att"),
        ]
        statuses = await TieredRecordAuthorizer(graph).decide(_make_actor(), sources)
        assert statuses == ["readable", "denied", "denied", "readable"]
# ---------------------------------------------------------------------------
# Tier 0: a record in the trash
# ---------------------------------------------------------------------------


def _stored_record(*, is_deleted: bool) -> Record:
    return Record(
        id="rec1",
        org_id="org1",
        record_name="report.pdf",
        record_type=RecordType.FILE,
        external_record_id="ext-1",
        version=1,
        origin=OriginTypes.UPLOAD,
        connector_name=Connectors.KNOWLEDGE_BASE,
        connector_id="kb1",
        is_deleted=is_deleted,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "record",
    [
        pytest.param(_stored_record(is_deleted=True), id="record-model"),
        pytest.param({"_key": "rec1", "orgId": "org1", "isDeleted": True}, id="stored-document"),
    ],
)
async def test_a_trashed_record_is_not_found_even_with_a_direct_edge(graph, access, record) -> None:
    """Trashed records keep their permission edges for restore; tier 2 alone would grant."""
    graph.get_edge.return_value = {"_id": "edges/e1"}
    authorizer = TieredRecordAuthorizer(graph)

    with pytest.raises(RecordNotFoundError, match="rec1 was deleted"):
        await authorizer.authorize(_make_actor(), record)

    graph.get_edge.assert_not_called()
    assert access.asked_about == []


@pytest.mark.asyncio
async def test_a_live_record_still_goes_through_the_tiers(graph, access) -> None:
    """Tier 0 lets a live record on to the org check and the permission model,
    which alone decides: a direct edge is not read."""
    graph.get_edge.return_value = {"_id": "edges/e1"}
    access.grant("rec1", user="user1")
    authorizer = TieredRecordAuthorizer(graph)

    await authorizer.authorize(_make_actor(), _stored_record(is_deleted=False))

    assert access.asked_about == [("user1", "org1", "rec1")]
    graph.get_edge.assert_not_called()


@pytest.mark.asyncio
async def test_a_live_record_the_model_denies_is_denied_not_missing(graph, access) -> None:
    graph.get_edge.return_value = {"_id": "edges/e1"}
    authorizer = TieredRecordAuthorizer(graph)

    with pytest.raises(RecordAccessDeniedError):
        await authorizer.authorize(_make_actor(), _stored_record(is_deleted=False))

    assert access.asked_about == [("user1", "org1", "rec1")]



_UNANSWERED = "Could not check access to record rec1: the knowledge graph did not answer. Try again later."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [ConnectionError("graph down at 10.0.0.7:8529"), PermissionVerificationUnavailableError()],
    ids=["read_failed", "check_unavailable"],
)
async def test_a_check_the_graph_could_not_answer_is_unavailable_without_its_text(graph, access, failure) -> None:
    """Not a denial, and not the driver's text either: the message goes to the model."""
    access.fail_with(failure)

    with pytest.raises(RecordContentUnavailableError) as exc:
        await TieredRecordAuthorizer(graph).authorize(_make_actor(), _make_record())

    assert str(exc.value) == _UNANSWERED
    assert exc.value.__cause__ is failure


@pytest.mark.asyncio
@pytest.mark.parametrize("bug", [MissingOrgContext("no org"), TypeError("bad call")], ids=["no_org", "type_error"])
async def test_our_own_bug_is_not_reported_as_an_outage(graph, access, bug) -> None:
    access.fail_with(bug)

    with pytest.raises(type(bug)) as exc:
        await TieredRecordAuthorizer(graph).authorize(_make_actor(), _make_record())

    assert exc.value is bug
