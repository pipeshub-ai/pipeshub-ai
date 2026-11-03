"""Content facts (UNIT-FP-05..06): what classification reads is digested apart from structure."""

from app.models.blocks import (
    Block,
    BlockGroup,
    BlocksContainer,
    BlockType,
    DataFormat,
    GroupType,
)
from app.modules.pipeline.fingerprint import block_text, content_facts, content_revision


def _text(index: int, text: str) -> Block:
    return Block(index=index, type=BlockType.TEXT, format=DataFormat.TXT, data=text)


def _row(index: int, text: str) -> Block:
    return Block(index=index, type=BlockType.TABLE_ROW, format=DataFormat.JSON, data={"row_natural_language_text": text})


def _image(index: int, uri: str) -> Block:
    return Block(index=index, type=BlockType.IMAGE, format=DataFormat.BASE64, data={"uri": uri})


def _container(*blocks: Block, groups: tuple[BlockGroup, ...] = ()) -> BlocksContainer:
    return BlocksContainer(blocks=list(blocks), block_groups=list(groups))


def test_the_same_content_has_the_same_facts() -> None:
    assert content_facts(_container(_text(0, "alpha"), _text(1, "beta"))) == content_facts(
        _container(_text(0, "alpha"), _text(1, "beta"))
    )


def test_changing_text_changes_both_digests() -> None:
    before = content_facts(_container(_text(0, "alpha")))
    after = content_facts(_container(_text(0, "alpha!")))
    assert before.text_digest != after.text_digest
    assert before.blocks_digest != after.blocks_digest


def test_block_order_matters() -> None:
    assert content_facts(_container(_text(0, "a"), _text(1, "b"))).text_digest != content_facts(
        _container(_text(0, "b"), _text(1, "a"))
    ).text_digest


def test_text_boundaries_are_part_of_the_digest() -> None:
    assert content_facts(_container(_text(0, "ab"), _text(1, "c"))).text_digest != content_facts(
        _container(_text(0, "a"), _text(1, "bc"))
    ).text_digest


def test_a_structure_only_change_leaves_the_text_digest_alone() -> None:
    blocks = (_text(0, "alpha"),)
    plain = content_facts(_container(*blocks, groups=(BlockGroup(index=0, type=GroupType.TEXT_SECTION, data="s1"),)))
    moved = content_facts(_container(*blocks, groups=(BlockGroup(index=0, type=GroupType.TEXT_SECTION, data="s2"),)))
    assert plain.text_digest == moved.text_digest
    assert plain.blocks_digest != moved.blocks_digest


def test_an_image_change_is_a_change_to_what_classification_reads() -> None:
    assert content_facts(_container(_image(0, "data:image/png;base64,AAA"))).text_digest != content_facts(
        _container(_image(0, "data:image/png;base64,BBB"))
    ).text_digest


def test_flags_and_length() -> None:
    facts = content_facts(_container(_text(0, "abc"), _row(1, "de"), _image(2, "u")))
    assert (facts.text_chars, facts.has_tables, facts.has_images) == (5, True, True)
    sheet = content_facts(_container(_text(0, "x"), groups=(BlockGroup(index=0, type=GroupType.SHEET),)))
    assert sheet.has_tables and not sheet.has_images


def test_row_and_code_text_come_from_their_payloads() -> None:
    assert block_text(_row(0, "row text")) == "row text"
    code = Block(index=0, type=BlockType.CODE, format=DataFormat.CODE, data={"text": "def f(): ..."})
    assert block_text(code) == "def f(): ..."
    assert block_text(Block(index=0, type=BlockType.TEXT, format=DataFormat.TXT, data=None)) == ""


def test_content_revision_is_a_stable_prefix_of_sha256() -> None:
    assert content_revision(b"hello") == content_revision(b"hello")
    assert content_revision(b"hello") != content_revision(b"hello!")
    assert len(content_revision(b"hello")) == 16
