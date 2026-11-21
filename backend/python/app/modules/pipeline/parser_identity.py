"""Which parser built a record's indexed copy, and the rules that compare it with what the record is now.

The same-bytes skip (``EventProcessor._is_same_bytes_update``) knows the bytes are unchanged; it cannot know the copy was
built by the parser the record would use today. A file renamed from ``.txt`` to ``.md`` keeps its bytes and changes its
parser, and an update that carries the new name would be skipped against a copy built as plain text.

The identity is ``"<pipeline>:<route>@<rev>"`` (``inproc:txt@1``). Its **key** is the part after the pipeline, ``<route>@<rev>``:
two copies are the same when their keys are equal. ``<route>`` is the route of the dispatch that runs (``file_types.legacy_route`` in process, ``file_types.parse_format`` in the service);
``<rev>`` is ``PARSER_ROUTE_REV[route]`` (default 1), bumped by hand when a change to that parser makes a re-run of
unchanged bytes worth its cost. The pipeline (``inproc`` or ``svc``) is stored but never compared, so a fleet that is
mixed on ``USE_PARSING_SERVICE`` does not re-run the same bytes back and forth. A change inside the parsing service is
not detected: that is a forced reindex.

Both sides of every comparison are derived by one function from the same four inputs (``parser_key``): the type
the event payload carries when the run starts, and the type the graph holds when a later update, a twin attach or a
promotion is judged. The graph keeps ``mimeType``, ``recordName`` and ``recordType`` on the ``records`` node and the
file's ``extension`` on its ``files`` document, so callers read that document too.

``None`` means "not tracked" (code, SQL, an unnamed route, anything unknown): nothing is stored and every check that
meets it behaves as it did before the identity existed.

The fields are written only while ``RECORD_PARSER_IDENTITY_WRITE`` is on (default off in the release that adds them);
see :func:`identity_write_enabled`.
"""

from __future__ import annotations

import os
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Final

from app.config import file_types
from app.config.constants.arangodb import (
    CODE_FILE_EXTENSION_VALUES,
    CODE_FILE_MIME_TYPE_VALUES,
    MimeTypes,
    RecordTypes,
    normalize_file_extension,
)
from app.config.file_types import Route
from app.modules.parsers.code_parser.file_role import is_generated_file_name
from app.modules.parsers.file_prep import prepared_labels

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

IDENTITY_FIELD: Final = "indexedParser"
#: The ``lastIndexTimestamp`` the identity was written with: it is known only while the record still carries it.
IDENTITY_AT_FIELD: Final = "indexedParserAt"
#: The ``virtualRecordId`` the identity was written with: a writer that moves the record to another copy makes it unknown.
IDENTITY_VRID_FIELD: Final = "indexedParserVrid"
IDENTITY_SOURCE_FIELD: Final = "indexedParserSource"
IDENTITY_FIELDS: Final = (IDENTITY_FIELD, IDENTITY_AT_FIELD, IDENTITY_VRID_FIELD, IDENTITY_SOURCE_FIELD)

#: A parse of this record built the copy.
SOURCE_PARSED: Final = "parsed"
#: A same-bytes update found no identity and labelled the copy with what the record is now (never confirmed by a parse).
SOURCE_ADOPTED: Final = "adopted"
#: The copy is another record's, attached or promoted with that record's identity.
SOURCE_TWIN: Final = "twin"

PIPELINE_IN_PROCESS: Final = "inproc"
PIPELINE_SERVICE: Final = "svc"

#: Bumped by hand: a bump makes every same-bytes update of that route run in full once, which is a spend decision.
PARSER_ROUTE_REV: Final[dict[str, int]] = {}

_UNTRACKED_ROUTES: Final = frozenset({Route.CODE, Route.SQL_TABLE, Route.SQL_VIEW})


def identity_write_enabled() -> bool:
    """``RECORD_PARSER_IDENTITY_WRITE``: write the identity and judge twins by it.

    Off (the default) in the release that adds the fields: a process on the previous record schema re-applies it at start and
    refuses a write of a field it does not know, so the schema ships first and the writes one release later. While it is off
    no stored identity is compared either: nothing would refresh a stale label.
    """
    return os.getenv("RECORD_PARSER_IDENTITY_WRITE", "false").strip().lower() in {"1", "true", "yes", "on"}


# --------------------------------------------------------------------------------------------------- how a record is read


def is_code_file(mime_type: str | None, code_ext: str | None) -> bool:
    """Whether a record is parsed as source and projected into the code graph (mime or extension; not the record type)."""
    return (
        mime_type in CODE_FILE_MIME_TYPE_VALUES
        or normalize_file_extension(code_ext) in CODE_FILE_EXTENSION_VALUES
    )


def reads_as_code_file(record_type: str | None, mime_type: str, code_ext: str | None, record_name: str) -> bool:
    """True when ``Processor.process_code_document`` decides how a record is read (see ``EventProcessor._reads_as_code_file``)."""
    if is_code_file(mime_type, code_ext):
        return True
    return record_type == RecordTypes.CODE_FILE.value and (
        mime_type == MimeTypes.PLAIN_TEXT.value or is_generated_file_name(record_name)
    )


def _known(value: object) -> str | None:
    return value if isinstance(value, str) and value not in ("", "unknown") else None


def service_pipeline_configured() -> bool:
    """Whether this process parses through the parsing service (``USE_PARSING_SERVICE``), as the store-side comparisons assume."""
    return os.environ.get("USE_PARSING_SERVICE", "false").lower() == "true"


def parser_key(
    mime_type: str | None, extension: str | None, record_name: str | None, record_type: str | None,
    *, service: bool | None = None, file_path: str | None = None,
) -> str | None:
    """``"<route>@<rev>"`` for the parser a record with these labels is read by, or None when it is not tracked.

    The four inputs are all a graph record and a file's ``files`` document hold, and all an event payload carries, so a
    payload and the graph give the same answer for the same record. The file path is not held by the graph
    record, so a key built with one is never compared (see :func:`parser_identity`).
    ``service`` says which dispatch reads the file: the in-process one (``file_types.legacy_route``) or the parsing
    service's (``file_types.parse_format``). The two name different parsers for some labels (``application/json`` with a
    ``csv`` extension is a CSV in process and JSON in the service), so the key is the route of the dispatch that runs.
    None takes the process's own setting.
    """
    if service is None:
        service = service_pipeline_configured()
    mime, ext, name = _known(mime_type), _known(extension), record_name or ""
    code_ext = file_types.code_extension(ext, file_path, name)
    as_code = reads_as_code_file(record_type, mime or "", code_ext, name)
    dispatch = file_types.legacy_route(
        mime, ext, code_ext=code_ext, reads_as_code_file=as_code,
        is_code_file_record=record_type == RecordTypes.CODE_FILE.value,
    )
    if dispatch is None or dispatch.route in _UNTRACKED_ROUTES:
        return None
    if dispatch.file_type is not None and dispatch.file_type.ext_wins:
        as_code = False
    if as_code or is_code_file(mime, code_ext) or record_type == RecordTypes.CODE_FILE.value:
        return None
    if mime in (MimeTypes.SQL_TABLE.value, MimeTypes.SQL_VIEW.value):
        return None
    if service:
        # The service is sent the file as prepared (a macro-enabled or template package as the plain one it holds).
        sent_ext, sent_mime = prepared_labels(dispatch.file_type, extension, mime)  # the raw extension: "unknown" is sent as it is
        fmt = file_types.parse_format(sent_mime, file_types.service_extension(sent_mime, sent_ext, name))
        return None if not fmt or fmt == "code" else f"{fmt}@{PARSER_ROUTE_REV.get(fmt, 1)}"
    return f"{dispatch.route.value}@{PARSER_ROUTE_REV.get(dispatch.route, 1)}"


def parser_identity(
    mime_type: str | None, extension: str | None, record_name: str | None, record_type: str | None, *, pipeline: str,
    file_path: str | None = None,
) -> str | None:
    """The identity a run with these labels stores: ``"<pipeline>:<route>@<rev>"``, or None when not tracked."""
    service = pipeline == PIPELINE_SERVICE
    if path_decides(mime_type, extension, record_name, record_type, file_path, service=service):
        # The graph record holds no path and could never reproduce this key, so a copy built this way is not tracked.
        return None
    key = parser_key(mime_type, extension, record_name, record_type, service=service)
    return None if key is None else f"{pipeline}:{key}"


def path_decides(
    mime_type: str | None, extension: str | None, record_name: str | None, record_type: str | None,
    file_path: str | None, *, service: bool | None = None,
) -> bool:
    """Whether the file path changes the parser an event's labels name (the graph record holds no path to repeat it)."""
    return bool(file_path) and parser_key(
        mime_type, extension, record_name, record_type, service=service,
    ) != parser_key(mime_type, extension, record_name, record_type, service=service, file_path=file_path)


def key_of(identity: object) -> str | None:
    """The comparable part (``route@rev``) of a stored identity; None for anything that is not one."""
    if not isinstance(identity, str) or ":" not in identity:
        return None
    key = identity.split(":", 1)[1]
    return key if "@" in key else None


def graph_parser_key(
    record: Mapping[str, Any], file_doc: Mapping[str, Any] | None, *, service: bool | None = None,
) -> str | None:
    """The key of a record as the graph holds it: its ``records`` node and the ``files`` document with the same key."""
    return parser_key(
        record.get("mimeType"), (file_doc or {}).get("extension"), record.get("recordName"), record.get("recordType"),
        service=service,
    )


# ------------------------------------------------------------------------------------------------ what a record stores


def known_identity(record: Mapping[str, Any]) -> str | None:
    """The stored identity when it still describes the copy the record holds, else None.

    Known means: written with the record's current ``lastIndexTimestamp`` and with its current ``virtualRecordId``. A writer
    that rebuilds the copy (a run that does not record an identity, an older process) changes the timestamp; one that moves
    the record to another copy (a twin attach, a re-point) changes the vrid. Either makes the identity stale.
    """
    stored = record.get(IDENTITY_FIELD)
    if not isinstance(stored, str) or not stored:
        return None
    at, indexed_at = record.get(IDENTITY_AT_FIELD), record.get("lastIndexTimestamp")
    if isinstance(at, bool) or not isinstance(at, int) or at != indexed_at:
        return None
    vrid = record.get("virtualRecordId")
    if not vrid or record.get(IDENTITY_VRID_FIELD) != vrid:
        return None
    return stored


def source_for_copy(source: object) -> str:
    """The label a record taking another record's copy gets: a copy never confirmed by a parse stays ``adopted``."""
    return SOURCE_ADOPTED if source == SOURCE_ADOPTED else SOURCE_TWIN


def indexed_parser_fields(identity: str | None, source: str, *, at: int, vrid: str | None) -> dict[str, Any]:
    """The identity fields that go with a write of ``lastIndexTimestamp=at`` and ``virtualRecordId=vrid``.

    The one builder of these fields: the run's own COMPLETED write, the twin attach, the promotions and the re-point call it.
    Empty while the flag is off, and for an identity that is not tracked (the record's previous fields are left as they are).
    """
    if not identity or not vrid or not identity_write_enabled():
        return {}
    return {
        IDENTITY_FIELD: identity, IDENTITY_AT_FIELD: at, IDENTITY_VRID_FIELD: vrid, IDENTITY_SOURCE_FIELD: source,
    }


def copied_identity_fields(source_record: Mapping[str, Any], *, at: int, vrid: str | None) -> dict[str, Any]:
    """The fields for a record that takes ``source_record``'s copy: its identity when known, with its source label carried."""
    identity = known_identity(source_record)
    if identity is None:
        return {}
    return indexed_parser_fields(identity, source_for_copy(source_record.get(IDENTITY_SOURCE_FIELD)), at=at, vrid=vrid)


# ------------------------------------------------------------------------------------------------------- twin decisions


def twin_may_share(
    record_key: str | None, twin: Mapping[str, Any], twin_key: str | None,
) -> bool:
    """Whether a record may take (or wait behind) a twin's copy, given both graph keys; True when either is not tracked.

    A twin whose identity is known must equal the record's key. A twin with no known identity is judged by its own graph
    type: equal keys share (the dedup saving stays), different keys do not. Not tracked on either side: as before.
    """
    if record_key is None:
        return True
    known = known_identity(twin)
    if known is not None:
        known_key = key_of(known)
        return known_key is None or known_key == record_key
    return twin_key is None or twin_key == record_key


def promotion_fields(
    source: Mapping[str, Any], queued: Mapping[str, Any], queued_file: Mapping[str, Any] | None, *, at: int,
    vrid: str | None,
) -> dict[str, Any] | None:
    """The identity fields for a QUEUED duplicate that a finished record promotes, or None when it must not be promoted.

    None leaves the duplicate QUEUED: it is released by its own event (a type change always publishes one, and that event
    finds a source of another identity and indexes the record itself) or, at the latest, by the stranded-record sweep
    (``STRANDED_RECORD_REPUBLISH_AFTER_SECONDS``). An empty dict promotes it with no identity written.
    """
    if not identity_write_enabled():
        return {}
    if source.get("virtualRecordId") != vrid:
        # The source moved to another copy since the caller read its own: its identity describes a copy the duplicate is not given.
        return None
    queued_key = graph_parser_key(queued, queued_file)
    source_known = known_identity(source)
    if queued_key is None:
        return {}
    if source_known is not None:
        source_key = key_of(source_known)
        if source_key is not None and source_key != queued_key:
            return None
        return copied_identity_fields(source, at=at, vrid=vrid)
    return {}


def plan_promotions(
    source: Mapping[str, Any], queued: list[Mapping[str, Any]], files_by_key: Mapping[str, Mapping[str, Any] | None],
    *, at: int, vrid: str | None,
) -> dict[str, dict[str, Any]]:
    """``{record key: identity fields}`` for the QUEUED duplicates a finished record promotes; a record left out is not promoted.

    ``files_by_key`` holds each duplicate's ``files`` document (the extension is not on the ``records`` node).
    """
    plan: dict[str, dict[str, Any]] = {}
    for doc in queued:
        key = doc.get("_key") or doc.get("id")
        if not key:
            continue
        fields = promotion_fields(source, doc, files_by_key.get(key), at=at, vrid=vrid)
        if fields is not None:
            plan[key] = fields
    return plan


async def promotion_plan(
    source: Mapping[str, Any], queued: list[Mapping[str, Any]], new_status: str, vrid: str | None, at: int,
    fetch_file: Callable[[str], Awaitable[Mapping[str, Any] | None]] | None,
) -> dict[str, dict[str, Any]] | None:
    """The plan of a promotion of QUEUED duplicates by a finished record, or None when every duplicate is promoted as before.

    Only a COMPLETED promotion while the identity is written is planned. ``fetch_file`` reads a duplicate's ``files``
    document by record key.
    """
    if new_status != "COMPLETED" or not identity_write_enabled():
        return None
    files: dict[str, Mapping[str, Any] | None] = {}
    if fetch_file is not None:
        for doc in queued:
            key = doc.get("_key") or doc.get("id")
            if key:
                files[key] = await fetch_file(key)
    return plan_promotions(source, queued, files, at=at, vrid=vrid)


#: The parser identity of the run in this task, set when the run's labels are read and carried by ``TransformContext``.
_run_identity: ContextVar[str | None] = ContextVar("parser_identity_of_run", default=None)


def begin_run_identity(identity: str | None) -> None:
    _run_identity.set(identity)


def end_run_identity() -> None:
    # Set back rather than reset by token: an async generator closed from another context cannot reset.
    _run_identity.set(None)


def run_identity() -> str | None:
    return _run_identity.get()
