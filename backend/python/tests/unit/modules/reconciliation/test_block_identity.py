"""`BlockIdentity` (C-1): ids are a pure function of org, record, content hash and occurrence."""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from app.models.blocks import (
    Block,
    BlockGroup,
    BlocksContainer,
    BlockType,
    DataFormat,
    GroupType,
)
from app.modules.reconciliation.block_identity import (
    ID_SCHEME,
    NS_BLOCK_V1,
    BlockIdentity,
)
from app.modules.reconciliation.service import (
    ReconciliationMetadata,
    ReconciliationService,
)

if TYPE_CHECKING:
    import pytest


def _container(*texts: str) -> BlocksContainer:
    return BlocksContainer(blocks=[
        Block(index=i, type=BlockType.TEXT, format=DataFormat.TXT, data=t) for i, t in enumerate(texts)
    ])


def _ids(c: BlocksContainer) -> list[str]:
    return [b.id for b in c.blocks]


def test_ids_are_deterministic_and_idempotent() -> None:
    a, b = _container("x", "y"), _container("x", "y")
    assert BlockIdentity.assign(a, "o", "v") and BlockIdentity.assign(b, "o", "v")
    assert _ids(a) == _ids(b)
    before = _ids(a)
    BlockIdentity.assign(a, "o", "v")
    assert _ids(a) == before


def test_the_documented_derivation() -> None:
    c = _container("x")
    BlockIdentity.assign(c, "o", "v")
    expected = uuid.uuid5(NS_BLOCK_V1, f"o|v|b|{c.blocks[0].content_hash}|0")
    assert c.blocks[0].id == str(expected)
    assert NS_BLOCK_V1 == uuid.uuid5(uuid.NAMESPACE_URL, "pipeshub:block-id:v1")


def test_equal_blocks_get_distinct_ids_by_occurrence_and_do_not_shift_the_others() -> None:
    a, b = _container("x", "x", "y"), _container("x", "x", "y", "z")
    BlockIdentity.assign(a, "o", "v")
    BlockIdentity.assign(b, "o", "v")
    assert len(set(_ids(a))) == 3
    assert _ids(b)[:3] == _ids(a)


def test_changed_block_changes_only_its_id() -> None:
    a, b = _container("x", "y", "z"), _container("x", "Y", "z")
    BlockIdentity.assign(a, "o", "v")
    BlockIdentity.assign(b, "o", "v")
    assert [p == q for p, q in zip(_ids(a), _ids(b), strict=True)] == [True, False, True]


def test_org_and_record_are_part_of_the_id() -> None:
    ids = []
    for org, vrid in (("o", "v"), ("p", "v"), ("o", "w")):
        c = _container("x")
        BlockIdentity.assign(c, org, vrid)
        ids.append(c.blocks[0].id)
    assert len(set(ids)) == 3


def test_groups_and_blocks_of_equal_content_do_not_share_an_id() -> None:
    c = BlocksContainer(
        blocks=[Block(index=0, type=BlockType.TEXT, format=DataFormat.TXT, data="x")],
        block_groups=[BlockGroup(index=0, type=GroupType.TEXT_SECTION, data="x")],
    )
    BlockIdentity.assign(c, "o", "v")
    assert c.blocks[0].id != c.block_groups[0].id


def test_a_block_with_no_hash_uses_its_position(monkeypatch: pytest.MonkeyPatch) -> None:
    c = BlocksContainer(blocks=[Block(index=0, type=BlockType.TEXT, format=DataFormat.TXT, data=None)])
    c.blocks[0].content_hash = ""
    monkeypatch.setattr(ReconciliationService, "fill_content_hashes", staticmethod(lambda container: None))
    BlockIdentity.assign(c, "o", "v")
    assert c.blocks[0].id == str(uuid.uuid5(NS_BLOCK_V1, "o|v|b|pos:0|0"))


def test_without_an_org_or_record_nothing_is_derived() -> None:
    c = _container("x")
    original = _ids(c)
    assert not BlockIdentity.assign(c, "", "v")
    assert not BlockIdentity.assign(c, "o", "")
    assert _ids(c) == original


def test_old_metadata_of_another_scheme_is_rekeyed_once_not_paired() -> None:
    service = ReconciliationService()
    old = _container("x", "y")
    old_meta = service.build_metadata(old)  # random ids, no scheme
    new = _container("x", "y")
    BlockIdentity.assign(new, "o", "v")
    new_meta = service.build_metadata(new)
    new_meta.id_scheme = ID_SCHEME
    to_index, to_delete, preserved = service.compute_diff(old_meta, new_meta)
    assert to_index == set(_ids(new))
    assert to_delete == set(_ids(old))
    assert preserved == {}


def test_metadata_of_the_same_scheme_pairs_unchanged_blocks() -> None:
    service = ReconciliationService()
    old, new = _container("x", "y"), _container("x", "z")
    for c in (old, new):
        BlockIdentity.assign(c, "o", "v")
    old_meta, new_meta = service.build_metadata(old), service.build_metadata(new)
    old_meta.id_scheme = new_meta.id_scheme = ID_SCHEME
    to_index, to_delete, _ = service.compute_diff(old_meta, new_meta)
    assert to_index == {new.blocks[1].id} and to_delete == {old.blocks[1].id}


def test_the_scheme_survives_a_round_trip() -> None:
    meta = ReconciliationService().build_metadata(_container("x"))
    assert "idScheme" not in meta.to_dict()
    meta.id_scheme = ID_SCHEME
    assert ReconciliationMetadata.from_dict(meta.to_dict()).id_scheme == ID_SCHEME


def test_the_recorded_set_of_pointed_blocks_survives_a_metadata_round_trip() -> None:
    meta = ReconciliationService().build_metadata(_container("x"))
    assert "pointedBlocks" not in meta.to_dict() and ReconciliationMetadata.from_dict(meta.to_dict()).pointed_blocks is None
    meta.pointed_blocks = {"b2", "b1"}
    assert meta.to_dict()["pointedBlocks"] == ["b1", "b2"]
    assert ReconciliationMetadata.from_dict(meta.to_dict()).pointed_blocks == {"b1", "b2"}
