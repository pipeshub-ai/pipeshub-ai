"""Content-based detection of OS/filesystem metadata "files".

Connectors and manual uploads occasionally hand the indexing pipeline files
like macOS AppleDouble sidecars (``._name.ext``) or Windows shortcuts that
carry a real document's extension/MIME type but are not valid documents of
that type — parsers fail on them with cryptic errors instead of a clean
"unsupported" status.

Detect these by content, not filename: a sidecar can be renamed, and a
filename convention (like a leading ``._``) alone is not proof either way.

Only add signatures here that cannot collide with a real, supported document
format. In particular, do not add generic container magic numbers (e.g. the
OLE2/CFBF header shared by legacy ``.doc``/``.xls``/``.ppt`` — see
``_MAGIC_OLE2`` in ``app/agents/actions/util/parse_file.py``) — those would
misclassify real documents as junk.
"""
from __future__ import annotations

METADATA_FILE_SIGNATURES: dict[str, bytes] = {
    "AppleDouble": b"\x00\x05\x16\x07",
    "AppleSingle": b"\x00\x05\x16\x00",
    "DS_Store": b"\x00\x00\x00\x01Bud1",
    "BinaryPlist": b"bplist",
    "WindowsLnk": b"\x4c\x00\x00\x00\x01\x14\x02\x00",
    "VimSwap": b"b0VIM",
}


def match_metadata_file_signature(file_content: bytes) -> str | None:
    """Name of the matched signature if *file_content* is OS/filesystem
    metadata masquerading as a real document, else None."""
    if not isinstance(file_content, (bytes, bytearray)):
        return None
    for name, magic in METADATA_FILE_SIGNATURES.items():
        if file_content.startswith(magic):
            return name
    return None
