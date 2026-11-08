"""compute_diff on metadata stored before group ids were recorded: no block is kept under an
old group's id, which has no vector."""
import hashlib

from app.models.blocks import BlocksContainer
from app.modules.parsers.code_parser import CodeFileParser
from app.modules.reconciliation.service import (
    ReconciliationMetadata,
    ReconciliationService,
)

ERR = '''class GraphError(Exception):
    """Base error."""
'''


def _parse(src: str, prefix: str) -> BlocksContainer:
    c = CodeFileParser().parse_to_blocks(src.encode(), "e.py", "src/e.py", "python")
    for b in c.blocks:
        b.id = f"{prefix}b{b.index}"
    for g in c.block_groups:
        g.id = f"{prefix}g{g.index}"
    return c


def _legacy(c) -> ReconciliationMetadata:
    """Metadata as 2113fd22f stored it: group hash sha256(text), no block_group_ids."""
    for g in c.block_groups:
        g.content_hash = hashlib.sha256(g.data["text"].encode()).hexdigest()
    stored = ReconciliationService().build_metadata(c).to_dict()
    stored.pop("block_group_ids", None)
    return ReconciliationMetadata.from_dict(stored)


def _diff(old_src: str, new_src: str) -> tuple:
    old, new = _parse(old_src, "o"), _parse(new_src, "n")
    svc = ReconciliationService()
    return old, new, *svc.compute_diff(_legacy(old), svc.build_metadata(new))


def test_upgrade_with_an_unchanged_file_keeps_every_block() -> None:
    old, new, to_index, to_delete, unchanged = _diff(ERR, ERR)
    assert {b.id: unchanged.get(b.id) for b in new.blocks} == {b.id: "o" + b.id[1:] for b in new.blocks}
    assert to_delete == {g.id for g in old.block_groups}


def test_upgrade_in_the_same_edit_that_duplicates_a_methodless_class() -> None:
    old, new, to_index, to_delete, unchanged = _diff(ERR, ERR + "\n\n" + ERR)
    old_groups = {g.id for g in old.block_groups}
    assert not [k for k, v in unchanged.items() if v in old_groups], unchanged
    assert {b.id for b in new.blocks if unchanged.get(b.id) is None} <= to_index


def test_legacy_split_when_new_blocks_outnumber_the_old_blocks_of_a_shared_hash() -> None:
    """Stored [group, block] under one hash, and the new side has two blocks with that hash: the tail rule
    would keep one of them under the old group's id. Nothing is paired: both are indexed, both old ids go."""
    old = ReconciliationMetadata(hash_to_block_ids={"h": ["og0", "ob0"]}, block_id_to_index={"og0": 0, "ob0": 0})
    new = ReconciliationMetadata(hash_to_block_ids={"h": ["nb0", "nb1"]}, block_id_to_index={"nb0": 0, "nb1": 1},
                                 block_group_ids=set())
    new.legacy_group_hashes = frozenset({"h"})  # a class of the new file had this text before the bump
    to_index, to_delete, unchanged = ReconciliationService().compute_diff(old, new)
    assert (to_index, to_delete, unchanged) == ({"nb0", "nb1"}, {"og0", "ob0"}, {})


def test_legacy_blocks_up_to_half_a_hash_keep_their_ids() -> None:
    """Two methodless classes of one text: [g, g, b, b] -> the two new blocks keep the old blocks' ids."""
    old = ReconciliationMetadata(hash_to_block_ids={"h": ["og0", "og1", "ob0", "ob1"]},
                                 block_id_to_index={"og0": 0, "og1": 1, "ob0": 0, "ob1": 1})
    new = ReconciliationMetadata(hash_to_block_ids={"h": ["nb0", "nb1"]}, block_id_to_index={"nb0": 0, "nb1": 1},
                                 block_group_ids=set())
    to_index, to_delete, unchanged = ReconciliationService().compute_diff(old, new)
    assert (to_index, to_delete, unchanged) == (set(), {"og0", "og1"}, {"nb0": "ob0", "nb1": "ob1"})


def test_an_unchanged_file_with_duplicate_blocks_keeps_every_block_on_upgrade() -> None:
    """`model: str` in three classes is one legacy hash listing three blocks and no group.
    Only a hash a code class had can hold a group, so these pair as before: nothing is re-embedded."""
    src = "".join(f'class C{i}(BaseModel):\n    """Doc {i}."""\n    model: str\n    n{i}: int = {i}\n\n\n'
                  for i in range(3))
    old, new, to_index, to_delete, unchanged = _diff(src, src)
    assert not [b.id for b in new.blocks if b.id in to_index], (to_index, to_delete)
    assert {b.id: unchanged.get(b.id) for b in new.blocks} == {b.id: "o" + b.id[1:] for b in new.blocks}


def test_a_non_code_legacy_record_with_unchanged_content_is_left_alone() -> None:
    old, new = _parse(ERR, "o"), _parse(ERR, "n")
    for c in (old, new):
        for g in c.block_groups:
            g.content_hash = hashlib.sha256(g.data["text"].encode()).hexdigest()
    svc = ReconciliationService()
    stored = svc.build_metadata(old).to_dict()
    stored.pop("block_group_ids")
    to_index, to_delete, unchanged = svc.compute_diff(ReconciliationMetadata.from_dict(stored), svc.build_metadata(new))
    assert (to_index, to_delete) == (set(), set())
    assert unchanged == {i: "o" + i[1:] for i in unchanged} and len(unchanged) == len(new.blocks) + len(new.block_groups)
