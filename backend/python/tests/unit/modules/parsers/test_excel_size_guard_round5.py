"""Ways round 5 of the size guard still admits workbooks that openpyxl loads in full.

Limits are scaled down so a few KB stand in for the gigabytes. Measured at full
size (default limits, openpyxl 3.1.5): see notes/qa/mem6-r5-report.md.
"""

import io
import struct
import zipfile
import zlib

import pytest

from app.modules.parsers.excel import excel_parser as module
from app.services.parsing.interface import ParseError

_NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _zip(parts: dict[str, str | bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in parts.items():
            z.writestr(name, body)
    return out.getvalue()


def _sheet(body: str) -> str:
    return f'<?xml version="1.0"?><worksheet {_NS}>{body}</worksheet>'


@pytest.fixture(autouse=True)
def small_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "EXCEL_MAX_CELLS", 1_000)
    monkeypatch.setattr(module, "EXCEL_MAX_SHEET_XML_BYTES", 10_000_000)


@pytest.mark.parametrize("tag", ["mergeCell", "hyperlink"])
def test_one_range_element_covering_many_cells_spends_the_budget(tag: str) -> None:
    # openpyxl creates one cell object per cell of a merged (or hyperlinked) range:
    # <mergeCell ref="A1:J200000"/> (60 bytes) became 2 M objects, 585 MB, in 49 s.
    ref = "A1:Z1000"  # 26 000 cells against a budget of 1 000
    inner = f'<mergeCells><mergeCell ref="{ref}"/></mergeCells>' if tag == "mergeCell" else f'<hyperlinks><hyperlink ref="{ref}" location="S!A1"/></hyperlinks>'
    with pytest.raises(ParseError):
        module.check_workbook_size(_zip({"xl/worksheets/sheet1.xml": _sheet("<sheetData/>" + inner)}))


@pytest.mark.parametrize("tag", ["dataValidation", "conditionalFormatting"])
def test_one_sqref_listing_many_ranges_spends_the_budget(tag: str) -> None:
    # A 7-byte "A123 " becomes a CellRange object (about 360 bytes) in a MultiCellRange:
    # 200 000 refs in one sqref, 1.4 MB of XML, load 72-85 MB; the 400 MB byte cap admits 20 GB.
    refs = " ".join(f"A{i}" for i in range(1, 3001))
    if tag == "dataValidation":
        inner = f'<dataValidations count="1"><dataValidation type="whole" sqref="{refs}"><formula1>1</formula1></dataValidation></dataValidations>'
    else:
        inner = f'<conditionalFormatting sqref="{refs}"><cfRule type="cellIs" priority="1" operator="equal"><formula>1</formula></cfRule></conditionalFormatting>'
    with pytest.raises(ParseError):
        module.check_workbook_size(_zip({"xl/worksheets/sheet1.xml": _sheet("<sheetData/>" + inner)}))


def test_comments_cost_more_than_their_weight() -> None:
    # Measured 200 000 comments: 446 MB, 2.2 KB each, against about 2 units (860 bytes).
    # Scaled: the same budget must not admit more comments than the load can hold (about 1/2.6 of it).
    comments = "".join(f'<comment ref="A{i}" authorId="0"><text><r><t>hi</t></r></text></comment>' for i in range(1, 451))
    xml = f'<?xml version="1.0"?><comments {_NS}><authors><author>a</author></authors><commentList>{comments}</commentList></comments>'
    with pytest.raises(ParseError):
        module.check_workbook_size(_zip({"xl/comments1.xml": xml}))


def test_table_columns_cost_more_than_their_weight() -> None:
    # Measured 200 000 tableColumn: 196 MB, 1 KB each, weight 1.0 (a cell is 430 bytes).
    cols = "".join(f'<tableColumn id="{i}" name="c{i}"/>' for i in range(1, 451))
    xml = f'<?xml version="1.0"?><table {_NS} id="1" name="T" displayName="T" ref="A1:B2"><tableColumns count="450">{cols}</tableColumns></table>'
    with pytest.raises(ParseError):
        module.check_workbook_size(_zip({"xl/tables/table1.xml": xml}))


def _png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"\x00\x00")) + chunk(b"IEND", b"")


def test_a_part_that_is_not_xml_is_counted_to_its_end(monkeypatch: pytest.MonkeyPatch) -> None:
    # openpyxl reads a drawing's image whole (archive.read) and keeps it: the guard stops at
    # the first chunk of a non-XML part, so a 1 MB chunk is all it ever counts of a
    # picture that decompresses to hundreds of MB (a few hundred KB in the zip).
    monkeypatch.setattr(module, "EXCEL_MAX_SHEET_XML_BYTES", 3_000_000)
    data = _zip({"xl/worksheets/sheet1.xml": _sheet("<sheetData/>"), "xl/media/image1.png": _png() + b"\0" * 8_000_000})
    with pytest.raises(ParseError):
        module.check_workbook_size(data)


def test_one_image_referenced_by_many_anchors_is_counted_each_time(monkeypatch: pytest.MonkeyPatch) -> None:
    # openpyxl reads the image bytes again for every anchor that uses it and keeps each copy:
    # 100 anchors on a 1 MB picture hold 100 MB. The guard counts the picture once, 1 MB.
    monkeypatch.setattr(module, "EXCEL_MAX_CELLS", 10_000)
    monkeypatch.setattr(module, "EXCEL_MAX_SHEET_XML_BYTES", 20_000_000)
    anchors = "".join(
        f'<xdr:oneCellAnchor><xdr:from><xdr:col>0</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>{i}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>'
        f'<xdr:ext cx="1" cy="1"/><xdr:pic><xdr:nvPicPr><xdr:cNvPr id="{i + 2}" name="p"/><xdr:cNvPicPr/></xdr:nvPicPr>'
        f'<xdr:blipFill><a:blip r:embed="rId1"/></xdr:blipFill><xdr:spPr/></xdr:pic><xdr:clientData/></xdr:oneCellAnchor>'
        for i in range(400)
    )
    drawing = (
        '<?xml version="1.0"?><xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" '
        f'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="{_REL}">{anchors}</xdr:wsDr>'
    )
    data = _zip({"xl/worksheets/sheet1.xml": _sheet("<sheetData/>"), "xl/drawings/drawing1.xml": drawing, "xl/media/image1.png": _png() + b"\0" * 1_000_000})
    # 400 anchors x 1 MB = 400 MB held; a limit of 20 MB decompressed XML-equivalent must refuse it.
    with pytest.raises(ParseError):
        module.check_workbook_size(data)


def _with_unreadable_first_entry(variant: str, sheet_cells: int) -> bytes:
    # An entry openpyxl never opens (customXml/item1.xml) that the guard cannot read, listed first.
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo("customXml/item1.xml"), b"<x>" + b"MARKERMARKER" * 20 + b"</x>", compress_type=zipfile.ZIP_STORED)
        z.writestr("xl/worksheets/sheet1.xml", _sheet("<sheetData>" + "".join(f'<row r="{i}"><c r="A{i}"/></row>' for i in range(1, sheet_cells + 1)) + "</sheetData>"))
    data = out.getvalue()
    if variant == "bad_crc":
        return data.replace(b"MARKERMARKER", b"MARKERMARKEZ", 1)
    buf = bytearray(data)
    central = buf.index(b"PK\x01\x02")
    if variant == "unknown_method":
        buf[8:10] = (99).to_bytes(2, "little")
        buf[central + 10 : central + 12] = (99).to_bytes(2, "little")
    else:  # encrypted flag
        buf[6] |= 1
        buf[central + 8] |= 1
    return bytes(buf)


@pytest.mark.parametrize("variant", ["bad_crc", "unknown_method", "encrypted_flag"])
def test_an_entry_the_guard_cannot_read_does_not_end_the_count(variant: str) -> None:
    # openpyxl does not open the junk entry and loads the 300 000-cell sheet behind it in full
    # (checked with openpyxl 3.1.5); the guard gave up at the first unreadable entry and admitted.
    # Full size: 8 M cells behind such an entry, default limits: ADMITTED in 0.0 s.
    with pytest.raises(ParseError):
        module.check_workbook_size(_with_unreadable_first_entry(variant, 3_000))


def test_calc_chain_does_not_double_the_weight_of_formula_cells() -> None:
    # False refusal. calcChain.xml is never loaded by openpyxl, yet each of its entries costs a
    # cell. Measured at full size: 2 M formula cells (+ calcChain) = 4.5 M units, REFUSED, while
    # the load takes 732 MB above the import floor (1 M formula cells: admitted, 368 MB).
    n = 600
    cells = "".join(f'<row r="{i}"><c r="A{i}"><f>B{i}*2</f><v>1</v></c></row>' for i in range(1, n + 1))
    chain = f'<?xml version="1.0"?><calcChain {_NS}>' + "".join(f'<c r="A{i}" i="1"/>' for i in range(1, n + 1)) + "</calcChain>"
    module.check_workbook_size(_zip({"xl/worksheets/sheet1.xml": _sheet(f"<sheetData>{cells}</sheetData>"), "xl/calcChain.xml": chain}))
