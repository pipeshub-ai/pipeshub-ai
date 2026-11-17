"""Per-upload content salts, so the org-wide md5 dedup never sees the same bytes twice.

Every upload of a benchmark run carries a token ``run_id:seq`` written into what the parsers *extract*
(not only into the bytes), so two uploads of one template differ in md5, in extracted text and, for
a PDF, in the rendered first page. The page count, the file kind and the cost class do not change.

* PDF: an incremental update (a new content stream and a new copy of page 1 that lists it after
  the old contents). Nothing already in the file moves, so the cost does not depend on the page
  count. The overlay draws the token as text and an 8x8 block pattern derived from it, so OCR of a
  scanned page sees different pixels as well.
* DOCX, PPTX, XLSX: a body paragraph, a slide text run, a cell, written into the one XML part that
  holds page one's content.
* Text, markdown, HTML: a line.

Pure standard library: the harness needs no PDF library to salt.
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile

TEXT_EXTS = frozenset({"txt", "md", "html"})
OOXML_EXTS = frozenset({"docx", "pptx", "xlsx"})


def salt_token(run_id: str, seq: int) -> str:
    """The token of upload number *seq* of a run."""
    return f"{run_id}:{seq}"


def salt_line(token: str) -> str:
    return f"bench-salt {token}"


def salt_bytes(data: bytes, ext: str, token: str) -> bytes:
    """*data* with *token* written into its content. *ext* is the extension without the dot."""
    ext = ext.lower().lstrip(".")
    if ext == "pdf":
        return _salt_pdf(data, token)
    if ext in ("txt", "md"):
        return data + b"\n\n" + salt_line(token).encode() + b"\n"
    if ext == "html":
        return _insert_before(data, b"</body>", f"<p>{salt_line(token)}</p>".encode(), last=True)
    if ext == "docx":
        return _rewrite_zip(data, "word/document.xml", lambda x: _salt_docx_xml(x, token))
    if ext == "pptx":
        return _rewrite_zip(data, "ppt/slides/slide1.xml", lambda x: _salt_pptx_xml(x, token))
    if ext == "xlsx":
        return _rewrite_zip(data, "xl/worksheets/sheet1.xml", lambda x: _salt_xlsx_xml(x, token))
    raise ValueError(f"no salt for .{ext} files")


def _xml_escape(text: str) -> bytes:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").encode()


def _insert_before(data: bytes, marker: bytes, addition: bytes, *, last: bool = False) -> bytes:
    at = data.rfind(marker) if last else data.find(marker)
    if at < 0:
        raise ValueError(f"cannot salt: {marker!r} not found")
    return data[:at] + addition + data[at:]


def _salt_docx_xml(xml: bytes, token: str) -> bytes:
    para = b"<w:p><w:r><w:t>" + _xml_escape(salt_line(token)) + b"</w:t></w:r></w:p>"
    # The body's own section properties must stay last in the body.
    marker = b"<w:sectPr" if xml.rfind(b"<w:sectPr") > xml.rfind(b"</w:p>") else b"</w:body>"
    return _insert_before(xml, marker, para, last=True)


def _salt_pptx_xml(xml: bytes, token: str) -> bytes:
    para = b'<a:p><a:r><a:rPr lang="en-US"/><a:t>' + _xml_escape(salt_line(token)) + b"</a:t></a:r></a:p>"
    return _insert_before(xml, b"</p:txBody>", para, last=True)


def _salt_xlsx_xml(xml: bytes, token: str) -> bytes:
    rows = [int(m) for m in re.findall(rb'<row r="(\d+)"', xml)]
    nxt = (max(rows) if rows else 0) + 1
    row = (
        f'<row r="{nxt}"><c r="A{nxt}" t="inlineStr"><is><t>'.encode()
        + _xml_escape(salt_line(token)) + b"</t></is></c></row>"
    )
    if b"</sheetData>" in xml:
        return _insert_before(xml, b"</sheetData>", row)
    return xml.replace(b"<sheetData/>", b"<sheetData>" + row + b"</sheetData>")  # an empty sheet


def _rewrite_zip(data: bytes, member: str, edit) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        names = src.namelist()
        if member not in names:
            raise ValueError(f"cannot salt: {member} is not in the archive")
        for info in src.infolist():
            body = src.read(info.filename)
            if info.filename == member:
                body = edit(body)
            clone = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            clone.compress_type = zipfile.ZIP_DEFLATED
            clone.external_attr = info.external_attr
            dst.writestr(clone, body)
    return out.getvalue()


# -- PDF incremental update -----------------------------------------------------------------


def _read_xref(data: bytes) -> tuple[dict[int, int], bytes, int]:
    """Object offsets of a classic cross-reference chain, the newest trailer, and the newest xref offset."""
    tail = data[-2048:]
    start = tail.rfind(b"startxref")
    if start < 0:
        raise ValueError("cannot salt: no startxref")
    newest = int(tail[start + 9:].split()[0])
    offsets: dict[int, int] = {}
    trailer = b""
    at: int | None = newest
    seen: set[int] = set()
    while at is not None and at not in seen:
        seen.add(at)
        if data[at:at + 4] != b"xref":
            raise ValueError("cannot salt: the PDF has a cross-reference stream, not a classic table")
        pos = at + 4
        while True:
            while data[pos:pos + 1] in (b"\n", b"\r", b" "):
                pos += 1
            if data[pos:pos + 7] == b"trailer":
                break
            line_end = data.index(b"\n", pos)
            first, count = (int(x) for x in data[pos:line_end].split())
            pos = line_end + 1
            for i in range(count):
                entry = data[pos + i * 20:pos + i * 20 + 20]
                if entry[17:18] == b"n":
                    offsets.setdefault(first + i, int(entry[:10]))
            pos += count * 20
        trailer_end = data.index(b"startxref", pos)
        this_trailer = data[pos + 7:trailer_end]
        trailer = trailer or this_trailer
        prev = re.search(rb"/Prev\s+(\d+)", this_trailer)
        at = int(prev.group(1)) if prev else None
    return offsets, trailer, newest


def _obj_body(data: bytes, offsets: dict[int, int], num: int) -> bytes:
    start = offsets[num]
    body = data[start:data.index(b"endobj", start)]
    return body[body.index(b"obj") + 3:].strip()


def _first_page(data: bytes, offsets: dict[int, int], root: int) -> tuple[int, bytes]:
    node = int(re.search(rb"/Pages\s+(\d+)\s+0\s+R", _obj_body(data, offsets, root)).group(1))  # type: ignore[union-attr]
    for _ in range(64):
        body = _obj_body(data, offsets, node)
        if re.search(rb"/Type\s*/Page(?![a-zA-Z])", body):
            return node, body
        kids = re.search(rb"/Kids\s*\[\s*(\d+)\s+0\s+R", body)
        if not kids:
            break
        node = int(kids.group(1))
    raise ValueError("cannot salt: page 1 not found")


def _overlay(token: str) -> bytes:
    """Content that draws the token as text and an 8x8 block pattern derived from it."""
    bits = hashlib.sha256(token.encode()).digest()  # 64 bits
    safe = token.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    ops = ["q", "0 g"]
    for row in range(8):
        for col in range(8):
            if bits[row] >> col & 1:
                ops.append(f"{20 + col * 4} {20 + row * 4} 4 4 re f")
    ops += ["Q", "BT", "/F1 8 Tf", "0 g", f"60 24 Td ({salt_line(safe)}) Tj", "ET"]
    return ("\n".join(ops) + "\n").encode("latin-1", "replace")


def _salt_pdf(data: bytes, token: str) -> bytes:
    offsets, trailer, prev_xref = _read_xref(data)
    root_m = re.search(rb"/Root\s+(\d+)\s+0\s+R", trailer)
    size_m = re.search(rb"/Size\s+(\d+)", trailer)
    if not root_m or not size_m:
        raise ValueError("cannot salt: trailer has no /Root or /Size")
    root, size = int(root_m.group(1)), int(size_m.group(1))
    page_num, page = _first_page(data, offsets, root)
    if not re.search(rb"/F1\b", data):
        raise ValueError("cannot salt: no /F1 font to draw the token with")
    stream_num, new_size = size, size + 1
    contents = re.search(rb"/Contents\s*(\[[^\]]*\]|\d+\s+0\s+R)", page)
    if contents:
        old = contents.group(1)
        inner = old if old.startswith(b"[") is False else old[1:-1]
        listed = b"/Contents [ " + inner.strip() + b" " + str(stream_num).encode() + b" 0 R ]"
        new_page = page[:contents.start()] + listed + page[contents.end():]
    else:
        new_page = page.rstrip()[:-2] + b" /Contents " + str(stream_num).encode() + b" 0 R >>"
    overlay = _overlay(token)
    out = io.BytesIO()
    out.write(data)
    if not data.endswith(b"\n"):
        out.write(b"\n")
    stream_off = out.tell()
    out.write(f"{stream_num} 0 obj\n<< /Length {len(overlay)} >>\nstream\n".encode() + overlay + b"endstream\nendobj\n")
    page_off = out.tell()
    out.write(f"{page_num} 0 obj\n".encode() + new_page + b"\nendobj\n")
    xref_off = out.tell()
    entries = sorted([(page_num, page_off), (stream_num, stream_off)])
    out.write(b"xref\n")
    for num, off in entries:
        out.write(f"{num} 1\n{off:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {new_size} /Root {root} 0 R /Prev {prev_xref} >>\nstartxref\n{xref_off}\n%%EOF\n".encode()
    )
    return out.getvalue()
