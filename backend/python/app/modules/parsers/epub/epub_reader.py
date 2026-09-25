"""Read an EPUB book into one HTML document for the HTML parser.

An EPUB is a zip of XHTML pages. ``META-INF/container.xml`` names the OPF
package file; its manifest lists every file and its spine gives the reading
order. The chapters' bodies are joined, in spine order, into a single HTML
document, with the book's own images inlined as ``data:`` URIs, so the output
goes through exactly the same HTML-to-blocks path as an uploaded HTML file.

The zip is untrusted input and is only ever read in memory.
"""

from __future__ import annotations

import base64
import codecs
import html
import io
import logging
import mimetypes
import posixpath
import re
import struct
import zipfile
import zlib
from dataclasses import dataclass, field
from urllib.parse import unquote, urldefrag, urlsplit

from bs4 import BeautifulSoup, Tag
from bs4.dammit import EncodingDetector
from lxml import etree

from app.modules.parsers.image_parser.image_parser import ImageParser
from app.services.parsing.interface import ParseError, ParseErrorCode
from app.utils.user_errors import (
    EPUB_COPY_PROTECTED,
    EPUB_NO_READABLE_CHAPTERS,
    EPUB_TOO_LARGE,
    EPUB_UNREADABLE,
    EPUB_UNSAFE_PATHS,
)

logger = logging.getLogger(__name__)

MAX_ENTRIES = 10_000
# Checked twice: against the sizes the zip declares, before anything is read,
# and against the bytes actually inflated, which a crafted zip can make larger.
MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
# Chapter markup is parsed several times on the way to blocks, so it gets a
# much smaller budget than the book as a whole.
MAX_CHAPTER_BYTES = 64 * 1024 * 1024
MAX_PACKAGE_FILE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_EMBEDDED_IMAGE_BYTES = 50 * 1024 * 1024
# 4096 x 4096. More than a full-page illustration needs (an A4 page at 300 dpi
# is about 8.7 megapixels), and it keeps the RGBA bitmap cairo allocates while
# rasterising at 64 MB.
MAX_SVG_PIXELS = 16 * 1024 * 1024
# ImageParser.svg_base64_to_png_base64 rasterises an SVG without a size at this.
_SVG_DEFAULT_SIZE = (800, 600)
_INFLATE_CHUNK = 64 * 1024
_LOCAL_HEADER = struct.Struct("<4s2B4HL2L2H")
_LOCAL_HEADER_SIGNATURE = b"PK\x03\x04"

CONTAINER_PATH = "META-INF/container.xml"
ENCRYPTION_PATH = "META-INF/encryption.xml"
_OPF_MEDIA_TYPE = "application/oebps-package+xml"
_CHAPTER_MEDIA_TYPES = frozenset({"application/xhtml+xml", "text/html", "application/html"})
# Font obfuscation, not DRM: only fonts are scrambled and every reader undoes it.
_FONT_OBFUSCATION_ALGORITHMS = frozenset({
    "http://www.idpf.org/2008/embedding",
    "http://ns.adobe.com/pdf/enc#RC",
})
# The raster types the HTML image path keeps (ImageParser.VALID_IMAGE_EXTENSIONS);
# SVG is converted to PNG the same way that path does.
_EMBEDDABLE_IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
_SVG_TYPE = "image/svg+xml"
_MAX_FALLBACK_HOPS = 8

_BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF8, "utf-8"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)
# Labels browsers read as Windows-1252: books tagged "iso-8859-1" routinely
# use its curly quotes and dashes, which Latin-1 turns into control characters.
_WINDOWS_1252_ALIASES = frozenset(codecs.lookup(name).name for name in ("latin-1", "ascii", "cp1252"))
# These can be pure ASCII bytes, so a clean UTF-8 decode proves nothing about them.
_STATEFUL_CODECS = frozenset(
    codecs.lookup(name).name for name in ("iso2022_jp", "iso2022_jp_2", "iso2022_kr", "hz", "utf-7")
)
_DRIVE_LETTER = re.compile(r"^[A-Za-z]:")


@dataclass
class EpubMetadata:
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    language: str | None = None


@dataclass
class EpubBook:
    metadata: EpubMetadata
    chapter_bodies: list[str]
    version: str | None = None

    def to_html(self) -> str:
        title = self.metadata.title
        lang = f' lang="{html.escape(self.metadata.language, quote=True)}"' if self.metadata.language else ""
        head_title = f"<title>{html.escape(title)}</title>" if title else ""
        sections = "".join(f"<section>{body}</section>" for body in self.chapter_bodies)
        return (
            f'<!DOCTYPE html><html{lang}><head><meta charset="utf-8">{head_title}</head>'
            f"<body>{sections}</body></html>"
        )


def _fail(code: ParseErrorCode, message: str, **details: object) -> ParseError:
    return ParseError(code, message, details={k: v for k, v in details.items() if v is not None})


def _local_name(tag: object) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def _is_unsafe_name(name: str) -> bool:
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or _DRIVE_LETTER.match(normalized):
        return True
    return ".." in normalized.split("/")


def _resolve_href(base_path: str, href: str) -> str | None:
    """Zip path that *href* (relative to the file at *base_path*) points to, or
    None when it is external, empty or would leave the book."""
    href = urldefrag(href.strip()).url
    if not href:
        return None
    parts = urlsplit(href)
    if parts.scheme or parts.netloc or href.startswith("/"):
        return None
    joined = posixpath.join(posixpath.dirname(base_path), unquote(parts.path))
    resolved = posixpath.normpath(joined)
    if resolved in (".", "", "..") or resolved.startswith(("../", "/")):
        return None
    return resolved


def _parse_xml(data: bytes) -> etree._Element | None:
    parser = etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False, recover=True,
    )
    try:
        root = etree.fromstring(data, parser)
    except (etree.XMLSyntaxError, ValueError):
        return None
    return root


def _declared_codec(raw: bytes) -> str | None:
    label = EncodingDetector.find_declared_encoding(raw, is_html=True)
    if not label:
        return None
    try:
        codec = codecs.lookup(label.strip().lower()).name
    except LookupError:
        return None
    return codecs.lookup("cp1252").name if codec in _WINDOWS_1252_ALIASES else codec


def _decode_markup(raw: bytes) -> str:
    """Byte-order mark, then a stateful declared charset, then UTF-8 (what EPUB
    requires), then any other declared charset, then Windows-1252."""
    for bom, encoding in _BOMS:
        if raw.startswith(bom):
            return raw[len(bom):].decode(encoding, errors="replace")
    declared = _declared_codec(raw)
    candidates = [declared] if declared in _STATEFUL_CODECS else []
    candidates.append("utf-8")
    if declared and declared not in candidates:
        candidates.append(declared)
    for encoding in candidates:
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("cp1252", errors="replace")


class _EpubArchive:
    """The zip, with every read checked against the book's limits."""

    def __init__(self, content: bytes) -> None:
        if not content:
            raise _fail(ParseErrorCode.PARSE_FAILED, EPUB_UNREADABLE, reason="empty file")
        self._content = memoryview(content)
        try:
            infos = zipfile.ZipFile(io.BytesIO(content)).infolist()
        except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError, EOFError) as exc:
            raise _fail(ParseErrorCode.PARSE_FAILED, EPUB_UNREADABLE, reason=f"not a zip archive: {exc}") from exc

        if len(infos) > MAX_ENTRIES:
            raise _fail(ParseErrorCode.INVALID_INPUT, EPUB_TOO_LARGE, entries=len(infos), max_entries=MAX_ENTRIES)
        declared_total = sum(info.file_size for info in infos)
        if declared_total > MAX_UNCOMPRESSED_BYTES:
            raise _fail(
                ParseErrorCode.INVALID_INPUT, EPUB_TOO_LARGE,
                uncompressed_bytes=declared_total, max_uncompressed_bytes=MAX_UNCOMPRESSED_BYTES,
            )
        unsafe = [info.filename for info in infos if _is_unsafe_name(info.filename)]
        if unsafe:
            raise _fail(ParseErrorCode.INVALID_INPUT, EPUB_UNSAFE_PATHS, first_unsafe_entry=unsafe[0][:200])

        self._infos = {info.filename: info for info in infos if not info.is_dir()}
        self._by_lower = {name.lower(): name for name in self._infos}
        self.chapter_bytes_read = 0
        self.bytes_inflated = 0

    def find(self, path: str) -> zipfile.ZipInfo | None:
        info = self._infos.get(path)
        if info is None:
            # Books made on case-insensitive file systems often get the case wrong.
            name = self._by_lower.get(path.lower())
            info = self._infos.get(name) if name else None
        return info

    def read(self, info: zipfile.ZipInfo) -> bytes:
        if info.flag_bits & 0x1:
            raise _fail(ParseErrorCode.PARSE_FAILED, EPUB_COPY_PROTECTED, reason="password-protected zip entry")
        try:
            data = self._inflate(info)
        except MemoryError as exc:
            raise _fail(ParseErrorCode.INVALID_INPUT, EPUB_TOO_LARGE, entry=info.filename[:200]) from exc
        except (zlib.error, struct.error, ValueError) as exc:
            raise _fail(
                ParseErrorCode.PARSE_FAILED, EPUB_UNREADABLE,
                reason=f"could not read {info.filename[:200]}: {exc}",
            ) from exc
        if len(data) != info.file_size or zlib.crc32(data) != info.CRC:
            raise _fail(ParseErrorCode.PARSE_FAILED, EPUB_UNREADABLE, reason=f"{info.filename[:200]} is damaged")
        return data

    def _compressed(self, info: zipfile.ZipInfo) -> memoryview:
        header = _LOCAL_HEADER.unpack_from(self._content, info.header_offset)
        if header[0] != _LOCAL_HEADER_SIGNATURE:
            raise ValueError("bad local file header")
        start = info.header_offset + _LOCAL_HEADER.size + header[10] + header[11]
        if start + info.compress_size > len(self._content):
            raise ValueError("entry runs past the end of the file")
        return self._content[start:start + info.compress_size]

    def _inflate(self, info: zipfile.ZipInfo) -> bytes:
        """The entry's bytes, inflated a bounded chunk at a time.

        zipfile's own reader inflates as much as the stream holds before
        trimming to the declared size, so an entry that under-declares its
        size could still expand to gigabytes in memory. Here nothing is kept
        past the entry's declared size or the book's total.
        """
        compressed = self._compressed(info)
        if info.compress_type == zipfile.ZIP_STORED:
            self._count(info, len(compressed), len(compressed))
            return bytes(compressed)
        if info.compress_type != zipfile.ZIP_DEFLATED:
            # The EPUB container format allows only stored and deflated entries.
            raise ValueError(f"compression method {info.compress_type} is not allowed in EPUB")
        inflater = zlib.decompressobj(-zlib.MAX_WBITS)
        out = bytearray()
        for start in range(0, len(compressed), _INFLATE_CHUNK):
            pending = compressed[start:start + _INFLATE_CHUNK]
            while pending and not inflater.eof:
                piece = inflater.decompress(pending, _INFLATE_CHUNK)
                self._count(info, len(out) + len(piece), len(piece))
                out += piece
                pending = inflater.unconsumed_tail
            if inflater.eof:
                break
        while not inflater.eof:
            piece = inflater.decompress(b"", _INFLATE_CHUNK)
            if not piece:
                break
            self._count(info, len(out) + len(piece), len(piece))
            out += piece
        return bytes(out)

    def _count(self, info: zipfile.ZipInfo, entry_bytes: int, new_bytes: int) -> None:
        self.bytes_inflated += new_bytes
        if entry_bytes > info.file_size or self.bytes_inflated > MAX_UNCOMPRESSED_BYTES:
            raise _fail(
                ParseErrorCode.INVALID_INPUT, EPUB_TOO_LARGE,
                entry=info.filename[:200], declared_bytes=info.file_size,
            )

    def read_package_file(self, path: str) -> bytes | None:
        info = self.find(path)
        if info is None:
            return None
        if info.file_size > MAX_PACKAGE_FILE_BYTES:
            raise _fail(ParseErrorCode.INVALID_INPUT, EPUB_TOO_LARGE, oversized_entry=path[:200])
        return self.read(info)

    def read_chapter(self, info: zipfile.ZipInfo) -> bytes:
        self.chapter_bytes_read += info.file_size
        if self.chapter_bytes_read > MAX_CHAPTER_BYTES:
            raise _fail(
                ParseErrorCode.INVALID_INPUT, EPUB_TOO_LARGE,
                chapter_bytes=self.chapter_bytes_read, max_chapter_bytes=MAX_CHAPTER_BYTES,
            )
        return self.read(info)


@dataclass
class _ManifestItem:
    path: str
    media_type: str
    fallback: str | None
    properties: frozenset[str] = frozenset()


def _find_opf_path(archive: _EpubArchive) -> str:
    container = archive.read_package_file(CONTAINER_PATH)
    if container is None:
        raise _fail(ParseErrorCode.PARSE_FAILED, EPUB_UNREADABLE, reason="no META-INF/container.xml")
    root = _parse_xml(container)
    if root is None:
        raise _fail(ParseErrorCode.PARSE_FAILED, EPUB_UNREADABLE, reason="container.xml is not XML")
    rootfiles = [el for el in root.iter() if _local_name(el.tag) == "rootfile" and el.get("full-path")]
    # Prefer the OPF when a container also lists other renditions (e.g. a PDF).
    rootfiles.sort(key=lambda el: (el.get("media-type") or _OPF_MEDIA_TYPE) != _OPF_MEDIA_TYPE)
    for rootfile in rootfiles:
        path = _resolve_href("", rootfile.get("full-path", ""))
        if path:
            return path
    raise _fail(ParseErrorCode.PARSE_FAILED, EPUB_UNREADABLE, reason="container.xml names no package file")


def _encrypted_paths(archive: _EpubArchive) -> set[str]:
    data = archive.read_package_file(ENCRYPTION_PATH)
    if data is None:
        return set()
    root = _parse_xml(data)
    if root is None:
        raise _fail(ParseErrorCode.PARSE_FAILED, EPUB_UNREADABLE, reason="encryption.xml is not XML")
    paths: set[str] = set()
    for encrypted in root.iter():
        if _local_name(encrypted.tag) != "encrypteddata":
            continue
        algorithm = next(
            (el.get("Algorithm", "") for el in encrypted.iter() if _local_name(el.tag) == "encryptionmethod"),
            "",
        )
        if algorithm in _FONT_OBFUSCATION_ALGORITHMS:
            continue
        for ref in encrypted.iter():
            if _local_name(ref.tag) == "cipherreference" and ref.get("URI"):
                # URIs in encryption.xml are relative to the root of the container.
                path = _resolve_href("", ref.get("URI", ""))
                if path:
                    paths.add(path.lower())
    return paths


def _text_of(elements: list[etree._Element]) -> list[str]:
    values = []
    for el in elements:
        text = " ".join("".join(el.itertext()).split())
        if text:
            values.append(text)
    return values


@dataclass
class _Package:
    metadata: EpubMetadata
    manifest: dict[str, _ManifestItem]
    chapters: list[_ManifestItem]
    version: str | None


def _read_package(archive: _EpubArchive, opf_path: str) -> _Package:
    data = archive.read_package_file(opf_path)
    if data is None:
        raise _fail(ParseErrorCode.PARSE_FAILED, EPUB_UNREADABLE, reason=f"package file {opf_path[:200]} is missing")
    root = _parse_xml(data)
    if root is None or _local_name(root.tag) != "package":
        raise _fail(ParseErrorCode.PARSE_FAILED, EPUB_UNREADABLE, reason="package file is not an OPF document")

    sections = {_local_name(child.tag): child for child in root if isinstance(child.tag, str)}
    metadata_el = sections.get("metadata")
    meta_children = list(metadata_el.iter()) if metadata_el is not None else []
    # EPUB 2 books may wrap Dublin Core fields in <dc-metadata>, so search the whole subtree.
    titles = _text_of([el for el in meta_children if _local_name(el.tag) == "title"])
    authors = _text_of([el for el in meta_children if _local_name(el.tag) == "creator"])
    languages = _text_of([el for el in meta_children if _local_name(el.tag) == "language"])
    metadata = EpubMetadata(
        title=titles[0] if titles else None,
        authors=list(dict.fromkeys(authors)),
        language=languages[0] if languages else None,
    )

    manifest: dict[str, _ManifestItem] = {}
    manifest_order: list[str] = []
    manifest_el = sections.get("manifest")
    for item in manifest_el if manifest_el is not None else []:
        if _local_name(item.tag) != "item" or not item.get("id") or not item.get("href"):
            continue
        path = _resolve_href(opf_path, item.get("href", ""))
        if path is None:
            continue
        manifest[item.get("id", "")] = _ManifestItem(
            path=path,
            media_type=(item.get("media-type") or "").strip().lower(),
            fallback=item.get("fallback"),
            properties=frozenset((item.get("properties") or "").split()),
        )
        manifest_order.append(item.get("id", ""))

    spine_el = sections.get("spine")
    spine_ids = [
        ref.get("idref", "")
        for ref in (spine_el if spine_el is not None else [])
        if _local_name(ref.tag) == "itemref" and ref.get("idref")
    ]
    if not spine_ids:
        # A missing spine is a broken book, but its pages are still in the manifest.
        spine_ids = [item_id for item_id in manifest_order if manifest[item_id].media_type in _CHAPTER_MEDIA_TYPES]

    # Items marked linear="no" (footnotes, answers, pop-ups) stay where the
    # spine puts them: they are content, and in place they sit next to the
    # chapters that link to them.
    chapters: list[_ManifestItem] = []
    seen: set[str] = set()
    for item_id in spine_ids:
        item = _chapter_item(manifest, item_id)
        if item is not None and item.path not in seen:
            seen.add(item.path)
            chapters.append(item)
    return _Package(metadata=metadata, manifest=manifest, chapters=chapters, version=root.get("version"))


def _chapter_item(manifest: dict[str, _ManifestItem], item_id: str) -> _ManifestItem | None:
    """The XHTML item for a spine entry, following EPUB fallback chains.
    The EPUB 3 navigation document is a table of contents, not a chapter."""
    for _ in range(_MAX_FALLBACK_HOPS):
        item = manifest.get(item_id)
        if item is None or "nav" in item.properties:
            return None
        if item.media_type in _CHAPTER_MEDIA_TYPES or (
            not item.media_type and item.path.lower().endswith((".xhtml", ".html", ".htm"))
        ):
            return item
        if not item.fallback:
            return None
        item_id = item.fallback
    return None


class _ImageEmbedder:
    def __init__(self, archive: _EpubArchive, manifest_types: dict[str, str], encrypted: set[str]) -> None:
        self._archive = archive
        self._manifest_types = manifest_types
        self._encrypted = encrypted
        self._cache: dict[str, str | None] = {}
        self.embedded_bytes = 0
        self.skipped = 0

    def data_uri(self, path: str) -> str | None:
        if path in self._cache:
            return self._cache[path]
        uri = self._load(path)
        self._cache[path] = uri
        if uri is None:
            self.skipped += 1
        return uri

    def _load(self, path: str) -> str | None:
        info = self._archive.find(path)
        if info is None or path.lower() in self._encrypted or info.flag_bits & 0x1:
            return None
        media_type = self._manifest_types.get(info.filename) or (mimetypes.guess_type(info.filename)[0] or "")
        media_type = "image/jpeg" if media_type == "image/jpg" else media_type
        if media_type not in _EMBEDDABLE_IMAGE_TYPES and media_type != _SVG_TYPE:
            return None
        if info.file_size > MAX_IMAGE_BYTES or self.embedded_bytes + info.file_size > MAX_EMBEDDED_IMAGE_BYTES:
            return None
        try:
            data = self._archive.read(info)
        except ParseError:
            return None
        if not data:
            return None
        if media_type == _SVG_TYPE:
            return self._svg_as_png(data, path)
        self.embedded_bytes += len(data)
        return f"data:{media_type};base64,{base64.b64encode(data).decode('ascii')}"

    def _svg_as_png(self, data: bytes, path: str) -> str | None:
        try:
            svg_text = data.decode("utf-8")
        except UnicodeDecodeError:
            return None
        width, height = ImageParser._extract_svg_dimensions(svg_text)
        width, height = width or _SVG_DEFAULT_SIZE[0], height or _SVG_DEFAULT_SIZE[1]
        if width * height > MAX_SVG_PIXELS:
            logger.info("Skipping SVG image %s: %dx%d is too large to rasterise", path[:200], width, height)
            return None
        try:
            png = ImageParser.svg_base64_to_png_base64(base64.b64encode(data).decode("ascii"))
        except Exception:
            # An SVG that cannot be rendered is skipped, as the HTML path skips it.
            logger.debug("Skipping SVG image %s that could not be converted", path[:200], exc_info=True)
            return None
        png_bytes = len(png) * 3 // 4
        if png_bytes > MAX_IMAGE_BYTES or self.embedded_bytes + png_bytes > MAX_EMBEDDED_IMAGE_BYTES:
            return None
        self.embedded_bytes += png_bytes
        return f"data:image/png;base64,{png}"


def _svg_image_href(svg: Tag) -> str | None:
    for image in svg.find_all("image"):
        href = image.get("xlink:href") or image.get("href")
        if href:
            return href
    return None


def _chapter_body(markup: str, chapter_path: str, images: _ImageEmbedder) -> tuple[str, bool]:
    """The chapter's body markup with its images inlined, and whether it has
    any text or pictures at all."""
    soup = BeautifulSoup(markup, "html.parser")
    body = soup.body or soup
    # The HTML cleaner deletes <header> and <footer> as page chrome, but in a
    # chapter they hold its title and its notes.
    for element in body.find_all(["header", "footer"]):
        element.name = "div"
    # Cover pages usually draw their picture as <svg><image href=…/></svg>,
    # which the HTML parser skips; an <img> keeps the picture.
    for svg in body.find_all("svg"):
        href = _svg_image_href(svg)
        if href:
            svg.replace_with(soup.new_tag("img", attrs={"src": href, "alt": svg.get("aria-label", "")}))
    for img in body.find_all("img"):
        src = (img.get("src") or "").strip()
        del img["srcset"]
        if src.startswith("data:"):
            continue
        path = _resolve_href(chapter_path, src) if src else None
        # Pictures outside the book are not fetched: the book is untrusted and
        # EPUB requires its images to be packaged inside it.
        uri = images.data_uri(path) if path else None
        if uri is None:
            img.decompose()
        else:
            img["src"] = uri
    has_content = bool(body.get_text(strip=True)) or body.find("img") is not None
    return "".join(str(child) for child in body.contents), has_content


def read_epub(content: bytes) -> EpubBook:
    """Read *content* as an EPUB 2 or EPUB 3 book.

    Raises:
        ParseError: The file is not a readable EPUB, is copy-protected, breaks
            one of the size or path limits, or has no readable chapters. The
            message is written for the person who uploaded the book.
    """
    try:
        return _read_book(content)
    except MemoryError as exc:
        raise _fail(ParseErrorCode.INVALID_INPUT, EPUB_TOO_LARGE, reason="ran out of memory") from exc


def _read_book(content: bytes) -> EpubBook:
    archive = _EpubArchive(content)
    opf_path = _find_opf_path(archive)
    encrypted = _encrypted_paths(archive)
    package = _read_package(archive, opf_path)

    protected = [item.path for item in package.chapters if item.path.lower() in encrypted]
    if protected:
        raise _fail(ParseErrorCode.PARSE_FAILED, EPUB_COPY_PROTECTED, first_encrypted_chapter=protected[0][:200])

    manifest_types = {}
    for item in package.manifest.values():
        info = archive.find(item.path)
        if info is not None and item.media_type:
            manifest_types[info.filename] = item.media_type
    images = _ImageEmbedder(archive, manifest_types, encrypted)

    bodies: list[str] = []
    has_content = False
    for item in package.chapters:
        info = archive.find(item.path)
        if info is None:
            logger.info("EPUB spine lists %s, which is not in the book; skipping it", item.path[:200])
            continue
        body, chapter_has_content = _chapter_body(_decode_markup(archive.read_chapter(info)), info.filename, images)
        bodies.append(body)
        has_content = has_content or chapter_has_content

    if not has_content:
        raise _fail(ParseErrorCode.PARSE_FAILED, EPUB_NO_READABLE_CHAPTERS, spine_items=len(package.chapters))
    if images.skipped:
        logger.info("EPUB: %d image(s) could not be embedded and were left out", images.skipped)
    return EpubBook(metadata=package.metadata, chapter_bodies=bodies, version=package.version)


__all__ = ["EpubBook", "EpubMetadata", "read_epub"]
