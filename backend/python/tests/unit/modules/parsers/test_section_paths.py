"""Heading levels and section paths stored at parse time."""

from app.models.blocks import (
    Block,
    BlockGroup,
    BlockGroupChildren,
    BlocksContainer,
    BlockSubType,
    BlockType,
    GroupType,
)
from app.modules.parsers.html_parser.html_to_blocks import HtmlToBlocksConverter
from app.modules.parsers.markdown.markdown_to_blocks import MarkdownToBlocksConverter
from app.modules.parsers.section_paths import (
    MAX_SECTION_PATH_CHARS,
    SectionOpener,
    assign_section_paths,
    block_section_path,
    clean_heading_text,
    format_section_path,
    heading_level,
)

HTML = """<html><head><title>Operations - Example Site</title></head><body>
<h1>Operations Guide</h1>
<p>Intro paragraph.</p>
<h2>Shipping</h2>
<ul><li>Pack the order</li></ul>
<h3>Rates</h3>
<table><tr><th>Zone</th><th>Price</th></tr><tr><td>A</td><td>5</td></tr></table>
<h2>Returns</h2>
<details><summary>Exceptions</summary><p>Perishables cannot be returned.</p></details>
</body></html>"""


def _by_text(container) -> dict[str, Block]:
    found = {}
    for block in container.blocks:
        key = block.data if isinstance(block.data, str) else block.data.get("row_natural_language_text")
        found[key] = block
    return found


class TestHtmlSections:
    def test_levelled_headings_nest_and_blocks_carry_their_path(self):
        blocks = _by_text(HtmlToBlocksConverter().convert(HTML))

        assert block_section_path(blocks["Pack the order"]) == "Operations Guide › Shipping"
        assert block_section_path(blocks["Zone: A, Price: 5"]) == "Operations Guide › Shipping › Rates"
        assert block_section_path(blocks["Perishables cannot be returned."]) == (
            "Operations Guide › Returns"
        )

    def test_heading_blocks_store_their_level_and_not_their_own_text_as_path(self):
        blocks = _by_text(HtmlToBlocksConverter().convert(HTML))

        assert heading_level(blocks["Shipping"]) == 2
        assert heading_level(blocks["Rates"]) == 3
        assert block_section_path(blocks["Shipping"]) == "Operations Guide"
        assert block_section_path(blocks["Returns"]) == "Operations Guide"

    def test_unlevelled_heading_takes_a_path_but_opens_no_level(self):
        blocks = _by_text(HtmlToBlocksConverter().convert(HTML))

        summary = blocks["Exceptions"]
        assert summary.sub_type == BlockSubType.HEADING
        assert heading_level(summary) is None
        assert block_section_path(summary) == "Operations Guide › Returns"

    def test_document_title_and_merged_heading_block(self):
        container = HtmlToBlocksConverter().convert(HTML)
        title, merged = container.blocks[0], container.blocks[1]

        assert title.data == "Operations - Example Site"
        assert block_section_path(title) == ""
        assert merged.data == "# Operations Guide\nIntro paragraph."
        assert block_section_path(merged) == ""

    def test_groups_take_the_path_of_their_first_block(self):
        container = HtmlToBlocksConverter().convert(HTML)
        table = next(g for g in container.block_groups if g.type == GroupType.TABLE)
        assert block_section_path(table) == "Operations Guide › Shipping › Rates"

    def test_document_without_headings_has_no_paths(self):
        container = HtmlToBlocksConverter().convert("<p>One.</p><ul><li>Two</li></ul>")
        assert all(block_section_path(b) == "" for b in container.blocks)
        assert all(b.citation_metadata is None for b in container.blocks)


class TestMarkdownSections:
    def test_paths_and_levels_keep_the_page_number(self):
        md = "# Handbook\n\nWelcome.\n\n## Leave [policy](https://example.com/p)\n\n- Annual\n\n## Travel\n\nBook early.\n"
        container = MarkdownToBlocksConverter().convert(md, page_number=4)
        blocks = _by_text(container)

        heading = blocks["Leave [policy](https://example.com/p)"]
        assert heading_level(heading) == 2
        assert block_section_path(heading) == "Handbook"
        assert block_section_path(blocks["Annual"]) == "Handbook › Leave policy"
        assert block_section_path(blocks["## Travel\nBook early."]) == "Handbook"
        assert all(b.citation_metadata.page_number == 4 for b in container.blocks)


class TestPathHelpers:
    def test_heading_text_is_cleaned(self):
        assert clean_heading_text("## **Bold** [Link](https://x.example/a) `code`  ##") == (
            "Bold Link code"
        )
        assert len(clean_heading_text("x" * 500)) == 80

    def test_long_paths_drop_outer_headings_first(self):
        parts = [f"Section {i} " + "y" * 40 for i in range(6)]
        path = format_section_path(parts)
        assert len(path) <= MAX_SECTION_PATH_CHARS
        assert path.startswith("…")
        assert path.endswith(parts[-1])

    def test_existing_paths_are_kept(self):
        from app.models.blocks import CitationMetadata

        kept = Block(index=1, type=BlockType.TEXT, data="b",
                     citation_metadata=CitationMetadata(section_title="$.items"))
        container = BlocksContainer(blocks=[
            Block(index=0, type=BlockType.TEXT, sub_type=BlockSubType.HEADING, data="Top", name="H1"),
            kept,
        ], block_groups=[BlockGroup(index=0, type=GroupType.LIST,
                                    children=BlockGroupChildren.from_indices(block_indices=[1]))])
        assign_section_paths(container, {0: SectionOpener(1, "Top")})
        assert kept.citation_metadata.section_title == "$.items"
