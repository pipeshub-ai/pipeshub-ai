import json

import pytest

from app.web.errors import NoText, UnsupportedContent
from app.web.fetch.parsers import MAX_PDF_PAGES, parse_body

URL = "https://example.com/doc"


def make_pdf(pages: list[str | None]) -> bytes:
    """A minimal valid PDF; a ``None`` page has no text layer, like a scanned page."""
    count = len(pages)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{' '.join(f'{4 + 2 * i} 0 R' for i in range(count))}] /Count {count} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for i, text in enumerate(pages):
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>".encode()
        )
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode() if text else b""
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


HTML = b"<html><body><main><p>" + b"Main article text. " * 30 + b"</p></main></body></html>"


class TestHtml:
    def test_html(self) -> None:
        blocks = parse_body("text/html; charset=utf-8", HTML, url=URL).blocks
        assert "Main article text." in blocks[0].content
        assert all(b.type == "text" for b in blocks)

    def test_declared_charset_is_used(self) -> None:
        body = "<html><body><p>Café crème brûlée</p></body></html>".encode("latin-1")
        blocks = parse_body("text/html; charset=ISO-8859-1", body, url=URL).blocks
        assert "Café crème brûlée" in blocks[0].content

    def test_unknown_charset_falls_back(self) -> None:
        blocks = parse_body("text/html; charset=no-such-charset", HTML, url=URL).blocks
        assert "Main article text." in blocks[0].content

    def test_title(self) -> None:
        body = b"<html><head><title>  The   Title </title></head><body><p>" + b"Text. " * 50 + b"</p></body></html>"
        assert parse_body("text/html", body, url=URL).title == "The Title"
        assert parse_body("text/html", HTML, url=URL).title == ""

    def test_long_title_is_cut(self) -> None:
        body = b"<html><head><title>" + b"x" * 1000 + b"</title></head><body><p>Words here.</p></body></html>"
        assert len(parse_body("text/html", body, url=URL).title) == 300

    def test_xhtml(self) -> None:
        assert parse_body("application/xhtml+xml", HTML, url=URL).blocks


class TestPdf:
    def test_every_page_with_its_number(self) -> None:  # WP-0-FETCH-01
        blocks = parse_body("application/pdf", make_pdf(["First page", "Second page", "Third page"]), url=URL).blocks
        assert [b.content.splitlines()[0] for b in blocks] == ["[Page 1]", "[Page 2]", "[Page 3]"]
        assert "Second page" in blocks[1].content

    def test_scanned_pdf_has_no_text(self) -> None:  # WP-0-FETCH-02
        with pytest.raises(NoText) as info:
            parse_body("application/pdf", make_pdf([None, None]), url=URL)
        assert "no text layer" in info.value.message

    def test_corrupt_pdf(self) -> None:
        with pytest.raises(NoText):
            parse_body("application/pdf", b"%PDF-1.4 not really", url=URL)

    def test_long_pdf_is_cut_with_a_note(self) -> None:
        blocks = parse_body("application/pdf", make_pdf([f"p{i}" for i in range(MAX_PDF_PAGES + 3)]), url=URL).blocks
        assert len(blocks) == MAX_PDF_PAGES + 1
        assert blocks[-1].content == f"[Stopped after {MAX_PDF_PAGES} of {MAX_PDF_PAGES + 3} pages]"


class TestTextAndJson:
    @pytest.mark.parametrize("content_type", ["text/plain", "text/markdown", "text/csv"])
    def test_text(self, content_type: str) -> None:
        assert parse_body(content_type, b"  hello  ", url=URL).blocks[0].content == "hello"

    def test_json_is_pretty_printed(self) -> None:
        blocks = parse_body("application/json", b'{"a":1,"b":[2]}', url=URL).blocks
        assert json.loads(blocks[0].content) == {"a": 1, "b": [2]}
        assert "\n" in blocks[0].content

    def test_json_suffix_types(self) -> None:
        assert parse_body("application/ld+json", b'{"a":1}', url=URL).blocks

    def test_invalid_json_kept_as_text(self) -> None:
        assert parse_body("application/json", b"{not json", url=URL).blocks[0].content == "{not json"

    def test_empty_body_has_no_blocks(self) -> None:
        assert parse_body("text/plain", b"   ", url=URL).blocks == []


class TestSniffing:
    @pytest.mark.parametrize("content_type", [None, "", "application/octet-stream"])
    def test_pdf_without_a_useful_header(self, content_type: str | None) -> None:
        assert parse_body(content_type, make_pdf(["Hi"]), url=URL).blocks[0].content.startswith("[Page 1]")

    def test_html_without_a_header(self) -> None:
        assert "Main article text." in parse_body(None, HTML, url=URL).blocks[0].content

    def test_text_without_a_header(self) -> None:
        assert parse_body(None, b"just words", url=URL).blocks[0].content == "just words"

    def test_binary_without_a_header(self) -> None:
        with pytest.raises(UnsupportedContent):
            parse_body(None, b"PK\x03\x04\x00\x00binary", url=URL)


@pytest.mark.parametrize("content_type", ["application/zip", "image/png", "video/mp4", "application/x-msdownload"])
def test_unsupported_types_name_the_type(content_type: str) -> None:  # WP-0-FETCH-09
    with pytest.raises(UnsupportedContent) as info:
        parse_body(content_type, b"\x00\x01", url=URL)
    assert content_type in info.value.message
    assert info.value.to_tool_payload()["error_type"] == "unsupported_content_type"
