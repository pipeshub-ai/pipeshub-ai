"""Real files of the types indexed since the second file-type registry, built in memory.

docm, dotx, potx and xlsm are packages python-docx, python-pptx and openpyxl wrote, with the main
part's content type changed to what Word, PowerPoint and Excel write for those types and, for the
macro-enabled ones, a VBA project part added. The VBA project holds ``VBA_MARKER`` so a test can
tell it never reached a parser. odt is a minimal OpenDocument package; rtf is plain RTF. The .msg
samples are python-oxmsg's own test files (``tests/fixtures/msg/PROVENANCE.md``).
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

VBA_MARKER = b"PIPESHUB-VBA-PROJECT-MARKER"
MSG_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "msg"

_CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_VBA_CONTENT_TYPE = "application/vnd.ms-office.vbaProject"
_VBA_REL = "http://schemas.microsoft.com/office/2006/relationships/vbaProject"
DOCM_MAIN = "application/vnd.ms-word.document.macroEnabled.main+xml"
DOTX_MAIN = "application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml"
POTX_MAIN = "application/vnd.openxmlformats-officedocument.presentationml.template.main+xml"
XLSM_MAIN = "application/vnd.ms-excel.sheet.macroEnabled.main+xml"
_DOCX_MAIN = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
_PPTX_MAIN = "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"
_XLSX_MAIN = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"


def _rewrite(package: bytes, *, plain_main: str, main: str, vba_dir: str | None) -> bytes:
    """*package* with its main content type set to *main*, plus a VBA project under *vba_dir*."""
    source = zipfile.ZipFile(io.BytesIO(package))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            data = source.read(info)
            if info.filename == "[Content_Types].xml":
                text = data.decode("utf-8").replace(plain_main, main)
                if vba_dir is not None:
                    text = text.replace(
                        "</Types>",
                        f'<Default Extension="bin" ContentType="{_VBA_CONTENT_TYPE}"/></Types>',
                    ) if 'Extension="bin"' not in text else text
                data = text.encode("utf-8")
            elif vba_dir is not None and info.filename == f"{vba_dir}/_rels/{_main_name(vba_dir)}.rels":
                data = data.decode("utf-8").replace(
                    "</Relationships>",
                    f'<Relationship Id="rIdVba1" Type="{_VBA_REL}" Target="vbaProject.bin"/></Relationships>',
                ).encode("utf-8")
            target.writestr(info, data)
        if vba_dir is not None:
            target.writestr(f"{vba_dir}/vbaProject.bin", b"\xd0\xcf\x11\xe0" + VBA_MARKER * 8)
    return out.getvalue()


def _main_name(folder: str) -> str:
    return {"word": "document.xml", "ppt": "presentation.xml", "xl": "workbook.xml"}[folder]


def _docx(text: str) -> bytes:
    import docx

    document = docx.Document()
    document.add_heading("Quarterly review", 1)
    document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def make_docm(text: str) -> bytes:
    return _rewrite(_docx(text), plain_main=_DOCX_MAIN, main=DOCM_MAIN, vba_dir="word")


def make_dotx(text: str) -> bytes:
    return _rewrite(_docx(text), plain_main=_DOCX_MAIN, main=DOTX_MAIN, vba_dir=None)


def make_potx(text: str) -> bytes:
    import pptx

    presentation = pptx.Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide.shapes.title.text = "Template title"
    slide.placeholders[1].text = text
    buffer = io.BytesIO()
    presentation.save(buffer)
    return _rewrite(buffer.getvalue(), plain_main=_PPTX_MAIN, main=POTX_MAIN, vba_dir=None)


def make_xlsm(text: str) -> bytes:
    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Budget"
    sheet.append(["Item", "Note"])
    sheet.append(["Row 1", text])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return _rewrite(buffer.getvalue(), plain_main=_XLSX_MAIN, main=XLSM_MAIN, vba_dir="xl")


_ODF_MANIFEST = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2">'
    '<manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.text"/>'
    '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"{size}>{encryption}'
    "</manifest:file-entry></manifest:manifest>"
)
_ODF_ENCRYPTION = (
    '<manifest:encryption-data manifest:checksum-type="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0#sha256-1k" '
    'manifest:checksum="AAAA"><manifest:algorithm manifest:algorithm-name="http://www.w3.org/2001/04/xmlenc#aes256-cbc" '
    'manifest:initialisation-vector="AAAA"/><manifest:key-derivation manifest:key-derivation-name="PBKDF2" '
    'manifest:key-size="32" manifest:iteration-count="100000" manifest:salt="AAAA"/></manifest:encryption-data>'
)


def make_odt(text: str, *, encrypted: bool = False) -> bytes:
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" office:version="1.2">'
        f"<office:body><office:text><text:h>Quarterly review</text:h><text:p>{text}</text:p>"
        "</office:text></office:body></office:document-content>"
    ).encode("utf-8")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr(zipfile.ZipInfo("mimetype"), "application/vnd.oasis.opendocument.text")
        package.writestr("content.xml", b"\x8f\x02encrypted" * 20 if encrypted else content)
        manifest = _ODF_MANIFEST.format(
            size=' manifest:size="1234"' if encrypted else "",
            encryption=_ODF_ENCRYPTION if encrypted else "",
        )
        package.writestr("META-INF/manifest.xml", manifest)
    return out.getvalue()


def make_rtf(text: str) -> bytes:
    return (
        r"{\rtf1\ansi\ansicpg1252\deff0{\fonttbl{\f0\fswiss Arial;}}"
        r"\f0\fs24 Quarterly review\par " + text + r"\par }"
    ).encode("ascii")


def make_log(sentinel: str, lines: int = 20) -> bytes:
    body = [f"2026-11-13T09:{i:02d}:00Z INFO worker-{i} handled request {i}" for i in range(lines)]
    body.insert(lines // 2, f"2026-11-13T09:30:00Z ERROR {sentinel}")
    return ("\n".join(body) + "\n").encode("utf-8")


def make_cfg(sentinel: str) -> bytes:
    return (
        "# Service configuration\n"
        "[server]\n"
        "host = 0.0.0.0\n"
        "port = 8080\n"
        "\n"
        "[notes]\n"
        f"owner = {sentinel}\n"
    ).encode("utf-8")


def msg_with_html_body() -> bytes:
    return (MSG_FIXTURES / "message.msg").read_bytes()


def msg_with_text_body() -> bytes:
    return (MSG_FIXTURES / "no-attachments.msg").read_bytes()


def not_a_msg() -> bytes:
    return (MSG_FIXTURES / "not-a-MSG-file.msg").read_bytes()


def one_of_each(sentinel: str) -> dict[str, bytes]:
    """One real file per new type, named by its extension."""
    return {
        "msg": msg_with_html_body(),
        "odt": make_odt(sentinel),
        "rtf": make_rtf(sentinel),
        "xlsm": make_xlsm(sentinel),
        "log": make_log(sentinel),
        "cfg": make_cfg(sentinel),
        "docm": make_docm(sentinel),
        "dotx": make_dotx(sentinel),
        "potx": make_potx(sentinel),
    }
