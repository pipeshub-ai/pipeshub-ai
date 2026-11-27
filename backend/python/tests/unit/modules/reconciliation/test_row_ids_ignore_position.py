"""A table row's id follows what the row says, not where it sits.

``row_number`` was part of a row's content hash, so inserting or deleting one row gave every row after it a new id, and the diff
re-embedded all of them. All data is synthetic.
"""

from __future__ import annotations

from app.models.blocks import Block, BlocksContainer, BlockType, DataFormat
from app.modules.pipeline.fingerprint import content_facts
from app.modules.reconciliation.block_identity import BlockIdentity


def _table(texts: list[str]) -> BlocksContainer:
    blocks = [
        Block(
            index=i, parent_index=0, type=BlockType.TABLE_ROW, format=DataFormat.JSON,
            data={"row_natural_language_text": text, "row_number": i + 2, "row_end_number": i + 2, "row_count": 1},
        )
        for i, text in enumerate(texts)
    ]
    container = BlocksContainer(blocks=blocks, block_groups=[])
    assert BlockIdentity.assign(container, "org", "vr")
    return container


def _ids(container: BlocksContainer) -> dict[str, str]:
    """Row text to id (texts are unique in these tables)."""
    return {b.data["row_natural_language_text"]: b.id for b in container.blocks}


ROWS = [f"Warehouse W-{i:02d} handled {1200 + i} pallets." for i in range(10)]


def test_a_row_inserted_at_the_top_leaves_every_other_rows_id_alone() -> None:
    # Kills: row_number in the hash, where an insert renumbers, and so re-embeds, every row after it.
    before = _ids(_table(ROWS))
    after = _ids(_table(["A new first row.", *ROWS]))
    assert {text: after[text] for text in ROWS} == before


def test_a_deleted_row_leaves_every_other_rows_id_alone() -> None:
    before = _ids(_table(ROWS))
    after = _ids(_table(ROWS[:3] + ROWS[4:]))
    assert all(after[text] == before[text] for text in after)


def test_equal_rows_still_get_their_own_ids() -> None:
    ids = [b.id for b in _table(["Same row.", "Same row.", "Other row."]).blocks]
    assert len(set(ids)) == 3


def test_a_row_whose_text_changes_gets_a_new_id() -> None:
    before, after = _ids(_table(ROWS)), _ids(_table([*ROWS[:4], "Warehouse W-04 handled 9999 pallets.", *ROWS[5:]]))
    assert "Warehouse W-04 handled 9999 pallets." not in before
    assert after["Warehouse W-04 handled 9999 pallets."] not in before.values()


def test_the_classification_fingerprint_does_not_read_a_rows_position() -> None:
    def unhashed(first_row: int) -> BlocksContainer:
        return BlocksContainer(blocks=[
            Block(
                index=i, parent_index=0, type=BlockType.TABLE_ROW, format=DataFormat.JSON,
                data={"row_natural_language_text": text, "row_number": first_row + i, "row_end_number": first_row + i},
            )
            for i, text in enumerate(ROWS)
        ], block_groups=[])

    assert content_facts(unhashed(2)) == content_facts(unhashed(102))
