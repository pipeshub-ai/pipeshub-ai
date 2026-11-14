"""Black-box snapshot of every decision PipesHub makes from a file's extension and mime type.

The drivers call only entry points that exist unchanged on the commit the snapshot was taken
from: the real record-handler gate, the real legacy dispatch (``EventProcessor.on_event`` with the
processor's parsers stubbed) and the public helpers each consumer calls. Nothing here reads the
file-type registry, so the snapshot can be regenerated on the old tree and diffed.

    python -m tests.unit.config.file_types.gen_pinned_decisions --write [PATH]
    python -m tests.unit.config.file_types.gen_pinned_decisions --replay PINNED OUT

``--write`` builds the input universe from the hand-kept lists, so it runs only on a tree that
still has them (main before the registry), and writes the snapshot (default:
``pinned_decisions.json`` beside this file). ``--replay`` and ``test_pinned_decisions`` recompute
the decisions for the pinned universe on any tree and compare every one.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn
from unittest.mock import AsyncMock, MagicMock, patch

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterable, Iterator, Sequence

    from app.services.messaging.kafka.handlers.record import RecordEventHandler

SNAPSHOT = Path(__file__).with_name("pinned_decisions.json")
_NODE_MIMETYPES = (
    Path(__file__).resolve().parents[5]
    / "nodejs/apps/src/modules/storage/mimetypes/mimetypes.ts"
)

OCTET = "application/octet-stream"
FILE, CODE_FILE = "FILE", "CODE_FILE"

# Office, OpenDocument, mail and text types Docling or LibreOffice know but the gate does not take
# yet, pinned so that starting to index one shows up as a reviewed delta.
_EXTRA_EXTENSIONS = (
    "docm", "dotx", "dotm", "potx", "potm", "pptm", "xlsm", "xltx", "xltm",
    "odt", "ods", "odp", "ott", "rtf", "msg", "eml", "log", "cfg",
)
_EXTRA_MIMES = (
    "application/vnd.ms-outlook",
    "application/vnd.oasis.opendocument.text",
    "application/rtf", "text/rtf", "application/x-rtf",
    "application/vnd.ms-excel.sheet.macroEnabled.12",
    "application/vnd.ms-word.document.macroEnabled.12",
    "application/vnd.ms-powerpoint.presentation.macroEnabled.12",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
    "application/vnd.openxmlformats-officedocument.presentationml.template",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.template",
    "message/rfc822",
)
_SPECIAL_EXTS: tuple[str | None, ...] = (None, "", "unknown")
_SPECIAL_MIMES: tuple[str | None, ...] = (OCTET, "application/unknown", "unknown", None, "")

# 12 x 12 conflicting pairs: the legacy chain's order decides these.
_CROSS_EXTS = ("pdf", "docx", "doc", "xlsx", "pptx", "html", "htm", "txt", "md", "py", "png", "json")
_CROSS_MIMES = (
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "text/html", "text/plain", "text/markdown", "text/x-python", "image/png",
    "application/json", "application/blocks",
)
# A producer that sends a list, a dict or a number where a string belongs: the gate must read
# these as it always has (a refusal, or the same crash), never newly fail or newly accept them.
_MALFORMED_VALUES: tuple[object, ...] = (["text/plain"], ["pdf"], {"a": 1}, 7)
_MALFORMED_ROWS: tuple[tuple[object, object], ...] = (
    *((None, v) for v in _MALFORMED_VALUES),
    *(("unknown", v) for v in _MALFORMED_VALUES),
    *(("pdf", v) for v in _MALFORMED_VALUES),
    *(("zip", v) for v in _MALFORMED_VALUES),
    *((v, None) for v in _MALFORMED_VALUES),
    *((v, "text/plain") for v in _MALFORMED_VALUES),
    *((v, "application/zip") for v in _MALFORMED_VALUES),
)
_EXPLICIT_ROWS: tuple[tuple[str | None, str | None], ...] = (
    ("blocks", None),
    (None, "application/blocks"),
    ("blocks", "application/blocks"),
    ("sql_table", None),
    (None, "application/vnd.sql.table"),
    ("sql_view", None),
    ("code", None),
)


# --------------------------------------------------------------------------- universe


def _values(obj: Iterable[object] | None) -> list[str]:
    if obj is None:
        return []
    if isinstance(obj, dict):
        return [str(k) for k in obj]
    return [str(getattr(v, "value", v)) for v in obj]


def _node_map() -> dict[str, str]:
    text = _NODE_MIMETYPES.read_text()
    return {
        k.strip("'\""): v
        for k, v in re.findall(r"^\s*('?[\w]+'?):\s*\n?\s*'([^']+)'", text, flags=re.M)
    }


def _source_lists() -> tuple[set[str], set[str]]:
    """Every extension and mime the tree's lists name, read from the lists themselves."""
    from app.config.constants import arangodb as a
    from app.modules.parsers import encryption
    from app.services.parsing import registry
    from app.services.parsing.providers import local_docling_parser as ldp
    from app.services.resource_governor import kinds, tiers
    from app.utils import image_utils, libreoffice_convert

    exts: set[str] = set(_EXTRA_EXTENSIONS)
    for obj in (
        a.ExtensionTypes, a.EXCLUDABLE_FILE_EXTENSIONS, a.SUPPORTED_CODE_FILE_EXTENSIONS,
        a.CODE_FILE_EXTENSION_VALUES, a.RECONCILIATION_ENABLED_EXTENSIONS,
        registry._EXT_TO_FORMAT, tiers.HEAVY_EXTENSIONS, tiers.LIGHT_EXTENSIONS,
        encryption.OOXML_EXTENSIONS, ldp._FORMAT_EXTS, libreoffice_convert._PROBE_SAMPLES,
        image_utils.mime_to_extension.values(), _node_map(),
    ):
        exts.update(_values(obj))
    exts.update(k.lstrip(".") for k in a.FILE_MIME_TYPES)
    for name in dir(kinds):
        if name.endswith("_EXTENSIONS") and isinstance(getattr(kinds, name), frozenset):
            exts.update(getattr(kinds, name))

    mimes: set[str] = set(_EXTRA_MIMES) | {"application/x-yaml"}
    for obj in (
        a.MimeTypes, a.CODE_FILE_MIME_TYPE_VALUES, a.RECONCILIATION_ENABLED_MIME_TYPES,
        registry._MIME_TO_FORMAT, tiers.HEAVY_MIME_TYPES, tiers.LIGHT_MIME_TYPES,
        getattr(kinds, "_MIME_KINDS", {}), encryption.OOXML_MIME_TYPES,
        image_utils.mime_to_extension, _node_map().values(), a.FILE_MIME_TYPES.values(),
    ):
        mimes.update(_values(obj))
    return {e for e in exts if e}, {m for m in mimes if m}


def _ext_forms(base: Iterable[str]) -> list[str | None]:
    forms: set[str] = set()
    for e in base:
        forms.update({e, e.upper(), f".{e}"})
    return [*_SPECIAL_EXTS, *sorted(forms)]


def _mime_forms(base: Iterable[str]) -> list[str | None]:
    forms: set[str] = set()
    for m in base:
        forms.update({m, m.lower(), m.upper(), f"{m}; charset=utf-8"})
    return [*_SPECIAL_MIMES, *sorted(forms)]


def _natural_pairs(exts: set[str]) -> set[tuple[str, str]]:
    from app.config.constants import arangodb as a
    from app.utils import image_utils

    pairs = {(e, m) for e, m in _node_map().items() if e in exts}
    pairs |= {(k.lstrip("."), v.value) for k, v in a.FILE_MIME_TYPES.items()}
    pairs |= {(e, m) for m, e in image_utils.mime_to_extension.items() if ";" not in m}
    return pairs


def build_cases() -> list[tuple[str | None, str | None, str, str]]:
    """(extension, mime, record name, record type) for every pinned input."""
    base_exts, base_mimes = _source_lists()
    exts, mimes = _ext_forms(base_exts), _mime_forms(base_mimes)
    cases: list[tuple[str | None, str | None, str, str]] = []

    def name_for(ext: str | None) -> str:
        return f"file.{ext.lstrip('.')}" if ext and ext.lstrip(".") else "file"

    cases.extend((e, m, name_for(e), FILE) for e in exts for m in (None, "unknown", OCTET))
    cases.extend(
        (e, m, name, FILE) for m in mimes for e in (None, "unknown") for name in ("file", "Report.DOCX")
    )
    cases.extend((e, m, name_for(e), FILE) for e, m in sorted(_natural_pairs(base_exts)))
    for e in sorted(base_exts):
        cases.append((e, "text/plain", name_for(e), FILE))
        cases.extend((e, m, name_for(e), CODE_FILE) for m in ("text/plain", OCTET))
    cases.extend((e, m, name_for(e), FILE) for e in _CROSS_EXTS for m in _CROSS_MIMES)
    cases.extend((e, m, name_for(e), FILE) for e, m in _EXPLICIT_ROWS)
    unique = list({case_key(c): c for c in cases}.values())
    unique.extend((e, m, "file", rt) for e, m in _MALFORMED_ROWS for rt in (FILE, CODE_FILE))  # type: ignore[misc]
    return unique


def case_key(case: Sequence[object]) -> str:
    """A hashable key for a case, whose extension or mime may be a list or a dict."""
    return json.dumps(list(case[:4]), sort_keys=True)


# --------------------------------------------------------------------------- drivers


class _Silent:
    """A logger that drops everything: the drivers run thousands of records."""

    def __getattr__(self, _name: str) -> Callable[..., None]:
        return lambda *a, **k: None


def _gate_handler(record: dict[str, Any]) -> RecordEventHandler:
    from app.config.constants.arangodb import CollectionNames
    from tests.unit.services.messaging.test_record_handler import _make_handler

    handler = _make_handler(logger=_Silent())
    gp = handler.event_processor.graph_provider
    connector = {"_key": "conn-1", "name": "Drive", "isActive": True, "isAgentActive": False}

    async def get_document(_doc_id: str, collection: str, **_k: object) -> dict[str, Any]:
        return dict(connector) if collection == CollectionNames.APPS.value else dict(record)

    async def update_node(_doc_id: str, _collection: str, fields: dict[str, Any]) -> bool:
        record.update(fields)
        return True

    gp.get_document = AsyncMock(side_effect=get_document)
    gp.update_node = AsyncMock(side_effect=update_node)
    gp.update_queued_duplicates_status = AsyncMock(return_value=0)
    return handler


async def gate(ext: str | None, mime: str | None, name: str, rt: str) -> str:
    """``ok`` when the gate lets the record through to the download, else the status and reason."""
    from app.config.constants.arangodb import EventTypes, ProgressStatus
    from app.services.messaging.config import IndexingEvent

    record: dict[str, Any] = {
        "_key": "r1", "orgId": "org-1", "virtualRecordId": "vr1", "recordType": rt,
        "recordName": name, "connectorId": "conn-1",
        "indexingStatus": ProgressStatus.QUEUED.value,
    }
    handler = _gate_handler(record)
    payload = {
        "recordId": "r1", "orgId": "org-1", "virtualRecordId": "vr1", "connectorId": "conn-1",
        "recordName": name, "extension": ext, "mimeType": mime,
        "signedUrl": "https://example.invalid/f",
    }
    gen = handler.process_event(EventTypes.NEW_RECORD.value, payload)
    try:
        async for event in gen:
            if event.event == IndexingEvent.START_DOWNLOAD:
                return "ok"
    except Exception as exc:
        return f"!{type(exc).__name__}"
    finally:
        await gen.aclose()
    return f"{record.get('indexingStatus')}|{record.get('reason')}"


def _processor() -> MagicMock:
    from app.events.processor import Processor
    from app.services.messaging.config import (
        IndexingEvent,
        PipelineEvent,
        PipelineEventData,
    )

    processor = MagicMock()
    processor.indexing_pipeline = AsyncMock()
    processor.sink_orchestrator.blob_storage.get_actual_content_path = AsyncMock(return_value="p")
    for attr in dir(Processor):
        if attr.startswith("process_"):

            def route(*_a: object, _attr: str = attr, **_k: object) -> AsyncIterator[PipelineEvent]:
                processor.routed.append(_attr)

                async def gen() -> AsyncIterator[PipelineEvent]:
                    yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r1"))
                    yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r1"))

                return gen()

            setattr(processor, attr, MagicMock(side_effect=route))
    processor.routed = []
    return processor


async def legacy(ext: str | None, mime: str | None, name: str, rt: str) -> str:
    """The parser the legacy (in-process) dispatch hands the record to, or the error it raises."""
    from app.events.events import DedupDecision, EventProcessor

    processor = _processor()
    gp = AsyncMock()
    gp.update_node = AsyncMock(return_value=True)
    gp.get_document.return_value = {"_key": "r1", "recordType": rt, "recordName": name}
    ep = EventProcessor(_Silent(), processor, gp, MagicMock())

    async def pdf(*_a: object, **_k: object) -> AsyncIterator[object]:
        processor.routed.append("_dispatch_pdf_binary")
        return
        yield
    data = {
        "eventType": "newRecord",
        "payload": {
            "recordId": "r1", "orgId": "org-1", "virtualRecordId": None, "version": 1,
            "connectorName": "", "extension": ext, "mimeType": mime, "recordName": name,
            "buffer": b"hello world",
        },
    }
    with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
                      return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)), \
         patch.object(ep, "_dispatch_pdf_binary", side_effect=pdf):
        try:
            async for _ in ep.on_event(data):
                pass
        except Exception as exc:
            if processor.routed:
                return f"{processor.routed[0]}!{type(exc).__name__}"
            return f"!{type(exc).__name__}"
    return processor.routed[0] if processor.routed else "-"


def direct(ext: str | None, mime: str | None) -> list[Any]:
    """The pure consumers keyed on (extension, mime)."""
    from app.config.constants.arangodb import EventTypes
    from app.events.events import _is_code_file
    from app.exceptions.indexing_exceptions import PasswordProtectedError
    from app.modules.indexing.connector_off_events import clears_embeddings_first
    from app.modules.parsers import encryption
    from app.services.parsing.registry import _normalize_format
    from app.services.resource_governor import kinds, tiers

    def password_checked() -> bool:
        try:
            encryption.raise_if_password_protected_office(b"x", extension=ext, mime_type=mime)
        except PasswordProtectedError:
            return True
        return False

    with patch.object(encryption, "has_encrypted_package", return_value=True):
        return [
            _outcome(_normalize_format, mime, ext),
            _outcome(lambda: tiers.classify(ext, mime).value),
            _outcome(lambda: kinds.classify_kind(ext, mime).value),
            _outcome(password_checked),
            _outcome(_is_code_file, mime, ext),
            _outcome(clears_embeddings_first, EventTypes.UPDATE_RECORD.value, {"mimeType": mime, "extension": ext}),
        ]


def _outcome(fn: Callable[..., object], *args: object) -> object:
    """The call's result, or ``!ErrorType`` when it raises."""
    try:
        return fn(*args)
    except Exception as exc:
        return f"!{type(exc).__name__}"


async def docling_name(ext: str) -> str:
    from app.services.parsing.providers.local_docling_parser import LocalDoclingParser

    class _Stop(Exception):
        pass

    seen: list[str] = []

    async def parse_document(doc_name: str, _content: bytes) -> NoReturn:
        seen.append(doc_name)
        raise _Stop

    proc = MagicMock()
    proc.parse_document = parse_document
    with patch("app.services.parsing.providers.local_docling_parser.ensure_docling", new_callable=AsyncMock):
        with contextlib.suppress(_Stop):
            await LocalDoclingParser(proc).parse(b"", f"Report.{ext}")
    return seen[0]


def per_extension(ext: str | None) -> list[Any]:
    from app.config.constants.arangodb import (
        SUPPORTED_CODE_FILE_EXTENSIONS,
        get_mime_type_for_extension,
    )
    from app.sandbox.artifact_upload import infer_artifact_type

    return [
        get_mime_type_for_extension(ext),
        get_mime_type_for_extension(ext, fallback="text/plain"),
        ext in SUPPORTED_CODE_FILE_EXTENSIONS,
        infer_artifact_type("application/x-none", f"f.{ext}").value,
    ]


def per_mime(mime: str | None) -> list[Any]:
    from app.utils.image_utils import get_extension_from_mimetype

    return [get_extension_from_mimetype(mime)]


def global_lists() -> dict[str, Any]:
    from app.config.constants.arangodb import (
        EXCLUDABLE_FILE_EXTENSIONS,
        FILE_MIME_TYPES,
        RECONCILIATION_ENABLED_EXTENSIONS,
        RECONCILIATION_ENABLED_MIME_TYPES,
        ExtensionTypes,
        MimeTypes,
    )
    from app.connectors.core.registry.connector_builder import CommonFields
    from app.connectors.sources.github_teams import constants as github
    from app.connectors.sources.gitlab import constants as gitlab
    from app.connectors.sources.web.connector import WEB_FILE_TYPES
    from app.modules.parsers.encryption import OOXML_EXTENSIONS, OOXML_MIME_TYPES
    from app.services.parsing.providers.local_docling_parser import LocalDoclingParser
    from app.utils import libreoffice_convert

    return {
        "ExtensionTypes": [e.value for e in ExtensionTypes],
        "MimeTypes": {m.name: m.value for m in MimeTypes},
        "EXCLUDABLE_FILE_EXTENSIONS": list(EXCLUDABLE_FILE_EXTENSIONS),
        "file_extension_filter": [o.id for o in CommonFields.file_extension_filter().options],
        "FILE_MIME_TYPES": {k: v.value for k, v in FILE_MIME_TYPES.items()},
        "RECONCILIATION_ENABLED_EXTENSIONS": sorted(RECONCILIATION_ENABLED_EXTENSIONS),
        "RECONCILIATION_ENABLED_MIME_TYPES": sorted(RECONCILIATION_ENABLED_MIME_TYPES),
        "OOXML_EXTENSIONS": sorted(OOXML_EXTENSIONS),
        "OOXML_MIME_TYPES": sorted(OOXML_MIME_TYPES),
        "WEB_FILE_TYPES": [e.value for e in WEB_FILE_TYPES],
        "gitlab.PREVIEW_RENDERABLE_EXTENSIONS": sorted(gitlab.PREVIEW_RENDERABLE_EXTENSIONS),
        "github.PREVIEW_RENDERABLE_EXTENSIONS": sorted(github.PREVIEW_RENDERABLE_EXTENSIONS),
        "libreoffice_probe_inputs": sorted(libreoffice_convert._PROBE_SAMPLES),
        "docling_formats": LocalDoclingParser(MagicMock()).supported_formats(),
    }


# --------------------------------------------------------------------------- run


async def decide(case: Sequence[Any]) -> list[Any]:
    ext, mime, name, rt = case
    return [await gate(ext, mime, name, rt), await legacy(ext, mime, name, rt), *direct(ext, mime)]


async def _decide_all(cases: Sequence[Sequence[Any]]) -> list[list[Any]]:
    return [await decide(c) for c in cases]


def _axes(cases: Sequence[Sequence[Any]]) -> tuple[list[Any], list[Any]]:
    def text(values: Iterable[object]) -> list[Any]:
        """The string (or missing) values; the malformed ones are pinned through the rows only."""
        return sorted({v for v in values if v is None or isinstance(v, str)}, key=lambda v: (v is not None, v or ""))

    return text(c[0] for c in cases), text(c[1] for c in cases)


async def compute(cases: Sequence[Sequence[Any]]) -> dict[str, Any]:
    exts, mimes = _axes(cases)
    docling_exts = sorted({e.lstrip(".") for e in exts if e and e.lstrip(".")})
    with _quiet_env():
        rows = await _decide_all(cases)
        return {
            "rows": [[*c, *r] for c, r in zip(cases, rows, strict=True)],
            "extensions": [[e, *per_extension(e)] for e in exts],
            "mimes": [[m, *per_mime(m)] for m in mimes],
            "docling_names": [[e, await docling_name(e)] for e in docling_exts],
            "globals": global_lists(),
        }


class _quiet_env:
    """The defaults main ships: the legacy pipeline, nothing forced on."""

    _VARS = ("USE_PARSING_SERVICE", "ENABLE_PDFPLUMBER_PROCESSOR")

    def __enter__(self) -> None:
        self._saved = {k: os.environ.pop(k, None) for k in self._VARS}

    def __exit__(self, *_exc: object) -> None:
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v


ROW_COLUMNS = ("extension", "mime", "name", "record_type", "gate", "legacy", "parse_format",
               "tier", "kind", "password_check", "is_code_file", "clears_embeddings_first")


def dump(snapshot: dict[str, Any]) -> str:
    """One row per line, so a delta reads as a one-line diff."""
    lines = ["{", f'  "columns": {json.dumps(list(ROW_COLUMNS))},']
    for key in ("rows", "extensions", "mimes", "docling_names"):
        lines.append(f'  "{key}": [')
        items = snapshot[key]
        lines.extend(f"    {json.dumps(r)}{',' if i < len(items) - 1 else ''}" for i, r in enumerate(items))
        lines.append("  ],")
    lines.append(f'  "globals": {json.dumps(snapshot["globals"], indent=2, sort_keys=True)}')
    lines.append("}")
    return "\n".join(lines) + "\n"


def load(path: Path = SNAPSHOT) -> dict[str, Any]:
    return json.loads(path.read_text())


def pinned_cases(snapshot: dict[str, Any]) -> list[tuple[Any, ...]]:
    return [tuple(r[:4]) for r in snapshot["rows"]]


def iter_shard(items: Sequence[Any], shard: int, shards: int) -> Iterator[Any]:
    return (x for i, x in enumerate(items) if i % shards == shard)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", nargs="?", const=str(SNAPSHOT), metavar="PATH")
    parser.add_argument("--replay", nargs=2, metavar=("PINNED", "OUT"),
                        help="recompute the decisions for the universe pinned in PINNED into OUT")
    args = parser.parse_args(argv)
    if args.write:
        cases = build_cases()
        Path(args.write).write_text(dump(asyncio.run(compute(cases))))
        print(f"wrote {len(cases)} cases to {args.write}")
        return 0
    if args.replay:
        pinned, out = (Path(p) for p in args.replay)
        out.write_text(dump(asyncio.run(compute(pinned_cases(load(pinned))))))
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
