"""Icon-only links, inline icons and image alt text in the Selectolax HTML parser."""

from app.models.blocks import BlockType
from app.modules.parsers.html_parser.html_to_blocks import HtmlToBlocksConverter
from app.modules.parsers.html_parser.selectolax_html_parser import SelectolaxHtmlParser
from app.modules.parsers.markdown.markdown_to_blocks import _split_raw_markdown_into_segments

PNG = "data:image/png;base64,iVBORw0KGgo="


def _convert(body: str):
    return HtmlToBlocksConverter().convert(f"<html><body>{body}</body></html>")


def _parse_like_indexing(body: str):
    """Clean, key and download images the way the selectolax parser does."""
    parser = SelectolaxHtmlParser()
    html, images = parser.extract_and_replace_images(f"<html><body>{body}</body></html>")
    caption_map = {image["new_alt_text"]: PNG for image in images}
    return HtmlToBlocksConverter().convert(html, caption_map=caption_map)


def _texts(container) -> list[str]:
    return [b.data.strip() for b in container.blocks if b.type == BlockType.TEXT and b.data]


class TestIconOnlyLinks:
    def test_title_labels_a_flag_link_in_a_list_item(self):
        container = _convert(
            '<ul><li><a href="https://example.com/places/North_Region" title="North Region">'
            '<img src="https://cdn.example.com/n.png" width="20" height="14"></a> Alice Park</li></ul>'
        )
        assert _texts(container) == ["[North Region] Alice Park"]

    def test_alt_labels_the_link_when_there_is_no_title(self):
        container = _convert(
            '<ul><li><a href="https://example.com/x"><img src="https://cdn.example.com/n.png" '
            'alt="Harbour Office"></a> opened in 1901</li></ul>'
        )
        assert _texts(container) == ["[Harbour Office] opened in 1901"]

    def test_target_path_labels_the_link_when_title_and_alt_are_empty(self):
        container = _convert(
            '<ul><li><a href="https://example.com/places/South_Region.html">'
            '<img src="https://cdn.example.com/s.png" alt=""></a> Bob Lee</li></ul>'
        )
        assert _texts(container) == ["[South Region] Bob Lee"]

    def test_label_and_name_share_a_table_cell_line(self):
        container = _convert(
            "<table><tr><th>Team</th><th>Lead</th></tr><tr>"
            '<td><a href="https://example.com/teams/Blue" title="Blue team">'
            '<img src="https://cdn.example.com/b.png"></a> Blue</td><td>Dana</td></tr></table>'
        )
        row = next(b for b in container.blocks if b.type == BlockType.TABLE_ROW)
        assert row.data["row_natural_language_text"] == "Team: [Blue team] Blue, Lead: Dana"

    def test_link_after_the_text_is_not_labelled(self):
        container = _convert(
            '<ul><li>Carol <a href="https://example.com/people/Carol" title="Profile">'
            '<img src="https://cdn.example.com/p.png"></a></li></ul>'
        )
        assert all("Profile" not in text for text in _texts(container))

    def test_generic_controls_are_not_labelled(self):
        container = _convert(
            '<ul><li><a href="#top"><img src="https://cdn.example.com/up.png" alt=""></a> Carol</li>'
            '<li><a href="https://example.com/doc/Plan" title="Edit">'
            '<img src="https://cdn.example.com/pen.png" alt=""></a> Dana</li>'
            '<li><a href="https://example.com/doc?action=history">'
            '<img src="https://cdn.example.com/clock.png" alt=""></a> Erin</li></ul>'
        )
        assert _texts(container) == ["Carol", "Dana", "Erin"]


class TestInlineIcons:
    def test_icon_inside_a_paragraph_does_not_split_it(self):
        container = _convert(
            '<p>Status <img src="https://cdn.example.com/ok.png" alt="OK" width="16" '
            'height="16"> for the build.</p>'
        )
        assert _texts(container) == ["Status OK for the build."]
        assert not [b for b in container.blocks if b.type == BlockType.IMAGE]

    def test_icon_without_alt_is_dropped_from_the_text(self):
        container = _convert(
            '<p>Signed <img src="https://cdn.example.com/dot.png" width="1" height="1">today</p>'
        )
        assert _texts(container) == ["Signed today"]

    def test_full_size_image_in_a_paragraph_is_still_an_image(self):
        container = _convert(f'<p>Before <img src="{PNG}" alt="Diagram" width="640"> after</p>')
        assert [b for b in container.blocks if b.type == BlockType.IMAGE]


class TestImageAltText:
    def test_alt_text_survives_the_download_key(self):
        container = _parse_like_indexing(
            '<p><img src="https://cdn.example.com/chart.png" alt="Quarterly revenue chart"></p>'
        )
        image = next(b for b in container.blocks if b.type == BlockType.IMAGE)
        assert image.media_metadata.alt_text == "Quarterly revenue chart"
        assert image.image_metadata.captions == ["Quarterly revenue chart"]

    def test_extract_and_replace_keeps_the_author_alt(self):
        html, images = SelectolaxHtmlParser().extract_and_replace_images(
            '<img src="https://cdn.example.com/a.png" alt="Floor plan">'
        )
        assert images[0]["new_alt_text"] == "Image_1"
        assert 'data-ph-alt="Floor plan"' in html

    def test_figcaption_is_the_caption_and_alt_is_kept(self):
        container = _parse_like_indexing(
            '<figure><img src="https://cdn.example.com/chart.png" alt="Bar chart">'
            "<figcaption>Revenue by quarter</figcaption></figure>"
        )
        image = next(b for b in container.blocks if b.type == BlockType.IMAGE)
        assert image.image_metadata.captions == ["Revenue by quarter"]
        assert image.media_metadata.alt_text == "Bar chart"

    def test_image_without_alt_gets_no_key_as_caption(self):
        container = _parse_like_indexing('<p><img src="https://cdn.example.com/a.png"></p>')
        image = next(b for b in container.blocks if b.type == BlockType.IMAGE)
        assert image.image_metadata.captions == []
        assert image.media_metadata is None


def test_linked_image_leaves_no_bracket_fragments():
    segments = _split_raw_markdown_into_segments(
        f"Carol [![Profile]({PNG})](https://example.com/people/Carol) joined"
    )
    assert [(s.kind, s.text.strip() or s.alt_text) for s in segments] == [
        ("text", "Carol"),
        ("image", "Profile"),
        ("text", "joined"),
    ]
