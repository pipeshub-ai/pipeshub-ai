"""`ArtifactViewerAccess` (SS0): a reader who does not own an artifact sees it only
when its provenance is known and every source in it is readable by them."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from app.models.entities import RecordType
from app.services.artifact_registry.models import Actor
from app.services.artifact_registry.viewer_access import (
    ArtifactViewerAccess,
    RecordedProvenance,
    artifact_viewer_access,
    is_artifact_record,
)
from app.services.graph_db.interface.record_access import RECORD_ACCESS_METHODS
from tests.support.record_access import InMemoryRecordAccess

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

VIEWER = Actor(org_id="org-1", user_id="u-viewer")
OWNER = Actor(org_id="org-1", user_id="u-owner")


class KnownProvenance:
    def __init__(self, sources: Mapping[str, frozenset[str] | None]) -> None:
        self.sources = dict(sources)
        self.asked: list[list[str]] = []

    async def sources_of(self, org_id: str, artifact_ids: Sequence[str]) -> Mapping[str, frozenset[str] | None]:
        self.asked.append(list(artifact_ids))
        return {aid: self.sources.get(aid) for aid in artifact_ids}


class Graph:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.records: dict[str, Any] = {
            "art-1": SimpleNamespace(id="art-1", org_id="org-1", record_type=RecordType.ARTIFACT),
            "art-2": SimpleNamespace(id="art-2", org_id="org-1", record_type=RecordType.ARTIFACT),
            "art-other-org": SimpleNamespace(id="art-other-org", org_id="org-2", record_type=RecordType.ARTIFACT),
            "kb-open": SimpleNamespace(id="kb-open", org_id="org-1", record_type=RecordType.FILE),
            "kb-closed": SimpleNamespace(id="kb-closed", org_id="org-1", record_type=RecordType.FILE),
        }
        self.acl = {("u-viewer", "kb-open")}
        self.edges = {("key-u-owner", "art-1"): "OWNER", ("key-u-viewer", "art-1"): "READER"}
        self.asked_about: list[tuple[str, str, str]] = []

    @property
    def acl_checks(self) -> list[str]:
        return [rid for _, _, rid in self.asked_about]

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        # Callers seed `records` and `acl` after construction (test_source_ledger), so each read sees the world as it is now.
        if name not in RECORD_ACCESS_METHODS:
            raise AttributeError(name)
        access = InMemoryRecordAccess(asked_about=self.asked_about)
        for rid, record in self.records.items():
            access.record(rid, record.org_id)
        for user_id, rid in self.acl:
            if user_id not in access.users:
                access.user(user_id, self.records[rid].org_id, key=f"key-{user_id}")
            access.grant(rid, user=user_id)
        return getattr(access, name)

    async def get_typed_records_batch(self, record_ids: list[str]) -> dict[str, Any]:
        if self.fail:
            raise ConnectionError("graph down")
        return {rid: self.records[rid] for rid in record_ids if rid in self.records}

    async def get_user_by_user_id(self, user_id: str) -> dict[str, str]:
        return {"_key": f"key-{user_id}"}

    async def get_edge(self, **kw: str) -> dict[str, str] | None:
        role = self.edges.get((kw["from_id"], kw["to_id"]))
        return {"role": role} if role else None

    async def get_document(self, doc_id: str, collection: str) -> dict | None:
        """No artifact here has recorded provenance (the legacy paths record none)."""
        return None


@pytest.mark.asyncio
async def test_unknown_provenance_is_unresolved_without_any_acl_check() -> None:
    """AV-1."""
    graph = Graph()
    access = ArtifactViewerAccess(graph, KnownProvenance({}))

    assert await access.decide(VIEWER, ["art-1"]) == {"art-1": "unresolved"}
    assert graph.acl_checks == []


@pytest.mark.asyncio
async def test_known_provenance_readable_denied_and_empty() -> None:
    """AV-2, AV-3, AV-4: one batch for every source."""
    graph = Graph()
    access = ArtifactViewerAccess(
        graph,
        KnownProvenance({
            "art-1": frozenset({"kb-open"}),
            "art-2": frozenset({"kb-open", "kb-closed"}),
        }),
    )

    assert await access.decide(VIEWER, ["art-1", "art-2"]) == {"art-1": "readable", "art-2": "denied"}
    assert sorted(graph.acl_checks) == ["kb-closed", "kb-open"]

    empty = ArtifactViewerAccess(Graph(), KnownProvenance({"art-1": frozenset()}))
    assert await empty.decide(VIEWER, ["art-1"]) == {"art-1": "readable"}


@pytest.mark.asyncio
async def test_missing_other_org_and_non_artifact_ids_are_unresolved() -> None:
    """AV-5: a record that is not an artifact is not decided as one."""
    provenance = KnownProvenance({"art-other-org": frozenset(), "kb-open": frozenset()})
    access = ArtifactViewerAccess(Graph(), provenance)

    decided = await access.decide(VIEWER, ["nope", "art-other-org", "kb-open", "", "nope"])

    assert decided == {"nope": "unresolved", "art-other-org": "unresolved", "kb-open": "unresolved"}
    assert provenance.asked == []


@pytest.mark.asyncio
async def test_graph_failure_propagates() -> None:
    """AV-6."""
    with pytest.raises(ConnectionError):
        await ArtifactViewerAccess(Graph(fail=True), KnownProvenance({})).decide(VIEWER, ["art-1"])


@pytest.mark.asyncio
async def test_production_provenance_is_unknown_for_every_artifact(caplog: pytest.LogCaptureFixture) -> None:
    """AV-7 (P3.5: production reads recorded provenance): an artifact without any stays hidden."""
    assert await RecordedProvenance(Graph()).sources_of("org-1", ["a", "b"]) == {"a": None, "b": None}
    caplog.set_level(logging.INFO, logger="app.services.artifact_registry.viewer_access")

    assert await artifact_viewer_access(Graph()).decide(VIEWER, ["art-1", "art-2"]) == {
        "art-1": "unresolved",
        "art-2": "unresolved",
    }
    line = next(r.getMessage() for r in caplog.records if "artifacts.viewer_access_checked" in r.getMessage())
    assert "requested=2" in line and "unknownProvenance=2" in line
    assert "art-1" not in line


@pytest.mark.asyncio
async def test_may_stream_owner_always_reader_only_by_provenance() -> None:
    """AV-11, AV-12 at the authority: a READER edge alone never streams an artifact."""
    graph = Graph()
    unknown = artifact_viewer_access(graph)
    assert await unknown.may_stream(OWNER, "art-1") is True
    assert await unknown.may_stream(VIEWER, "art-1") is False

    known = ArtifactViewerAccess(graph, KnownProvenance({"art-1": frozenset({"kb-open"})}))
    assert await known.may_stream(VIEWER, "art-1") is True


def test_is_artifact_record_reads_typed_and_raw_records() -> None:
    assert is_artifact_record(SimpleNamespace(record_type=RecordType.ARTIFACT))
    assert is_artifact_record({"recordType": "ARTIFACT"})
    assert not is_artifact_record(SimpleNamespace(record_type=RecordType.FILE))
    assert not is_artifact_record({})
