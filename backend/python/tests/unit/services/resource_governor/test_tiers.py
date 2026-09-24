import pytest

from app.services.resource_governor.models import ParseTier, Pool
from app.services.resource_governor.tiers import (
    XL_HEAVY_BYTES,
    classify,
    gate_pool,
    parse_cost,
)


class TestClassify:
    @pytest.mark.parametrize("ext", ["pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx", "png", "jpg", "PDF"])
    def test_heavy_extensions(self, ext) -> None:
        assert classify(ext, None) is ParseTier.HEAVY

    @pytest.mark.parametrize("ext", ["txt", "md", "html", "csv", "json", "yaml", "py", "sql_table", "MD"])
    def test_light_extensions(self, ext) -> None:
        assert classify(ext, None) is ParseTier.LIGHT

    def test_mime_used_when_extension_unknown(self) -> None:
        assert classify(None, "application/pdf") is ParseTier.HEAVY
        assert classify(None, "text/markdown") is ParseTier.LIGHT
        # An unrecognized (not merely missing) extension must still fall
        # through to the mime lookup rather than being treated as authoritative.
        assert classify("xyz-unknown", "application/pdf") is ParseTier.HEAVY
        assert classify("xyz-unknown", "text/markdown") is ParseTier.LIGHT

    @pytest.mark.parametrize(
        ("mime", "handler"),
        [
            ("text/gmail_content", "Processor.process_gmail_message"),
            ("text/mdx", "Processor.process_mdx_document"),
            ("application/blocks", "Processor.process_blocks"),
        ],
    )
    def test_connector_mime_types_that_reach_a_text_parser_are_light(
        self, mime, handler
    ) -> None:
        """These carry no file extension, so the mime lookup is the only
        thing standing between a high-volume connector sync and the heavy
        tier. The tier picks the index pool a record holds for its whole
        lifetime, so misclassifying one means a Gmail sync draws entirely on
        the small heavy budget — the head-of-line blocking the split exists
        to prevent."""
        assert classify(None, mime) is ParseTier.LIGHT, handler

    @pytest.mark.parametrize("mime", ["application/xml", "notion/text", "image/heic"])
    def test_unproven_mime_types_stay_heavy(self, mime) -> None:
        """Heavy is the safe default and stays the default: none of these has
        a demonstrated light parse path, so promoting them would be a guess
        that costs a scarce heavy permit to get wrong in the other
        direction."""
        assert classify(None, mime) is ParseTier.HEAVY

    def test_extension_takes_priority_over_mime(self) -> None:
        # Contrived: extension says light, mime says heavy -> extension wins.
        assert classify("txt", "application/pdf") is ParseTier.LIGHT

    def test_unknown_defaults_to_heavy(self) -> None:
        assert classify("xyz-unknown", "application/x-unknown") is ParseTier.HEAVY
        assert classify(None, None) is ParseTier.HEAVY

    def test_leading_dot_and_case_insensitive(self) -> None:
        assert classify(".PDF", None) is ParseTier.HEAVY
        assert classify(".Md", None) is ParseTier.LIGHT


class TestParseCost:
    def test_light_is_always_cost_one(self) -> None:
        assert parse_cost(ParseTier.LIGHT, 0) == 1
        assert parse_cost(ParseTier.LIGHT, XL_HEAVY_BYTES * 10) == 1

    def test_heavy_below_threshold_is_cost_one(self) -> None:
        assert parse_cost(ParseTier.HEAVY, XL_HEAVY_BYTES - 1) == 1

    def test_heavy_at_threshold_is_cost_one(self) -> None:
        # parse_cost uses a strict ">" comparison, so the boundary value
        # itself is not yet XL.
        assert parse_cost(ParseTier.HEAVY, XL_HEAVY_BYTES) == 1

    def test_heavy_above_threshold_is_cost_two(self) -> None:
        assert parse_cost(ParseTier.HEAVY, XL_HEAVY_BYTES + 1) == 2

    def test_heavy_unknown_size_is_cost_one(self) -> None:
        assert parse_cost(ParseTier.HEAVY, None) == 1


class TestGatePool:
    def test_heavy_routes_to_heavy_parse_pool(self) -> None:
        assert gate_pool(ParseTier.HEAVY) is Pool.HEAVY_PARSE

    def test_light_routes_to_light_parse_pool(self) -> None:
        assert gate_pool(ParseTier.LIGHT) is Pool.LIGHT_PARSE
