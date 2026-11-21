"""The twin writers: an md5 twin attach, the in-flight twin and the identity that travels with a copy.

A same-bytes ``.txt`` and ``.md`` are md5 twins (the dedup md5 is type-qualified only for html). The record whose type changed must
not be attached to a copy another parser built; a twin attached with an identity carries it, with its own timestamp.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import ProgressStatus
from app.events.dedup import DuplicateMatch
from app.modules.pipeline import parser_identity as pi
from tests.support.parser_identity_world import T0, World

TWIN_VRID = "vr-twin"


@pytest.fixture(autouse=True)
def _identity_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECORD_PARSER_IDENTITY_WRITE", "true")
    monkeypatch.delenv("USE_PARSING_SERVICE", raising=False)
    pi.end_run_identity()


def _twin(identity: str | None = "inproc:txt@1", *, source: str = pi.SOURCE_PARSED, mime: str = "text/plain", name: str = "a.txt", **extra: Any) -> dict[str, Any]:  # noqa: ANN401
    twin: dict[str, Any] = {
        "_key": "rec-9", "recordType": "FILE", "orgId": "org-1", "mimeType": mime, "recordName": name,
        "indexingStatus": "COMPLETED", "hasIndexedCopy": True, "virtualRecordId": TWIN_VRID, "lastIndexTimestamp": T0 + 7,
        "contentRev": "rev-twin", "extractionStatus": "COMPLETED", "summaryDocumentId": "sum",
    }
    if identity:
        twin.update({
            pi.IDENTITY_FIELD: identity, pi.IDENTITY_AT_FIELD: T0 + 7, pi.IDENTITY_VRID_FIELD: TWIN_VRID,
            pi.IDENTITY_SOURCE_FIELD: source,
        })
    twin.update(extra)
    return twin


def _world(*, markdown: bool) -> World:
    world = World(node={"contentRev": "older", "virtualRecordId": TWIN_VRID})
    if markdown:
        world.renamed_to_markdown()
    return world


def _files_by_key(world: World, twin_ext: str = "txt") -> None:
    real = world.graph.get_document.side_effect

    async def get(key: str, collection: str, **kw: object) -> dict[str, Any] | None:
        if collection == "files" and key == "rec-9":
            return {"_key": "rec-9", "extension": twin_ext}
        return await real(key, collection, **kw)

    world.graph.get_document = AsyncMock(side_effect=get)


def _attach_stubs(world: World) -> None:
    ep = world.ep
    ep.mark_record_status = AsyncMock()
    ep._copy_twin_edges = AsyncMock()
    ep._clear_pending = AsyncMock()
    ep.sync_vector_membership = AsyncMock()
    ep._sync_entities_for_attached_copy = AsyncMock()
    ep._vrid_has_stored_content = AsyncMock(return_value=True)
    ep.update_record_fields = AsyncMock(side_effect=lambda doc, fields, **_k: world.state.update(fields) or True)


def _match(twin: dict[str, Any]) -> DuplicateMatch:
    return DuplicateMatch(record=twin, same_collection=True, is_processed=True)


@pytest.mark.asyncio
async def test_a_record_renamed_behind_a_same_bytes_twin_is_not_attached_to_the_other_parser_copy() -> None:
    """A=a.txt and B=b.txt share copy X; B is renamed to b.md. B's update must parse, not take X again."""
    world = _world(markdown=True)
    _files_by_key(world)
    _attach_stubs(world)
    world.ep._attach_to_finished_twin = AsyncMock()

    decision = await world.ep._reuse_finished_duplicate(world.state, _match(_twin("inproc:txt@1")))

    assert decision.skip_indexing is False
    assert decision.virtual_record_id is None
    assert decision.rebuild_shared_vrid is False  # the other record's copy is not rebuilt under it
    world.ep._attach_to_finished_twin.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_known_equal_twin_is_attached_with_its_identity_and_the_attach_writes_own_timestamp() -> None:
    world = _world(markdown=False)
    _files_by_key(world)
    _attach_stubs(world)

    decision = await world.ep._reuse_finished_duplicate(world.state, _match(_twin("inproc:txt@1")))

    assert decision.skip_indexing is True
    assert world.state["virtualRecordId"] == TWIN_VRID
    assert world.state[pi.IDENTITY_FIELD] == "inproc:txt@1"
    assert world.state[pi.IDENTITY_SOURCE_FIELD] == pi.SOURCE_TWIN
    assert world.state[pi.IDENTITY_VRID_FIELD] == TWIN_VRID
    assert world.state[pi.IDENTITY_AT_FIELD] == world.state["lastIndexTimestamp"] != T0 + 7
    assert pi.known_identity(world.state) == "inproc:txt@1"


@pytest.mark.asyncio
async def test_an_adopted_identity_stays_adopted_when_it_travels() -> None:
    world = _world(markdown=False)
    _files_by_key(world)
    _attach_stubs(world)

    await world.ep._reuse_finished_duplicate(world.state, _match(_twin("inproc:txt@1", source=pi.SOURCE_ADOPTED)))

    assert world.state[pi.IDENTITY_SOURCE_FIELD] == pi.SOURCE_ADOPTED


@pytest.mark.asyncio
async def test_an_unknown_twin_of_the_same_graph_type_is_attached_and_writes_no_identity() -> None:
    world = _world(markdown=False)
    _files_by_key(world)
    _attach_stubs(world)

    decision = await world.ep._reuse_finished_duplicate(world.state, _match(_twin(None)))

    assert decision.skip_indexing is True
    assert pi.IDENTITY_FIELD not in world.state


@pytest.mark.asyncio
async def test_an_unknown_twin_of_another_graph_type_is_not_attached() -> None:
    world = _world(markdown=True)
    _files_by_key(world, "txt")
    _attach_stubs(world)
    world.ep._attach_to_finished_twin = AsyncMock()

    decision = await world.ep._reuse_finished_duplicate(world.state, _match(_twin(None)))

    assert decision.skip_indexing is False
    world.ep._attach_to_finished_twin.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_attach_to_an_unknown_twin_leaves_the_records_previous_identity_fields_as_they_are() -> None:
    """Not unset: unset, the next update would adopt the record's type over a copy nothing confirmed."""
    world = _world(markdown=False)
    world.identified("inproc:txt@1")
    before = {k: world.state[k] for k in pi.IDENTITY_FIELDS}
    _files_by_key(world)
    _attach_stubs(world)

    await world.ep._reuse_finished_duplicate(world.state, _match(_twin(None)))

    assert {k: world.state[k] for k in pi.IDENTITY_FIELDS} == before
    assert pi.known_identity(world.state) is None  # stale now: the next update runs in full, it is not adopted


@pytest.mark.asyncio
@pytest.mark.parametrize("record", [{"mimeType": "application/octet-stream", "recordName": "blob"}])
async def test_a_record_with_nothing_tracked_is_attached_as_before(record: dict[str, Any]) -> None:
    world = World(node={"contentRev": "older", "virtualRecordId": TWIN_VRID, **record}, file_doc={"_key": "rec-1", "extension": "unknown"})
    _files_by_key(world)
    _attach_stubs(world)

    decision = await world.ep._reuse_finished_duplicate(world.state, _match(_twin("inproc:txt@1")))

    assert decision.skip_indexing is True


@pytest.mark.asyncio
async def test_a_twin_with_nothing_tracked_is_attached_as_before() -> None:
    world = _world(markdown=False)
    _files_by_key(world, "unknown")
    _attach_stubs(world)

    decision = await world.ep._reuse_finished_duplicate(
        world.state, _match(_twin(None, mime="application/octet-stream", name="blob"))
    )

    assert decision.skip_indexing is True


@pytest.mark.asyncio
@pytest.mark.parametrize(("markdown", "queued"), [(False, True), (True, False)], ids=["same-type-waits", "other-type-indexes-itself"])
async def test_a_twin_still_being_processed_is_waited_for_only_when_read_by_the_same_parser(
    markdown: bool, queued: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The in-flight twin has no stored identity yet: it is compared by its own graph type."""
    from app.events import events as events_module

    other = _twin(None, indexingStatus="IN_PROGRESS", mime="text/plain", name="a.txt", md5Checksum="m")
    world = _world(markdown=markdown)
    world.state["md5Checksum"] = "m"
    _files_by_key(world, "txt")
    ep = world.ep
    ep._find_duplicate_records = AsyncMock(return_value=[other])
    ep._hash_for_dedup = MagicMock(return_value="m")
    ep._resolve_write_collection = MagicMock(return_value="c")
    ep._resolves_to_same_collection = MagicMock(return_value=True)
    ep._require_persisted = MagicMock()
    ep.update_record_fields = AsyncMock(side_effect=lambda doc, fields, **_k: world.state.update(fields) or True)
    monkeypatch.setattr(events_module, "offload_if_large", AsyncMock(return_value="m"))
    monkeypatch.setattr(events_module, "will_promote_queued_copies", lambda *_a, **_k: True)
    del ep._check_duplicate_by_md5  # the real method (the world stubs it)

    decision = await ep._check_duplicate_by_md5(b"x", world.state, content_rev="r")

    assert (decision.skip_indexing is True) is queued
    assert (world.state.get("indexingStatus") == ProgressStatus.QUEUED.value) is queued


@pytest.mark.asyncio
async def test_an_octet_stream_record_named_without_a_suffix_is_read_from_its_files_document_before_it_attaches() -> None:
    """The record's own extension is on its files document only; reading the records node alone would call it not tracked and attach."""
    world = World(
        node={"contentRev": "older", "virtualRecordId": TWIN_VRID, "mimeType": "application/octet-stream", "recordName": "notes"},
        file_doc={"_key": "rec-1", "extension": "md"},
    )
    _files_by_key(world)
    _attach_stubs(world)
    world.ep._attach_to_finished_twin = AsyncMock()

    decision = await world.ep._reuse_finished_duplicate(world.state, _match(_twin("inproc:txt@1")))

    assert decision.skip_indexing is False
    world.ep._attach_to_finished_twin.assert_not_awaited()
