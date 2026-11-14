"""A macro-enabled Word document or an Office template, rewritten as the plain package it holds.

python-docx opens only a ``…wordprocessingml.document.main+xml`` main part and python-pptx only a
presentation or macro-enabled presentation one, so a .docm, .dotx or .potx is refused although its
content is the same markup. The rewrite changes the main part's content type, drops the VBA
project, and copies every other member as it was. Macros are never run, and the rewritten
package is only handed to the parser, never stored.

The package is untrusted: members are checked before any is read (duplicate or unsafe names,
declared sizes), and the three XML parts read are parsed without entities, DTDs or network.
"""
from __future__ import annotations

import io
import posixpath
import zipfile
from typing import TYPE_CHECKING, Literal

from lxml import etree

from app.services.parsing.interface import UnreadableFileError, damaged_file_reason

if TYPE_CHECKING:
    from collections.abc import Iterable

Family = Literal["word", "slides"]

_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_OFFICE_DOCUMENT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
_VBA_PROJECT_REL = "http://schemas.microsoft.com/office/2006/relationships/vbaProject"
_CONTENT_TYPES = "[Content_Types].xml"
_ROOT_RELS = "_rels/.rels"
_XML_PART_MAX_BYTES = 1024 * 1024

_WORD_MAIN = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
_SLIDES_MAIN = "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"
_PLAIN_MAIN: dict[Family, str] = {"word": _WORD_MAIN, "slides": _SLIDES_MAIN}
# The main-part content types each family is rewritten from. A package that is already plain
# (or a macro-enabled deck, which python-pptx opens) passes through with its content type.
_REWRITTEN: dict[Family, frozenset[str]] = {
    "word": frozenset({
        "application/vnd.ms-word.document.macroEnabled.main+xml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml",
        "application/vnd.ms-word.template.macroEnabledTemplate.main+xml",
    }),
    "slides": frozenset({
        "application/vnd.openxmlformats-officedocument.presentationml.template.main+xml",
        "application/vnd.ms-powerpoint.template.macroEnabled.main+xml",
    }),
}
_ACCEPTED: dict[Family, frozenset[str]] = {
    "word": _REWRITTEN["word"] | {_WORD_MAIN},
    "slides": _REWRITTEN["slides"] | {
        _SLIDES_MAIN, "application/vnd.ms-powerpoint.presentation.macroEnabled.main+xml",
    },
}
_VBA_NAMES = frozenset({"vbaproject.bin", "vbadata.xml"})


def _parser() -> etree.XMLParser:
    return etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)


def _unsafe(name: str) -> bool:
    if not name or name.startswith("/") or "\\" in name or (len(name) > 1 and name[1] == ":"):
        return True
    return any(part == ".." for part in name.split("/"))


def _is_vba(name: str) -> bool:
    base = posixpath.basename(name).lower()
    if base in _VBA_NAMES:
        return True
    return base == "vbaproject.bin.rels" and posixpath.basename(posixpath.dirname(name)) == "_rels"


def _check_members(archive: zipfile.ZipFile, damaged: UnreadableFileError) -> None:
    from docling.backend.utils.archive_limits import (
        ArchiveLimitError,
        ArchiveLimits,
        check_archive_limits,
    )
    from docling.datamodel.backend_options import MsWordBackendOptions

    names = [info.filename for info in archive.infolist()]
    if len(set(names)) != len(names) or any(_unsafe(n) for n in names):
        raise damaged
    try:
        check_archive_limits(archive, ArchiveLimits.from_options(MsWordBackendOptions()))
    except ArchiveLimitError as exc:
        raise damaged from exc


def _read_xml(archive: zipfile.ZipFile, name: str, damaged: UnreadableFileError) -> etree._Element:
    from docling.backend.utils.archive_limits import ArchiveLimitError, read_member

    try:
        data = read_member(archive, name, _XML_PART_MAX_BYTES)
        return etree.fromstring(data, _parser())
    except (KeyError, ArchiveLimitError, etree.XMLSyntaxError, zipfile.BadZipFile, ValueError) as exc:
        raise damaged from exc


def _rels_part(part: str) -> str:
    folder, base = posixpath.split(part)
    return posixpath.join(folder, "_rels", f"{base}.rels")


def _main_part(root_rels: etree._Element, damaged: UnreadableFileError) -> str:
    for rel in root_rels.iter(f"{{{_REL}}}Relationship"):
        if rel.get("Type") == _OFFICE_DOCUMENT and rel.get("TargetMode") != "External":
            target = (rel.get("Target") or "").lstrip("/")
            if target and not _unsafe(target):
                return posixpath.normpath(target)
    raise damaged


def _drop_vba_relationships(rels: etree._Element) -> None:
    for rel in list(rels.iter(f"{{{_REL}}}Relationship")):
        if rel.get("Type") == _VBA_PROJECT_REL:
            rels.remove(rel)


def _serialize(element: etree._Element) -> bytes:
    return etree.tostring(element, xml_declaration=True, encoding="UTF-8", standalone=True)


def _rewrite_content_types(
    types: etree._Element, main: str, family: Family, dropped: Iterable[str], damaged: UnreadableFileError
) -> None:
    dropped_parts = {f"/{name}" for name in dropped}
    main_override = None
    for override in list(types.iter(f"{{{_CT}}}Override")):
        part = override.get("PartName") or ""
        if part in dropped_parts:
            types.remove(override)
        elif part.lstrip("/") == main:
            main_override = override
    if main_override is None:
        raise damaged
    content_type = main_override.get("ContentType") or ""
    if content_type not in _ACCEPTED[family]:
        raise damaged
    if content_type in _REWRITTEN[family]:
        main_override.set("ContentType", _PLAIN_MAIN[family])


def as_plain_package(content: bytes, family: Family, *, label: str) -> bytes:
    """*content* (a .docm, .dotx or .potx) as a plain .docx or .pptx package.

    Raises ``UnreadableFileError`` when it is not a zip, has duplicate or unsafe member names,
    exceeds the declared-size limits, or has no main part of the expected family.
    """
    damaged = UnreadableFileError(label, damaged_file_reason(label))
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except (zipfile.BadZipFile, ValueError, OSError) as exc:
        raise damaged from exc
    with archive:
        _check_members(archive, damaged)
        types = _read_xml(archive, _CONTENT_TYPES, damaged)
        main = _main_part(_read_xml(archive, _ROOT_RELS, damaged), damaged)
        main_rels_name = _rels_part(main)
        names = {info.filename for info in archive.infolist()}
        main_rels = _read_xml(archive, main_rels_name, damaged) if main_rels_name in names else None
        dropped = [n for n in names if _is_vba(n)]
        _rewrite_content_types(types, main, family, dropped, damaged)
        if main_rels is not None:
            _drop_vba_relationships(main_rels)

        out = io.BytesIO()
        try:
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as rewritten:
                rewritten.writestr(archive.getinfo(_CONTENT_TYPES), _serialize(types))
                for info in archive.infolist():
                    if info.filename == _CONTENT_TYPES or info.filename in dropped:
                        continue
                    if main_rels is not None and info.filename == main_rels_name:
                        rewritten.writestr(info, _serialize(main_rels))
                    else:
                        rewritten.writestr(info, archive.read(info))
        except (zipfile.BadZipFile, OSError, EOFError, ValueError) as exc:
            # A member whose data does not match its directory entry (bad CRC, truncated).
            raise damaged from exc
    return out.getvalue()
