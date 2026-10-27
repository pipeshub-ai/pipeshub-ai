"""Hand-built workbooks for tests of the Excel loader's memory limit.

Each is a complete, loadable workbook (content types, relationships, workbook
part) around the sheet XML a test supplies, so what a test measures is what
openpyxl does with that XML, not a missing part.
"""
from __future__ import annotations

import io
import logging
import zipfile
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from app.modules.parsers.excel.excel_parser import ExcelParser

if TYPE_CHECKING:
    from collections.abc import Callable

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"


def column_letters(i: int) -> str:
    s = ""
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def empty_cells(rows: int, cols: int, cell: Callable[[str], str] | None = None) -> str:
    cell = cell or (lambda ref: f'<c r="{ref}"/>')
    names = [column_letters(c) for c in range(1, cols + 1)]
    return "".join(f'<row r="{r}">' + "".join(cell(f"{n}{r}") for n in names) + "</row>" for r in range(1, rows + 1))


def workbook_zip(
    sheet_body: str,
    *,
    sheet_path: str = "xl/worksheets/sheet1.xml",
    extra: dict[str, str | bytes] | None = None,
    sheet_rels: str | None = None,
    head: str = "",
    first: list[tuple[zipfile.ZipInfo, bytes]] | None = None,
    sheet_xml: str | None = None,
    sheet_ns_attrs: str = "",
) -> bytes:
    """A workbook with one sheet whose XML is ``<worksheet>`` + *sheet_body*.

    *sheet_xml* replaces the whole sheet part; *first* are entries written
    before everything else; *sheet_rels* is the sheet's relationships part.
    """
    content_types = (
        '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="png" ContentType="image/png"/>'
        + ('<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>' if "xl/sharedStrings.xml" in (extra or {}) else "")
        + '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        f'<Override PartName="/{sheet_path}" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for info, body in first or []:
            z.writestr(info, body, compress_type=zipfile.ZIP_STORED)
        z.writestr("[Content_Types].xml", content_types)
        z.writestr(
            "_rels/.rels",
            f'<?xml version="1.0"?><Relationships xmlns="{_PKG_REL}"><Relationship Id="rId1" '
            f'Type="{REL_NS}/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        )
        z.writestr(
            "xl/workbook.xml",
            f'<?xml version="1.0"?><workbook xmlns="{MAIN_NS}" xmlns:r="{REL_NS}">'
            '<sheets><sheet name="S" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        target = sheet_path.removeprefix("xl/")
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            f'<?xml version="1.0"?><Relationships xmlns="{_PKG_REL}"><Relationship Id="rId1" '
            f'Type="{REL_NS}/worksheet" Target="{target}"/>'
            + (f'<Relationship Id="rId2" Type="{REL_NS}/sharedStrings" Target="sharedStrings.xml"/>' if "xl/sharedStrings.xml" in (extra or {}) else "")
            + "</Relationships>",
        )
        z.writestr(
            sheet_path,
            sheet_xml
            if sheet_xml is not None
            else f'<?xml version="1.0"?>{head}<worksheet xmlns="{MAIN_NS}" xmlns:r="{REL_NS}"{sheet_ns_attrs}>{sheet_body}</worksheet>',
        )
        if sheet_rels is not None:
            name = sheet_path.rsplit("/", 1)
            z.writestr(f"{name[0]}/_rels/{name[1]}.rels", sheet_rels)
        for name, body in (extra or {}).items():
            z.writestr(name, body)
    return out.getvalue()


def load(data: bytes) -> ExcelParser:
    """Load *data* the way every caller does (``load_workbook_from_binary``) and return the parser."""
    parser = ExcelParser(logging.getLogger("excel-test"), MagicMock())
    parser.load_workbook_from_binary(data)
    return parser
