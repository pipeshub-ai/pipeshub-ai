"""The class-units upgrade reconcile must not delete the vector of a block whose text equals its class's.

A class with no methods (`class E(Exception)` with only a docstring) is one CODE_CLASS group and one "header"
block with the same text. Before class units both hashed to sha256(text), so old metadata lists
[group id, block id] under one hash. After them only the block keeps that hash; paired positionally,
compute_diff would match it with the OLD GROUP id, mark the block unchanged (no re-embed) and queue the
block's old id -- the one that owns its vector -- for removal.
"""
import hashlib
from unittest.mock import MagicMock

import pytest

from app.models.blocks import BlocksContainer
from app.modules.parsers.code_parser import CodeFileParser
from app.modules.reconciliation.service import (
    ReconciliationMetadata,
    ReconciliationService,
)
from app.modules.transformers.transformer import ReconciliationContext

from .test_class_header_units import _embedded, _record, _store

SRC = b'''class GraphError(Exception):
    """Base error."""


class Client:
    def get(self):
        return 1
'''


def _parse(prefix: str) -> BlocksContainer:
    c = CodeFileParser().parse_to_blocks(SRC, "e.py", "src/e.py", "python")
    for b in c.blocks:
        b.id = f"{prefix}b{b.index}"
    for g in c.block_groups:
        g.id = f"{prefix}g{g.index}"
    return c


@pytest.mark.asyncio
@pytest.mark.parametrize("stored_before_group_ids", [True, False])
async def test_upgrade_reconcile_keeps_a_vector_for_every_block(stored_before_group_ids: bool) -> None:
    old, new = _parse("o"), _parse("n")
    for g in old.block_groups:  # how 2113fd22f hashed a group
        g.content_hash = hashlib.sha256(g.data["text"].encode()).hexdigest()
    svc = ReconciliationService()
    old_metadata = svc.build_metadata(old)
    if stored_before_group_ids:  # what an upgrade actually reads: metadata stored by 2113fd22f
        stored = old_metadata.to_dict()
        stored.pop("block_group_ids")
        old_metadata = ReconciliationMetadata.from_dict(stored)
    to_index, to_delete, unchanged = svc.compute_diff(old_metadata, svc.build_metadata(new))
    svc.apply_preserved_ids(new, unchanged)
    vs = _store()
    await vs.apply(MagicMock(record=_record(new), reconciliation_context=ReconciliationContext(
        blocks_to_index_ids=to_index, block_ids_to_delete=to_delete)))
    deleted = {i for call in vs.delete_blocks_by_ids.call_args_list for i in call.args[0]}
    embedded = {d.metadata["blockId"] for d in _embedded(vs)}
    old_code_block_ids = {b.id for b in old.blocks if b.type.value == "code"}
    lost = old_code_block_ids & deleted
    # each block whose old point is removed must be re-embedded under its new id
    new_by_old = {v: k for k, v in unchanged.items()}
    assert not lost, (f"old block points removed {sorted(lost)}; unchanged map {unchanged}; "
                      f"re-embedded {sorted(embedded)}; new ids for them {[new_by_old.get(x) for x in lost]}")
