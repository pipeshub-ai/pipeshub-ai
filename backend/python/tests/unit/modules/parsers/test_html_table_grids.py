"""HTML tables parsed as grids: one row per ``<tr>``, headers only where the
source marks columns, nothing dropped.

Golden fixtures are tables cut from Wikipedia pages (CC BY-SA 4.0) that the
FRAMES benchmark asks about; each pins a defect the old normalizer had. The
property tests hold for any table.
"""

from __future__ import annotations

import random
import re
from pathlib import Path

import pytest

from app.models.blocks import BlockType
from app.modules.parsers.html_parser.html_to_blocks import HtmlToBlocksConverter

_FIXTURES = Path(__file__).parent / "fixtures" / "html_tables"
# A link target may itself hold one level of parentheses: `Life_of_Pi_(film)`.
_LINK = re.compile(r"\[([^\]]*)\]\((?:[^()]|\([^()]*\))*\)")


def _plain(text: str) -> str:
    """Row text without link targets or emphasis, line breaks shown as " / "."""
    return _LINK.sub(r"\1", text).replace("*", "").replace("\n", " / ").replace("\xa0", " ")


def _parse(html: str) -> tuple[list[str], list[str], list[str]]:
    """(column names, captions, plain row texts) of the first table."""
    container = HtmlToBlocksConverter().convert(html)
    table = next(g for g in container.block_groups if g.type.value == "table")
    rows = [
        _plain(block.data["row_natural_language_text"])
        for block in container.blocks
        if block.type == BlockType.TABLE_ROW
    ]
    metadata = table.table_metadata
    return (metadata.column_names or []), metadata.captions, rows


def _fixture(name: str) -> tuple[list[str], list[str], list[str]]:
    return _parse((_FIXTURES / f"{name}.html").read_text(encoding="utf-8"))


class TestRealTables:
    def test_a_rowspan_keeps_each_row_its_own_values(self) -> None:
        """Zac Efron's MTV Movie Awards span 11 rows. Folding them into one
        row kept a single year, so his 2008 win read as 2009."""
        _columns, _captions, rows = _fixture("zac_efron_awards")

        mtv = [row for row in rows if row.startswith("Award: MTV Movie Awards")]

        assert len(mtv) == 11
        assert any(
            "Year of ceremony: 2008" in row and "Best Breakthrough Performance" in row
            and "Result: Won" in row
            for row in mtv
        )

    def test_row_label_cells_do_not_become_column_headers(self) -> None:
        """Each Best Director row starts with a `<th scope="row">` year. Read
        as a header, the first data row labelled every column ("Year 2010
        (83rd): 2012 (85th)")."""
        columns, _captions, rows = _fixture("best_director_2010s")

        assert columns == ["Year", "Director(s)", "Film", "Ref."]
        assert "Year: 2012 / (85th), Director(s): Ang Lee, Film: Life of Pi" in rows
        assert not any("Tom Hooper" in row for row in rows if "2012" in row)

    def test_a_year_spanning_from_the_header_region_stays_in_its_column(self) -> None:
        """The first year's rowspan starts in the rows once taken for the
        header; its expansion restarted in the body and shifted the columns."""
        _columns, _captions, rows = _fixture("best_director_2010s")

        assert "Year: 2010 / (83rd), Director(s): Darren Aronofsky, Film: Black Swan" in rows

    def test_a_table_of_row_labels_is_not_swallowed_into_its_header(self) -> None:
        """Every Lawson demographics row starts with a `<th>`; all of them
        were taken for the header and the table had no rows."""
        columns, captions, rows = _fixture("lawson_demographics")

        assert captions == ["Demographics of student body in fall 2020"]
        assert columns == ["", "Full and Part Time Students", "U.S. Census"]
        assert "Black/African American, Full and Part Time Students: 80%, U.S. Census: 13.4%" in rows
        assert len(rows) == 9

    def test_an_infobox_title_is_its_caption_and_rows_read_label_value(self) -> None:
        columns, captions, rows = _fixture("lawson_infobox")

        assert columns == []
        assert captions == ["Lawson State Community College"]
        assert any(row.startswith("Established: 1949") for row in rows)
        assert not any(" | " in row and "Established" in row and "Type" in row for row in rows)

    def test_a_table_with_empty_label_cells_keeps_its_rows(self) -> None:
        """Poland's ministers table puts an empty `<th>` (a party colour) at
        the start of each row; the section came out as a bare heading."""
        _columns, _captions, rows = _fixture("poland_ministers")

        assert len(rows) == 3
        assert any(
            "Name: Andrzej Halicki" in row and "22 September 2014" in row for row in rows
        )
        assert not any(": ," in row or row.startswith(": ") for row in rows)

    def test_episode_rows_with_a_row_label_number_are_kept(self) -> None:
        _columns, _captions, rows = _fixture("hot_ones_season")

        assert len(rows) == 13
        assert any("Shane Gillis" in row and "June 13, 2024" in row for row in rows)


def _random_table(rng: random.Random) -> tuple[str, list[list[str | None]], int]:
    """A random table with a one-row header, row-label `<th>`s and spans.

    Returns the HTML, the expected grid (a span's text in its first column on
    every row it covers, "" in the columns its colspan adds) and the data row
    count.
    """
    width = rng.randint(2, 5)
    height = rng.randint(1, 8)
    header = "".join(f"<th>H{c}</th>" for c in range(width))
    grid: list[list[str | None]] = [[None] * width for _ in range(height)]
    rows_html: list[str] = []
    for r in range(height):
        cells: list[str] = []
        c = 0
        while c < width:
            if grid[r][c] is not None:
                c += 1
                continue
            text = f"v{r}_{c}"
            rowspan = min(rng.choice([1, 1, 1, 2, 3]), height - r)
            colspan = 1
            while colspan < 2 and c + colspan < width and grid[r][c + colspan] is None and rng.random() < 0.25:
                colspan += 1
            if any(grid[rr][cc] is not None for rr in range(r, r + rowspan) for cc in range(c, c + colspan)):
                rowspan = 1
            for rr in range(r, r + rowspan):
                grid[rr][c] = text
                for cc in range(c + 1, c + colspan):
                    grid[rr][cc] = ""
            tag = "th" if c == 0 and rng.random() < 0.5 else "td"
            scope = ' scope="row"' if tag == "th" else ""
            span = (f' rowspan="{rowspan}"' if rowspan > 1 else "") + (f' colspan="{colspan}"' if colspan > 1 else "")
            cells.append(f"<{tag}{scope}{span}>{text}</{tag}>")
            c += colspan
        rows_html.append("<tr>" + "".join(cells) + "</tr>")
    html = "<table><tr>" + header + "</tr>" + "".join(rows_html) + "</table>"
    return html, grid, height


class TestAnyTable:
    @pytest.mark.parametrize("seed", range(150))
    def test_one_row_per_tr_with_every_spanned_value_in_place(self, seed: int) -> None:
        rng = random.Random(seed)
        html, grid, height = _random_table(rng)

        container = HtmlToBlocksConverter().convert(html)
        rows = [b.data["cells"] for b in container.blocks if b.type == BlockType.TABLE_ROW]
        table = next(g for g in container.block_groups if g.type.value == "table")

        assert len(rows) == height
        assert table.table_metadata.column_names == [f"H{c}" for c in range(len(grid[0]))]
        for expected, actual in zip(grid, rows, strict=True):
            assert actual == expected

    @pytest.mark.parametrize("seed", range(50))
    def test_no_row_text_carries_an_empty_pair(self, seed: int) -> None:
        rng = random.Random(1_000 + seed)
        width = rng.randint(2, 5)
        body = "".join(
            "<tr>" + "".join(
                f"<td>{'x' if rng.random() < 0.6 else ''}</td>" for _ in range(width)
            ) + "</tr>"
            for _ in range(rng.randint(1, 5))
        )
        html = "<table><tr>" + "".join(f"<th>H{c}</th>" for c in range(width)) + "</tr>" + body + "</table>"

        container = HtmlToBlocksConverter().convert(html)

        for block in container.blocks:
            if block.type == BlockType.TABLE_ROW:
                text = block.data["row_natural_language_text"]
                assert ": ," not in text
                assert not text.rstrip().endswith(":")

    def test_a_cell_spanning_rows_and_columns_repeats_only_where_it_starts(self) -> None:
        _columns, _captions, rows = _parse(
            "<table><tr><th>A</th><th>B</th><th>C</th></tr>"
            "<tr><td colspan=2 rowspan=2>Shared</td><td>X</td></tr><tr><td>Y</td></tr></table>",
        )

        assert rows == ["A: Shared, C: X", "A: Shared, C: Y"]

    def test_equal_neighbours_under_one_wide_header_are_both_kept(self) -> None:
        _columns, _captions, rows = _parse(
            "<table><tr><th colspan=2>Score</th><th>Name</th></tr>"
            "<tr><td>5</td><td>5</td><td>Ann</td></tr></table>",
        )

        assert rows == ["Score: 5 | 5, Name: Ann"]

    def test_a_leading_full_width_td_is_the_title_and_keeps_the_header_below_it(self) -> None:
        columns, captions, rows = _parse(
            "<table><tr><td colspan=3>Title</td></tr><tr><th>A</th><th>B</th><th>C</th></tr>"
            "<tr><td>1</td><td>2</td><td>3</td></tr></table>",
        )

        assert captions == ["Title"]
        assert columns == ["A", "B", "C"]
        assert rows == ["A: 1, B: 2, C: 3"]

    def test_a_title_row_is_kept_beside_a_caption(self) -> None:
        _columns, captions, rows = _parse(
            "<table><caption>Awards</caption><tr><th colspan=2>2010s</th></tr>"
            "<tr><th>Year</th><th>Film</th></tr><tr><td>2010</td><td>X</td></tr></table>",
        )

        assert captions == ["Awards", "2010s"]
        assert rows == ["Year: 2010, Film: X"]

    def test_a_nested_table_keeps_its_title_row(self) -> None:
        _columns, _captions, rows = _parse(
            "<table><tr><th>Club</th><th>History</th></tr><tr><td>FC</td><td>"
            "<table><tr><td colspan=2>Former names</td></tr><tr><td>1900</td><td>Old FC</td></tr></table>"
            "</td></tr></table>",
        )

        assert rows[0].startswith("Club: FC, History: Former names /")
        assert "| 1900 | Old FC |" in rows[0]

    def test_a_lone_full_width_row_stays_a_row(self) -> None:
        _columns, captions, rows = _parse("<table><tr><td colspan=2>Only row</td></tr></table>")

        assert captions == []
        assert rows == ["Only row"]

    def test_a_labelled_row_with_an_image_keeps_its_label_and_no_column_numbers(self) -> None:
        image = '<img src="data:image/png;base64,iVBORw0KGgo=" alt="photo">'
        container = HtmlToBlocksConverter().convert(
            "<table><tr><th scope=row>Born</th><td>1915</td><td>x</td></tr>"
            f"<tr><th scope=row>Photo</th><td>{image}</td><td>caption text</td></tr></table>",
        )

        fragments = [b.data for b in container.blocks if b.type == BlockType.TEXT]
        assert fragments == ["Photo", "caption text"]

    def test_a_table_of_only_header_cells_still_yields_rows(self) -> None:
        _columns, _captions, rows = _parse(
            "<table><tr><th>A</th><th>B</th></tr><tr><th>C</th><th>D</th></tr></table>",
        )

        assert rows

    def test_a_mixed_th_td_leading_row_is_data(self) -> None:
        columns, _captions, rows = _parse(
            "<table><tr><th>Born</th><td>1915</td></tr><tr><th>Died</th><td>2011</td></tr></table>",
        )

        assert columns == []
        assert rows == ["Born: 1915", "Died: 2011"]
