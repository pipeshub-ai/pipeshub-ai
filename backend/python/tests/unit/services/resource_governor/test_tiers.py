import pytest

from app.config import file_types
from app.services.resource_governor.models import ParseTier, Pool
from app.services.resource_governor.tiers import (
    XL_HEAVY_BYTES,
    classify,
    gate_pool,
    parse_cost,
    reader_tier,
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

    def test_the_reader_the_labels_select_decides(self) -> None:
        """Was test_extension_takes_priority_over_mime: a PDF labelled txt is read by the PDF reader, so it is heavy. The
        extension still decides over a generic mime."""
        assert classify("txt", "application/pdf") is ParseTier.HEAVY
        assert classify("pdf", "application/octet-stream") is ParseTier.HEAVY
        assert classify("txt", "application/octet-stream") is ParseTier.LIGHT

    def test_unknown_defaults_to_heavy(self) -> None:
        assert classify("xyz-unknown", "application/x-unknown") is ParseTier.HEAVY
        assert classify(None, None) is ParseTier.HEAVY

    def test_leading_dot_and_case_insensitive(self) -> None:
        assert classify(".PDF", None) is ParseTier.HEAVY
        assert classify(".Md", None) is ParseTier.LIGHT


_EXTENSIONS = [*sorted({e for ft in file_types.FILE_TYPES for e in ft.extensions if ft.ext_indexable}), "unknown", ""]
_MIMES = [
    *sorted({ft.canonical_mime for ft in file_types.FILE_TYPES if ft.mime_indexable and ft.canonical_mime}),
    "unknown", "", "application/octet-stream", "text/plain",
]


_LIGHT_TEXT = {"csv", "htm", "html", "json", "md", "mdx", "sql_table", "sql_view", "tsv", "txt", "yaml", "yml"}
# A Google Docs, Slides or Sheets mime is read by its Google reader whatever the extension, source code included.
_SOURCE = {
    "bash", "c", "cc", "cjs", "cpp", "cs", "cxx", "dart", "go", "h", "hpp", "hxx", "java", "js", "jsx", "kt", "kts", "mjs",
    "php", "py", "rb", "rs", "sh", "swift", "ts", "tsx",
}
_GOOGLE = (
    "application/vnd.google-apps.document",
    "application/vnd.google-apps.presentation",
    "application/vnd.google-apps.spreadsheet",
)


def _in_process_reader(extension: str, mime_type: str) -> file_types.Tier | None:
    code_ext = file_types.code_extension(extension, None, f"x.{extension}")
    return file_types.read_tier(file_types.legacy_route(
        mime_type, extension, code_ext=code_ext, reads_as_code_file=file_types.reads_as_source(mime_type, code_ext),
    ))


def _service_reader(extension: str, mime_type: str) -> file_types.Tier | None:
    return file_types.format_tier(file_types.parse_format(mime_type, extension))


class TestReaderTier:
    @pytest.mark.parametrize(("reader", "raised_cells", "raised_extensions"), [
        # In-process `classify` already is the labels' reader; the service reads 72 label pairs heavier.
        (_in_process_reader, 0, set()),
        (_service_reader, 72, _LIGHT_TEXT - {"htm"} | {"bash", "sh"}),
    ], ids=["in-process", "service"])
    def test_every_cell_is_heavy_exactly_when_its_labels_or_its_reader_are(
        self, reader, raised_cells: int, raised_extensions: set[str],
    ) -> None:
        raised: list[tuple[str, str]] = []
        for extension in _EXTENSIONS:
            for mime in _MIMES:
                labels, read_by = classify(extension, mime), reader(extension, mime)
                tier = reader_tier(extension, mime, read_by)
                assert tier is (ParseTier.HEAVY if read_by == "heavy" else labels), (extension, mime)
                if labels is ParseTier.LIGHT and tier is ParseTier.HEAVY:
                    raised.append((extension, mime))
        # Pinned: a format that loses its tier, or a reader that changes, moves these.
        assert len(raised) == raised_cells
        assert {extension for extension, _ in raised} == raised_extensions

    @pytest.mark.parametrize("reader", [_in_process_reader, _service_reader], ids=["in-process", "service"])
    def test_a_pdf_labelled_txt_is_admitted_heavy(self, reader) -> None:
        assert reader("txt", "application/pdf") == "heavy"
        assert reader_tier("txt", "application/pdf", reader("txt", "application/pdf")) is ParseTier.HEAVY

    @pytest.mark.parametrize(("extension", "mime", "expected"), [
        ("pdf", "application/octet-stream", ParseTier.HEAVY),  # the extension still decides over a generic mime
        ("txt", "application/octet-stream", ParseTier.LIGHT),
        ("py", "text/plain", ParseTier.LIGHT),
        ("unknown", "unknown", ParseTier.HEAVY),  # an unknown format stays heavy
        ("txt", "text/plain", ParseTier.LIGHT),
        ("md", "text/markdown", ParseTier.LIGHT),
    ])
    @pytest.mark.parametrize("reader", [_in_process_reader, _service_reader], ids=["in-process", "service"])
    def test_controls(self, reader, extension: str, mime: str, expected: ParseTier) -> None:
        assert reader_tier(extension, mime, reader(extension, mime)) is expected

    @pytest.mark.parametrize("mime", _GOOGLE)
    @pytest.mark.parametrize("reader", [_in_process_reader, _service_reader], ids=["in-process", "service"])
    def test_a_google_export_labelled_txt_is_admitted_heavy(self, reader, mime: str) -> None:
        """The Google readers have no tier of their own; they parse as docx, pptx or xlsx, and the labels' tier is theirs."""
        assert classify("txt", mime) is ParseTier.HEAVY
        assert reader("txt", mime) == "heavy"
        assert reader_tier("txt", mime, reader("txt", mime)) is ParseTier.HEAVY

    def test_no_reader_leaves_the_labels_tier(self) -> None:
        assert reader_tier("txt", "text/plain", None) is ParseTier.LIGHT
        assert reader_tier("pdf", None, None) is ParseTier.HEAVY
        assert reader_tier("xyz", "application/x-unknown", None) is ParseTier.HEAVY

    def test_a_light_reader_never_lowers_a_heavy_label(self) -> None:
        assert reader_tier("pdf", "application/pdf", "light") is ParseTier.HEAVY


class TestReaderTierTables:
    def test_the_code_reader_is_light_and_no_reader_has_none(self) -> None:
        assert file_types.read_tier(file_types.Dispatch(file_types.Route.CODE, None)) == "light"
        assert file_types.read_tier(None) is None

    def test_no_type_has_two_tiers(self) -> None:
        for ft in file_types.FILE_TYPES:
            assert not (ft.tier and ft.mime_tier and ft.tier != ft.mime_tier), ft.key

    def test_every_service_format_has_one_tier(self) -> None:
        tiers: dict[str, set[str]] = {}
        for ft in file_types.FILE_TYPES:
            if ft.format and (ft.tier or ft.mime_tier):
                tiers.setdefault(ft.format, set()).add(ft.tier or ft.mime_tier)  # type: ignore[arg-type]
        assert {fmt: t for fmt, t in tiers.items() if len(t) > 1} == {}
        assert all(file_types.format_tier(fmt) == next(iter(t)) for fmt, t in tiers.items())
        assert file_types.format_tier(None) is None
        assert file_types.format_tier("no-such-format") is None


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
