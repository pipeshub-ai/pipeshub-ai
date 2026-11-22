"""Diffs and source files reach the markdown parser as fenced code, whole."""

import pytest

from app.models.blocks import BlockSubType, BlockType
from app.modules.parsers.markdown.markdown_to_blocks import MarkdownToBlocksConverter
from app.modules.parsers.text_splitting import MAX_TEXT_BLOCK_CHARS
from app.utils.markdown_code import MAX_CODE_FENCE_CHARS, fenced_code

# The shape that went wrong: indented removed lines read as list items, a context
# line of four spaces as an indented code block, "#" as a heading, "_" as emphasis.
DIFF = (
    "@@ -1,6 +1,6 @@\n"
    "-    total = compute(item)\n"
    "+    total = compute_all(items)\n"
    "     # keep the old __name__ for callers\n"
    " # Heading-looking comment\n"
    "-    return _private_value\n"
    "+    return __public_value__\n"
)


def _code_blocks(markdown: str) -> list[str]:
    container = MarkdownToBlocksConverter().convert(markdown)
    return [
        b.data for b in container.blocks
        if b.type == BlockType.TEXT and b.sub_type == BlockSubType.CODE
    ]


def test_the_cap_is_the_parsers_block_cap() -> None:
    # Kept apart so connectors needn't import the parser modules; they must agree.
    assert MAX_CODE_FENCE_CHARS == MAX_TEXT_BLOCK_CHARS


def test_a_diff_parses_as_one_code_block_with_every_line_kept() -> None:
    markdown = "[modified file]\n\nDiff:\n" + fenced_code(DIFF, "diff")
    container = MarkdownToBlocksConverter().convert(markdown)

    assert _code_blocks(markdown) == [DIFF.rstrip("\n")]
    # Nothing of the diff is left as list items, headings or other prose.
    prose = [b.data for b in container.blocks if b.sub_type != BlockSubType.CODE]
    assert prose == ["[modified file]", "Diff:"]


def test_bare_the_same_diff_is_read_as_markdown() -> None:
    # What connectors produced before: the removed lines become list items.
    container = MarkdownToBlocksConverter().convert("[modified file]\n\nDiff:\n" + DIFF)
    assert any(b.sub_type == BlockSubType.LIST_ITEM for b in container.blocks)


def test_the_fence_is_longer_than_any_backtick_run_inside() -> None:
    text = "+    fence = \"```\"\n+    longer = \"`````\""
    fenced = fenced_code(text, "diff")
    assert fenced.startswith("``````diff\n") and fenced.endswith("\n``````")
    assert _code_blocks(fenced) == [text]


def test_the_language_goes_on_the_fence() -> None:
    assert fenced_code("print(1)", "python") == "```python\nprint(1)\n```"
    assert fenced_code("{}", "") == "```\n{}\n```"


def test_a_long_diff_is_fenced_in_pieces_at_hunk_starts() -> None:
    hunk = "@@ -1 +1 @@\n" + "".join(f"-line {i}\n+line {i}!\n" for i in range(20))
    text = hunk * 6
    fenced = fenced_code(text, "diff", max_chars=len(hunk) * 2 + 10)
    blocks = _code_blocks(fenced)

    assert len(blocks) == 3
    assert all(block.startswith("@@ -1 +1 @@") for block in blocks)
    assert "\n".join(blocks) == text.rstrip("\n")


def test_a_hunk_longer_than_the_cap_is_cut_at_line_ends() -> None:
    text = "@@ -1 +1 @@\n" + "".join(f"+row {i:04d}\n" for i in range(200))
    blocks = _code_blocks(fenced_code(text, "diff", max_chars=500))

    assert all(len(block) <= 500 for block in blocks)
    assert "\n".join(blocks) == text.rstrip("\n")


def test_a_single_line_longer_than_the_cap_is_cut_where_it_must_be() -> None:
    blocks = _code_blocks(fenced_code("+" + "x" * 1200, "diff", max_chars=500))
    assert [len(b) for b in blocks] == [500, 500, 201]


@pytest.mark.parametrize("text", ["", "\n\n", "   \n"])
def test_nothing_to_fence_gives_nothing(text: str) -> None:
    assert fenced_code(text, "diff") == ""


@pytest.mark.parametrize("text", [
    "1234567890\n1234567890\nabcdefghij\n",
    "1234567890\n1234567890\n\n\nabcdefghij\n",
    "1234567890\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\nabcdefghij\n",
])
def test_blank_lines_at_a_split_are_kept(text: str) -> None:
    # Each block loses its trailing newlines to the parser and a block boundary
    # restores one, so blank lines at a cut must not end a piece.
    blocks = _code_blocks(fenced_code(text, "python", max_chars=25))
    assert len(blocks) > 1
    assert "\n".join(blocks) == text.rstrip("\n")


def test_a_long_text_is_cut_by_offset_with_every_character_kept() -> None:
    from app.utils.markdown_code import _cut_lines

    text = "".join(f"line {i}\n" for i in range(5000)) + "x" * 300
    pieces = _cut_lines(text, 1000)
    assert "".join(pieces) == text
    assert all(len(p) <= 1000 for p in pieces)
    assert all(p.endswith("\n") for p in pieces[:-2])


def test_a_trailing_context_line_of_spaces_is_kept() -> None:
    """An empty context line in a unified diff is a single space, so `strip()`
    reads it as blank and it was carried to a next piece that never came. The
    last line of the diff went missing with it."""
    text = "y" * 49 + "\n \n"

    blocks = _code_blocks(fenced_code(text, "diff", max_chars=50))

    assert "\n".join(blocks) == text.rstrip("\n")


def test_text_that_is_only_blank_is_still_nothing_to_fence() -> None:
    """The other side of that: a line of spaces on its own is not content
    somebody meant to keep, and must not become a block of three spaces."""
    assert fenced_code("   \n", "diff") == ""


def test_a_block_can_exceed_max_chars_by_the_blank_lines_carried_into_it() -> None:
    """Known, and bounded. Blank lines a piece had no room for are prepended to
    the next body, which can take it over the limit by the length of that run.

    Pinned rather than fixed: the limit mirrors the prose block size and is a
    target, not something downstream enforces, and the obvious fix — cutting
    the body again before fencing — breaks what the blank-line handling is for,
    because a chunk of only newlines becomes a block and the blocks no longer
    rebuild the text (`test_blank_lines_at_a_split_are_kept` catches it).
    Making it exact means reserving the carry before the cut is chosen.
    """
    text = "x\n\n" + "y" * 25

    blocks = _code_blocks(fenced_code(text, max_chars=25))

    assert "\n".join(blocks) == text.rstrip("\n")
    assert max(len(b) for b in blocks) == 26
