"""Heading levels and the headings each block sits under, stored at parse time."""

import pytest

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
    block_heading_path,
    clean_heading_text,
    format_section_path,
    heading_level,
)

HTML = """<html><head><title>Kiln Notes - Pottery Club</title></head><body>
<h1>Kiln Notes</h1>
<p>Read this before the first firing.</p>
<h2>Loading</h2>
<ul><li>Leave a finger gap between pots</li></ul>
<h3>Shelf heights</h3>
<table><tr><th>Shelf</th><th>Height</th></tr><tr><td>Top</td><td>12 cm</td></tr></table>
<h2>Cooling</h2>
<details><summary>Exceptions</summary><p>Thick platters stay shut overnight.</p></details>
</body></html>"""


@pytest.fixture
def section_paths_on(monkeypatch) -> None:
    monkeypatch.setenv("INDEXING_SECTION_PATHS", "true")


def _by_text(container) -> dict[str, Block]:
    found = {}
    for block in container.blocks:
        key = block.data if isinstance(block.data, str) else block.data.get("row_natural_language_text")
        found[key] = block
    return found


@pytest.mark.usefixtures("section_paths_on")
class TestHtmlSections:
    def test_blocks_carry_the_headings_they_sit_under(self) -> None:
        blocks = _by_text(HtmlToBlocksConverter().convert(HTML))

        assert block_heading_path(blocks["Leave a finger gap between pots"]) == ["Kiln Notes", "Loading"]
        assert block_heading_path(blocks["Shelf: Top, Height: 12 cm"]) == [
            "Kiln Notes", "Loading", "Shelf heights",
        ]
        assert block_heading_path(blocks["Thick platters stay shut overnight."]) == ["Kiln Notes", "Cooling"]

    def test_heading_blocks_store_their_level_and_not_their_own_text(self) -> None:
        blocks = _by_text(HtmlToBlocksConverter().convert(HTML))

        assert heading_level(blocks["Loading"]) == 2
        assert heading_level(blocks["Shelf heights"]) == 3
        assert block_heading_path(blocks["Loading"]) == ["Kiln Notes"]
        assert block_heading_path(blocks["Cooling"]) == ["Kiln Notes"]

    def test_a_heading_without_a_level_takes_a_path_but_nests_nothing(self) -> None:
        blocks = _by_text(HtmlToBlocksConverter().convert(HTML))

        summary = blocks["Exceptions"]
        assert summary.sub_type == BlockSubType.HEADING
        assert heading_level(summary) is None
        assert block_heading_path(summary) == ["Kiln Notes", "Cooling"]

    def test_title_block_and_a_heading_merged_into_its_paragraph(self) -> None:
        container = HtmlToBlocksConverter().convert(HTML)
        title, merged = container.blocks[0], container.blocks[1]

        assert title.data == "Kiln Notes - Pottery Club"
        assert heading_level(title) is None
        assert block_heading_path(title) == []
        assert merged.data == "# Kiln Notes\nRead this before the first firing."
        assert heading_level(merged) == 1
        assert block_heading_path(merged) == []

    def test_groups_take_the_path_of_their_first_block(self) -> None:
        container = HtmlToBlocksConverter().convert(HTML)
        table = next(g for g in container.block_groups if g.type == GroupType.TABLE)
        assert block_heading_path(table) == ["Kiln Notes", "Loading", "Shelf heights"]

    def test_document_without_headings_stores_nothing(self) -> None:
        container = HtmlToBlocksConverter().convert("<p>One.</p><ul><li>Two</li></ul>")
        assert all(b.citation_metadata is None for b in container.blocks)


class TestSwitchedOff:
    def test_html_and_markdown_store_no_levels_or_paths(self, monkeypatch) -> None:
        monkeypatch.delenv("INDEXING_SECTION_PATHS", raising=False)
        html = HtmlToBlocksConverter().convert(HTML)
        markdown = MarkdownToBlocksConverter().convert("# Top\n\nIntro.\n\n## Part\n\n- item\n")

        for block in [*html.blocks, *markdown.blocks]:
            assert block.name is None
            assert block.citation_metadata is None or block.citation_metadata.heading_path is None


@pytest.mark.usefixtures("section_paths_on")
class TestMarkdownSections:
    def test_paths_and_levels_keep_the_page_number(self) -> None:
        md = (
            "# Field Guide\n\nStart here.\n\n## Moss [types](https://example.com/m)\n\n"
            "- Cushion\n\n## Lichen\n\nGrows slowly.\n"
        )
        container = MarkdownToBlocksConverter().convert(md, page_number=4)
        blocks = _by_text(container)

        heading = blocks["Moss [types](https://example.com/m)"]
        assert heading_level(heading) == 2
        assert block_heading_path(heading) == ["Field Guide"]
        assert block_heading_path(blocks["Cushion"]) == ["Field Guide", "Moss types"]
        assert block_heading_path(blocks["## Lichen\nGrows slowly."]) == ["Field Guide"]
        assert heading_level(blocks["## Lichen\nGrows slowly."]) == 2
        assert all(b.citation_metadata.page_number == 4 for b in container.blocks)


class TestPathHelpers:
    def test_heading_text_is_cleaned(self) -> None:
        assert clean_heading_text("## **Bold** [Link](https://x.example/a) `code`  ##") == (
            "Bold Link code"
        )
        assert len(clean_heading_text("x" * 500)) == 80

    def test_long_paths_drop_outer_headings_first(self) -> None:
        parts = [f"Section {i} " + "y" * 40 for i in range(6)]
        path = format_section_path(parts)
        assert len(path) <= MAX_SECTION_PATH_CHARS
        assert path.startswith("…")
        assert path.endswith(parts[-1])

    def test_stored_dicts_are_read_too(self) -> None:
        assert block_heading_path({"citation_metadata": {"heading_path": ["A", "", "B"]}}) == ["A", "B"]
        assert block_heading_path({"citation_metadata": {"section_title": "$.items"}}) == []
        assert block_heading_path({"data": "no citation"}) == []

    def test_a_list_group_takes_its_first_items_path(self) -> None:
        container = BlocksContainer(
            blocks=[
                Block(index=0, type=BlockType.TEXT, sub_type=BlockSubType.HEADING, data="Top"),
                Block(index=1, type=BlockType.TEXT, data="item", parent_index=0),
            ],
            block_groups=[BlockGroup(index=0, type=GroupType.LIST,
                                     children=BlockGroupChildren.from_indices(block_indices=[1]))],
        )
        assign_section_paths(container, {0: SectionOpener(1, "Top")})
        assert block_heading_path(container.block_groups[0]) == ["Top"]
        assert block_heading_path(container.blocks[0]) == []


class TestHeadingTextKeepsWhatIsNotMarkup:
    @pytest.mark.parametrize(("text", "label"), [
        ("Using get_user_id", "Using get_user_id"),
        ("Learning C#", "Learning C#"),
        ("Using _private_ names", "Using private names"),
        ("## Steps ##", "Steps"),
    ])
    def test_markdown_markup_goes_and_names_stay(self, text: str, label: str) -> None:
        assert clean_heading_text(text) == label

    def test_html_heading_text_is_taken_as_written(self) -> None:
        assert clean_heading_text("__init__ in C# *now*", markdown=False) == "__init__ in C# *now*"

    @pytest.mark.usefixtures("section_paths_on")
    def test_parsed_paths_keep_identifiers_and_trailing_hashes(self) -> None:
        html = HtmlToBlocksConverter().convert(
            "<h2>Hooks: __init__ in C#</h2><ul><li>first</li></ul>"
        )
        markdown = MarkdownToBlocksConverter().convert("## Using get_user_id\n\n- second\n")

        assert block_heading_path(_by_text(html)["first"]) == ["Hooks: __init__ in C#"]
        assert block_heading_path(_by_text(markdown)["second"]) == ["Using get_user_id"]
