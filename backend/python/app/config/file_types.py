"""The one table of file types PipesHub knows, and every list derived from it.

Each consumer that decides something from an extension or a mime type (the indexing gate, the
parsing-service format key, the parse tiers and kinds, the mime-to-extension hints, the password
check, the Docling file name, the sync filter, the Node upload table) reads a list derived here
instead of keeping its own copy.

The module imports nothing from the app, so any service can import it; ``arangodb`` re-exports
from it. ``python -m app.config.file_types --emit node`` writes the Node upload table.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

REGISTRY_VERSION: Final = 2

Tier = Literal["heavy", "light"]
MimeRole = Literal["gate", "parse", "tier", "kind", "ext_hint", "code", "reconcile", "ooxml"]


class Route(StrEnum):
    """The legacy (in-process) dispatch branch a type is parsed by."""

    GOOGLE_SLIDES = "google_slides"
    GOOGLE_DOCS = "google_docs"
    GOOGLE_SHEETS = "google_sheets"
    GMAIL = "gmail"
    BLOCKS = "blocks"
    PDF = "pdf"
    EPUB = "epub"
    DOCX = "docx"
    DOC = "doc"
    XLSX = "xlsx"
    XLS = "xls"
    CSV = "csv"
    TSV = "tsv"
    HTML = "html"
    PPTX = "pptx"
    PPT = "ppt"
    MD = "md"
    MDX = "mdx"
    TXT = "txt"
    SQL_TABLE = "sql_table"
    SQL_VIEW = "sql_view"
    JSON = "json"
    YAML = "yaml"
    IMAGE = "image"
    CODE = "code"
    LIBREOFFICE_DOCX = "libreoffice_docx"
    MSG = "msg"
    PLAIN_TEXT = "plain_text"


class Prep(StrEnum):
    """What the indexer does to a file's bytes before either pipeline parses them."""

    NONE = "none"
    OOXML_WORD = "ooxml_word"  # macro-enabled or template Word package, rewritten as a plain .docx
    OOXML_SLIDES = "ooxml_slides"  # PowerPoint template, rewritten as a plain .pptx
    CAPPED_TEXT = "capped_text"  # text kept to its head and tail, decoded to UTF-8


class FilterRole(StrEnum):
    OFFER = "offer"  # offered by the extension filter; the FILE gate accepts the extension
    OFFER_LEGACY = "offer_legacy"  # offered, but the FILE gate refuses the extension
    EXCLUDE_ONLY = "exclude_only"  # not indexable; offered so it can be excluded
    HIDDEN = "hidden"


@dataclass(frozen=True, slots=True, kw_only=True)
class FileType:
    key: str
    format: str | None = None  # parsing-service format key, for the extensions and the "parse" mimes
    extensions: tuple[str, ...] = ()  # lower case, no dot
    mimes: tuple[str, ...] = ()  # every spelling this type owns; [0] is canonical
    alias_mime: str | None = None  # stored as another type's mime (owns none of its own)
    ext_indexable: bool = False  # the FILE gate accepts the extension
    mime_indexable: bool = False  # the FILE gate accepts its "gate" mimes
    tier: Tier | None = None  # parse tier by extension
    mime_tier: Tier | None = None  # parse tier by its "tier" mimes
    kind: str | None = None  # parse kind by extension
    mime_kind: str | None = None  # parse kind by its "kind" mimes
    route: Route | None = None
    repo_code: bool = False  # a repository blob with this extension is a CODE_FILE
    grammar: bool = False  # the extension alone sends a record to the code parser
    filter: FilterRole = FilterRole.HIDDEN
    reconcile: bool = False  # an update is reconciled block by block (by extension)
    ooxml_container: bool = False  # its encrypted form is a CFBF EncryptedPackage
    libreoffice_from: str | None = None  # converted by LibreOffice from this input format
    docling_name_ext: str | None = None  # suffix of the name the file is handed to Docling under
    legacy_parser: bool = False  # the in-process parser set has one for it
    file_mime_map: bool = False  # in FILE_MIME_TYPES (ext -> mime for connectors)
    upload: bool = False  # in the Node upload table
    upload_mime: str | None = None  # Node's value when it is not the canonical mime
    preview: bool = False  # repository connectors render it as a document preview
    internal: bool = False  # a PipesHub type, not a file extension a user has
    prep: Prep = Prep.NONE
    ext_wins: bool = False  # the extension decides over any mime, text/plain included
    odf_container: bool = False  # its encrypted form is a zip whose manifest lists encryption data
    blocks_like: str | None = None  # the extension its blocks are cited as (sheet rows, pages)
    since: int = 1  # the REGISTRY_VERSION that made it indexable
    route_since: int = 1  # the REGISTRY_VERSION that gave its extension a dispatch branch
    # A FILE stored as text/plain is read as this type, not as text: what was read as text before
    # was the package's raw bytes. False for log/cfg, which stay on the text path they had.
    wins_over_text_plain: bool = False
    # Checked for a password by its extension or mime wherever it is routed. Off for dotx/potx,
    # which are checked only when read as themselves, so a cell read as another type stays as it was.
    password_check_by_name: bool = True

    @property
    def indexable(self) -> bool:
        return self.ext_indexable or self.mime_indexable

    @property
    def canonical_mime(self) -> str | None:
        return self.mimes[0] if self.mimes else self.alias_mime


_DOCX_BASE = "application/vnd.openxmlformats-officedocument.wordprocessingml"
_PPTX_BASE = "application/vnd.openxmlformats-officedocument.presentationml"
_DOCX = f"{_DOCX_BASE}.document"
_PPTX = f"{_PPTX_BASE}.presentation"
_DOCM = "application/vnd.ms-word.document.macroEnabled.12"
_XLSM = "application/vnd.ms-excel.sheet.macroEnabled.12"
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_OCTET = "application/octet-stream"


def _roles(*names: MimeRole) -> frozenset[MimeRole]:
    return frozenset(names)


_ALL = _roles("gate", "parse", "tier", "kind", "ext_hint")
_GPT_X = _roles("gate", "parse", "tier", "ext_hint")
_GPC = _roles("gate", "parse", "code")
_GPR = _roles("gate", "parse", "reconcile")
_GPTK = _roles("gate", "parse", "tier", "kind")
_HINT = _roles("ext_hint")
_PARSE = _roles("parse")
_OOXML = _roles("ooxml")

# Which consumers recognise each mime spelling, exactly as each of them matches it: most match the
# spelling as given (case and parameters included), so a spelling one consumer knows may be unknown
# to another. Removing a role changes what that consumer accepts.
_MIME_ROLES: dict[str, frozenset[MimeRole]] = {
    "application/pdf": _ALL | _roles("reconcile"),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": _ALL | _roles("reconcile", "ooxml"),
    "application/msword": _ALL | _roles("reconcile"),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": _ALL | _roles("ooxml"),
    "application/vnd.ms-powerpoint": _ALL,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": _ALL | _roles("ooxml"),
    "application/vnd.ms-excel": _ALL,
    "text/csv": _ALL,
    "text/tab-separated-values": _ALL,
    "text/plain": _ALL | _roles("reconcile"),
    "text/markdown": _ALL,
    "text/mdx": _ALL,
    "text/html": _ALL | _roles("reconcile"),
    "image/png": _GPT_X,
    "image/jpeg": _GPT_X,
    "image/jpg": _roles("gate", "parse", "ext_hint"),
    "image/webp": _GPT_X,
    "image/svg+xml": _GPT_X,
    "image/heic": _PARSE,
    "image/heif": _PARSE,
    "application/epub+zip": _roles("gate", "parse"),
    "application/vnd.sql.table": _GPR,
    "application/vnd.sql.view": _GPR,
    "application/sql_table": _PARSE,
    "application/sql_view": _PARSE,
    "application/blocks": _roles("gate", "parse", "tier", "reconcile"),
    "application/json": _GPTK,
    "application/yaml": _GPTK,
    "application/x-yaml": _GPTK,
    "text/yaml": _PARSE,
    "text/gmail_content": _GPT_X,
    "application/vnd.google-apps.gmail": _PARSE,
    "application/vnd.google-apps.document": _GPR,
    "application/vnd.google-apps.presentation": _GPR,
    "application/vnd.google-apps.spreadsheet": _GPR,
    "text/x-python": _GPC,
    "text/x-python-script": _GPC,
    "text/x-script.python": _GPC,
    "text/x-java-source": _GPC,
    "text/x-c": _GPC,
    "text/x-csrc": _PARSE,
    "text/x-c++": _GPC,
    "text/x-c++src": _PARSE,
    "text/x-php": _GPC,
    "application/javascript": _GPC,
    "text/javascript": _GPC,
    "application/typescript": _GPC,
    "text/x-csharp": _GPC,
    "text/x-go": _GPC,
    "text/x-rust": _GPC,
    "text/x-ruby": _GPC,
    "text/x-swift": _GPC,
    "text/x-kotlin": _GPC,
    "application/dart": _GPC,
    "application/x-sh": _GPC,
    "text/x-sh": _GPC,
    "text/x-shellscript": _GPC,
    "text/x-scala": _PARSE,
    "text/x-lua": _PARSE,
    "text/x-groovy": _PARSE,
    "text/css": _PARSE,
    **dict.fromkeys((
        "application/x-pdf", "application/acrobat", "application/vnd.pdf", "text/pdf", "text/x-pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document+xml",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation+xml",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet+xml",
        "application/x-msword", "application/doc", "application/x-doc", "zz-application/zz-winassoc-doc",
        "application/x-mspowerpoint", "application/powerpoint", "application/x-ppt",
        "zz-application/zz-winassoc-ppt",
        "application/x-msexcel", "application/x-excel", "application/excel", "application/xls",
        "application/x-xls", "zz-application/zz-winassoc-xls",
        "application/csv", "text/comma-separated-values", "text/x-comma-separated-values", "text/x-csv",
        "text/tsv", "application/tsv",
        "text/x-text", "text/txt", "application/text", "application/txt",
        "text/x-markdown", "text/x-md", "application/markdown", "application/x-markdown",
        "text/x-mdx", "application/mdx", "application/x-mdx",
        "application/xhtml+xml", "application/xhtml", "text/xhtml", "application/html",
        "image/x-png", "application/png", "application/x-png", "image/vnd.mozilla.apng",
        "image/x-jpeg", "image/x-jpg", "image/pjpeg",
        "image/x-webp",
        "image/svg", "application/svg+xml", "text/xml-svg", "application/xml-svg",
    ), _HINT),
    _DOCM: _ALL | _roles("ooxml"),
    _XLSM: _ALL | _roles("ooxml"),
    f"{_DOCX_BASE}.template": _ALL,
    f"{_PPTX_BASE}.template": _ALL,
    "application/vnd.ms-outlook": _ALL,
    "application/vnd.oasis.opendocument.text": _ALL,
    "application/rtf": _ALL,
    "text/rtf": _ALL,
    "application/x-rtf": _ALL,
    "text/x-log": _ALL,
    "text/x-cfg": _ALL,
    "application/vnd.ms-powerpoint.presentation.macroEnabled.12": _OOXML,
}

# Mime -> extension hints that are not the owning type's first extension, keyed exactly as the
# hint lookup matches them (with parameters, in mixed case). The macro-enabled workbook and deck
# map to xls and ppt; records stored with those hints rely on them.
_EXT_HINT_OVERRIDES: dict[str, str] = {
    "image/jpeg": "jpeg",
    "image/x-jpeg": "jpeg",
    "image/pjpeg": "jpeg",
    "image/jpeg; charset=binary": "jpeg",
    "image/jpg; charset=binary": "jpg",
    "image/png; charset=binary": "png",
    "image/svg+xml; charset=utf-8": "svg",
    "application/msword; charset=utf-8": "doc",
    "application/x-msword; charset=utf-8": "doc",
    "application/vnd.ms-excel; charset=utf-8": "xls",
    "application/csv; charset=utf-8": "csv",
    "text/csv; charset=utf-8": "csv",
    "text/csv; charset=us-ascii": "csv",
    "text/tab-separated-values; charset=utf-8": "tsv",
    "text/tsv; charset=utf-8": "tsv",
    "application/vnd.ms-powerpoint.presentation.macroEnabled.12": "ppt",
    "application/vnd.ms-powerpoint; charset=utf-8": "ppt",
    "text/mdx; charset=utf-8": "mdx",
    "text/plain; charset=utf-8": "txt",
    "text/plain; charset=us-ascii": "txt",
    "text/plain; charset=iso-8859-1": "txt",
    "text/plain; charset=windows-1252": "txt",
    "text/plain; charset=ascii": "txt",
    "text/html; charset=utf-8": "html",
    "text/html; charset=us-ascii": "html",
    "text/html; charset=iso-8859-1": "html",
    "text/html; charset=windows-1252": "html",
    "text/html; charset=ascii": "html",
    "text/markdown; charset=utf-8": "md",
    "text/markdown; charset=us-ascii": "md",
    "text/gmail_content": "html",
}


def _offer(**kw: object) -> FileType:
    """An indexable type the extension filter offers."""
    return FileType(**{"ext_indexable": True, "filter": FilterRole.OFFER, "preview": True, **kw})  # type: ignore[arg-type]


def _code(key: str, mimes: tuple[str, ...] = (), *, extensions: tuple[str, ...] | None = None,
          fmt: str = "code") -> FileType:
    """A source file the gate takes by extension and the code parser reads."""
    return _offer(
        key=key, format=fmt, extensions=extensions or (key,), mimes=mimes,
        mime_indexable=bool(mimes), tier="light", kind="code", route=Route.CODE,
        repo_code=True, grammar=True, upload=True,
    )


def _grammar(key: str, mimes: tuple[str, ...] = (), *, repo_code: bool = True) -> FileType:
    """A grammar the code parser has; the gate takes it by extension only."""
    return _offer(
        key=key, format="code", extensions=(key,), mimes=mimes, grammar=True, repo_code=repo_code,
        route=Route.CODE, since=2,
    )


def _new(**kw: object) -> FileType:
    """A type the extension decides, whatever mime a connector stored with it."""
    return _offer(**{
        "ext_wins": True, "mime_indexable": True, "upload": True, "since": 2, "preview": False,
        "wins_over_text_plain": True, **kw,
    })


def _repo_text(key: str, mime: str | None = None) -> FileType:
    """A repository source file read as text."""
    return FileType(
        key=key, format="txt", extensions=(key,), mimes=(mime,) if mime else (),
        repo_code=True, upload=mime is not None,
    )


def _exclude(key: str, mime: str | None = None, *, alias: str | None = None,
             file_mime_map: bool = False) -> FileType:
    """A type PipesHub cannot index, offered so a sync can leave it out."""
    return FileType(
        key=key, extensions=(key,), mimes=(mime,) if mime else (), alias_mime=alias,
        filter=FilterRole.EXCLUDE_ONLY, upload=bool(mime or alias), file_mime_map=file_mime_map,
    )


def _stored(key: str, mime: str | None = None, *, alias: str | None = None,
            extensions: tuple[str, ...] | None = None) -> FileType:
    """A type the Node upload table stores but nothing indexes."""
    return FileType(
        key=key, extensions=extensions or (key,), mimes=(mime,) if mime else (),
        alias_mime=alias, upload=True,
    )


def _internal(key: str, *mimes: str, **kw: object) -> FileType:
    return FileType(key=key, mimes=mimes, internal=True, **kw)  # type: ignore[arg-type]


# Offered types come first, in the order the extension filter lists them; the exclude-only types
# follow in their filter order.
FILE_TYPES: tuple[FileType, ...] = (
    _offer(key="pdf", format="pdf", extensions=("pdf",),
           mimes=("application/pdf", "application/x-pdf", "application/acrobat", "application/vnd.pdf",
                  "text/pdf", "text/x-pdf"),
           mime_indexable=True, tier="heavy", mime_tier="heavy", kind="pdf", mime_kind="pdf",
           route=Route.PDF, reconcile=True, docling_name_ext=".pdf", file_mime_map=True, upload=True),
    _offer(key="docx", format="docx", extensions=("docx",), mimes=(_DOCX, f"{_DOCX}+xml"),
           mime_indexable=True, tier="heavy", mime_tier="heavy", kind="docling_docx",
           mime_kind="docling_docx", route=Route.DOCX, reconcile=True, ooxml_container=True,
           docling_name_ext=".docx", file_mime_map=True, upload=True),
    _offer(key="doc", format="doc", extensions=("doc",),
           mimes=("application/msword", "application/x-msword", "application/doc", "application/x-doc",
                  "zz-application/zz-winassoc-doc"),
           mime_indexable=True, tier="heavy", mime_tier="heavy", kind="docling_docx",
           mime_kind="docling_docx", route=Route.DOC, reconcile=True, libreoffice_from="doc",
           docling_name_ext=".docx", legacy_parser=True, file_mime_map=True, upload=True),
    _offer(key="pptx", format="pptx", extensions=("pptx",), mimes=(_PPTX, f"{_PPTX}+xml"),
           mime_indexable=True, tier="heavy", mime_tier="heavy", kind="docling_docx",
           mime_kind="docling_docx", route=Route.PPTX, ooxml_container=True, docling_name_ext=".pptx",
           legacy_parser=True, file_mime_map=True, upload=True),
    _offer(key="ppt", format="ppt", extensions=("ppt",),
           mimes=("application/vnd.ms-powerpoint", "application/x-mspowerpoint", "application/powerpoint",
                  "application/x-ppt", "zz-application/zz-winassoc-ppt"),
           mime_indexable=True, tier="heavy", mime_tier="heavy", kind="docling_docx",
           mime_kind="docling_docx", route=Route.PPT, libreoffice_from="ppt", docling_name_ext=".pptx",
           legacy_parser=True, file_mime_map=True, upload=True),
    _offer(key="xlsx", format="xlsx", extensions=("xlsx",), mimes=(_XLSX, f"{_XLSX}+xml"),
           mime_indexable=True, tier="heavy", mime_tier="heavy", kind="excel_load",
           mime_kind="excel_load", route=Route.XLSX, ooxml_container=True, legacy_parser=True,
           file_mime_map=True, upload=True),
    _offer(key="xls", format="xls", extensions=("xls",),
           mimes=("application/vnd.ms-excel", "application/x-msexcel", "application/x-excel",
                  "application/excel", "application/xls", "application/x-xls",
                  "zz-application/zz-winassoc-xls"),
           mime_indexable=True, tier="heavy", mime_tier="heavy", kind="excel_load",
           mime_kind="excel_load", route=Route.XLS, libreoffice_from="xls", legacy_parser=True,
           file_mime_map=True, upload=True),
    _offer(key="csv", format="csv", extensions=("csv",),
           mimes=("text/csv", "application/csv", "text/comma-separated-values",
                  "text/x-comma-separated-values", "text/x-csv"),
           mime_indexable=True, tier="light", mime_tier="light", kind="csv", mime_kind="csv",
           route=Route.CSV, legacy_parser=True, file_mime_map=True, upload=True),
    _offer(key="tsv", format="tsv", extensions=("tsv",),
           mimes=("text/tab-separated-values", "text/tsv", "application/tsv"),
           mime_indexable=True, tier="light", mime_tier="light", kind="csv", mime_kind="csv",
           route=Route.TSV, legacy_parser=True, file_mime_map=True, upload=True),
    _offer(key="txt", format="txt", extensions=("txt",),
           mimes=("text/plain", "text/x-text", "text/txt", "application/text", "application/txt"),
           mime_indexable=True, tier="light", mime_tier="light", kind="text", mime_kind="text",
           route=Route.TXT, reconcile=True, file_mime_map=True, upload=True),
    _offer(key="md", format="md", extensions=("md",),
           mimes=("text/markdown", "text/x-markdown", "text/x-md", "application/markdown",
                  "application/x-markdown"),
           mime_indexable=True, tier="light", mime_tier="light", kind="text", mime_kind="text",
           route=Route.MD, repo_code=True, reconcile=True, docling_name_ext=".md", legacy_parser=True,
           file_mime_map=True, upload=True),
    _offer(key="mdx", format="mdx", extensions=("mdx",),
           mimes=("text/mdx", "text/x-mdx", "application/mdx", "application/x-mdx"),
           mime_indexable=True, tier="light", mime_tier="light", kind="text", mime_kind="text",
           route=Route.MDX, reconcile=True, docling_name_ext=".md", legacy_parser=True,
           file_mime_map=True, upload=True),
    _offer(key="html", format="html", extensions=("html",),
           mimes=("text/html", "application/xhtml+xml", "application/xhtml", "text/xhtml",
                  "application/html"),
           mime_indexable=True, tier="light", mime_tier="light", kind="text", mime_kind="text",
           route=Route.HTML, repo_code=True, reconcile=True, legacy_parser=True, file_mime_map=True,
           upload=True),
    _offer(key="png", format="png", extensions=("png",),
           mimes=("image/png", "image/x-png", "application/png", "application/x-png",
                  "image/vnd.mozilla.apng"),
           mime_indexable=True, tier="heavy", mime_tier="heavy", route=Route.IMAGE, legacy_parser=True,
           file_mime_map=True, upload=True),
    _offer(key="jpeg", format="jpg", extensions=("jpg", "jpeg"),
           mimes=("image/jpeg", "image/jpg", "image/x-jpeg", "image/x-jpg", "image/pjpeg"),
           mime_indexable=True, tier="heavy", mime_tier="heavy", route=Route.IMAGE, legacy_parser=True,
           file_mime_map=True, upload=True),
    _offer(key="webp", format="webp", extensions=("webp",), mimes=("image/webp", "image/x-webp"),
           mime_indexable=True, tier="heavy", mime_tier="heavy", route=Route.IMAGE, legacy_parser=True,
           file_mime_map=True, upload=True),
    _offer(key="svg", format="svg", extensions=("svg",),
           mimes=("image/svg+xml", "image/svg", "application/svg+xml", "text/xml-svg",
                  "application/xml-svg"),
           mime_indexable=True, tier="heavy", mime_tier="heavy", route=Route.IMAGE, legacy_parser=True,
           file_mime_map=True, upload=True),
    FileType(key="heic", format="heic", extensions=("heic",), mimes=("image/heic",),
             filter=FilterRole.OFFER_LEGACY, preview=True, legacy_parser=True, file_mime_map=True,
             upload=True),
    FileType(key="heif", format="heif", extensions=("heif",), mimes=("image/heif",),
             filter=FilterRole.OFFER_LEGACY, preview=True, legacy_parser=True, file_mime_map=True,
             upload=True),
    _offer(key="epub", format="epub", extensions=("epub",), mimes=("application/epub+zip",),
           mime_indexable=True, route=Route.EPUB, file_mime_map=True, upload=True),
    _offer(key="sql_table", format="sql_table", extensions=("sql_table",),
           mimes=("application/vnd.sql.table", "application/sql_table"), mime_indexable=True,
           tier="light", kind="text", route=Route.SQL_TABLE, reconcile=True, legacy_parser=True,
           internal=True),
    _offer(key="sql_view", format="sql_view", extensions=("sql_view",),
           mimes=("application/vnd.sql.view", "application/sql_view"), mime_indexable=True,
           tier="light", kind="text", route=Route.SQL_VIEW, reconcile=True, legacy_parser=True,
           internal=True),
    # The tree-sitter parser's own key in the in-process parser set, not an extension.
    FileType(key="code", extensions=("code",), filter=FilterRole.OFFER_LEGACY, preview=True,
             legacy_parser=True, internal=True),
    _code("py", ("text/x-python", "text/x-python-script", "text/x-script.python")),
    _code("js", ("application/javascript", "text/javascript"), extensions=("js", "jsx", "mjs", "cjs")),
    _code("ts", ("application/typescript",), extensions=("ts", "tsx")),
    _code("java", ("text/x-java-source",)),
    _code("c", ("text/x-c", "text/x-csrc"), extensions=("c", "h")),
    _code("cpp", ("text/x-c++", "text/x-c++src"), extensions=("cpp", "cc", "cxx", "hpp", "hxx")),
    _code("cs", ("text/x-csharp",)),
    _code("go", ("text/x-go",)),
    _code("rs", ("text/x-rust",)),
    _code("rb", ("text/x-ruby",)),
    _code("php", ("text/x-php",)),
    _code("swift", ("text/x-swift",)),
    _code("kt", ("text/x-kotlin",), extensions=("kt", "kts")),
    _code("dart", ("application/dart",)),
    _code("sh", ("application/x-sh", "text/x-sh", "text/x-shellscript"), extensions=("sh", "bash"),
          fmt="txt"),
    _grammar("pyi"),
    _grammar("mts", repo_code=False),
    _grammar("cts", repo_code=False),
    _grammar("scala", ("text/x-scala",)),
    _grammar("groovy", ("text/x-groovy",)),
    _grammar("gradle"),
    _grammar("lua", ("text/x-lua",)),
    _offer(key="htm", format="html", extensions=("htm",), alias_mime="text/html", tier="light",
           kind="text", route=Route.HTML, route_since=2, repo_code=True, file_mime_map=True, upload=True),
    FileType(key="blocks", format="blocks", extensions=("blocks",), mimes=("application/blocks",),
             mime_indexable=True, mime_tier="light", route=Route.BLOCKS,
             filter=FilterRole.OFFER_LEGACY, preview=True, internal=True),
    _offer(key="json", format="json", extensions=("json",), mimes=("application/json",),
           mime_indexable=True, tier="light", mime_tier="light", kind="text", mime_kind="text",
           route=Route.JSON, legacy_parser=True, file_mime_map=True, upload=True),
    _offer(key="yaml", format="yaml", extensions=("yaml", "yml"),
           mimes=("application/yaml", "application/x-yaml", "text/yaml"), mime_indexable=True,
           tier="light", mime_tier="light", kind="text", mime_kind="text", route=Route.YAML,
           legacy_parser=True, file_mime_map=True, upload=True, upload_mime="application/x-yaml"),
    _new(key="msg", format="msg", extensions=("msg",), mimes=("application/vnd.ms-outlook",),
         tier="light", mime_tier="light", kind="text", mime_kind="text", route=Route.MSG,
         blocks_like="html"),
    _new(key="odt", format="odt", extensions=("odt",), mimes=("application/vnd.oasis.opendocument.text",),
         tier="heavy", mime_tier="heavy", kind="docling_docx", mime_kind="docling_docx",
         route=Route.LIBREOFFICE_DOCX, odf_container=True, libreoffice_from="odt", blocks_like="docx"),
    _new(key="rtf", format="rtf", extensions=("rtf",),
         mimes=("application/rtf", "text/rtf", "application/x-rtf"), tier="heavy", mime_tier="heavy",
         kind="docling_docx", mime_kind="docling_docx", route=Route.LIBREOFFICE_DOCX,
         libreoffice_from="rtf", blocks_like="docx", upload_mime="text/rtf"),
    _new(key="xlsm", format="xlsx", extensions=("xlsm",), mimes=(_XLSM,), tier="heavy",
         mime_tier="heavy", kind="excel_load", mime_kind="excel_load", route=Route.XLSX,
         ooxml_container=True, blocks_like="xlsx"),
    # Stored as text/x-log, which nothing read before, so a new file takes the capped text route; a row
    # stored as text/plain keeps the text path it had (wins_over_text_plain=False).
    _new(key="log", format="log", extensions=("log",), mimes=("text/x-log",), wins_over_text_plain=False,
         tier="light", mime_tier="light", kind="text", mime_kind="text", route=Route.PLAIN_TEXT,
         prep=Prep.CAPPED_TEXT, blocks_like="txt"),
    # Stored as text/x-cfg, which nothing read before, so a new file takes the capped text route; a row
    # stored as text/plain keeps the text path it had (wins_over_text_plain=False).
    _new(key="cfg", format="cfg", extensions=("cfg",), mimes=("text/x-cfg",), wins_over_text_plain=False,
         tier="light", mime_tier="light", kind="text", mime_kind="text", route=Route.PLAIN_TEXT,
         prep=Prep.CAPPED_TEXT, blocks_like="txt"),
    _new(key="docm", format="docm", extensions=("docm",), mimes=(_DOCM,), tier="heavy",
         mime_tier="heavy", kind="docling_docx", mime_kind="docling_docx", route=Route.DOCX,
         prep=Prep.OOXML_WORD, ooxml_container=True, blocks_like="docx"),
    _new(key="dotx", format="dotx", password_check_by_name=False, extensions=("dotx",), mimes=(f"{_DOCX_BASE}.template",),
         tier="heavy", mime_tier="heavy", kind="docling_docx", mime_kind="docling_docx",
         route=Route.DOCX, prep=Prep.OOXML_WORD, ooxml_container=True, blocks_like="docx"),
    _new(key="potx", format="potx", password_check_by_name=False, extensions=("potx",), mimes=(f"{_PPTX_BASE}.template",),
         tier="heavy", mime_tier="heavy", kind="docling_docx", mime_kind="docling_docx",
         route=Route.PPTX, prep=Prep.OOXML_SLIDES, ooxml_container=True, blocks_like="pptx"),
    # Exclude-only, in filter order.
    _exclude("zip", "application/zip", file_mime_map=True),
    _exclude("7z", "application/x-7z-compressed"),
    _exclude("rar", "application/x-rar-compressed"),
    _exclude("tar", "application/x-tar"),
    _exclude("gz", "application/gzip"),
    _exclude("tgz"),
    _exclude("bz2", "application/x-bzip2"),
    _exclude("xz", "application/x-xz"),
    _exclude("db", alias=_OCTET),
    _exclude("sqlite", "application/vnd.sqlite3"),
    _exclude("sqlite3"),
    _exclude("mdb", "application/x-msaccess"),
    _exclude("accdb"),
    _exclude("exe", "application/vnd.microsoft.portable-executable"),
    _exclude("dll", "application/x-msdownload"),
    _exclude("msi"),
    _exclude("bin", _OCTET),
    _exclude("iso"),
    _exclude("dmg"),
    _exclude("apk"),
    _exclude("jar"),
    _exclude("tmp"),
    _exclude("bak"),
    _exclude("swp"),
    _exclude("lock"),
    _exclude("mp4", "video/mp4"),
    _exclude("mov", "video/quicktime"),
    _exclude("avi", "video/x-msvideo"),
    _exclude("mkv", "video/x-matroska"),
    _exclude("webm", "video/webm"),
    _exclude("wmv", "video/x-ms-wmv"),
    _exclude("mp3", "audio/mpeg"),
    _exclude("wav", "audio/wav"),
    _exclude("flac", "audio/flac"),
    _exclude("m4a", "audio/x-m4a"),
    _exclude("aac", "audio/aac"),
    _exclude("ogg", "application/ogg"),
    # Repository sources read as text; not offered by the filter.
    *(_repo_text(k) for k in ("vue", "svelte", "pl", "pm", "r", "ex", "exs", "erl", "hs", "clj", "cljs",
                              "zsh", "fish", "ps1", "scss", "sass", "less", "proto", "graphql", "gql",
                              "tf", "tfvars")),
    _repo_text("css", "text/css"),
    _repo_text("sql", "application/sql"),
    # An Office container only the password check knows.
    FileType(key="pptm", extensions=("pptm",),
             mimes=("application/vnd.ms-powerpoint.presentation.macroEnabled.12",), ooxml_container=True),
    # PipesHub's own types: connectors write these mimes, no file carries them.
    _internal("gmail", "text/gmail_content", "application/vnd.google-apps.gmail", format="html",
              mime_indexable=True, mime_tier="light", route=Route.GMAIL),
    _internal("google_slides", "application/vnd.google-apps.presentation", format="pptx",
              mime_indexable=True, route=Route.GOOGLE_SLIDES),
    _internal("google_docs", "application/vnd.google-apps.document", format="docx",
              mime_indexable=True, route=Route.GOOGLE_DOCS),
    _internal("google_sheets", "application/vnd.google-apps.spreadsheet", format="xlsx",
              mime_indexable=True, route=Route.GOOGLE_SHEETS),
    _internal("google_drive_folder", "application/vnd.google-apps.folder"),
    _internal("folder", "text/directory", "application/vnd.folder"),
    _internal("notion_text", "notion/text"),
    _internal("notion_page_comment_text", "notion/pageCommentText"),
    _internal("sql_column", "application/vnd.sql.column"),
    _internal("genesis_32x_rom", "application/x-genesis-32x-rom"),
    _internal("unknown", "application/unknown"),
    # Stored by the upload table; nothing indexes them.
    _stored("gdoc", alias="application/vnd.google-apps.document"),
    _stored("gsheet", alias="application/vnd.google-apps.spreadsheet"),
    _stored("gslides", alias="application/vnd.google-apps.presentation"),
    _stored("gdraw", "application/vnd.google-apps.drawing"),
    _stored("gform", "application/vnd.google-apps.form"),
    _stored("gmap", "application/vnd.google-apps.map"),
    _stored("gsite", "application/vnd.google-apps.site"),
    _stored("gscript", "application/vnd.google-apps.script"),
    _stored("jp2", "image/jp2"),
    _stored("jpx", "image/jpx"),
    _stored("jpm", "image/jpm"),
    _stored("jxl", "image/jxl"),
    _stored("jxs", "image/jxs"),
    FileType(key="gif", extensions=("gif",), mimes=("image/gif",), file_mime_map=True, upload=True),
    _stored("bmp", "image/bmp"),
    _stored("ico", "image/x-icon"),
    _stored("tiff", "image/tiff"),
    _stored("psd", "image/vnd.adobe.photoshop"),
    _stored("avif", "image/avif"),
    _stored("jxr", "image/jxr"),
    _stored("icns", "image/x-icns"),
    _stored("ods", "application/vnd.oasis.opendocument.spreadsheet"),
    _stored("odp", "application/vnd.oasis.opendocument.presentation"),
    FileType(key="xml", extensions=("xml",), mimes=("application/xml",), file_mime_map=True, upload=True,
             upload_mime="text/xml"),
    _stored("appmdx", alias="application/mdx"),
    _stored("zst", "application/zstd"),
    _stored("cab", "application/vnd.ms-cab-compressed"),
    _stored("rpm", "application/x-rpm"),
    _stored("deb", "application/vnd.debian.binary-package"),
    _stored("cpio", "application/x-cpio"),
    _stored("lz", "application/lzip"),
    _stored("ape", "audio/ape"),
    _stored("amr", "audio/amr"),
    _stored("au", "audio/basic"),
    _stored("qcp", "audio/qcelp"),
    _stored("midi", "audio/midi", extensions=("midi", "mid")),
    _stored("flv", "video/x-flv"),
    _stored("3gp", "video/3gpp"),
    _stored("ttf", "font/ttf"),
    _stored("otf", "font/otf"),
    _stored("woff", "font/woff"),
    _stored("woff2", "font/woff2"),
    _stored("eot", "application/vnd.ms-fontobject"),
    _stored("xlt", alias="application/vnd.ms-excel"),
    _stored("xlm", alias="application/vnd.ms-excel"),
    _stored("pps", alias="application/vnd.ms-powerpoint"),
    _stored("pot", alias="application/vnd.ms-powerpoint"),
    _stored("ott", "application/vnd.oasis.opendocument.text-template"),
    _stored("ots", "application/vnd.oasis.opendocument.spreadsheet-template"),
    _stored("otp", "application/vnd.oasis.opendocument.presentation-template"),
    _stored("odg", "application/vnd.oasis.opendocument.graphics"),
    _stored("odf", "application/vnd.oasis.opendocument.formula"),
    _stored("dat", alias=_OCTET),
    _stored("so", "application/x-sharedlib"),
    _stored("dylib", "application/x-mach-o-dylib"),
    _stored("wopitest", alias=_DOCX),
    _stored("wopitestx", alias=_DOCX),
    _stored("dwg", "image/vnd.dwg"),
    _stored("dxf", "image/vnd.dxf"),
    _stored("x3d", "model/x3d+xml"),
    _stored("glb", "model/gltf-binary"),
    _stored("gltf", "model/gltf+json"),
    _stored("stl", "model/stl"),
    _stored("3mf", "application/vnd.ms-package.3dmanufacturing-3dmodel+xml"),
    _stored("kml", "application/vnd.google-earth.kml+xml"),
    _stored("kmz", "application/vnd.google-earth.kmz"),
    _stored("gpx", "application/gpx+xml"),
    _stored("shp", "application/vnd.shp"),
    _stored("shx", "application/vnd.shx"),
    _stored("dbf", "application/x-dbf"),
    _stored("geojson", "application/geo+json"),
    _stored("fits", "application/fits"),
    _stored("dcm", "application/dicom"),
    _stored("nii", "application/x-nifti"),
    _stored("mrc", "application/marc"),
    _stored("pages", "application/x-iwork-pages-sffpages"),
    _stored("numbers", "application/x-iwork-numbers-sffnumbers"),
    _stored("key", "application/x-iwork-keynote-sffkey"),
)


# --------------------------------------------------------------------------- lookups


def normalize_extension(extension: str | None) -> str:
    return (extension or "").lower().lstrip(".")


def normalize_mime(mime_type: str | None) -> str:
    return (mime_type or "").split(";")[0].strip().lower()


def _index_extensions() -> dict[str, FileType]:
    index: dict[str, FileType] = {}
    for ft in FILE_TYPES:
        for ext in ft.extensions:
            if ext in index:
                raise ValueError(f"extension {ext!r} belongs to {index[ext].key} and {ft.key}")
            index[ext] = ft
    return index


def _index_mimes() -> dict[str, FileType]:
    index: dict[str, FileType] = {}
    for ft in FILE_TYPES:
        for mime in ft.mimes:
            key = normalize_mime(mime)
            if key in index:
                raise ValueError(f"mime {mime!r} belongs to {index[key].key} and {ft.key}")
            index[key] = ft
    return index


_BY_EXTENSION: Final = _index_extensions()
_BY_MIME: Final = _index_mimes()


def by_extension(extension: str | None) -> FileType | None:
    return _BY_EXTENSION.get(normalize_extension(extension))


def by_mime(mime_type: str | None) -> FileType | None:
    return _BY_MIME.get(normalize_mime(mime_type))


def mime_roles(mime: str) -> frozenset[MimeRole]:
    return _MIME_ROLES.get(mime, frozenset())


def _spellings(role: MimeRole) -> Iterator[tuple[str, FileType]]:
    for ft in FILE_TYPES:
        for mime in ft.mimes:
            if role in mime_roles(mime):
                yield mime, ft


def _extensions(types: Iterable[FileType]) -> Iterator[str]:
    for ft in types:
        yield from ft.extensions


def _mime_side(table: dict[str, FileType]) -> dict[str, FileType]:
    """Lower-cased spellings for the consumers that lower-case the mime before matching."""
    return {m.lower(): ft for m, ft in table.items()}


_EXTENSION_RE: Final = re.compile(r"[a-z0-9][a-z0-9_+-]{0,15}")
_ROLE_NEEDS: Final[dict[MimeRole, str]] = {
    "gate": "mime_indexable", "tier": "mime_tier", "kind": "mime_kind", "parse": "format",
}


def _problems() -> Iterator[str]:
    owned = {m: ft for ft in FILE_TYPES for m in ft.mimes}
    for ft in FILE_TYPES:
        if ft.key != ft.key.lower():
            yield f"{ft.key}: key is not lower case"
        for ext in ft.extensions:
            if not _EXTENSION_RE.fullmatch(ext):
                yield f"{ft.key}: extension {ext!r} is not a lower-case extension"
        if ft.filter is FilterRole.OFFER and not ft.ext_indexable:
            yield f"{ft.key}: offered, but the gate refuses its extension"
        if ft.filter is FilterRole.OFFER_LEGACY and ft.ext_indexable:
            yield f"{ft.key}: OFFER_LEGACY, but the gate accepts its extension"
        if ft.filter is FilterRole.EXCLUDE_ONLY and ft.indexable:
            yield f"{ft.key}: exclude-only, but indexable"
        if ft.alias_mime is not None and (ft.mimes or owned.get(ft.alias_mime) in (None, ft)):
            yield f"{ft.key}: alias_mime must be another type's mime, on a type owning none"
        if ft.mime_indexable and not any("gate" in mime_roles(m) for m in ft.mimes):
            yield f"{ft.key}: mime_indexable with no mime the gate matches"
    for mime, roles in _MIME_ROLES.items():
        ft = owned.get(mime)
        if ft is None:
            yield f"roles for {mime!r}, which no type owns"
            continue
        for role, field in _ROLE_NEEDS.items():
            if role in roles and not getattr(ft, field):
                yield f"{mime!r} has role {role} but {ft.key}.{field} is unset"
    for mime in _EXT_HINT_OVERRIDES:
        if normalize_mime(mime) not in _BY_MIME:
            yield f"extension hint for {mime!r}, which no type owns"


def _validate() -> None:
    problems = list(_problems())
    if problems:
        raise ValueError("file type registry: " + "; ".join(problems))


_validate()


# --------------------------------------------------------------------------- derived lists

# Tuples, not sets: the gate matches whatever a producer sent, and a list or dict mime must read
# as "not supported" rather than raise on hashing.
GATE_MIMES: Final = tuple(sorted(m for m, ft in _spellings("gate") if ft.mime_indexable))
GATE_EXTENSIONS: Final = tuple(sorted(_extensions(ft for ft in FILE_TYPES if ft.ext_indexable)))

REPO_CODE_EXTENSIONS: Final = frozenset(_extensions(ft for ft in FILE_TYPES if ft.repo_code))
GRAMMAR_EXTENSIONS: Final = frozenset(_extensions(ft for ft in FILE_TYPES if ft.grammar))
CODE_MIMES: Final = frozenset(m for m, _ in _spellings("code"))

EXCLUDE_ONLY_EXTENSIONS: Final = tuple(
    _extensions(ft for ft in FILE_TYPES if ft.filter is FilterRole.EXCLUDE_ONLY)
)

RECONCILED_MIMES: Final = frozenset(m for m, _ in _spellings("reconcile"))
RECONCILED_EXTENSIONS: Final = frozenset(_extensions(ft for ft in FILE_TYPES if ft.reconcile))

MIME_FORMATS: Final = {m: ft.format for m, ft in _spellings("parse") if ft.format}
EXTENSION_FORMATS: Final = {
    ext: ft.format for ft in FILE_TYPES if ft.format for ext in ft.extensions
}

MIME_EXTENSION_HINTS: Final = {
    **{m: ft.extensions[0] for m, ft in _spellings("ext_hint") if ft.extensions},
    **_EXT_HINT_OVERRIDES,
}

OOXML_EXTENSIONS: Final = frozenset(_extensions(
    ft for ft in FILE_TYPES if ft.ooxml_container and ft.password_check_by_name
))
OOXML_MIMES: Final = frozenset(m.lower() for m, ft in _spellings("ooxml") if ft.password_check_by_name)

DOCLING_NAME_EXTENSIONS: Final = {
    ext: ft.docling_name_ext for ft in FILE_TYPES if ft.docling_name_ext for ext in ft.extensions
}
DOCLING_FORMATS: Final = tuple(
    dict.fromkeys(ft.format for ft in FILE_TYPES if ft.docling_name_ext and ft.format)
)

FILE_MIME_MAP: Final = {
    ext: ft.canonical_mime for ft in FILE_TYPES if ft.file_mime_map for ext in ft.extensions
}

# Every spelling of a type the gate takes by mime, case and parameters aside. Source code is left
# out: the code route is chosen by the exact code mimes (or the extension), so a code mime in
# another spelling stays as unknown as it always was.
_ROUTED_MIMES: Final = {
    normalize_mime(m): ft
    for ft in FILE_TYPES
    if ft.mime_indexable and ft.route is not Route.CODE
    for m in ft.mimes
}


def _routed_spellings(field: str) -> dict[str, FileType]:
    return {m: ft for m, ft in _ROUTED_MIMES.items() if getattr(ft, field)}


_TIER_BY_MIME: Final = {
    **_mime_side({m: ft for m, ft in _spellings("tier") if ft.mime_tier}),
    **_routed_spellings("mime_tier"),
}
_KIND_BY_MIME: Final = {
    **_mime_side({m: ft for m, ft in _spellings("kind") if ft.mime_kind}),
    **_routed_spellings("mime_kind"),
}
_FORMAT_BY_MIME: Final = {
    **{normalize_mime(m): ft.format for m, ft in _spellings("parse") if ft.format},
    **{m: ft.format for m, ft in _routed_spellings("format").items()},
}


# The mimes the dispatch matched before the registry's second version, each as it matched it
# (exactly), and the extensions it had a branch for. Whatever these route keeps that route.
_MAIN_CHAIN_MIMES: Final = {
    **{
        ft.mimes[0]: ft
        for ft in FILE_TYPES
        if ft.since == 1 and ft.mime_indexable and ft.route not in (None, Route.CODE) and ft.mimes
    },
    "image/jpg": _BY_MIME["image/jpg"],
}
_MAIN_CHAIN_EXTENSIONS: Final = {
    ext: ft
    for ft in FILE_TYPES
    if ft.since == 1 and ft.route_since == 1 and ft.ext_indexable and ft.route not in (None, Route.CODE)
    for ext in ft.extensions
}


def _new_type_wins(ft: FileType, mime_type: object, *, is_code_file_record: bool = False) -> bool:
    """Whether a type the extension decides is read as itself rather than as its stored mime.

    Only where the dispatch read nothing before: a mime it had a branch for keeps that branch
    (a .log stored as application/json stays JSON), except text/plain for the packages whose raw
    bytes were read as text. A CODE_FILE was judged on its extension and refused, so it is free.
    """
    if is_code_file_record or not isinstance(mime_type, str):
        return True
    if mime_type in CODE_MIMES:
        return False
    if mime_type == "text/plain":
        return ft.wins_over_text_plain
    return mime_type not in _MAIN_CHAIN_MIMES


# xlsm had its parse kind by extension before it was indexable; it keeps it whatever the mime.
_KIND_BY_EXTENSION_BEFORE: Final = frozenset({"xlsm"})


def _extension_decides(ft: FileType, mime_type: object) -> bool:
    return not ft.ext_wins or _new_type_wins(ft, mime_type)


def tier(extension: str | None, mime_type: str | None) -> Tier:
    """Parse tier: by extension, else by mime, else heavy (the safe side for an unknown format).

    The mime is lower-cased before the extension is looked at, so a malformed mime fails the
    same way whatever the extension.
    """
    mime_key = (mime_type or "").lower()
    ft = _BY_EXTENSION.get(normalize_extension(extension))
    if ft is not None and ft.tier and _extension_decides(ft, mime_type):
        return ft.tier
    ft = _TIER_BY_MIME.get(mime_key) or _TIER_BY_MIME.get(normalize_mime(mime_key))
    if ft is not None and ft.mime_tier:
        return ft.mime_tier
    return "heavy"


def kind(extension: str | None, mime_type: str | None) -> str | None:
    """Parse kind: by extension, else by mime, else None."""
    ft = _BY_EXTENSION.get(normalize_extension(extension))
    if ft is not None and ft.kind and (_extension_decides(ft, mime_type) or ft.key in _KIND_BY_EXTENSION_BEFORE):
        return ft.kind
    mime_key = (mime_type or "").lower()
    ft = _KIND_BY_MIME.get(mime_key) or _KIND_BY_MIME.get(normalize_mime(mime_key))
    return ft.mime_kind if ft is not None else None


def extension_for_mime(mime_type: str | None) -> str | None:
    """The extension a mime type stands for, by its hint, in any case and with any parameters."""
    hint = MIME_EXTENSION_HINTS.get(mime_type)  # type: ignore[arg-type]
    if hint is not None or not isinstance(mime_type, str):
        return hint
    return MIME_EXTENSION_HINTS.get(normalize_mime(mime_type))


def _ext_type(extension: object) -> FileType | None:
    return _BY_EXTENSION.get(normalize_extension(extension)) if isinstance(extension, str) else None


def _mime_type(mime_type: object) -> FileType | None:
    return _ROUTED_MIMES.get(normalize_mime(mime_type)) if isinstance(mime_type, str) else None


def ext_wins(extension: str | None) -> bool:
    """Whether the extension alone decides how the file is read."""
    ft = _ext_type(extension)
    return ft is not None and ft.ext_wins


def parse_format(mime_type: str | None, extension: str | None) -> str | None:
    """The parsing service's format key.

    A source file goes by its extension (a file read out of a git tree arrives as text/plain);
    otherwise the mime wins over the extension, as it always has. A type the extension decides
    goes by its extension wherever the mime named nothing that was read before.
    """
    ft = _BY_EXTENSION.get((extension or "").lower().lstrip("."))
    ext_format = ft.format if ft is not None else None
    if ft is not None and ext_format == "code":
        return ext_format
    if ft is not None and ft.ext_wins and _new_type_wins(ft, mime_type):
        return ext_format
    if mime_type and mime_type in MIME_FORMATS and _BY_MIME[normalize_mime(mime_type)].since == 1:
        return MIME_FORMATS[mime_type]
    if ft is not None and ft.since == 1 and ext_format:
        return ext_format
    if isinstance(mime_type, str) and (fmt := _FORMAT_BY_MIME.get(normalize_mime(mime_type))):
        return fmt
    return ext_format


def blocks_like(extension: str | None) -> str | None:
    """The extension whose citations a type's blocks get: an .xlsm is cited like an .xlsx."""
    ft = _ext_type(extension)
    return ft.blocks_like if ft is not None and ft.blocks_like else extension


# The legacy dispatch's order after its mime-only branches: the first route the extension or the
# mime names wins.
_LEGACY_ORDER: Final = (
    Route.PDF, Route.EPUB, Route.DOCX, Route.DOC, Route.XLSX, Route.XLS, Route.CSV, Route.TSV,
    Route.HTML, Route.PPTX, Route.PPT, Route.MD, Route.MDX, Route.TXT, Route.SQL_TABLE,
    Route.SQL_VIEW, Route.JSON, Route.YAML, Route.IMAGE, Route.MSG, Route.LIBREOFFICE_DOCX, Route.PLAIN_TEXT,
)
_GOOGLE_ROUTES: Final = frozenset({Route.GOOGLE_SLIDES, Route.GOOGLE_DOCS, Route.GOOGLE_SHEETS})


@dataclass(frozen=True, slots=True)
class Dispatch:
    route: Route
    file_type: FileType | None  # None for the code route, which the record decides


def _main_route(mime_type: object, extension: object, *, reads_as_code_file: bool) -> Dispatch | None:
    """The dispatch as it was before the registry's second version: exact spellings only."""
    mime = mime_type if isinstance(mime_type, str) else None
    by_mime = _MAIN_CHAIN_MIMES.get(mime) if mime is not None else None
    if by_mime is not None and by_mime.route in _GOOGLE_ROUTES:
        return Dispatch(by_mime.route, by_mime)  # type: ignore[arg-type]
    if mime == "text/html":
        return Dispatch(Route.HTML, by_mime)
    if reads_as_code_file:
        return Dispatch(Route.CODE, None)
    if mime == "text/plain":
        return Dispatch(Route.TXT, by_mime)
    if by_mime is not None and by_mime.route in (Route.BLOCKS, Route.GMAIL):
        return Dispatch(by_mime.route, by_mime)  # type: ignore[arg-type]
    by_ext = _MAIN_CHAIN_EXTENSIONS.get(extension) if isinstance(extension, str) else None
    return _first_in_order(by_ext, by_mime)


def _first_in_order(by_ext: FileType | None, by_mime: FileType | None) -> Dispatch | None:
    for route in _LEGACY_ORDER:
        for ft in (by_ext, by_mime):
            if ft is not None and ft.route is route:
                return Dispatch(route, ft)
    return None


def _widened_route(mime_type: object, extension: object) -> Dispatch | None:
    """A route for what the dispatch read nothing of before: any case, parameters, alias spelling."""
    mime = normalize_mime(mime_type) if isinstance(mime_type, str) else None
    by_mime = _ROUTED_MIMES.get(mime) if mime else None
    if by_mime is not None and by_mime.route in _GOOGLE_ROUTES:
        return Dispatch(by_mime.route, by_mime)  # type: ignore[arg-type]
    if mime == "text/html":
        return Dispatch(Route.HTML, by_mime)
    if mime == "text/plain":
        return Dispatch(Route.TXT, by_mime)
    if by_mime is not None and by_mime.route in (Route.BLOCKS, Route.GMAIL):
        return Dispatch(by_mime.route, by_mime)  # type: ignore[arg-type]
    by_ext = _ext_type(extension)
    if by_ext is not None and not by_ext.ext_indexable:
        by_ext = None
    if by_ext is not None and by_mime is not None:
        # An existing type's spelling keeps its reader against a newer one's.
        if by_ext.since > 1 and by_mime.since == 1:
            by_ext = None
        elif by_mime.since > 1 and by_ext.since == 1:
            by_mime = None
    late = by_ext if by_ext is not None and by_ext.route_since > 1 else None
    found = _first_in_order(None if late else by_ext, by_mime)
    if found is None and late is not None and late.route is not None:
        # htm reaches its HTML branch only when the mime names nothing.
        return Dispatch(late.route, late)
    return found


def legacy_route(
    mime_type: str | None,
    extension: str | None,
    *,
    code_extension: str | None,
    reads_as_code_file: bool,
    is_code_file_record: bool = False,
) -> Dispatch | None:
    """The in-process parser a file goes to, or None when there is none.

    Whatever the dispatch routed before keeps its route. A type the extension decides (from
    *code_extension*, the extension with the file name as fallback) is read as itself where the
    mime named nothing read before, and a package stored as text/plain is read as itself, not as
    text. What still has no route is matched in any case, with parameters and alias spellings.
    """
    winner = _ext_type(code_extension)
    if (
        winner is not None
        and winner.ext_wins
        and winner.route is not None
        and _new_type_wins(winner, mime_type, is_code_file_record=is_code_file_record)
    ):
        return Dispatch(winner.route, winner)
    main = _main_route(mime_type, extension, reads_as_code_file=reads_as_code_file)
    if main is not None:
        return main
    return _widened_route(mime_type, extension)


GateRefusal = Literal["type", "disabled", "too_large"]
TEXT_MAX_DOWNLOAD_BYTES: Final = 256 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class GateDecision:
    supported: bool
    judged_extension: object  # the extension the refusal is reported for
    file_type: FileType | None  # the type that decides how the file is read
    refusal: GateRefusal | None = None


def gate_decision(
    *,
    mime: object,
    extension: object,
    record_name: str | None,
    is_code_file: bool,
    code_file_extension: str | None,
    size_bytes: object = None,
    disabled: frozenset[str] = frozenset(),
) -> GateDecision:
    """Whether the indexer reads a record's file, from what the record says about it.

    A FILE is taken by a mime or an extension of an indexable type. A CODE_FILE's mime is not
    trusted (repository walkers default it to text/plain), so it is judged on the extension of its
    name alone. A type in *disabled*, or a capped-text file larger than the download ceiling, is
    refused before anything is downloaded.
    """
    if (extension is None or extension == "unknown") and mime is not None and mime != "unknown":
        derived = extension_for_mime(mime)  # type: ignore[arg-type]
        if derived:
            extension = derived
    from_name = False
    if extension == "unknown" and mime != "text/gmail_content" and record_name and "." in record_name:
        extension = record_name.split(".")[-1]
        from_name = True

    if is_code_file:
        judged: object = code_file_extension
        by_ext = _ext_type(code_file_extension)
        supported = code_file_extension in REPO_CODE_EXTENSIONS or (
            by_ext is not None and by_ext.ext_indexable
        )
        decisive = by_ext
    else:
        judged = extension
        by_ext = _ext_type(extension)
        by_mime = _mime_type(mime)
        if by_mime is None and mime in GATE_MIMES:
            by_mime = _BY_MIME.get(normalize_mime(mime))  # type: ignore[arg-type]
        ext_ok = by_ext is not None and by_ext.ext_indexable
        if from_name and ext_ok and not by_ext.ext_wins and extension not in GATE_EXTENSIONS:
            # The dispatch never sees an extension taken from the name, only the record's own:
            # beyond the types the extension decides, the name is matched as it always was.
            ext_ok = False
        supported = ext_ok or by_mime is not None
        if ext_ok and by_ext.ext_wins and _new_type_wins(by_ext, mime):
            decisive = by_ext
        else:
            decisive = by_mime if by_mime is not None else (by_ext if ext_ok else None)
    if not supported:
        return GateDecision(False, judged, None, "type")
    if decisive is not None and decisive.key in disabled:
        return GateDecision(False, judged, decisive, "disabled")
    if (
        decisive is not None
        and decisive.prep is Prep.CAPPED_TEXT
        and isinstance(size_bytes, int)
        and size_bytes > TEXT_MAX_DOWNLOAD_BYTES
    ):
        return GateDecision(False, judged, decisive, "too_large")
    return GateDecision(True, judged, decisive)


FILE_TYPES_CONFIG_KEY: Final = "/services/file_types/config"


def disabled_from_env(value: str | None) -> dict[str, list[str]] | None:
    """``FILE_TYPES_DISABLED`` ("msg,rtf") as the setting it stands in for, or None when unset."""
    if not value:
        return None
    return {"disabled": [v.strip() for v in value.split(",") if v.strip()]}


def disabled_keys(config: object) -> frozenset[str]:
    """The registry keys a ``{"disabled": [...]}`` setting turns off; anything else turns off none."""
    values = config.get("disabled") if isinstance(config, dict) else None
    if not isinstance(values, (list, tuple)):
        return frozenset()
    keys = {ft.key for ft in FILE_TYPES}
    return frozenset(v.lower() for v in values if isinstance(v, str) and v.lower() in keys)


_UNNAMED_MIMES: Final = frozenset({"", "text/plain", "application/octet-stream", "unknown", "application/unknown"})


def stored_mime_for_new_file(mime_type: str | None, name: str | None) -> str | None:
    """The mime a connector stores for a file it saves for the first time.

    A .log/.cfg sent as text/plain, octet-stream or nothing (a desktop stamp, a guess_type answer)
    gets its own mime, which the old text path never read, so it is read as capped text. Any
    other file keeps what its connector sent.
    """
    extension = name.rsplit(".", 1)[-1] if isinstance(name, str) and "." in name else None
    ft = _ext_type(extension)
    if ft is None or not ft.ext_wins or ft.wins_over_text_plain or ft.canonical_mime is None:
        return mime_type
    if mime_type is None or (isinstance(mime_type, str) and normalize_mime(mime_type) in _UNNAMED_MIMES):
        return ft.canonical_mime
    return mime_type


def keeps_stored_mime(stored_mime: str | None, new_mime: str | None, name: str | None) -> bool:
    """Whether a re-synced file keeps the mime its row was stored under instead of *new_mime*.

    A .log/.cfg stored as text/plain before it had a mime of its own is read as text, as it always
    was; moving it to its own mime would re-index it onto another reader.
    """
    ft = _BY_MIME.get(normalize_mime(new_mime)) if isinstance(new_mime, str) else None
    extension = name.rsplit(".", 1)[-1] if isinstance(name, str) and "." in name else None
    return (
        stored_mime == "text/plain"
        and ft is not None
        and ft.ext_wins
        and not ft.wins_over_text_plain
        and _ext_type(extension) is ft
    )


def canonical_mime_for_new_extension(extension: str | None) -> str | None:
    """The mime to store for a type made indexable after the first registry, by its extension."""
    ft = _ext_type(extension)
    return ft.canonical_mime if ft is not None and ft.since > 1 else None


def filter_option_ids() -> tuple[str, ...]:
    """The extension filter's options: every offered type, then the exclude-only ones."""
    offered = (FilterRole.OFFER, FilterRole.OFFER_LEGACY)
    return (*_extensions(ft for ft in FILE_TYPES if ft.filter in offered), *EXCLUDE_ONLY_EXTENSIONS)


def offered_extensions() -> frozenset[str]:
    return frozenset(_extensions(
        ft for ft in FILE_TYPES if ft.filter in (FilterRole.OFFER, FilterRole.OFFER_LEGACY)
    ))


def preview_extensions() -> frozenset[str]:
    return frozenset(_extensions(ft for ft in FILE_TYPES if ft.preview))


def node_upload_table() -> dict[str, str]:
    table: dict[str, str] = {}
    for ft in FILE_TYPES:
        if ft.upload:
            mime = ft.upload_mime or ft.canonical_mime
            if mime is None:
                raise ValueError(f"{ft.key} is in the upload table without a mime")
            for ext in ft.extensions:
                table[ext] = mime
    return dict(sorted(table.items()))


def node_indexable_extensions() -> tuple[str, ...]:
    return tuple(sorted(_extensions(
        ft for ft in FILE_TYPES if ft.ext_indexable and ft.upload and not ft.internal
    )))


# --------------------------------------------------------------------------- Node emit

NODE_GENERATED_PATH: Final = (
    Path(__file__).resolve().parents[3]
    / "nodejs/apps/src/modules/storage/mimetypes/file-types.generated.ts"
)


def node_tables() -> dict[str, object]:
    return _node_tables(node_upload_table(), node_indexable_extensions())


def _node_tables(upload: dict[str, str], indexable: tuple[str, ...]) -> dict[str, object]:
    return {
        "REGISTRY_VERSION": REGISTRY_VERSION,
        "UPLOAD_EXTENSION_TO_MIME": upload,
        "INDEXABLE_EXTENSIONS": list(indexable),
    }


def content_sha256(tables: dict[str, object]) -> str:
    canonical = json.dumps(tables, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _ts_key(key: str) -> str:
    return key if re.fullmatch(r"[A-Za-z_$][\w$]*", key) else f"'{key}'"


def _ts_property(key: str, value: str) -> str:
    """One property as prettier (printWidth 80) lays it out: a long one breaks after a key of 5+."""
    line = f"  {key}: '{value}',"
    if len(line) <= 80 or len(key) < 5:
        return line
    return f"  {key}:\n    '{value}',"


def render_node() -> str:
    upload, indexable = node_upload_table(), node_indexable_extensions()
    tables = _node_tables(upload, indexable)
    lines = [
        "// Generated from backend/python/app/config/file_types.py. Do not edit by hand:",
        "// run `python -m app.config.file_types --emit node` from backend/python.",
        "",
        f"export const REGISTRY_VERSION = {REGISTRY_VERSION};",
        "",
        "export const UPLOAD_EXTENSION_TO_MIME: Readonly<Record<string, string>> = {",
        *(_ts_property(_ts_key(ext), mime) for ext, mime in upload.items()),
        "};",
        "",
        "export const INDEXABLE_EXTENSIONS: readonly string[] = [",
        *(f"  '{ext}'," for ext in indexable),
        "];",
        "",
        "export const CONTENT_SHA256 =",
        f"  '{content_sha256(tables)}';",
        "",
    ]
    return "\n".join(lines)


def _main(argv: list[str]) -> int:
    if argv != ["--emit", "node"]:
        _ = sys.stderr.write("usage: python -m app.config.file_types --emit node\n")
        return 2
    _ = NODE_GENERATED_PATH.write_text(render_node())
    _ = sys.stdout.write(f"wrote {NODE_GENERATED_PATH}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
