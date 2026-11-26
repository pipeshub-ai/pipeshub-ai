"""P2.13 LB-1, LB-2: a label names the publication a record came through, and the one access check
(`/api/v1/records/access`, `TieredRecordAuthorizer.decide`) shows such a source only to the publication's audience.

That check decides SS2's visible turns, SS3's copied turns and the citations shared viewers see, so a turn built on
published content is never re-shared outside the audience: a non-audience collaborator gets the stub.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.harness.contracts.generated.pipeshub.citation_source import (
    PipeshubRecordSource,
)
from app.harness.knowledge.service import Container, KnowledgePublications
from app.harness.labels import (
    LabelSource,
    citation_sources,
    classify_record,
    label_from_wire,
    label_wire,
)
from app.services.record_content.authorizer import TieredRecordAuthorizer
from harness_core.knowledge.memory import InMemoryPublicationDirectory
from harness_core.knowledge.publications import Audience, Principal, PublicationDraft
from tests.unit.services.record_content.test_authorizer import (
    _Graph,
    _make_actor,
    _make_record,
)

NOW = 1_000_000
KB = "apps/kb-hr"


def test_lb1_a_published_record_names_its_publication() -> None:
    source = classify_record({"recordId": "r1", "connector": "KB", "publicationId": "p1", "publicationMode": "answer_only"})
    assert source == LabelSource("indexed", record_id="r1", publication_id="p1")
    assert source.to_wire() == {"kind": "indexed", "recordId": "r1", "publicationId": "p1"}
    assert classify_record({"recordId": "r1", "connector": "KB"}) == LabelSource("indexed", record_id="r1")


def test_lb1_a_bare_id_of_a_published_record_is_folded_into_it() -> None:
    label = citation_sources(
        {
            "records": {"v1": {"id": "r1", "connector_name": "KB", "publicationId": "p1"}},
            "knownRecordIds": ["r1", "r2"],
        }
    )
    assert label.complete
    assert label.sources == frozenset({LabelSource("indexed", record_id="r1", publication_id="p1"), LabelSource("indexed", record_id="r2")})
    round_trip = label_from_wire(label_wire(label))
    assert round_trip == label


class _Publications(KnowledgePublications):
    def __init__(self, records: dict[str, set[str]], *, enabled: bool = True) -> None:
        super().__init__(graph=None, config_service=None, directory=InMemoryPublicationDirectory(), now_ms=lambda: self.now)  # type: ignore[arg-type]
        self.now = NOW
        self._enabled = enabled
        self._records_of = records
        self.principals = {
            "alice": Principal("org1", "alice"),
            "carol": Principal("org1", "carol", frozenset({"t-hr"})),
            "bob": Principal("org1", "bob"),
        }

    async def enabled(self) -> bool:
        return self._enabled

    async def principal(self, user_id: str | None, org_id: str) -> Principal | None:
        return self.principals.get(user_id or "")

    async def container(self, org_id: str, qualified: str) -> Container | None:
        if qualified not in self._records_of:
            return None
        collection, key = qualified.split("/", 1)
        return Container(qualified, collection, key, key)

    async def container_records(self, org_id: str, container: Container, cap: int) -> dict[str, str]:
        return {f"v-{rid}": rid for rid in self._records_of[container.id]}


async def _decide(publications: _Publications | None, viewer: str, *sources: PipeshubRecordSource) -> list[str]:
    graph = _Graph({"r1": _make_record(record_id="r1"), "r-own": _make_record(record_id="r-own")}, acl={(viewer, "org1", "r-own")})
    return await TieredRecordAuthorizer(graph).decide(_make_actor(user_id=viewer), list(sources), publications=publications)


def _src(record_id: str, publication: str | None = None, **extra: Any) -> PipeshubRecordSource:  # noqa: ANN401
    return PipeshubRecordSource.model_validate({"kind": "indexed", "recordId": record_id, **({"publicationId": publication} if publication else {}), **extra})


@pytest.fixture
async def published() -> _Publications:
    publications = _Publications({KB: {"r1"}})
    draft = PublicationDraft("org1", KB, Audience(user_ids=frozenset({"alice"}), group_ids=frozenset({"t-hr"})), "answer_only", "owner")
    await publications.directory.create(draft, publication_id="p1", now_ms=NOW)
    return publications


async def test_lb2_the_audience_sees_a_published_source_others_do_not(published: _Publications) -> None:
    assert await _decide(published, "alice", _src("r1", "p1")) == ["readable"]
    assert await _decide(published, "carol", _src("r1", "p1")) == ["readable"]
    # Bob shares the session but not the audience: the turn naming r1 stays a stub for him.
    assert await _decide(published, "bob", _src("r1", "p1")) == ["denied"]


async def test_lb2_an_unqualified_source_needs_own_access(published: _Publications) -> None:
    assert await _decide(published, "alice", _src("r1")) == ["denied"]
    assert await _decide(published, "bob", _src("r-own")) == ["readable"]


async def test_lb2_another_publication_id_or_record_never_passes(published: _Publications) -> None:
    assert await _decide(published, "alice", _src("r1", "p-forged")) == ["denied"]
    assert await _decide(published, "alice", _src("r-other", "p1")) == ["denied"]
    assert await _decide(published, "alice", _src("r1", "p1", virtualRecordId="v-x")) == ["denied"]


async def test_lb2_revocation_and_expiry_take_the_source_away(published: _Publications) -> None:
    await published.directory.revoke("org1", "p1", by="owner", now_ms=NOW + 1)
    assert await _decide(published, "alice", _src("r1", "p1")) == ["denied"]


async def test_lb2_flag_off_is_todays_check(published: _Publications) -> None:
    published._enabled = False
    assert await _decide(published, "alice", _src("r1", "p1")) == ["denied"]
    assert await _decide(None, "alice", _src("r1", "p1")) == ["denied"]
