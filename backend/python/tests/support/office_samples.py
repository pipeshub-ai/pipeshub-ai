"""Office files for tests: password-protected ones made by a real encryption tool, and CFBF containers built by hand.

The three ``encrypted.*`` files under ``encrypted_office/`` were written by
msoffcrypto-tool (see the README there). ``cfbf`` builds the container shapes
a test needs that no tool would write: a 4096-byte-sector file, a directory
that loops, a header that points outside the file.
"""
from __future__ import annotations

import io
import struct
from pathlib import Path

import docx
import openpyxl
import pptx

_DIR = Path(__file__).parent / "encrypted_office"
ENCRYPTED_PASSWORD = "s3cret"

END_OF_CHAIN = 0xFFFFFFFE
FREE = 0xFFFFFFFF
FAT_SECTOR = 0xFFFFFFFD


def encrypted_docx() -> bytes:
    return (_DIR / "encrypted.docx").read_bytes()


def encrypted_xlsx() -> bytes:
    return (_DIR / "encrypted.xlsx").read_bytes()


def encrypted_pptx() -> bytes:
    return (_DIR / "encrypted.pptx").read_bytes()


def encrypted_docm() -> bytes:
    return (_DIR / "encrypted.docm").read_bytes()


def encrypted_xlsm() -> bytes:
    return (_DIR / "encrypted.xlsm").read_bytes()


def encrypted_pptm() -> bytes:
    return (_DIR / "encrypted.pptm").read_bytes()


ENCRYPTED_BY_EXTENSION = {
    "docx": encrypted_docx,
    "xlsx": encrypted_xlsx,
    "pptx": encrypted_pptx,
}
ENCRYPTED_MACRO_BY_EXTENSION = {
    "docm": encrypted_docm,
    "xlsm": encrypted_xlsm,
    "pptm": encrypted_pptm,
}
MACRO_MIME = {
    "docm": "application/vnd.ms-word.document.macroEnabled.12",
    "xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    "pptm": "application/vnd.ms-powerpoint.presentation.macroEnabled.12",
}


def plain_docx() -> bytes:
    document = docx.Document()
    document.add_paragraph("Quarterly planning notes")
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def plain_xlsx() -> bytes:
    workbook = openpyxl.Workbook()
    workbook.active["A1"] = "Quarterly planning notes"
    buf = io.BytesIO()
    workbook.save(buf)
    return buf.getvalue()


def plain_pptx() -> bytes:
    presentation = pptx.Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[5])
    slide.shapes.title.text = "Quarterly planning notes"
    buf = io.BytesIO()
    presentation.save(buf)
    return buf.getvalue()


PLAIN_BY_EXTENSION = {"docx": plain_docx, "xlsx": plain_xlsx, "pptx": plain_pptx}


def _entry(name: str, kind: int, start: int = END_OF_CHAIN, size: int = 0) -> bytes:
    raw = name.encode("utf-16-le") + b"\0\0" if name else b""
    return (
        raw.ljust(64, b"\0")
        + struct.pack("<H", len(raw))
        + bytes([kind, 1])
        + struct.pack("<III", FREE, FREE, FREE)
        + b"\0" * 16  # CLSID
        + b"\0" * 4  # state bits
        + b"\0" * 16  # timestamps
        + struct.pack("<I", start)
        + struct.pack("<Q", size)
    )


def cfbf(stream_names: list[str], *, sector_shift: int = 9, directory_loops: bool = False) -> bytes:
    """An OLE compound file whose root holds an empty stream per name (one FAT sector, one directory chain)."""
    sector_size = 1 << sector_shift
    entries = [_entry("Root Entry", 5)] + [_entry(n, 2) for n in stream_names]
    per_sector = sector_size // 128
    directory_sectors = (len(entries) + per_sector - 1) // per_sector
    entries += [_entry("", 0)] * (directory_sectors * per_sector - len(entries))
    directory = b"".join(entries)

    # sector 0 is the FAT; the directory follows it
    fat = [FAT_SECTOR] + [i + 2 for i in range(directory_sectors - 1)] + [END_OF_CHAIN]
    if directory_loops:
        fat[-1] = 1
    fat += [FREE] * (sector_size // 4 - len(fat))

    header = (
        bytes.fromhex("D0CF11E0A1B11AE1") + b"\0" * 16
        + struct.pack("<HHHHH", 0x3E, 3 if sector_shift == 9 else 4, 0xFFFE, sector_shift, 6)
        + b"\0" * 6
        + struct.pack("<I", 0 if sector_shift == 9 else directory_sectors)  # directory sectors (v4)
        + struct.pack("<I", 1)  # FAT sectors
        + struct.pack("<I", 1)  # first directory sector
        + struct.pack("<I", 0)
        + struct.pack("<I", 4096)  # mini stream cutoff
        + struct.pack("<II", END_OF_CHAIN, 0)  # mini FAT
        + struct.pack("<II", END_OF_CHAIN, 0)  # DIFAT
        + struct.pack("<I", 0)  # FAT sector 0
        + struct.pack("<108I", *([FREE] * 108))
    )
    header = header.ljust(sector_size, b"\0")
    return header + struct.pack(f"<{sector_size // 4}I", *fat) + directory
