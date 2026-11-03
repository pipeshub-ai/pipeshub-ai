"""Citation helpers that build or strip text-fragment URLs."""

from unittest.mock import patch

from app.utils.citations import (
    _enrich_metadata_from_fragment,
    _fragment_children_format,
    _page_of,
)
from app.utils.text_fragments import SourceFormat


class TestPageOf:
    def test_plain_url_unchanged(self) -> None:
        assert _page_of("https://x.io/a") == "https://x.io/a"

    def test_strips_bare_directive(self) -> None:
        assert _page_of("https://x.io/a#:~:text=foo%2Dbar") == "https://x.io/a"

    def test_keeps_anchor_before_directive(self) -> None:
        assert _page_of("https://x.io/a#m1:~:text=foo") == "https://x.io/a#m1"


class TestFragmentChildrenFormat:
    def test_reads_format_of_first_text_child(self) -> None:
        blocks = [
            {"parent_block_index": None, "data": ""},
            {"parent_block_index": 0, "data": "<p>hi</p>", "format": "html"},
        ]
        assert _fragment_children_format(blocks, 0) is SourceFormat.HTML

    def test_skips_children_that_contribute_no_fragment_text(self) -> None:
        blocks = [
            {"parent_block_index": 0, "index": 4, "data": "<p>later</p>", "format": "html"},
            {"parent_block_index": 0, "index": 3, "data": "**md**", "format": "markdown"},
            {"parent_block_index": 0, "index": 1, "data": "   ", "format": "txt"},
            {"parent_block_index": 0, "index": 2, "type": "image", "data": "data:image/png;base64,x", "format": "txt"},
        ]
        assert _fragment_children_format(blocks, 0) is SourceFormat.MARKDOWN

    def test_none_without_text_children(self) -> None:
        assert _fragment_children_format([{"parent_block_index": 1, "data": "x"}], 0) is None

    def test_none_for_unknown_format(self) -> None:
        blocks = [{"parent_block_index": 0, "data": "x", "format": "bin"}]
        assert _fragment_children_format(blocks, 0) is None


class TestEnrichMetadataFromFragment:
    record = {"weburl": "https://x.io/doc", "origin": "CONNECTOR", "record_type": "FILE"}

    def test_builds_web_url_with_the_given_format(self) -> None:
        metadata: dict = {}
        with patch(
            "app.utils.citations.generate_text_fragment_url", return_value="https://x.io/doc#:~:text=a"
        ) as gen:
            _enrich_metadata_from_fragment(
                metadata, self.record, "shown", "fragment words here", SourceFormat.HTML
            )

        gen.assert_called_once_with("https://x.io/doc", "fragment words here", SourceFormat.HTML)
        assert metadata == {"blockText": "shown", "webUrl": "https://x.io/doc#:~:text=a"}

    def test_noop_when_block_text_already_set(self) -> None:
        metadata = {"blockText": "existing"}
        _enrich_metadata_from_fragment(metadata, self.record, "shown", "fragment words here")
        assert metadata == {"blockText": "existing"}

    def test_skips_url_for_uploads_mail_and_hidden_weburl(self) -> None:
        for record, metadata in (
            ({**self.record, "origin": "UPLOAD"}, {}),
            ({**self.record, "record_type": "MAIL"}, {}),
            (self.record, {"hideWeburl": True}),
        ):
            _enrich_metadata_from_fragment(metadata, record, "shown", "fragment words here")
            assert "webUrl" not in metadata
            assert metadata["blockText"] == "shown"

    def test_real_generator_produces_a_directive(self) -> None:
        metadata: dict = {}
        _enrich_metadata_from_fragment(metadata, self.record, "shown", "**Bold** opening words")
        assert metadata["webUrl"] == "https://x.io/doc#:~:text=Bold%20opening%20words"
