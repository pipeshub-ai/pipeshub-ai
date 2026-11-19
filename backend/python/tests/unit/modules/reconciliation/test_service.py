import hashlib
import json

from app.models.blocks import (
    Block,
    BlockGroup,
    BlocksContainer,
    BlockType,
    CitationMetadata,
    GroupType,
)
from app.modules.reconciliation.service import (
	ReconciliationMetadata,
	ReconciliationService,
)


def _hash_for(data):
	payload = json.dumps(data, sort_keys=True).encode("utf-8")
	return hashlib.sha256(payload).hexdigest() + ":" + hashlib.md5(payload).hexdigest()


def _block(block_id, data, content_hash=None):
	return Block(id=block_id, type=BlockType.TEXT, data=data, content_hash=content_hash)


def _block_group(group_id, data, content_hash=None):
	return BlockGroup(
		id=group_id,
		type=GroupType.TEXT_SECTION,
		data=data,
		content_hash=content_hash,
	)


def test_reconciliation_metadata_to_dict_and_from_dict_filters_invalid_indices():
	metadata = ReconciliationMetadata(
		hash_to_block_ids={"h1": ["b1", "b2"]},
		block_id_to_index={"b1": 0, "b2": 1},
	)

	serialized = metadata.to_dict()
	restored = ReconciliationMetadata.from_dict(
		{
			"hash_to_block_ids": serialized["hash_to_block_ids"],
			"block_id_to_index": {"b1": 0, "b2": "1", "b3": None},
		}
	)

	assert serialized["hash_to_block_ids"] == {"h1": ["b1", "b2"]}
	assert serialized["block_id_to_index"] == {"b1": 0, "b2": 1}
	assert restored.hash_to_block_ids == {"h1": ["b1", "b2"]}
	assert restored.block_id_to_index == {"b1": 0}


def test_build_metadata_computes_missing_hashes_and_preserves_order_for_duplicates():
	service = ReconciliationService()

	shared_data = {"text": "same"}
	explicit_hash = "explicit-hash"

	container = BlocksContainer(
		block_groups=[
			_block_group("g1", shared_data, content_hash=None),
			_block_group("g2", {"title": "prehashed"}, content_hash=explicit_hash),
		],
		blocks=[
			_block("b1", shared_data, content_hash=None),
			_block("b2", shared_data, content_hash=None),
			_block("b3", {"title": "prehashed"}, content_hash=explicit_hash),
		],
	)

	metadata = service.build_metadata(container)
	computed_shared_hash = _hash_for(shared_data)

	assert metadata.hash_to_block_ids[computed_shared_hash] == ["g1", "b1", "b2"]
	assert metadata.hash_to_block_ids[explicit_hash] == ["g2", "b3"]

	assert metadata.block_id_to_index == {
		"g1": 0,
		"g2": 1,
		"b1": 0,
		"b2": 1,
		"b3": 2,
	}

	assert container.block_groups[0].content_hash == computed_shared_hash
	assert container.blocks[0].content_hash == computed_shared_hash
	assert container.blocks[1].content_hash == computed_shared_hash


def test_compute_diff_handles_duplicates_new_old_and_unchanged_hashes():
	service = ReconciliationService()

	old_metadata = ReconciliationMetadata(
		hash_to_block_ids={
			"shared": ["old_shared_1"],
			"grow": ["old_grow_1"],
			"shrink": ["old_shrink_1", "old_shrink_2"],
			"old_only": ["old_only_1"],
		}
	)
	new_metadata = ReconciliationMetadata(
		hash_to_block_ids={
			"shared": ["new_shared_1"],
			"grow": ["new_grow_1", "new_grow_2"],
			"shrink": ["new_shrink_1"],
			"new_only": ["new_only_1"],
		}
	)

	blocks_to_index, blocks_to_delete, unchanged_map = service.compute_diff(
		old_metadata,
		new_metadata,
	)

	assert unchanged_map == {
		"new_shared_1": "old_shared_1",
		"new_grow_1": "old_grow_1",
		"new_shrink_1": "old_shrink_1",
	}
	assert blocks_to_index == {"new_grow_2", "new_only_1"}
	assert blocks_to_delete == {"old_shrink_2", "old_only_1"}


def test_compute_diff_when_no_hash_overlap_indexes_all_new_and_deletes_all_old():
	service = ReconciliationService()

	old_metadata = ReconciliationMetadata(hash_to_block_ids={"h_old": ["old1", "old2"]})
	new_metadata = ReconciliationMetadata(hash_to_block_ids={"h_new": ["new1"]})

	blocks_to_index, blocks_to_delete, unchanged_map = service.compute_diff(
		old_metadata,
		new_metadata,
	)

	assert blocks_to_index == {"new1"}
	assert blocks_to_delete == {"old1", "old2"}
	assert unchanged_map == {}


def test_build_metadata_records_which_ids_are_groups_and_it_round_trips() -> None:
    container = BlocksContainer(block_groups=[_block_group("g1", {"text": "x"})], blocks=[_block("b1", {"text": "x"})])
    metadata = ReconciliationService().build_metadata(container)
    assert metadata.block_group_ids == {"g1"}
    assert ReconciliationMetadata.from_dict(metadata.to_dict()).block_group_ids == {"g1"}
    # metadata stored before groups were recorded reads as "kinds unknown", not "no groups"
    assert ReconciliationMetadata.from_dict({"hash_to_block_ids": {}, "block_id_to_index": {}}).block_group_ids is None


def test_a_block_is_never_paired_with_a_group_id_when_both_record_their_groups() -> None:
    service = ReconciliationService()
    # A group and a block share a hash on both sides, listed in the opposite order
    old = ReconciliationMetadata(hash_to_block_ids={"h": ["ob", "og"]}, block_group_ids={"og"})
    new = ReconciliationMetadata(hash_to_block_ids={"h": ["ng", "nb"]}, block_group_ids={"ng"})
    to_index, to_delete, unchanged = service.compute_diff(old, new)
    assert unchanged == {"ng": "og", "nb": "ob"}
    assert (to_index, to_delete) == (set(), set())


def test_metadata_without_recorded_groups_pairs_new_blocks_with_its_tail() -> None:
    """Metadata from before groups were recorded lists each hash as [groups..., blocks...]. After the
    class-units hash bump a class equal to its only block keeps the block's hash and loses the group's:
    the block must keep its own id (and vector), and the old group id is the one dropped."""
    service = ReconciliationService()
    old = ReconciliationMetadata(hash_to_block_ids={"h": ["og", "ob"], "class": ["og2"]})
    new = ReconciliationMetadata(hash_to_block_ids={"h": ["nb"], "class-v2": ["ng"]}, block_group_ids={"ng"})
    to_index, to_delete, unchanged = service.compute_diff(old, new)
    assert unchanged == {"nb": "ob"}
    assert to_index == {"ng"}
    assert to_delete == {"og", "og2"}


def test_metadata_without_recorded_groups_still_pairs_groups_with_its_head() -> None:
    service = ReconciliationService()
    old = ReconciliationMetadata(hash_to_block_ids={"h": ["og", "ob1", "ob2"]})
    new = ReconciliationMetadata(hash_to_block_ids={"h": ["ng", "nb1", "nb2"]}, block_group_ids={"ng"})
    _, to_delete, unchanged = service.compute_diff(old, new)
    assert unchanged == {"ng": "og", "nb1": "ob1", "nb2": "ob2"}
    assert to_delete == set()


def test_apply_preserved_ids_updates_blocks_and_block_groups():
	service = ReconciliationService()

	container = BlocksContainer(
		block_groups=[
			_block_group("new_g1", {"k": "v"}),
			_block_group("unchanged_g2", {"k": "v2"}),
		],
		blocks=[
			_block("new_b1", {"text": "a"}),
			_block("unchanged_b2", {"text": "b"}),
		],
	)

	service.apply_preserved_ids(
		container,
		unchanged_id_map={
			"new_g1": "old_g1",
			"new_b1": "old_b1",
		},
	)

	assert [g.id for g in container.block_groups] == ["old_g1", "unchanged_g2"]
	assert [b.id for b in container.blocks] == ["old_b1", "unchanged_b2"]


def test_apply_preserved_ids_noop_for_empty_mapping():
	service = ReconciliationService()

	container = BlocksContainer(
		block_groups=[_block_group("g1", {"k": "v"})],
		blocks=[_block("b1", {"text": "a"})],
	)

	service.apply_preserved_ids(container, unchanged_id_map={})

	assert container.block_groups[0].id == "g1"
	assert container.blocks[0].id == "b1"


def _table(heading: str | None) -> BlocksContainer:
    """A table on page 2: an optional heading line, the table group, and one row."""
    page = CitationMetadata(page_number=2)
    blocks = [Block(id="h", index=0, type=BlockType.TEXT, data=heading, citation_metadata=page)] if heading else []
    blocks.append(Block(id="r", index=len(blocks), type=BlockType.TABLE_ROW, parent_index=0,
                        data={"row_natural_language_text": "Total is 10"}, citation_metadata=page))
    group = BlockGroup(id="t", index=0, type=GroupType.TABLE, data={"table_summary": "Totals"}, citation_metadata=page)
    return BlocksContainer(blocks=blocks, block_groups=[group])


def _hashes(container: BlocksContainer) -> dict[str, list[str]]:
    return ReconciliationService().build_metadata(container).hash_to_block_ids


def test_a_table_without_a_title_keeps_the_data_only_hash() -> None:
    hashes = _hashes(_table(None))
    assert hashes[_hash_for({"row_natural_language_text": "Total is 10"})] == ["r"]
    assert hashes[_hash_for({"table_summary": "Totals"})] == ["t"]


def test_a_changed_heading_marks_its_rows_and_summary_for_reindexing() -> None:
    service = ReconciliationService()
    old = service.build_metadata(_table("Consolidated Balance Sheets"))
    same = service.build_metadata(_table("Consolidated Balance Sheets"))
    new = service.build_metadata(_table("Consolidated Statements of Income"))

    assert service.compute_diff(old, same)[0] == set()
    to_index, to_delete, _ = service.compute_diff(old, new)
    assert to_index == {"h", "r", "t"}
    assert to_delete == {"h", "r", "t"}


def test_a_parser_supplied_hash_is_replaced_when_the_table_has_a_title() -> None:
    """JSON and SQL parsers set their own hashes, which know nothing of the title."""
    container = _table("Consolidated Balance Sheets")
    for item in (*container.blocks, *container.block_groups):
        item.content_hash = "from-the-parser"
    hashes = ReconciliationService().build_metadata(container).hash_to_block_ids

    assert hashes["from-the-parser"] == ["h"]
    assert "from-the-parser" not in {h for h, ids in hashes.items() if "r" in ids or "t" in ids}
