"""A cheap look inside an Office package for the cost class: page or slide count, or the area of a workbook's sheets.

Read from the zip's central directory and the first few kilobytes of a handful of members, never the whole
package, so it costs milliseconds whatever the file's size. Best effort: any problem means no facts and the
size prior stands. Every read is bounded, so a hostile package cannot make it expensive.
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass

_MAX_MEMBERS = 5000  # a package with more entries than this is not a document we inspect
_MAX_DIRECTORY_BYTES = 1 << 20  # the central directory of 5000 ordinary entries is far smaller; the declared count is not trusted
_HEAD = 8192  # bytes read from a member: the dimension element and the document properties sit at the start
_MAX_SHEETS = 200
_MAX_CELLS = 10**12
_PAGES = re.compile(rb"<(?:\w+:)?Pages>\s*(\d{1,9})\s*</")
_SLIDES = re.compile(rb"<(?:\w+:)?Slides>\s*(\d{1,9})\s*</")
_DIMENSION = re.compile(rb'<(?:\w+:)?dimension\s[^>]*?ref="([A-Za-z]{1,3})(\d{1,7})(?::([A-Za-z]{1,3})(\d{1,7}))?"')


@dataclass(frozen=True)
class OfficeFacts:
    pages: int | None = None  # docx Pages, pptx Slides
    cells: int | None = None  # xlsx: the sum of the sheets' dimension areas


def _column(letters: bytes) -> int:
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ch - 64)
    return n


def _area(match: re.Match[bytes]) -> int:
    c1, r1, c2, r2 = match.group(1), match.group(2), match.group(3), match.group(4)
    if c2 is None:
        return 1
    cols = abs(_column(c2) - _column(c1)) + 1
    rows = abs(int(r2) - int(r1)) + 1
    return min(_MAX_CELLS, cols * rows)


def _entry_count(content: bytes) -> int | None:
    """The entry count in the end-of-central-directory record, read before the directory itself is parsed."""
    tail = content[-(65535 + 22):]
    at = tail.rfind(b"PK\x05\x06")
    if at < 0 or len(tail) - at < 22:
        return None
    if int.from_bytes(tail[at + 12:at + 16], "little") > _MAX_DIRECTORY_BYTES:
        return None  # a directory this large is refused whatever count it declares
    return int.from_bytes(tail[at + 10:at + 12], "little")


def office_facts(content: bytes) -> OfficeFacts:
    """The facts of a docx, pptx or xlsx package; empty when it cannot be read this way.

    Any failure (a bad zip, a damaged member, a stream that does not inflate) is no facts: the size prior stands.
    """
    count = _entry_count(content)
    if count is None or count > _MAX_MEMBERS:
        return OfficeFacts()  # refused before one ZipInfo is built
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            names = {i.filename for i in zf.infolist()}
            if "xl/workbook.xml" in names:
                total = 0
                sheets = sorted(n for n in names if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
                for name in sheets[:_MAX_SHEETS]:
                    with zf.open(name) as f:
                        m = _DIMENSION.search(f.read(_HEAD))
                    if m:
                        total += _area(m)
                return OfficeFacts(cells=min(total, _MAX_CELLS)) if total else OfficeFacts()
            if "docProps/app.xml" in names:
                with zf.open("docProps/app.xml") as f:
                    head = f.read(_HEAD)
                for pattern in (_PAGES, _SLIDES):
                    m = pattern.search(head)
                    if m and int(m.group(1)) > 0:
                        return OfficeFacts(pages=int(m.group(1)))
    except Exception:  # best effort by design: zlib.error, BadZipFile, OSError and the rest all mean no facts
        return OfficeFacts()
    return OfficeFacts()
