"""Rebuild an Office package that LibreOffice refuses to open.

LibreOffice checks the zip container of a .docx, .pptx or .xlsx strictly: a
duplicate entry, a local header that disagrees with the central directory,
stray bytes between entries, or a root relationship to a part that is not there
make it refuse the whole file. PowerPoint and the Python readers used for
indexing accept such files, so a deck can be indexed and still have no preview.
"""

from __future__ import annotations

import contextlib
import logging
import posixpath
import zipfile
import zlib
from typing import TYPE_CHECKING

from lxml import etree

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

logger = logging.getLogger(__name__)

# The file comes from a user or an outside source: bound what it can inflate to.
MAX_ENTRIES = 10_000
MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
_MAX_RELATIONSHIPS_BYTES = 1024 * 1024
# zipfile loads the whole directory before anything can be counted.
_MAX_DIRECTORY_BYTES = 4 * 1024 * 1024
_OPC_COMPRESSION = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})
_READ_CHUNK = 1024 * 1024
_ENCRYPTED_ENTRY = 0x1

_CONTENT_TYPES = "[Content_Types].xml"
_ROOT_RELS = "_rels/.rels"
_RELATIONSHIP = "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"


class _NotRepairableError(Exception):
    """The file is not a package this module can rebuild."""


def _part_name(entry: str) -> str:
    name = entry.replace("\\", "/").lstrip("/")
    if not name or "\x00" in name or ".." in name.split("/"):
        raise _NotRepairableError("unsafe entry name")
    return name


def _check_directory(source: Path) -> None:
    """Refuse an oversized directory. Asks zipfile's own reader, so that both see the same one:
    the public API loads the directory first."""
    with source.open("rb") as handle:
        end = zipfile._EndRecData(handle)
    if not end or end[zipfile._ECD_SIZE] > _MAX_DIRECTORY_BYTES:
        raise _NotRepairableError("no directory, or one over the size limit")


def _parts(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    """Each part once. Part names are case-insensitive; a repeated entry's last
    copy is the one zip readers use."""
    infos = archive.infolist()
    if len(infos) > MAX_ENTRIES or sum(i.file_size for i in infos) > MAX_UNCOMPRESSED_BYTES:
        raise _NotRepairableError("over the size limits")
    parts: dict[str, zipfile.ZipInfo] = {}
    names: dict[str, str] = {}
    for info in infos:
        if info.is_dir():
            continue
        if info.flag_bits & _ENCRYPTED_ENTRY:
            raise _NotRepairableError("encrypted entry")
        if info.compress_type not in _OPC_COMPRESSION:
            raise _NotRepairableError("compression an Office package does not use")
        name = _part_name(info.filename)
        if names.setdefault(name.lower(), name) == name:
            parts[name] = info
    if _CONTENT_TYPES not in parts or _ROOT_RELS not in parts:
        raise _NotRepairableError("not an Office package")
    return parts


def _root_relationships(archive: zipfile.ZipFile, parts: dict[str, zipfile.ZipInfo]) -> bytes:
    """The package's root relationships without those to parts that are missing."""
    info = parts[_ROOT_RELS]
    # A sized read: the declared size is the file's own claim, and an unsized
    # read inflates everything before it is cut to that claim.
    with archive.open(info) as reader:
        content = reader.read(_MAX_RELATIONSHIPS_BYTES + 1)
    if len(content) > _MAX_RELATIONSHIPS_BYTES:
        raise _NotRepairableError("root relationships too large")
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)
    root = etree.fromstring(content, parser)
    present = {name.lower() for name in parts}
    for relationship in list(root.iter(_RELATIONSHIP)):
        if relationship.get("TargetMode") == "External":
            continue
        target = posixpath.normpath((relationship.get("Target") or "").lstrip("/"))
        if target.lower() not in present:
            root.remove(relationship)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _rebuild(source: Path, target: Path, stopped: Callable[[], bool]) -> None:
    _check_directory(source)
    inflated = 0
    with zipfile.ZipFile(source) as archive:
        parts = _parts(archive)
        relationships = _root_relationships(archive, parts)
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as rebuilt:
            for name, info in parts.items():
                if name == _ROOT_RELS:
                    rebuilt.writestr(name, relationships)
                    continue
                with archive.open(info) as reader, rebuilt.open(name, "w", force_zip64=False) as writer:
                    while chunk := reader.read(_READ_CHUNK):
                        if stopped():
                            raise _NotRepairableError("stopped")
                        inflated += len(chunk)
                        if inflated > MAX_UNCOMPRESSED_BYTES:
                            raise _NotRepairableError("over the size limits")
                        writer.write(chunk)


def rebuild_package(source: Path, target: Path, stopped: Callable[[], bool] = lambda: False) -> bool:
    """Write *source* to *target* as a clean package; False when it cannot be.

    Every part's bytes are kept (their CRC is checked on the way), except root
    relationships to missing parts. Blocking: call it in a thread; it gives up soon
    after *stopped* answers True.
    """
    try:
        _rebuild(source, target, stopped)
    except (
        _NotRepairableError, zipfile.BadZipFile, zipfile.LargeZipFile, zlib.error, etree.LxmlError,
        OSError, EOFError, ValueError, RuntimeError, NotImplementedError,
    ) as exc:
        # Not the message: it can quote the file.
        logger.info("Office package not rebuilt (%s)", type(exc).__name__)
        with contextlib.suppress(OSError):
            target.unlink(missing_ok=True)
        return False
    return True
