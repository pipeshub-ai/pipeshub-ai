import asyncio
import base64
import concurrent.futures
import contextlib
import errno
import json
import os
import random
import threading
import time
import uuid
from typing import TYPE_CHECKING, Any, Dict, NamedTuple, TypedDict

import aiohttp
import msgspec
import zstandard as zstd
from yarl import URL

from app.config.constants.arangodb import CollectionNames
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import (
    DefaultEndpoints,
    Routes,
    TokenScopes,
    config_node_constants,
)
from app.exceptions.storage_exceptions import (
    StorageRequestRejectedError,
    StoredRecordTooLargeError,
)
from app.modules.transformers.transformer import TransformContext, Transformer
from app.services.cache.interface import ISignedUrlCache, NoopSignedUrlCache
from app.services.cache.redis_signed_url_cache import RedisSignedUrlCache
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.services.redis.config import ClientOptions, RedisConnectionConfig
from app.services.redis.connection_provider_factory import get_redis_provider
from app.services.resource_governor.feedback import get_default_downstream_feedback
from app.services.vector_db.membership import (
    EMPTY_CONFIRM_DELAY_SECONDS,
    remaining_record_keys,
)
from app.utils.jwt import mint_service_token
from app.utils.request_context import inject_request_headers
from app.utils.storage_move import post_move_tree
from app.utils.storage_path import build_hierarchical_storage_path
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from app.utils.worker_scaling import scaled

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

COMPRESSION_THRESHOLD_BYTES_DEFAULT = 20 * 1024 * 1024
# Decks repeat the same base64 image on every slide, further apart than the
# default 4 MiB window at this level, so they barely compressed (112 -> 84 MiB).
# A 128 MiB window with long-distance matching finds them (-> 13 MiB). 2^27 is
# the default decoder's window limit; a bigger one would fail to decode. Level 6
# matched level 10 on decks and was within 8% on text, at 2-3x the speed.
COMPRESSION_LEVEL = 6
COMPRESSION_WINDOW_LOG = 27
DOWNLOAD_CONNECTION_LIMIT_DEFAULT = 100

# Decoding a large record holds ~2.4x its msgpack size as Python objects and,
# inline, freezes the event loop for the whole decode. Records over
# LARGE_DECODE_BYTES (msgpack, read from the zstd header) decode in a thread,
# LARGE_DECODE_SLOTS at a time per event loop. Independent of the compression
# threshold: that is a JSON size that steers writes, and at 0 it would send
# every record of a chat turn through the one slot. A record whose *stored*
# size is over it also downloads inside the slot: otherwise readers queued for
# the decode each hold their whole download while they wait.
LARGE_DECODE_BYTES = 20 * 1024 * 1024
LARGE_DECODE_SLOTS = 1
# Optional hard cap on one record's decoded (msgpack) bytes, for a deployment
# whose pods can't afford a single huge decode. Off by default: a refused record
# drops out of every answer and re-index without telling anyone.
_MAX_DECODED_BYTES_ENV = "PIPESHUB_RECORD_MAX_DECODED_BYTES"


def _decode_json(raw: "bytes | str") -> Any:  # noqa: ANN401 - stored records are free-form
    """Decode a stored-record envelope.

    Records under the compression threshold are stored as plain JSON, so the
    envelope now carries the whole record and parsing it *is* the record decode.
    That moved the cost from msgpack (already msgspec, a C decoder) onto the
    stdlib json module, which measured 12% of query-service CPU. msgspec is
    ~1.9x faster on a real 48KB record and is already a dependency.

    Accepts str so it can be passed to ``resp.json(loads=...)``; the UTF-8
    decode aiohttp does first costs ~2us on that record, well inside the win.

    Falls back to the stdlib rather than failing the fetch: a record msgspec
    rejects is still worth trying to read.
    """
    try:
        return msgspec.json.decode(raw)
    except Exception:
        return json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode("utf-8"))
_COMPRESSION_THRESHOLD_ENV = "PIPESHUB_RECORD_COMPRESSION_THRESHOLD_BYTES"


def _json_utf8_bytes(obj: Any) -> bytes:  # noqa: ANN401 - stored records are free-form
    """Stored records keep non-ASCII text as written, so `grep Borgartún` matches.

    A lone surrogate (from broken text extraction) cannot be encoded as UTF-8;
    such a record falls back to ASCII escapes rather than failing to store.
    """
    try:
        return json.dumps(obj, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:
        return json.dumps(obj).encode("utf-8")


class _RecordJson(NamedTuple):
    """A record JSON-encoded once, for both the compression decision and the upload.

    The stdlib encoder holds the GIL for the whole call, so a thread would not
    keep it off the event loop; on a large record each extra pass costs as
    much as the upload's own encode.
    """

    body: bytes  # as _json_utf8_bytes would write the record
    is_utf8: bool  # False when a lone surrogate forced ASCII escapes
    ascii_size: int  # len(json.dumps(record).encode("utf-8")), what the threshold measures


_BELOW_0X80 = bytes(range(0x80))
_CONTINUATION_BYTES = bytes(range(0x80, 0xC0))
_TWO_AND_THREE_BYTE_LEADS = bytes(range(0xC0, 0xF0))


def _ascii_json_size(utf8_json: bytes) -> int:
    """The length *utf8_json* would have had with ``ensure_ascii=True``.

    ensure_ascii changes only how DEL and non-ASCII characters are written: a
    6-byte ``\\uXXXX`` for DEL and every BMP character (1-3 bytes in UTF-8),
    and a 12-byte surrogate pair for a 4-byte character above the BMP.
    """
    size = len(utf8_json) + 5 * utf8_json.count(0x7F)
    if utf8_json.isascii():
        return size
    non_ascii = utf8_json.translate(None, _BELOW_0X80)
    leads = non_ascii.translate(None, _CONTINUATION_BYTES)
    astral = len(leads.translate(None, _TWO_AND_THREE_BYTE_LEADS))
    return size - len(non_ascii) + 6 * (len(leads) - astral) + 12 * astral


def _encode_record_json(record: Any) -> _RecordJson | None:  # noqa: ANN401 - stored records are free-form
    """None when the record is not JSON-serializable."""
    try:
        text = json.dumps(record, ensure_ascii=False)
    except (TypeError, ValueError):
        return None
    try:
        body = text.encode("utf-8")
    except UnicodeEncodeError:
        body = json.dumps(record).encode("utf-8")
        return _RecordJson(body, False, len(body))
    return _RecordJson(body, True, _ascii_json_size(body))


def _plain_envelope_bytes(
    record: Any,  # noqa: ANN401 - stored records are free-form
    encoded: _RecordJson | None,
    virtual_record_id: str | None,
) -> bytes:
    """``_json_utf8_bytes`` of the uncompressed envelope, reusing *encoded*."""
    if encoded is not None:
        try:
            vrid = json.dumps(
                virtual_record_id, ensure_ascii=not encoded.is_utf8
            ).encode("utf-8")
        except UnicodeEncodeError:
            vrid = None
        if vrid is not None:
            return b"".join((
                b'{"isCompressed": false, "record": ',
                encoded.body,
                b', "virtualRecordId": ',
                vrid,
                b"}",
            ))
    return _json_utf8_bytes(
        {"isCompressed": False, "record": record, "virtualRecordId": virtual_record_id}
    )


def _cloud_envelope_size(encoded: _RecordJson, virtual_record_id: str | None) -> int:
    """``len(json.dumps(envelope).encode())`` for the uncompressed cloud upload.

    aiohttp's ``json=`` writes ASCII escapes, so this is the size it sends.
    """
    return (
        len('{"record": , "isCompressed": false, "virtualRecordId": }')
        + encoded.ascii_size
        + len(json.dumps(virtual_record_id))
    )


def clean_top_level_empty_values(obj: dict[str, Any]) -> dict[str, Any]:
    """``obj`` without top-level keys whose value is None, "", [] or {}."""
    return {
        k: v
        for k, v in obj.items()
        if v is not None and v != "" and v != [] and v != {}
    }


def clean_stored_record(data: dict[str, Any]) -> dict[str, Any]:
    """A record dict as it is stored: empty values dropped at the top level of
    the record, of each block and of each block group."""
    cleaned = clean_top_level_empty_values(data)
    block_containers = cleaned.get("block_containers")
    if isinstance(block_containers, dict):
        for key in ("blocks", "block_groups"):
            if isinstance(block_containers.get(key), list):
                block_containers[key] = [
                    clean_top_level_empty_values(item) if isinstance(item, dict) else item
                    for item in block_containers[key]
                ]
    return cleaned


def compression_threshold_bytes() -> int:
    """Records whose JSON form exceeds this are stored compressed; 0 compresses everything."""
    raw = os.getenv(_COMPRESSION_THRESHOLD_ENV)
    if raw is None:
        return COMPRESSION_THRESHOLD_BYTES_DEFAULT
    try:
        return max(int(raw), 0)
    except ValueError:
        return COMPRESSION_THRESHOLD_BYTES_DEFAULT


def max_decoded_record_bytes() -> int | None:
    """Largest record (msgpack bytes) a reader will decode, or None for no limit (the default)."""
    try:
        value = int(os.getenv(_MAX_DECODED_BYTES_ENV) or 0)
    except ValueError:
        return None
    return value if value > 0 else None


_SIGNED_URL_CACHE_ENV = "PIPESHUB_SIGNED_URL_CACHE_SECONDS"
# storage.controller.ts signs download URLs for 3600s. Cache well inside that so
# a URL handed out at the end of its cached life still has plenty left to use.
_SIGNED_URL_CACHE_SECONDS_DEFAULT = 0  # off unless configured


def signed_url_cache_seconds() -> int:
    """TTL for cached storage download URLs; 0 disables the cache.

    Resolving one is a gateway round trip that does a Mongo document lookup, a
    KV config read and an S3 signing call, and a chat turn does ~120 of them.
    Off by default so behaviour is unchanged until a deployment opts in.
    """
    raw = os.getenv(_SIGNED_URL_CACHE_ENV)
    if raw is None:
        return _SIGNED_URL_CACHE_SECONDS_DEFAULT
    try:
        return max(min(int(raw), 3000), 0)
    except ValueError:
        return _SIGNED_URL_CACHE_SECONDS_DEFAULT


# Must expire before Node closes an idle keep-alive socket, or we reuse one the
# gateway has already closed. Node holds them 65s (server.keepAliveTimeout in
# app.ts). The wide gap is the point: our clock only starts once the event loop
# gets round to releasing the connection, which a busy loop delays by seconds.
NODE_KEEPALIVE_MARGIN_SECONDS = 4.0

# Per-read stall bound on every request the shared session makes. No total
# bound: a multi-GB upload to blob storage is legitimately long, but a socket
# that goes this long without delivering a byte is a hung gateway, and
# aiohttp's default (a 5-minute total, applied per call) let each one of
# those sit on a record's processing budget.
BLOB_HTTP_SOCK_CONNECT_TIMEOUT_SECONDS = 10.0
BLOB_HTTP_SOCK_READ_TIMEOUT_SECONDS = 120.0

# A storage request that fails transiently is retried a few times, quickly.
# Without this, one reset connection or one gateway 503 on a 200ms request
# failed the whole record, which the consumer then re-downloaded, re-parsed
# and re-embedded to redo that request. Bounded to a few seconds in total: a
# storage service that is genuinely down is the record-level retry's job,
# and the governor's, once told.
_STORAGE_RETRY_ATTEMPTS = 3
_STORAGE_RETRY_BASE_SECONDS = 0.5
_STORAGE_RETRY_CAP_SECONDS = 4.0
_TRANSIENT_STORAGE_STATUSES = frozenset({502, 503, 504})
_STORAGE_SERVICE_NAME = "storage"


class TransientStorageError(aiohttp.ClientError):
    """A storage response (502/503/504) that a retry can reasonably fix."""


# What a stored record holds that is the same for every record sharing its vrid;
# everything else (name, link, ids, connector, statuses) belongs to one record.
SHARED_CONTENT_FIELDS = (
    "block_containers", "semantic_metadata", "md5_hash", "virtual_record_id",
    "is_vlm_ocr_processed",
)
#: ``TransformContext.settings`` key: the stored copy's fields outside
#: ``SHARED_CONTENT_FIELDS``, as ``apply`` wrote them. They are the owner's when
#: another record stored the shared content first.
STORED_RECORD_FIELDS = "stored_record_fields"
#: ``TransformContext.settings`` key: the ``contentRev`` the record being stored
#: is of (None when it has none). ``apply`` records it on the vrid's mapping as
#: ``storedRev`` in the same write, so the artifact reader serves a set only when
#: it is the revision the stored record holds. Without the key the mapping's
#: ``storedRev`` is left as it is.
STORED_REV = "stored_rev"

# Leaves the mapping's ``storedRev`` as it is: a rewrite of the same revision
# (classification) does not know the revision, and must not clear it.
_KEEP_STORED_REV: Any = object()


class StorageLookupUnavailableError(Exception):
    """Whether stored content exists could not be determined (not: it is absent)."""


class StorageDocumentNotFoundError(aiohttp.ClientError):
    """The storage service has no live document with this id (404).

    Deleted or never created, so repeating the request cannot help. A write
    can replace the document; a read has nothing to return.
    """


# Statuses meaning the storage service refused the request's content (too large,
# unsupported, malformed): every retry gets the same answer.
_REJECTED_STORAGE_STATUSES = frozenset({400, 413, 415, 422})


def _storage_status_error(
    status: int, message: str, detail: str | None = None
) -> aiohttp.ClientError:
    if status in _TRANSIENT_STORAGE_STATUSES:
        return TransientStorageError(message)
    if status == HttpStatusCode.NOT_FOUND.value:
        return StorageDocumentNotFoundError(message)
    if status in _REJECTED_STORAGE_STATUSES:
        reason = f"{message}: the storage service refused it (HTTP {status})"
        too_large = status == HttpStatusCode.PAYLOAD_TOO_LARGE.value or "larger than" in (detail or "").lower()
        return StorageRequestRejectedError(
            f"{reason}: {detail}" if detail else reason, status, too_large=too_large, detail=detail
        )
    return aiohttp.ClientError(message)


def _storage_error_detail(body: object) -> str | None:
    """The human-readable reason in a storage error response, when it has one."""
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
        if isinstance(body.get("message"), str):
            return body["message"]
    if isinstance(body, str) and body.strip():
        return body.strip()[:300]
    return None


# Failures that can be retried only when the request is idempotent: the
# request may have reached the server before the connection died.
_RETRY_AFTER_SEND = (
    TransientStorageError,
    aiohttp.ServerDisconnectedError,
    aiohttp.ClientOSError,
    asyncio.TimeoutError,
)


def _with_idempotency_key(headers: dict[str, str]) -> dict[str, str]:
    """Headers for one logical document create, reused by all its retries.

    The storage service returns the document a first attempt already created
    for this key instead of a duplicate, so the create can be retried after
    any transient failure, including one where the server may have acted.
    """
    return {**headers, "Idempotency-Key": uuid.uuid4().hex}


def _request_body_not_delivered(error: BaseException) -> bool:
    """The connection died before the whole request body had left the client.

    Typically a pooled keep-alive socket the server closed just as we reused
    it. The server then never has a complete body to act on, so even a
    non-idempotent request (appending a version) is safe to send again. Two
    shapes prove it, depending on whether aiohttp's body writer or the
    connection reports the failure first:

    - "Can not write request body": raised only for an OSError while the body
      is still being written. ClientRequest.write_bytes writes EOF after that
      block, and a drain inside it waits only while bytes are unsent; a test
      pins the ordering against aiohttp upgrades.
    - EPIPE: only a send fails with it, and nothing is sent once the whole
      request has left.

    A bare ECONNRESET proves nothing: it can equally come from reading the
    response of a request the server already processed.
    """
    if not isinstance(error, aiohttp.ClientOSError):
        return False
    return error.errno == errno.EPIPE or (error.strerror or "").startswith(
        "Can not write request body"
    )

def _is_azure_sas_url(signed_url: str) -> bool:
    """An Azure Blob SAS URL: its query carries the service version and signature."""
    try:
        query = URL(signed_url, encoded=True).query
    except (TypeError, ValueError):
        return False
    return "sv" in query and "sig" in query


def _signed_upload_headers(signed_url: str) -> dict[str, str]:
    """Headers a PUT to a signed URL from the direct-upload route needs.

    Azure's Put Blob refuses a request that does not name the blob type, and
    the route returns only the URL.
    """
    return {"x-ms-blob-type": "BlockBlob"} if _is_azure_sas_url(signed_url) else {}


def _upload_succeeded(status: int) -> bool:
    # S3 and local storage answer a PUT with 200, Azure with 201.
    return 200 <= status < 300


_shared_sessions: "dict[asyncio.AbstractEventLoop, aiohttp.ClientSession]" = {}


def download_connection_limit() -> int:
    """Max simultaneous connections to the storage API, 0 for unbounded."""
    raw = os.getenv("PIPESHUB_STORAGE_CONNECTION_LIMIT", "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError:
            pass
        else:
            if value >= 0:
                # 0 means unbounded; scaling it would turn that into a limit of 1.
                return value if value == 0 else scaled(value)
    return scaled(DOWNLOAD_CONNECTION_LIMIT_DEFAULT)


def get_shared_session() -> aiohttp.ClientSession:
    """Process-wide download session, one per running event loop.

    ``BlobStorage`` is constructed ad hoc at ~20 call sites (per request, per
    tool call), so a per-instance session would build and leak a connection
    pool per request. Keyed by loop because a session binds to the loop that
    created it.

    The pool is bounded: record fetches fan out per concurrent turn, and an
    unbounded pool opened ~1,400 simultaneous sockets to the Node API at 32
    concurrent users, past its 511-deep listen backlog, so connections were
    refused and record fetches failed. Queueing above the limit is strictly
    better than a refused connection.
    """
    loop = asyncio.get_running_loop()
    session = _shared_sessions.get(loop)
    if session is not None and not session.closed:
        return session

    if len(_shared_sessions) > 1:
        for stale_loop in [lp for lp in _shared_sessions if lp.is_closed()]:
            _shared_sessions.pop(stale_loop, None)

    session = aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(
            limit=download_connection_limit(),
            # Well inside Node's keep-alive window; see
            # NODE_KEEPALIVE_MARGIN_SECONDS. aiohttp's own default (15s)
            # outlived Node's old 5s window, and reusing a connection the
            # gateway had already closed failed mid-request with "Server
            # disconnected": 51 record fetches and one tool call in a single
            # day's logs.
            keepalive_timeout=NODE_KEEPALIVE_MARGIN_SECONDS,
        ),
        timeout=aiohttp.ClientTimeout(
            total=None,
            sock_connect=BLOB_HTTP_SOCK_CONNECT_TIMEOUT_SECONDS,
            sock_read=BLOB_HTTP_SOCK_READ_TIMEOUT_SECONDS,
        ),
    )
    _shared_sessions[loop] = session
    return session


@contextlib.asynccontextmanager
async def _borrowed_session() -> "AsyncIterator[aiohttp.ClientSession]":
    """The shared session for one call's worth of requests.

    Replaces the throwaway session these upload paths used to open per call
    -- each of those built its own connector with no timeout and no
    connection limit, sidestepping the pooled session this module exists to
    provide. Never closes the session: it is shared.
    """
    yield get_shared_session()


async def close_shared_session() -> None:
    """Close the pooled session for the running loop; call from service shutdown."""
    loop = asyncio.get_running_loop()
    session = _shared_sessions.pop(loop, None)
    if session is not None and not session.closed:
        await session.close()


# Same reasoning as _shared_sessions: one cache per loop, not per BlobStorage.
# A cached `NoopSignedUrlCache` is the "unavailable" verdict, so an outage
# costs one failed connect per loop instead of one per record fetch.
_shared_redis: "dict[asyncio.AbstractEventLoop, ISignedUrlCache]" = {}
# One lock per loop, not one shared lock: agent action tools run background loops
# in this process (see agents/actions/*, asyncio.new_event_loop in a thread), and
# a single asyncio.Lock contended from two loops parks a waiter on one loop that
# the other's release() never wakes -- a hang, not an error. The threading.Lock
# only guards creating the per-loop lock, which is not awaited.
_shared_redis_locks: "dict[asyncio.AbstractEventLoop, asyncio.Lock]" = {}
_shared_redis_locks_guard = threading.Lock()


def _redis_lock_for(loop: "asyncio.AbstractEventLoop") -> asyncio.Lock:
    with _shared_redis_locks_guard:
        for stale in [lp for lp in _shared_redis_locks if lp.is_closed()]:
            _shared_redis_locks.pop(stale, None)
        lock = _shared_redis_locks.get(loop)
        if lock is None:
            lock = _shared_redis_locks[loop] = asyncio.Lock()
        return lock


# Per loop for the same reason as _shared_redis_locks: a semaphore shared
# across loops hangs instead of failing.
_decode_slots: "dict[asyncio.AbstractEventLoop, asyncio.Semaphore]" = {}
_decode_slots_guard = threading.Lock()


def _decode_slot_for(loop: "asyncio.AbstractEventLoop") -> asyncio.Semaphore:
    with _decode_slots_guard:
        for stale in [lp for lp in _decode_slots if lp.is_closed()]:
            del _decode_slots[stale]
        slot = _decode_slots.get(loop)
        if slot is None:
            slot = _decode_slots[loop] = asyncio.Semaphore(LARGE_DECODE_SLOTS)
        return slot


# One thread for every large decode in the process, not the default executor's
# many: glibc gives each thread its own malloc arena, which the rest of the
# process can't reuse, so decodes spread over N threads kept N arenas' worth of
# record memory (+40% peak on a text-heavy record, and growing with N).
_DECODE_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="record-decode")


class _DecodeSlotLease:
    """A held decode slot. Its holder releases it, unless a decode thread was started
    on it: then the thread releases it when it finishes, even if the holder is cancelled."""

    def __init__(self, slot: asyncio.Semaphore) -> None:
        self._slot = slot
        self.handed_off = False

    def hand_off(self) -> asyncio.Semaphore:
        self.handed_off = True
        return self._slot

    def release(self) -> None:
        if not self.handed_off:
            self.handed_off = True
            self._slot.release()


async def _acquire_decode_slot() -> asyncio.Semaphore:
    # No time limit: a reader waits its turn rather than drop the record from an
    # answer, and a cancelled reader leaves the queue at once.
    slot = _decode_slot_for(asyncio.get_running_loop())
    await slot.acquire()
    return slot


def _compressed_content_size(data: dict[str, Any]) -> int | None:
    """Decoded size from the zstd frame header; None if not compressed or unreadable."""
    encoded: object = data.get("record")
    if not data.get("isCompressed") or not isinstance(encoded, str):
        return None
    try:
        # A zstd frame header is at most 18 bytes = 24 base64 chars.
        return zstd.get_frame_parameters(base64.b64decode(encoded[:24])).content_size
    except Exception:
        return None


async def get_shared_signed_url_cache(config_service: Any, logger: Any) -> ISignedUrlCache:  # noqa: ANN401
    """Process-wide `ISignedUrlCache`, one per event loop.

    Returns a `NoopSignedUrlCache` when the cache is disabled or Redis is
    unreachable, so callers never need a None check -- they get the same
    uncached behaviour they had before this cache existed.
    """
    if not signed_url_cache_seconds():
        return NoopSignedUrlCache()
    loop = asyncio.get_running_loop()
    if loop in _shared_redis:
        return _shared_redis[loop]

    async with _redis_lock_for(loop):
        if loop in _shared_redis:
            return _shared_redis[loop]
        for stale_loop in [lp for lp in _shared_redis if lp.is_closed()]:
            _shared_redis.pop(stale_loop, None)

        cache: ISignedUrlCache
        client = None
        try:
            cfg = await config_service.get_redis_config()
            provider = get_redis_provider(
                RedisConnectionConfig.from_host_port(
                    host=cfg.host, port=cfg.port, password=cfg.password, db=cfg.db, tls=cfg.tls
                )
            )
            client = provider.create_client(
                ClientOptions(
                    decode_responses=True,
                    socket_timeout_seconds=2.0,
                    socket_connect_timeout_seconds=2.0,
                )
            )
            await client.ping()
            cache = RedisSignedUrlCache(client, provider.key_namespace)
        except Exception as e:
            if client is not None:
                try:
                    await client.aclose()
                except Exception:
                    pass
            cache = NoopSignedUrlCache()
            logger.warning("Signed-URL cache unavailable, disabled: %s", str(e))
        _shared_redis[loop] = cache
        return cache


async def close_shared_redis() -> None:
    """Close the pooled signed-URL cache for the running loop; call from shutdown."""
    loop = asyncio.get_running_loop()
    cache = _shared_redis.pop(loop, None)
    if cache is not None:
        await cache.close()


class CustomMetadataEntry(TypedDict):
    key: str
    value: Any  # NOTE: 'Any' is used here because storage metadata values may be str, int, bool, or even structured types, depending on the client and blob store requirements.

def _versioned_json_form(
    json_data: bytes,
    document_name: str,
    virtual_record_id: str,
    record_id: str,
    *,
    compressed: bool,
    document_path: str | None = None,
    connector_id: str | None = None,
    record_group_id: str | None = None,
) -> aiohttp.FormData:
    """Multipart body for a JSON document upload.

    When *document_path* is supplied the document is stored at that
    hierarchical path as a non-versioned file (used by the blob-tree
    storage layout).  Otherwise it falls back to the flat
    ``records/<vrid>`` path with versioning enabled.

    Build one per attempt: an aiohttp FormData can be sent only once.
    """
    use_hierarchical = document_path is not None
    effective_path = document_path if use_hierarchical else f'records/{virtual_record_id}'

    form_data = aiohttp.FormData()
    form_data.add_field('file', json_data, filename=f'{document_name}.json', content_type='application/json')
    form_data.add_field('documentName', document_name)
    form_data.add_field('documentPath', effective_path)
    form_data.add_field('isVersionedFile', 'false' if use_hierarchical else 'true')
    form_data.add_field('extension', 'json')
    form_data.add_field('recordId', record_id)
    idx = 0
    if compressed:
        form_data.add_field(f'customMetadata[{idx}][key]', 'compression')
        form_data.add_field(f'customMetadata[{idx}][value][algorithm]', 'zstd')
        form_data.add_field(f'customMetadata[{idx}][value][level]', str(COMPRESSION_LEVEL))
        form_data.add_field(f'customMetadata[{idx}][value][format]', 'msgspec')
        form_data.add_field(f'customMetadata[{idx}][value][version]', 'v1')
        form_data.add_field(f'customMetadata[{idx}][value][compressed]', 'true')
        idx += 1
    if connector_id:
        form_data.add_field(f'customMetadata[{idx}][key]', 'connectorId')
        form_data.add_field(f'customMetadata[{idx}][value]', connector_id)
        idx += 1
    if record_group_id:
        form_data.add_field(f'customMetadata[{idx}][key]', 'recordGroupId')
        form_data.add_field(f'customMetadata[{idx}][value]', record_group_id)
    return form_data


def _add_custom_metadata_to_form(
    form_data: aiohttp.FormData,
    custom_metadata: list[CustomMetadataEntry],
) -> None:
    """Append ``customMetadata`` fields for multipart storage uploads."""
    for i, meta in enumerate(custom_metadata):
        form_data.add_field(f"customMetadata[{i}][key]", meta["key"])
        value = meta["value"]
        if isinstance(value, bool):
            form_data.add_field(
                f"customMetadata[{i}][value]",
                str(value).lower(),
            )
        elif isinstance(value, str):
            form_data.add_field(f"customMetadata[{i}][value]", value)
        else:
            form_data.add_field(f"customMetadata[{i}][value]", str(value))


class BlobStorage(Transformer):
    def __init__(self,logger,config_service, graph_provider: IGraphDBProvider = None) -> None:
        self.logger = logger
        self.config_service = config_service
        self.graph_provider = graph_provider
        self.compression_enabled = os.environ.get("BLOB_STORAGE_COMPRESSION", "true").lower() in ("true", "1", "yes")

    async def _signed_url_client(self) -> ISignedUrlCache:
        """`ISignedUrlCache` for this loop; a `NoopSignedUrlCache` when disabled.

        Shared per event loop rather than per instance: BlobStorage is built ad
        hoc at ~20 call sites (per request, per tool call), so a per-instance
        client would open and leak a connection pool per request -- the same
        reason get_shared_session exists. Failures are never fatal; the caller
        falls back to asking the gateway.
        """
        return await get_shared_signed_url_cache(self.config_service, self.logger)

    async def _record_from_signed_url(
        self,
        session: "aiohttp.ClientSession",
        signed_url: str,
        file_size_bytes: int | None,
        virtual_record_id: str,
        lease: _DecodeSlotLease | None = None,
    ) -> dict | None:
        """Download and decode a record from an already-signed storage URL.

        Returns None when the payload carries no record, so the caller can decide
        whether that is an error or a reason to re-sign.
        """
        # Ranged download only pays off on large objects; an unknown size is
        # assumed large because that is the pre-existing behaviour.
        MIN_SIZE_FOR_PARALLEL = 3 * 1024 * 1024
        use_parallel = file_size_bytes is None or file_size_bytes >= MIN_SIZE_FOR_PARALLEL

        async def _single() -> dict:
            async with session.get(URL(signed_url, encoded=True)) as res:
                if res.status != HttpStatusCode.SUCCESS.value:
                    raise Exception(f"Failed to retrieve record: status {res.status}")
                return await res.json(content_type=None, loads=_decode_json)

        try:
            if use_parallel:
                # Not kept in a local: a reader waiting for a decode slot would hold
                # the raw download alongside the parsed envelope it was parsed into.
                data = _decode_json(await self._download_with_range_requests(
                    session, signed_url, chunk_size_mb=2, max_connections=6
                ))
            else:
                data = await _single()
        except Exception as e:
            if not use_parallel:
                self.logger.error("❌ Failed to retrieve record: %s", str(e))
                raise
            self.logger.warning(
                "⚠️ Parallel download failed: %s. Falling back to single download...", str(e)
            )
            try:
                data = await _single()
            except Exception as fallback_error:
                self.logger.error("❌ Fallback download also failed: %s", str(fallback_error))
                raise Exception(
                    f"Both parallel and fallback downloads failed: {str(e)}"
                ) from fallback_error

        if not data.get("record"):
            return None
        record = await self._decode_record(data, lease)
        self.logger.debug(
            "✅ Successfully retrieved record %s from storage for virtual_record_id: %s",
            record.get("record_name"), virtual_record_id,
        )
        return record

    @staticmethod
    def _signed_url_key(org_id: str, document_id: str) -> str:
        # org-scoped: the gateway is called with an org-scoped service token, and
        # per-user access is enforced before a record reaches this path.
        return f"sigurl:{org_id}:{document_id}"

    async def _cached_signed_url(self, org_id: str, document_id: str) -> str | None:
        cache = await self._signed_url_client()
        try:
            return await cache.get(self._signed_url_key(org_id, document_id))
        except Exception as e:
            self.logger.debug("Signed-URL cache read failed: %s", str(e))
            return None

    async def _store_signed_url(self, org_id: str, document_id: str, url: str) -> None:
        if not url:
            return
        cache = await self._signed_url_client()
        try:
            await cache.set(
                self._signed_url_key(org_id, document_id), url, signed_url_cache_seconds()
            )
        except Exception as e:
            self.logger.debug("Signed-URL cache write failed: %s", str(e))

    async def _get_auth_and_config(self, org_id: str) -> tuple[dict, str, str]:
        """
        Returns (headers, nodejs_endpoint, storage_type).
        """
        payload = {
            "orgId": org_id,
            "scopes": [TokenScopes.STORAGE_TOKEN.value],
        }
        # use_cache: these three reads are otherwise an etcd round trip each, on
        # every record download (~100 per chat turn). The config cache is
        # invalidated by the etcd watch and Pub/Sub, so reads stay current.
        secret_keys = await self.config_service.get_config(
            config_node_constants.SECRET_KEYS.value, use_cache=True
        )
        scoped_jwt_secret = secret_keys.get("scopedJwtSecret")
        if not scoped_jwt_secret:
            raise ValueError("Missing scoped JWT secret")

        jwt_token = mint_service_token(scoped_jwt_secret, payload)
        # Headers are rebuilt per call, never cached: inject_request_headers
        # stamps the caller's request id from a ContextVar.
        headers = inject_request_headers({"Authorization": f"Bearer {jwt_token}"})

        endpoints = await self.config_service.get_config(
            config_node_constants.ENDPOINTS.value, use_cache=True
        )
        nodejs_endpoint = endpoints.get("cm", {}).get(
            "endpoint", DefaultEndpoints.NODEJS_ENDPOINT.value
        )
        if not nodejs_endpoint:
            raise ValueError("Missing CM endpoint configuration")

        storage = await self.config_service.get_config(
            config_node_constants.STORAGE.value, use_cache=True
        )
        storage_type = storage.get("storageType")
        if not storage_type:
            raise ValueError("Missing storage type configuration")

        return headers, nodejs_endpoint, storage_type

    async def _get_public_download_base_url(self) -> str:
        """Resolve the externally-reachable base URL for user-facing document links.

        The CM endpoint (``cm.endpoint``) is the *internal* Node.js URL — in a
        containerised deployment it resolves to e.g. ``http://nodejs:3000`` and
        in a vanilla local stack it may be absent entirely, falling through to
        ``http://localhost:3000``. Neither is reachable from the user's
        browser, so it must not be used as the prefix for links returned to
        the client.

        Falls back through ``frontend.publicEndpoint`` (the standard
        user-facing URL across the codebase) → ``storage.endpoint`` → the
        ``FRONTEND_ENDPOINT`` default.
        """
        endpoints = await self.config_service.get_config(
            config_node_constants.ENDPOINTS.value
        )
        return (
            endpoints.get("frontend", {}).get("publicEndpoint")
            or endpoints.get("storage", {}).get("endpoint")
            or DefaultEndpoints.FRONTEND_ENDPOINT.value
        )

    def _maybe_compress_record(
        self, record: dict, *, label: str = "record", encoded: _RecordJson | None = None,
    ) -> tuple[str | None, bool]:
        """Decide whether a record is worth compressing, and compress it if so.

        Returns ``(compressed_base64_or_None, is_compressed)``. Pass *encoded*
        (from ``_encode_record_json``) to size the record without encoding it again.

        Compression is not free on the read side: the blob is base64'd into a
        JSON envelope, so every reader parses megabytes of base64 before it can
        even start the zstd+msgpack decode. Below the threshold that costs more
        than the bytes it saves, so small records are stored as plain JSON —
        a shape ``_process_downloaded_record`` already accepts.
        """
        try:
            serialized_size = (
                encoded.ascii_size if encoded is not None
                else len(json.dumps(record).encode("utf-8"))
            )
        except (TypeError, ValueError) as e:
            # Not JSON-serializable, so the uncompressed envelope would fail to
            # build. msgpack accepts more types — compress regardless of size.
            self.logger.debug("%s is not JSON-serializable (%s); compressing", label, str(e))
            serialized_size = None

        if serialized_size is not None and (
            not self.compression_enabled or serialized_size <= compression_threshold_bytes()
        ):
            return None, False

        try:
            return self._compress_record(record), True
        except Exception as e:
            self.logger.warning("⚠️ Compression failed, uploading uncompressed: %s", str(e))
            return None, False

    def _compress_record(self, record: dict) -> str:
        """
        Compress record data using msgspec (C-based) + zstd.
        Returns: base64_encoded_compressed_data
        """
        # Serialize directly to bytes using msgspec (high-performance msgpack encoder)
        msgpack_bytes = msgspec.msgpack.encode(record)
        original_size = len(msgpack_bytes)

        # level goes in from_level: ZstdCompressor ignores level= when given compression_params.
        params = zstd.ZstdCompressionParameters.from_level(
            COMPRESSION_LEVEL, window_log=COMPRESSION_WINDOW_LOG, enable_ldm=True,
        )
        # One-shot compress() writes the content size the reader's decode guard needs.
        # A compressor per call: they are not thread-safe.
        compressed = zstd.ZstdCompressor(compression_params=params).compress(msgpack_bytes)

        compressed_size = len(compressed)
        ratio = (1 - compressed_size / original_size) * 100
        self.logger.debug("📦 Compressed record (msgspec): %d -> %d bytes (%.1f%% reduction)",
                        original_size, compressed_size, ratio)

        return base64.b64encode(compressed).decode('utf-8')



    def _decompress_bytes(self, compressed_bytes: bytes) -> bytes:
        """
        Decompress raw bytes using zstd.
        Returns decompressed bytes.
        """
        decompressor = zstd.ZstdDecompressor()
        return decompressor.decompress(compressed_bytes)

    def _process_downloaded_record(self, data: dict) -> dict:
        """
        Process downloaded record data, handling decompression if needed.
        Supports new isCompressed flag format and backward compatibility with uncompressed records.
        """
        # NEW FORMAT: Check for isCompressed flag
        if data.get("isCompressed"):
            return self._unpack_payload(self._decompressed_payload(data))

        # OLD FORMAT: Uncompressed record
        elif data.get("record"):
            return data.get("record")

        else:
            # Unknown format
            self.logger.error("❌ Unknown record format in S3")
            raise Exception("Unknown record format")

    def _decompressed_payload(self, data: dict) -> bytes:
        """The msgpack bytes of a compressed envelope: base64 and zstd decode."""
        compressed_base64 = data.get("record")
        if not compressed_base64:
            self.logger.error("❌ isCompressed is true but no record found")
            raise Exception("Missing record in compressed record")
        try:
            return self._decompress_bytes(base64.b64decode(compressed_base64))
        except Exception as e:
            self.logger.error("❌ Failed to decompress record: %s", str(e))
            raise Exception(f"Decompression failed: {str(e)}") from e

    def _unpack_payload(self, payload: bytes) -> dict:
        try:
            return msgspec.msgpack.decode(payload)
        except Exception as e:
            self.logger.error("❌ Failed to decompress record: %s", str(e))
            raise Exception(f"Decompression failed: {str(e)}") from e

    async def _decode_record(
        self, data: dict[str, Any], lease: _DecodeSlotLease | None = None
    ) -> dict[str, Any]:
        """``_process_downloaded_record`` with large records gated and taken off the event loop.

        *lease* is a slot the caller already holds for this record; a large decode uses
        it instead of queueing for another.
        """
        size = _compressed_content_size(data)
        if size is None:
            return self._process_downloaded_record(data)
        # Unknown size (a streamed frame) still takes the gate, but has no size to check.
        ceiling = max_decoded_record_bytes()
        if ceiling is not None and size != zstd.CONTENTSIZE_UNKNOWN and size > ceiling:
            raise StoredRecordTooLargeError(
                f"record decodes to {size} bytes, over the {ceiling}-byte limit ({_MAX_DECODED_BYTES_ENV})"
            )
        if size <= LARGE_DECODE_BYTES:
            return self._process_downloaded_record(data)
        if lease is not None and not lease.handed_off:
            slot = lease.hand_off()
        else:
            slot = await _acquire_decode_slot()
        # Only zstd runs in the thread: it releases the GIL. msgpack holds it, so it
        # would stall the loop from a thread too, and there it would allocate the
        # record from the thread's malloc arena, which the process can't reuse.
        work = asyncio.get_running_loop().run_in_executor(
            _DECODE_EXECUTOR, self._decompressed_payload, data
        )
        released = abandoned = False

        def release() -> None:
            nonlocal released
            if not released:
                released = True
                slot.release()

        # A cancelled caller must not free the slot mid-decode: the thread does, when it ends.
        work.add_done_callback(lambda _: abandoned and release())
        # Read the exception so a cancelled caller doesn't leave "exception never retrieved" noise.
        work.add_done_callback(lambda f: f.cancelled() or f.exception())
        try:
            payload = await asyncio.shield(work)
        except BaseException:
            abandoned = True
            if work.done():
                release()
            raise
        try:
            return self._unpack_payload(payload)
        finally:
            release()

    async def _get_content_length(self, session: aiohttp.ClientSession, url: str) -> int:
        """
        Get content length of S3 object using Range GET request to fetch only headers.

        Args:
            session: aiohttp session
            url: S3 signed URL

        Returns:
            Content length in bytes, or 0 if not available
        """
        try:
            # Use Range header to request only the first byte to avoid downloading entire file
            headers = {'Range': 'bytes=0-0'}

            async with session.get(URL(url, encoded=True), headers=headers) as response:
                # For Range requests, Content-Range header contains the total size
                # Format: "bytes 0-0/total_size"
                if response.status == HttpStatusCode.PARTIAL_CONTENT.value:  # Partial Content
                    content_range = response.headers.get('Content-Range', '')
                    if content_range and '/' in content_range:
                        total_size = content_range.split('/')[-1]
                        return int(total_size)

                # Fallback to Content-Length if available (status 200)
                content_length = response.headers.get('Content-Length', None)
                return int(content_length) if content_length else None
        except Exception as e:
            self.logger.warning("⚠️ Failed to get content length: %s", str(e))
            return None

    async def _download_chunk_with_retry(
        self,
        session: aiohttp.ClientSession,
        url: str,
        start: int,
        end: int,
        chunk_index: int,
        max_retries: int = 3
    ) -> tuple[int, bytes]:
        """
        Download a single chunk with retry logic.

        Args:
            session: aiohttp session
            url: S3 signed URL
            start: Start byte position
            end: End byte position
            chunk_index: Index of this chunk (for ordering)
            max_retries: Maximum retry attempts

        Returns:
            Tuple of (chunk_index, chunk_bytes)
        """
        for attempt in range(max_retries):
            try:
                headers = {'Range': f'bytes={start}-{end}'}

                async with session.get(URL(url, encoded=True), headers=headers) as response:
                    if response.status in (HttpStatusCode.SUCCESS.value, HttpStatusCode.PARTIAL_CONTENT.value):
                        chunk_bytes = await response.read()
                        return (chunk_index, chunk_bytes)
                    else:
                        raise aiohttp.ClientError(f"Unexpected status {response.status}")
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = 0.5 * (2 ** attempt)  # Exponential backoff
                    self.logger.warning(
                        "⚠️ Chunk %d download failed (attempt %d/%d): %s. Retrying in %.1fs...",
                        chunk_index, attempt + 1, max_retries, str(e), wait_time
                    )
                    await asyncio.sleep(wait_time)
                else:
                    self.logger.exception(
                        "❌ Chunk %d download failed after %d attempts: %s",
                        chunk_index,
                        max_retries,
                        e,
                    )
                    raise


    async def _download_with_range_requests(
        self,
        session: aiohttp.ClientSession,
        signed_url: str,
        chunk_size_mb: int = 2,
        max_connections: int = 6
    ) -> bytes:
        """
        Download file in parallel chunks using HTTP Range requests.

        Args:
            session: aiohttp session
            signed_url: S3 signed URL
            chunk_size_mb: Size of each chunk in MB (default: 8MB)
            max_connections: Max parallel connections (default: 6)

        Returns:
            Complete file bytes

        Raises:
            Exception: If download fails or range requests not supported
        """
        download_start_time = time.time()

        # Get total file size
        total_size = await self._get_content_length(session, signed_url)

        if total_size is None or total_size == 0:
            raise Exception("Could not determine file size for parallel download")

        # Calculate chunk ranges
        chunk_size_bytes = chunk_size_mb * 1024 * 1024
        chunks = []
        for i in range(0, total_size, chunk_size_bytes):
            start = i
            end = min(i + chunk_size_bytes - 1, total_size - 1)
            chunks.append((start, end))

        num_chunks = len(chunks)

        # Download chunks in parallel with semaphore to limit concurrent connections
        semaphore = asyncio.Semaphore(max_connections)

        async def download_with_semaphore(chunk_index: int, start: int, end: int) -> tuple[int, bytes]:
            async with semaphore:
                return await self._download_chunk_with_retry(
                    session, signed_url, start, end, chunk_index
                )

        # Create tasks for all chunks
        tasks = [
            download_with_semaphore(i, start, end)
            for i, (start, end) in enumerate(chunks)
        ]

        # Execute all downloads in parallel
        try:
            results = await asyncio.gather(*tasks, return_exceptions=False)
        except Exception as e:
            self.logger.exception("❌ Parallel download failed: %s", e)
            raise

        # Reassemble chunks in correct order
        results.sort(key=lambda x: x[0])
        file_bytes = b''.join(chunk_data for _, chunk_data in results)

        # Calculate and log overall performance
        total_download_duration_ms = (time.time() - download_start_time) * 1000
        total_size_mb = total_size / (1024 * 1024)
        effective_speed_mbps = 0
        if total_download_duration_ms > 0:
            effective_speed_mbps = total_size_mb / (total_download_duration_ms / 1000)

        self.logger.info(
            "🚀 Parallel download complete: %.2f MB in %.0fms (%.2f MB/s, %d chunks)",
            total_size_mb, total_download_duration_ms, effective_speed_mbps, num_chunks
        )

        # Verify size
        if len(file_bytes) != total_size:
            raise Exception(f"Size mismatch: expected {total_size} bytes, got {len(file_bytes)} bytes")

        return file_bytes

    def _clean_top_level_empty_values(self, obj: Dict[str, Any]) -> Dict[str, Any]:
        return clean_top_level_empty_values(obj)

    def _clean_empty_values(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return clean_stored_record(data)

    async def _build_hierarchical_storage_path(
        self,
        record: Any,
        virtual_record_id: str,
    ) -> str:
        """Build filesystem-like storage path for a record.

        Delegates to the shared ``build_hierarchical_storage_path`` utility
        so the same algorithm is used by both the writer (here) and the mover
        (``StorageCleanupHelper``).  Always returns a usable path — falls back
        to ``records/<virtual_record_id>`` when the hierarchy can't be computed.
        """
        try:
            path = await build_hierarchical_storage_path(
                record,
                self.graph_provider,
                virtual_record_id=virtual_record_id,
                logger=self.logger,
            )
            return path or f"records/{virtual_record_id}"
        except Exception as e:
            self.logger.warning(
                "Failed to build hierarchical path for record, falling back to flat path: %s", str(e)
            )
            return f"records/{virtual_record_id}"

    async def update_record_buffer(
        self,
        org_id: str,
        document_id: str,
        record_dict: dict,
        virtual_record_id: str,
    ) -> tuple[str | None, int | None]:
        """Override the current blob content in place without creating a new version.

        Uses PUT /buffer which accepts multipart/form-data with field 'file'.
        Works on both versioned and non-versioned storage documents.
        """
        try:
            headers, nodejs_endpoint, _ = await self._get_auth_and_config(org_id)

            encoded = _encode_record_json(record_dict)
            compressed_record, use_compression = self._maybe_compress_record(
                record_dict, encoded=encoded
            )

            if use_compression:
                json_bytes = _json_utf8_bytes({
                    "isCompressed": True,
                    "record": compressed_record,
                    "virtualRecordId": virtual_record_id,
                })
            else:
                json_bytes = _plain_envelope_bytes(record_dict, encoded, virtual_record_id)
            file_size_bytes = len(json_bytes)

            buffer_url = f"{nodejs_endpoint}{Routes.STORAGE_BUFFER.value.format(documentId=document_id)}"
            self.logger.info("📤 Overriding record buffer for document: %s", document_id)

            session = get_shared_session()
            form_data = aiohttp.FormData()
            form_data.add_field(
                "file",
                json_bytes,
                filename=f"record_{virtual_record_id}.json",
                content_type="application/json",
            )
            async with session.put(buffer_url, data=form_data, headers=headers) as resp:
                if resp.status != HttpStatusCode.SUCCESS.value:
                    error_text = await resp.text()
                    raise _storage_status_error(
                        resp.status, f"Failed to update buffer: {resp.status} {error_text[:200]}"
                    )

            self.logger.info("✅ Successfully overrode buffer for document: %s", document_id)
            return document_id, file_size_bytes

        except StorageDocumentNotFoundError:
            raise
        except Exception as e:
            self.logger.error("❌ Error in update_record_buffer for document %s: %s", document_id, str(e))
            raise

    @staticmethod
    def _strip_org_prefix(org_id: str, document_path: str) -> str:
        """Node stores documentPath as '<orgId>/PipesHub/<relative>'; Python
        builds relative paths ('records/...'). Strip the prefix so the two
        forms are comparable."""
        prefix = f"{org_id}/PipesHub/"
        if document_path.startswith(prefix):
            return document_path[len(prefix):]
        return document_path

    async def _get_current_document_path(
        self, org_id: str, document_id: str, *, strict: bool = False,
    ) -> str | None:
        """Fetch the ACTUAL currently-stored documentPath for a document via
        Node's internal document-info endpoint -- not recomputed/guessed.

        None means the document is gone (404). Any other failure is None too,
        unless *strict*, which raises StorageLookupUnavailableError instead.
        """
        try:
            headers, nodejs_endpoint, _ = await self._get_auth_and_config(org_id)
            get_url = f"{nodejs_endpoint}{Routes.STORAGE_DOCUMENT.value.format(documentId=document_id)}"
            session = get_shared_session()
            async with session.get(get_url, headers=headers) as response:
                if response.status == 404:
                    return None
                if response.status != HttpStatusCode.SUCCESS.value:
                    if strict:
                        raise StorageLookupUnavailableError(
                            f"Storage answered {response.status} for document {document_id}"
                        )
                    self.logger.warning(
                        "Unexpected status fetching current document path for %s: %s",
                        document_id, response.status
                    )
                    return None
                doc = await response.json()
                return doc.get("documentPath")
        except Exception as e:
            if strict:
                if isinstance(e, StorageLookupUnavailableError):
                    raise
                raise StorageLookupUnavailableError(
                    f"Could not read stored document {document_id}: {e}"
                ) from e
            self.logger.warning(
                "Could not fetch current document path for %s: %s", document_id, str(e)
            )
            return None

    async def get_actual_content_path(self, org_id: str, virtual_record_id: str) -> str | None:
        """Return a record's content document's ACTUAL current stored path, or
        None when there is provably none (no mapping, or storage answers 404).

        Raises StorageLookupUnavailableError when that cannot be told (graph or
        storage error, a 503 during a move): callers decide what "unknown"
        means instead of reading it as "content gone" and re-indexing.
        """
        if not self.graph_provider:
            return None
        try:
            existing_lookup = await self.get_document_id_by_virtual_record_id(virtual_record_id)
        except Exception as e:
            raise StorageLookupUnavailableError(
                f"Could not look up the stored document of vrid {virtual_record_id}: {e}"
            ) from e
        if not existing_lookup or not existing_lookup.get("record_doc_id"):
            return None
        raw_path = await self._get_current_document_path(
            org_id, existing_lookup["record_doc_id"], strict=True,
        )
        return self._strip_org_prefix(org_id, raw_path) if raw_path else None

    async def apply(self, ctx: TransformContext) -> TransformContext:
        record = ctx.record
        org_id = record.org_id
        record_id = record.id
        virtual_record_id = record.virtual_record_id
        if not virtual_record_id:
            raise ValueError(f"record {record_id} has no virtual record id to store under")
        # Use exclude_none=True to skip None values, then clean empty values
        record_dict = record.model_dump(mode='json', exclude_none=True)
        record_dict = self._clean_empty_values(record_dict)

        storage_path = await self._build_hierarchical_storage_path(record, virtual_record_id)
        self.logger.info(
            "📄 Built storage path for vrid %s: %s",
            virtual_record_id, storage_path
        )

        written: dict[str, Any] = {}
        actual_storage_path = await self._store_record_dict(
            org_id, record_id, virtual_record_id, record_dict,
            storage_path=storage_path,
            connector_id=getattr(record, "connector_id", None),
            record_group_id=getattr(record, "record_group_id", None),
            written=written,
            stored_rev=ctx.settings.get(STORED_REV) if STORED_REV in ctx.settings else _KEEP_STORED_REV,
        )

        ctx.settings["storage_path"] = actual_storage_path
        if written:
            ctx.settings[STORED_RECORD_FIELDS] = {
                k: v for k, v in written.items() if k not in SHARED_CONTENT_FIELDS
            }
        else:
            ctx.settings.pop(STORED_RECORD_FIELDS, None)
        ctx.record = record
        return ctx

    async def _store_record_dict(
        self,
        org_id: str,
        record_id: str,
        virtual_record_id: str,
        record_dict: dict[str, Any],
        *,
        storage_path: str | None = None,
        connector_id: str | None = None,
        record_group_id: str | None = None,
        written: dict[str, Any] | None = None,
        stored_rev: str | None = _KEEP_STORED_REV,
    ) -> str:
        """Upload a record as the next version of its stored document, or as a new one.

        Returns the path the content is stored at. *written*, when given, is filled
        with the record dict actually stored: shared content keeps its owner's
        identity, so it can differ from *record_dict*. Without *storage_path* (a rewrite of a
        record already stored, which has no Record entity to build a path from), a new
        upload goes where the current document is, else to ``records/<vrid>``; the
        connector and record group default to the ones in *record_dict*.
        *stored_rev* is written on the mapping as ``storedRev`` unless left out.
        """
        if storage_path is None:
            storage_path = (
                await self.get_actual_content_path(org_id, virtual_record_id)
                or f"records/{virtual_record_id}"
            )
        if connector_id is None:
            connector_id = record_dict.get("connector_id")
        if record_group_id is None:
            record_group_id = record_dict.get("record_group_id")
        existing_lookup = None
        if self.graph_provider:
            # A failed lookup must not read as "no document": creating one then
            # leaves the existing copy unreferenced but still on disk.
            existing_lookup = await self.get_document_id_by_virtual_record_id(virtual_record_id)

        actual_storage_path = storage_path

        replaced_doc_id: str | None = None
        holders: list[str] | None = None
        if existing_lookup and existing_lookup.get("record_doc_id"):
            holders = await self._live_holders(virtual_record_id)
            record_dict = await self._keep_owner_identity(
                org_id, record_id, virtual_record_id, record_dict, existing_lookup, holders,
            )
            existing_doc_id = existing_lookup["record_doc_id"]
            self.logger.info(
                "📄 Overriding buffer in place for vrid %s (doc_id=%s)",
                virtual_record_id, existing_doc_id
            )
            try:
                document_id, file_size_bytes = await self.update_record_buffer(
                    org_id, existing_doc_id, record_dict, virtual_record_id
                )
                raw_path = await self._get_current_document_path(org_id, existing_doc_id)
                if raw_path:
                    actual_storage_path = self._strip_org_prefix(org_id, raw_path)
            except StorageDocumentNotFoundError:
                # A concurrent move fails the write with 503, never 404; a 404
                # means the document was deleted (e.g. with the connector that
                # stored this shared VRID first), so a retry cannot succeed.
                self.logger.warning(
                    "Stored document %s for vrid %s no longer exists; uploading a replacement",
                    existing_doc_id, virtual_record_id,
                )
                document_id, file_size_bytes = await self.save_record_to_storage(
                    org_id, record_id, virtual_record_id, record_dict, document_path=storage_path,
                    connector_id=connector_id, record_group_id=record_group_id,
                )
            except Exception as e:
                # A concurrent move_record_tree may have renamed the
                # directory between the document's MongoDB read and the
                # disk write (TOCTOU in moveTreeLocal). Retry once after
                # a brief pause so the move's MongoDB update can land.
                self.logger.info(
                    "⚠️ update_record_buffer failed for doc %s, retrying: %s",
                    existing_doc_id, str(e),
                )
                await asyncio.sleep(0.5)
                try:
                    document_id, file_size_bytes = await self.update_record_buffer(
                        org_id, existing_doc_id, record_dict, virtual_record_id
                    )
                    raw_path = await self._get_current_document_path(org_id, existing_doc_id)
                    if raw_path:
                        actual_storage_path = self._strip_org_prefix(org_id, raw_path)
                except Exception as retry_e:
                    self.logger.warning(
                        "⚠️ Retry also failed for doc %s, falling back to new upload: %s",
                        existing_doc_id, str(retry_e),
                    )
                    # Shared content stays on its owner's path, not this writer's.
                    replacement_path = storage_path
                    if holders is None or set(holders) - {record_id}:
                        raw_path = await self._get_current_document_path(org_id, existing_doc_id)
                        if raw_path:
                            replacement_path = self._strip_org_prefix(org_id, raw_path)
                    document_id, file_size_bytes = await self.save_record_to_storage(
                        org_id, record_id, virtual_record_id, record_dict, document_path=replacement_path,
                        connector_id=connector_id, record_group_id=record_group_id,
                    )
                    actual_storage_path = replacement_path
                    replaced_doc_id = existing_doc_id
        else:
            unmapped_doc_id = (
                await self._find_unmapped_record_document(org_id, virtual_record_id)
                if self.graph_provider else None
            )
            document_id = None
            if unmapped_doc_id:
                # An earlier attempt stored this record but died before mapping it.
                self.logger.info(
                    "📄 Reusing unmapped storage doc %s for vrid %s",
                    unmapped_doc_id, virtual_record_id,
                )
                try:
                    document_id, file_size_bytes = await self.update_record_buffer(
                        org_id, unmapped_doc_id, record_dict, virtual_record_id
                    )
                    if document_id:
                        # The earlier attempt may have filed it where the record no longer is.
                        holders = await self._live_holders(virtual_record_id)
                    raw_path = await self._get_current_document_path(org_id, unmapped_doc_id)
                    if raw_path:
                        actual_storage_path = self._strip_org_prefix(org_id, raw_path)
                except StorageDocumentNotFoundError:
                    document_id = None
            if not document_id:
                self.logger.debug(
                    "📄 No existing storage doc for vrid %s, creating new document at path: %s",
                    virtual_record_id, storage_path
                )
                document_id, file_size_bytes = await self.save_record_to_storage(
                    org_id, record_id, virtual_record_id, record_dict, document_path=storage_path,
                    connector_id=connector_id, record_group_id=record_group_id,
                )

        if (
            document_id
            and self.graph_provider
            and holders is not None
            and actual_storage_path
            and storage_path != f"records/{virtual_record_id}"
            and actual_storage_path != storage_path
        ):
            actual_storage_path = await self._relocate_stored_copy(
                org_id, record_id, virtual_record_id, actual_storage_path, storage_path,
                holders=holders, document_id=document_id,
            )

        if document_id and self.graph_provider:
            revision = {} if stored_rev is _KEEP_STORED_REV else {"stored_rev": stored_rev}
            mapped = await self.store_virtual_record_mapping(
                org_id, virtual_record_id, document_id, file_size_bytes, **revision,
            )
            if mapped and replaced_doc_id and replaced_doc_id != document_id:
                await self._delete_replaced_document(org_id, replaced_doc_id, virtual_record_id)

        if written is not None and document_id:
            written.clear()
            written.update(record_dict)
        return actual_storage_path

    async def save_semantic_metadata(
        self, org_id: str, record_id: str, virtual_record_id: str, semantic_metadata: dict[str, Any]
    ) -> None:
        """Write classification into the stored record as its next version."""
        stored = await self.get_record_from_storage(virtual_record_id, org_id)
        if stored is None:
            raise LookupError(f"no stored record for virtual record {virtual_record_id}")
        stored["semantic_metadata"] = semantic_metadata
        await self._store_record_dict(org_id, record_id, virtual_record_id, stored)

    async def _with_storage_retry(
        self,
        what: str,
        attempt: "Callable[[], Awaitable[Any]]",
        *,
        idempotent: bool = True,
    ) -> Any:  # noqa: ANN401 - returns whatever the attempt returns
        """Run *attempt* again on a transient failure, with jittered backoff.

        A non-idempotent request (creating a document) is retried only when it
        provably never reached the server -- the connection could not be
        established, or it died before the body was fully written. Anything
        after that point may already have been processed, and a repeat would
        create a duplicate.
        """
        for number in range(1, _STORAGE_RETRY_ATTEMPTS + 1):
            try:
                return await attempt()
            except Exception as error:
                timed_out = isinstance(error, asyncio.TimeoutError)
                if timed_out:
                    get_default_downstream_feedback().report_timeout(_STORAGE_SERVICE_NAME)
                retryable = (
                    isinstance(error, aiohttp.ClientConnectorError)
                    or _request_body_not_delivered(error)
                    or (idempotent and isinstance(error, _RETRY_AFTER_SEND))
                )
                if not retryable:
                    raise
                if number >= _STORAGE_RETRY_ATTEMPTS:
                    if not timed_out:
                        get_default_downstream_feedback().report_unavailable(_STORAGE_SERVICE_NAME)
                    raise
                delay = random.uniform(
                    0.0, min(_STORAGE_RETRY_CAP_SECONDS, _STORAGE_RETRY_BASE_SECONDS * 2 ** (number - 1))
                )
                self.logger.warning(
                    "%s failed (attempt %d/%d): %s; retrying in %.1fs",
                    what, number, _STORAGE_RETRY_ATTEMPTS, error, delay,
                )
                await asyncio.sleep(delay)
        raise AssertionError("unreachable")

    async def _get_signed_url(self, session, url, data, headers) -> dict | None:
        """Ask the gateway for a signed URL; transient failures are retried."""
        try:
            return await self._with_storage_retry(
                "signed URL request", lambda: self._request_signed_url(session, url, data, headers),
            )
        except aiohttp.ClientError as e:
            self.logger.error("❌ Network error getting signed URL: %s", str(e))
            raise
        except Exception as e:
            self.logger.error("❌ Unexpected error getting signed URL: %s", str(e))
            raise aiohttp.ClientError(f"Unexpected error: {str(e)}")

    async def _request_signed_url(self, session, url, data, headers) -> dict | None:
        async with session.post(url, json=data, headers=headers) as response:
            if response.status != HttpStatusCode.SUCCESS.value:
                error_detail = ""
                try:
                    error_response = await response.json()
                    self.logger.error("❌ Failed to get signed URL. Status: %d, Error: %s",
                                    response.status, error_response)
                    if isinstance(error_response, dict):
                        error_obj = error_response.get("error")
                        if isinstance(error_obj, dict):
                            error_detail = str(error_obj.get("message", "")).strip()
                        elif error_obj is not None:
                            error_detail = str(error_obj).strip()
                        if not error_detail:
                            error_detail = str(error_response)
                    else:
                        error_detail = str(error_response)
                    if "cannot be versioned" in error_detail.lower():
                        self.logger.warning("⚠️ Signed URL request indicates legacy non-versioned document")
                except aiohttp.ContentTypeError:
                    error_text = await response.text()
                    error_detail = error_text[:200].strip()
                    self.logger.error("❌ Failed to get signed URL. Status: %d, Response: %s",
                                    response.status, error_text[:200])
                if error_detail:
                    raise _storage_status_error(
                        response.status, f"Failed with status {response.status}: {error_detail}"
                    )
                raise _storage_status_error(response.status, f"Failed with status {response.status}")

            response_data = await response.json()
            return response_data

    # async def _upload_to_signed_url(self, session, signed_url, data) -> int | None:
    #     """Upload data to a pre-signed URL using httpx.
    #     Uses httpx instead of aiohttp because aiohttp's yarl URL parser
    #     normalises percent-encoded characters (e.g. %2F → /) in query
    #     strings even with encoded=True, which invalidates S3/Azure
    #     pre-signed signatures
    #     """
    #     try:
    #         json_bytes = json.dumps(data).encode('utf-8')

    #         async with httpx.AsyncClient() as client:
    #             response = await client.put(
    #                 signed_url,
    #                 content=json_bytes,
    #                 headers={
    #                     "Content-Type": "application/json",
    #                 },
    #             )

    #             if response.status_code != HttpStatusCode.SUCCESS.value:
    #                 response_text = response.text[:200]
    #                 self.logger.error(
    #                     "❌ Failed to upload to signed URL. Status: %d, Response: %s",
    #                     response.status_code, response_text,
    #                 )
    #                 raise aiohttp.ClientError(f"Failed to upload with status {response.status_code}")

    #             self.logger.debug("✅ Successfully uploaded to signed URL")
    #             return response.status_code
    #     except aiohttp.ClientError:
    #         raise
    #     except Exception as e:
    #         self.logger.error("❌ Unexpected error uploading to signed URL: %s", str(e))
    #         raise aiohttp.ClientError(f"Unexpected error: {str(e)}")

    # async def _upload_raw_to_signed_url(
    #     self, signed_url: str, content: bytes, content_type: str
    # ) -> None:
    #     """Upload raw bytes to a pre-signed URL (for CSV, images, etc.)."""
    #     try:
    #         async with httpx.AsyncClient() as client:
    #             response = await client.put(
    #                 signed_url,
    #                 content=content,
    #                 headers={"Content-Type": content_type},
    #             )
    #             if response.status_code != HttpStatusCode.SUCCESS.value:
    #                 response_text = response.text[:200]
    #                 self.logger.error(
    #                     "❌ Failed to upload raw content. Status: %d, Response: %s",
    #                     response.status_code, response_text,
    #                 )
    #                 raise aiohttp.ClientError(
    #                     f"Failed to upload with status {response.status_code}"
    #                 )
    #             self.logger.debug("✅ Successfully uploaded raw content to signed URL")
    #     except aiohttp.ClientError:
    #         raise
    #     except Exception as e:
    #         self.logger.error("❌ Unexpected error uploading raw content: %s", str(e))
    #         raise aiohttp.ClientError(f"Unexpected error: {str(e)}")

    async def _upload_to_signed_url(self, session, signed_url, data) -> int | None:
        """PUT JSON to a signed URL; transient failures are retried (same
        key, so a repeat is harmless)."""
        async def _attempt() -> int:
            async with session.put(
                URL(signed_url, encoded=True),
                json=data,
                headers=_signed_upload_headers(signed_url),
                skip_auto_headers={'Content-Type'}
            ) as response:
                if not _upload_succeeded(response.status):
                    try:
                        error_response = await response.json()
                        self.logger.error("❌ Failed to upload to signed URL. Status: %d, Error: %s",
                                        response.status, error_response)
                    except aiohttp.ContentTypeError:
                        error_text = await response.text()
                        self.logger.error("❌ Failed to upload to signed URL. Status: %d, Response: %s",
                                        response.status, error_text[:200])
                    raise _storage_status_error(
                        response.status, f"Failed to upload with status {response.status}"
                    )

                return response.status

        try:
            return await self._with_storage_retry("signed URL upload", _attempt)
        except aiohttp.ClientError as e:
            self.logger.error("❌ Network error uploading to signed URL: %s", str(e))
            raise
        except Exception as e:
            self.logger.error("❌ Unexpected error uploading to signed URL: %s", str(e))
            raise aiohttp.ClientError(f"Unexpected error: {str(e)}")

    async def _upload_raw_to_signed_url(
        self,
        session: aiohttp.ClientSession,
        signed_url: str,
        content: bytes,
        content_type: str,
    ) -> None:
        """Upload raw bytes to a pre-signed URL (for CSV, images, etc.)."""
        async def _attempt() -> None:
            async with session.put(
                URL(signed_url, encoded=True),
                data=content,
                headers=_signed_upload_headers(signed_url),
                skip_auto_headers={"Content-Type"},
            ) as response:
                if not _upload_succeeded(response.status):
                    response_text = (await response.text())[:200]
                    self.logger.error(
                        "❌ Failed to upload raw content. Status: %d, Response: %s",
                        response.status,
                        response_text,
                    )
                    raise _storage_status_error(
                        response.status, f"Failed to upload with status {response.status}"
                    )

        try:
            await self._with_storage_retry("raw upload", _attempt)
        except aiohttp.ClientError:
            raise
        except Exception as e:
            self.logger.error("❌ Unexpected error uploading raw content: %s", str(e))
            raise aiohttp.ClientError(f"Unexpected error: {str(e)}")

    async def _create_placeholder(self, session, url, data, headers) -> dict | None:
        """Create the placeholder document. Retried like an idempotent request:
        its Idempotency-Key makes a repeat return the first attempt's placeholder."""
        create_headers = _with_idempotency_key(headers)

        async def _attempt() -> dict | None:
            async with session.post(url, json=data, headers=create_headers) as response:
                if response.status != HttpStatusCode.SUCCESS.value:
                    try:
                        error_response = await response.json()
                        self.logger.error("❌ Failed to create placeholder. Status: %d, Error: %s",
                                        response.status, error_response)
                    except aiohttp.ContentTypeError:
                        error_text = await response.text()
                        self.logger.error("❌ Failed to create placeholder. Status: %d, Response: %s",
                                        response.status, error_text[:200])
                    raise _storage_status_error(response.status, f"Failed with status {response.status}")

                response_data = await response.json()
                return response_data

        try:
            return await self._with_storage_retry("placeholder creation", _attempt)
        except aiohttp.ClientError as e:
            self.logger.error("❌ Network error creating placeholder: %s", str(e))
            raise
        except Exception as e:
            self.logger.error("❌ Unexpected error creating placeholder: %s", str(e))
            raise aiohttp.ClientError(f"Unexpected error: {str(e)}")

    async def save_record_to_storage(
        self, org_id: str, record_id: str, virtual_record_id: str,
        record: dict, document_path: str | None = None,
        *, connector_id: str | None = None, record_group_id: str | None = None,
    ) -> tuple[str | None, int | None]:
        """
        Save document to storage using FormData upload
        Returns:
            tuple[str | None, int | None]: (document_id, file_size_bytes) if successful, (None, None) if failed
        """
        try:
            headers, nodejs_endpoint, storage_type = await self._get_auth_and_config(org_id)

            encoded = _encode_record_json(record)
            compressed_record, use_compression = self._maybe_compress_record(
                record, encoded=encoded
            )

            if storage_type == "local":
                if use_compression:
                    json_data = _json_utf8_bytes({
                        "isCompressed": True,
                        "record": compressed_record,
                        "virtualRecordId": virtual_record_id,
                    })
                else:
                    json_data = _plain_envelope_bytes(record, encoded, virtual_record_id)
                file_size_bytes = len(json_data)
                upload_url = f"{nodejs_endpoint}{Routes.STORAGE_UPLOAD.value}"
                create_headers = _with_idempotency_key(headers)

                async def _attempt() -> str:
                    form_data = _versioned_json_form(
                        json_data, f'record_{virtual_record_id}', virtual_record_id, record_id,
                        compressed=use_compression,
                        document_path=document_path,
                        connector_id=connector_id,
                        record_group_id=record_group_id,
                    )
                    async with _borrowed_session() as session, session.post(
                        upload_url, data=form_data, headers=create_headers
                    ) as response:
                        if response.status == HttpStatusCode.CONFLICT.value:
                            # Our own earlier attempt is still storing it.
                            raise TransientStorageError("Record upload still in progress")
                        if response.status != HttpStatusCode.SUCCESS.value:
                            try:
                                error_body: object = await response.json()
                                self.logger.error("❌ Failed to upload record. Status: %d, Error: %s",
                                                response.status, error_body)
                            except aiohttp.ContentTypeError:
                                error_body = await response.text()
                                self.logger.error("❌ Failed to upload record. Status: %d, Response: %s",
                                                response.status, error_body[:200])
                            raise _storage_status_error(
                                response.status, "Failed to upload record", _storage_error_detail(error_body)
                            )

                        response_data = await response.json()
                        document_id = response_data.get('_id')
                        if not document_id:
                            self.logger.error("❌ No document ID in upload response")
                            raise Exception("No document ID in upload response")
                        return document_id

                document_id = await self._with_storage_retry("record upload", _attempt)
                self.logger.debug("✅ Successfully uploaded record for document: %s", document_id)
                return document_id, file_size_bytes
            else:
                # Prepare placeholder for S3/Azure storage
                effective_path = document_path or f'records/{virtual_record_id}'
                metadata_entries: list[dict] = []
                if use_compression:
                    metadata_entries.append({
                        "key": "compression",
                        "value": {
                            "algorithm": "zstd",
                            "level": COMPRESSION_LEVEL,
                            "format": "msgspec",
                            "version": "v1",
                            "compressed": True
                        }
                    })
                if connector_id:
                    metadata_entries.append({"key": "connectorId", "value": connector_id})
                if record_group_id:
                    metadata_entries.append({"key": "recordGroupId", "value": record_group_id})

                placeholder_data: dict = {
                    "documentName": f"record_{virtual_record_id}",
                    "documentPath": effective_path,
                    "extension": "json",
                    "isVersionedFile": False,
                }
                if metadata_entries:
                    placeholder_data["customMetadata"] = metadata_entries

                try:
                    async with _borrowed_session() as session:
                        placeholder_url = f"{nodejs_endpoint}{Routes.STORAGE_PLACEHOLDER.value}"
                        document = await self._create_placeholder(session, placeholder_url, placeholder_data, headers)

                        document_id = document.get("_id")
                        if not document_id:
                            self.logger.error("❌ No document ID found in placeholder response")
                            raise Exception("No document ID found in placeholder response")

                        upload_url = f"{nodejs_endpoint}{Routes.STORAGE_DIRECT_UPLOAD.value.format(documentId=document_id)}"
                        upload_result = await self._get_signed_url(session, upload_url, {}, headers)

                        signed_url = upload_result.get('signedUrl')
                        if not signed_url:
                            self.logger.error("❌ No signed URL in response for document: %s", document_id)
                            raise Exception("No signed URL in response for document")

                        # Upload with isCompressed flag format
                        if compressed_record:
                            # Compressed format
                            upload_data = {
                                "isCompressed": True,
                                "record": compressed_record,
                                "virtualRecordId": virtual_record_id,
                            }
                        else:
                            # Uncompressed fallback format
                            upload_data = {
                                "record": record,
                                "isCompressed": False,
                                "virtualRecordId": virtual_record_id,
                            }

                        file_size_bytes = (
                            _cloud_envelope_size(encoded, virtual_record_id)
                            if encoded is not None and not compressed_record
                            else len(json.dumps(upload_data).encode('utf-8'))
                        )

                        await self._upload_to_signed_url(session, signed_url, upload_data)

                        self.logger.info("✅ Successfully completed record storage process for document: %s", document_id)
                        return document_id, file_size_bytes

                except Exception as e:
                    raise

        except Exception as e:
            self.logger.exception("❌ Error in saving record to storage: %s", str(e))
            raise e

    async def save_binary_to_storage(
        self,
        org_id: str,
        record_id: str,
        file_name: str,
        extension: str,
        content_type: str,
        binary_data: bytes,
    ) -> tuple[str | None, int | None]:
        """Upload a raw binary file (e.g. PDF) to storage for later retrieval via the buffer endpoint."""
        import os

        try:
            headers, nodejs_endpoint, storage_type = await self._get_auth_and_config(org_id)
            file_size_bytes = len(binary_data)
            doc_name_no_ext = os.path.splitext(file_name)[0]

            # Single session for all HTTP steps in this upload (local: one POST; cloud: placeholder + signed URL + PUT).
            async with _borrowed_session() as session:
                if storage_type == "local":
                    upload_url = f"{nodejs_endpoint}{Routes.STORAGE_UPLOAD.value}"
                    create_headers = _with_idempotency_key(headers)

                    async def _attempt() -> tuple[str | None, int | None]:
                        form_data = aiohttp.FormData()
                        form_data.add_field(
                            "file", binary_data, filename=file_name, content_type=content_type
                        )
                        form_data.add_field("documentName", doc_name_no_ext)
                        form_data.add_field("documentPath", f"attachments/{record_id}")
                        form_data.add_field("isVersionedFile", "false")
                        form_data.add_field("extension", extension)
                        form_data.add_field("recordId", record_id)
                        async with session.post(upload_url, data=form_data, headers=create_headers) as response:
                            if response.status == HttpStatusCode.CONFLICT.value:
                                # Our own earlier attempt is still storing it.
                                raise TransientStorageError("Binary upload still in progress")
                            if response.status != HttpStatusCode.SUCCESS.value:
                                text = await response.text()
                                self.logger.error(
                                    "❌ Failed to upload binary to storage: %d %s", response.status, text[:200]
                                )
                                # Raised, not returned, so a 502/503/504 is retried; a
                                # failure the retry cannot fix still ends as (None, None)
                                # in the handler below.
                                raise _storage_status_error(
                                    response.status, "Failed to upload binary to storage"
                                )
                            response_data = await response.json()
                            return response_data.get("_id"), file_size_bytes

                    return await self._with_storage_retry("binary upload", _attempt)
                else:
                    # S3/cloud: placeholder → signed URL → raw upload
                    placeholder_data = {
                        "documentName": doc_name_no_ext,
                        "documentPath": f"attachments/{record_id}",
                        "extension": extension,
                        "isVersionedFile": False,
                        "recordId": record_id,
                    }
                    placeholder_url = f"{nodejs_endpoint}{Routes.STORAGE_PLACEHOLDER.value}"
                    document = await self._create_placeholder(session, placeholder_url, placeholder_data, headers)
                    document_id = document.get("_id")
                    if not document_id:
                        self.logger.error("❌ No document ID in placeholder response for binary upload")
                        return None, None

                    upload_url = f"{nodejs_endpoint}{Routes.STORAGE_DIRECT_UPLOAD.value.format(documentId=document_id)}"
                    upload_result = await self._get_signed_url(session, upload_url, {}, headers)
                    signed_url = upload_result.get("signedUrl")
                    if not signed_url:
                        self.logger.error("❌ No signed URL for binary upload of document: %s", document_id)
                        return None, None

                    await self._upload_raw_to_signed_url(session, signed_url, binary_data, content_type)
                    return document_id, file_size_bytes

        except Exception as e:
            self.logger.error("❌ Failed to save binary to storage for record %s: %s", record_id, str(e))
            return None, None

    async def _relocate_stored_copy(
        self,
        org_id: str,
        record_id: str,
        virtual_record_id: str,
        current_path: str,
        expected_path: str,
        *,
        holders: list[str] | None,
        document_id: str,
    ) -> str:
        """Move this record's stored copy to where its graph path says it belongs.

        A copy lands on a wrong path when its path could not be computed (a
        graph error falls back to records/<vrid>), when it was indexed before its
        parent existed, when a move was skipped, or when it predates
        hierarchical storage. Grep searches only records/<connector>/, so such a
        copy is invisible to it until moved. Shared content stays where its
        first record filed it. Returns the path the copy is at afterwards.
        """
        # Only the content's sole live holder moves it. A record being deleted
        # holds nothing any more: its path is computed without the links the
        # delete already removed, and moving the copy there would take it away
        # from where the delete is cleaning up.
        if holders is None or set(holders) != {record_id}:
            return current_path
        if expected_path.startswith(current_path + "/"):
            # A record whose name used to clean to nothing was filed at its
            # parent's path; storage refuses to move a folder into itself, and
            # moving the parent would carry its siblings along.
            self.logger.debug(
                "Not relocating vrid %s into its own folder: %s -> %s",
                virtual_record_id, current_path, expected_path,
            )
            return current_path
        try:
            headers, nodejs_endpoint, _ = await self._get_auth_and_config(org_id)
            result = await post_move_tree(
                get_shared_session(), nodejs_endpoint, headers, current_path, expected_path,
                virtual_record_ids=[virtual_record_id],
            )
        except Exception as e:
            self.logger.warning(
                "Could not relocate vrid %s from %s to %s: %s",
                virtual_record_id, current_path, expected_path, e,
            )
            return current_path
        if result.get("failed"):
            return current_path
        if not result.get("moved"):
            # Nothing at current_path: a concurrent move already carried it.
            raw_path = await self._get_current_document_path(org_id, document_id)
            return self._strip_org_prefix(org_id, raw_path) if raw_path else current_path
        self.logger.info(
            "📦 Relocated stored copy of vrid %s: %s -> %s", virtual_record_id, current_path, expected_path,
        )
        return expected_path

    async def _live_holders(self, virtual_record_id: str) -> list[str] | None:
        """The live records holding *virtual_record_id*, or None when the graph
        cannot say (callers then leave shared content as it is)."""
        try:
            return remaining_record_keys(
                await self.graph_provider.get_records_by_virtual_record_id(
                    virtual_record_id, raise_on_error=True,
                )
            )
        except Exception as e:
            self.logger.warning("Could not list records sharing vrid %s: %s", virtual_record_id, e)
            return None

    async def _keep_owner_identity(
        self,
        org_id: str,
        record_id: str,
        virtual_record_id: str,
        record_dict: dict,
        lookup_result: dict,
        holders: list[str] | None,
    ) -> dict:
        """The stored copy to write when *record_id* rewrites shared content.

        Deduplicated content is stored once, filed under and named after the
        record that stored it first. Another record re-indexing it must replace
        only the content: writing its own name, link and ids into that file shows
        them to the owner's connector (grep, linked-record context).

        When the record named in the copy no longer holds the content (it was
        deleted and the copy handed over), the owner is the one storage hands
        over to: the smallest live holder key. Until that record writes, the
        copy carries no record's identity.
        """
        if holders is not None and set(holders) <= {record_id}:
            return record_dict
        try:
            stored = await self.get_record_from_storage(
                virtual_record_id, org_id, lookup_result=lookup_result,
            )
        except Exception as e:
            self.logger.warning(
                "Could not read the stored copy of shared vrid %s; writing it whole: %s",
                virtual_record_id, e,
            )
            return record_dict
        owner = stored.get("id") if isinstance(stored, dict) else None
        if holders is not None and owner not in holders:
            if min(holders, default=record_id) == record_id:
                return record_dict
            stored = {k: v for k, v in stored.items() if k in SHARED_CONTENT_FIELDS}
            owner = "a record that no longer holds it"
        elif not owner or owner == record_id:
            return record_dict
        merged = dict(stored)
        for key in SHARED_CONTENT_FIELDS:
            if key in record_dict:
                merged[key] = record_dict[key]
            else:
                merged.pop(key, None)
        self.logger.info(
            "Shared vrid %s: record %s replaces the content of %s's stored copy only",
            virtual_record_id, record_id, owner,
        )
        return merged

    async def _find_unmapped_record_document(
        self, org_id: str, virtual_record_id: str, kind: str = "record",
    ) -> str | None:
        """The newest live ``<kind>_<vrid>`` document (kind: record / metadata),
        for a vrid whose mapping does not name one.

        Raises when storage cannot be asked: guessing "none" would store a
        second copy beside the one an interrupted attempt left behind.
        """
        headers, nodejs_endpoint, _ = await self._get_auth_and_config(org_id)
        url = (
            f"{nodejs_endpoint}"
            f"{Routes.STORAGE_VIRTUAL_RECORD_DOCUMENTS.value.format(virtualRecordId=virtual_record_id)}"
        )
        async with get_shared_session().get(url, headers=headers) as resp:
            if resp.status == HttpStatusCode.NOT_FOUND.value:
                # A storage service older than this route; nothing to reuse.
                return None
            if resp.status != HttpStatusCode.SUCCESS.value:
                raise _storage_status_error(resp.status, "Could not list stored documents")
            body = await resp.json()
        name = f"{kind}_{virtual_record_id}"
        for doc in body.get("documents") or []:
            if doc.get("documentName") == name and doc.get("_id"):
                return str(doc["_id"])
        return None

    async def get_document_id_by_virtual_record_id(self, virtual_record_id: str) -> dict | None:
        """
        Get the document ID(s) and file size by virtual record ID from ArangoDB.

        Returns:
            dict | None: A dict with keys 'record_doc_id', 'fileSizeBytes', and optionally
                         'record_metadata_doc_id' if found, else None.
        """
        if not self.graph_provider:
            self.logger.error("❌ GraphProvider not initialized, cannot get document ID by virtual record ID.")
            raise Exception("GraphProvider not initialized, cannot get document ID by virtual record ID.")


        try:
            collection_name = CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value

            # The mapping is keyed by the vrid. Older releases keyed it
            # f"{vrid}_{documentId}", one node per upload, reachable only by the
            # field; a metadata write since may have left a keyed node with no
            # document id beside them.
            doc = await self.graph_provider.get_document(
                virtual_record_id,
                collection_name
            )
            if doc and self._mapped_document_id(doc):
                return self._shape_document_lookup(doc)
            nodes = await self.graph_provider.get_nodes_by_filters(
                collection_name,
                {"virtualRecordId": virtual_record_id}
            ) or []
            mapped = [node for node in nodes if self._mapped_document_id(node)]
            if mapped:
                newest = max(mapped, key=lambda node: node.get("updatedAt") or node.get("createdAt") or 0)
                return self._shape_document_lookup(newest)
            fallback = doc or (nodes[0] if nodes else None)
            if fallback:
                return self._shape_document_lookup(fallback)
            self.logger.debug("No document ID found for virtual record ID: %s", virtual_record_id)
            return None
        except Exception as e:
            self.logger.exception(
                "❌ Error getting document ID by virtual record ID: %s",
                virtual_record_id,
            )
            raise e

    @staticmethod
    def _mapped_document_id(doc: dict) -> str | None:
        return doc.get("record_doc_id") or doc.get("documentId")

    @staticmethod
    def _shape_document_lookup(doc: dict) -> dict:
        """Project a virtual-record mapping node onto the lookup result shape."""
        result = {
            "record_doc_id": BlobStorage._mapped_document_id(doc),
            "fileSizeBytes": doc.get("fileSizeBytes"),
        }
        record_metadata_doc_id = doc.get("record_metadata_doc_id")
        if record_metadata_doc_id:
            result["record_metadata_doc_id"] = record_metadata_doc_id
        # The artifact set of the current content revision, for the artifact reader.
        for key in ("currentRev", "manifestDocId", "storedRev"):
            if doc.get(key):
                result[key] = doc[key]
        return result

    async def _records_still_using_vrid(self, virtual_record_id: str) -> list[str]:
        """Live records (any connector) referencing *virtual_record_id*.

        Same rule as the vector cleanup: raise rather than read a failed lookup
        as "unreferenced", and confirm an empty answer once after a short pause
        so a lagging replica cannot turn into a deleted blob.
        """
        raw = await self.graph_provider.get_records_by_virtual_record_id(
            virtual_record_id, raise_on_error=True
        )
        remaining = remaining_record_keys(raw)
        if not remaining:
            await asyncio.sleep(EMPTY_CONFIRM_DELAY_SECONDS)
            raw = await self.graph_provider.get_records_by_virtual_record_id(
                virtual_record_id, raise_on_error=True
            )
            remaining = remaining_record_keys(raw)
        return remaining

    async def _delete_replaced_document(
        self, org_id: str, document_id: str, virtual_record_id: str,
    ) -> None:
        """Best-effort removal of a document superseded by a replacement upload.

        Called only once the VRID mapping points at the replacement, so nothing
        reads this document any more; a failure leaves it unreferenced.
        """
        try:
            headers, nodejs_endpoint, _ = await self._get_auth_and_config(org_id)
            delete_url = (
                f"{nodejs_endpoint}{Routes.STORAGE_DOCUMENT.value.format(documentId=document_id)}"
                "?hard=true"
            )
            async with get_shared_session().delete(delete_url, headers=headers) as resp:
                if resp.status not in (200, 204, 404):
                    self.logger.warning(
                        "Could not delete replaced storage doc %s (vrid %s): status %d",
                        document_id, virtual_record_id, resp.status,
                    )
        except Exception as exc:
            self.logger.warning(
                "Could not delete replaced storage doc %s (vrid %s): %s",
                document_id, virtual_record_id, exc,
            )

    async def delete_storage_docs_for_vrid(self, org_id: str, virtual_record_id: str) -> None:
        """Delete every storage document of an abandoned virtualRecordId (its
        record and metadata documents, wherever filed, and its content
        revisions) and then its graph mapping node.

        Called when VRID reconciliation isolates a record with a new VRID and
        the old one is no longer needed.  Safe to call when the mapping or the
        documents have already been removed — every step is idempotent.

        Content is deduplicated across connectors, so other records can still
        hold this VRID and read these very documents; nothing is deleted while
        any live record references it.

        Raises on transient failures (HTTP errors, connection issues, a graph
        that cannot be read) so the caller's retry loop can re-attempt the
        entire cleanup; giving up leaves the documents in place.
        """
        if not self.graph_provider:
            self.logger.warning(
                "No graph provider — cannot clean up storage docs for abandoned VRID %s",
                virtual_record_id,
            )
            return

        remaining = await self._records_still_using_vrid(virtual_record_id)
        if remaining:
            self.logger.info(
                "Keeping storage docs for VRID %s: still used by %d record(s)",
                virtual_record_id, len(remaining),
            )
            return

        collection_name = CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value

        try:
            mapping = await self.graph_provider.get_document(
                virtual_record_id, collection_name
            )
        except Exception as exc:
            self.logger.warning(
                "Failed to look up VRID mapping for cleanup (%s): %s",
                virtual_record_id, exc,
            )
            raise

        # By virtual record id, not by the ids on the mapping: that also finds
        # documents the mapping never pointed at, such as content revisions.
        # A failure keeps the mapping node so a retry can find them.
        try:
            purged = await self.purge_virtual_record_documents(org_id, virtual_record_id)
        except Exception as exc:
            self.logger.error(
                "Failed to delete storage docs for abandoned VRID %s: %s",
                virtual_record_id, exc,
            )
            raise
        self.logger.info(
            "Deleted %d storage doc(s) for abandoned VRID %s", purged, virtual_record_id,
        )

        if not mapping:
            return
        try:
            await self.graph_provider.remove_nodes_by_field(
                collection_name, "_key", field_value=virtual_record_id,
            )
            self.logger.info(
                "Removed VRID mapping node for abandoned %s", virtual_record_id,
            )
        except Exception as exc:
            self.logger.warning(
                "Failed to remove VRID mapping node for %s: %s",
                virtual_record_id, exc,
            )
            raise

    VIRTUAL_RECORD_LOOKUP_CHUNK_SIZE = 500

    async def get_document_ids_by_virtual_record_ids(
        self, virtual_record_ids: list[str], *, raise_on_error: bool = False
    ) -> dict[str, dict]:
        """Resolve many virtual-record → document mappings with one query per chunk.

        Answering a chat turn fetches ~100 records, each of which otherwise costs
        its own mapping query.

        The mapping node's key *is* the virtual record id, which is what the
        per-id path matches on and what carries the index. Batching on a
        ``virtualRecordId`` property instead matched nothing and fell through to
        the per-id path for every id, with an unindexed scan added on top.

        Ids the batch does not return still fall back to the per-id path. Ids
        with no mapping at all are absent from the result rather than
        present-and-empty, so callers can tell the difference between "not
        found" and "not looked up".

        A failed read is logged and its ids left out, so a chat turn still
        answers. ``raise_on_error`` raises it instead, for callers that act on
        an id being absent.
        """
        if not self.graph_provider:
            self.logger.error("❌ GraphProvider not initialized, cannot resolve virtual record IDs.")
            raise Exception("GraphProvider not initialized, cannot resolve virtual record IDs.")

        unique_ids = list(dict.fromkeys(vrid for vrid in virtual_record_ids if vrid))
        if not unique_ids:
            return {}

        collection_name = CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value
        resolved: dict[str, dict] = {}
        # Only when set: the chat path's provider calls stay exactly as they were.
        strict = {"raise_on_error": True} if raise_on_error else {}

        chunk_size = self.VIRTUAL_RECORD_LOOKUP_CHUNK_SIZE
        for start in range(0, len(unique_ids), chunk_size):
            chunk = unique_ids[start:start + chunk_size]
            try:
                nodes = await self.graph_provider.get_nodes_by_field_in(
                    collection_name, "id", chunk, **strict
                )
            except Exception as e:
                if raise_on_error:
                    raise
                # Degrade to the per-id path for this chunk rather than failing the turn.
                self.logger.warning("Batch virtual-record lookup failed, falling back: %s", str(e))
                nodes = []

            for node in nodes or []:
                vrid = node.get("id") or node.get("_key") or node.get("virtualRecordId")
                if vrid and vrid not in resolved:
                    resolved[vrid] = self._shape_document_lookup(node)

        # Opt-in only. The mapping node's key IS the virtual record id, so the
        # keyed batch above finds every mapping this release writes; the field
        # only adds nodes older releases keyed f"{vrid}_{documentId}". Each id
        # the batch missed (a deleted or missing mapping is normal) costs one
        # more query here, so it stays off unless a deployment still holds them.
        missing = [vrid for vrid in unique_ids if vrid not in resolved]
        if missing and os.getenv("PIPESHUB_VRID_FIELD_LOOKUP", "").lower() in ("1", "true", "yes"):
            for start in range(0, len(missing), chunk_size):
                chunk = missing[start:start + chunk_size]
                try:
                    nodes = await self.graph_provider.get_nodes_by_field_in(
                        collection_name, "virtualRecordId", chunk
                    )
                except Exception as e:
                    self.logger.warning(
                        "Batch virtual-record lookup by field failed, falling back: %s", str(e)
                    )
                    continue
                for node in nodes or []:
                    vrid = node.get("virtualRecordId")
                    if vrid and vrid not in resolved:
                        resolved[vrid] = self._shape_document_lookup(node)
            missing = [vrid for vrid in unique_ids if vrid not in resolved]

        if missing:
            fallbacks = await asyncio.gather(
                *[
                    self.graph_provider.get_document(vrid, collection_name, **strict)
                    for vrid in missing
                ],
                return_exceptions=True,
            )
            for vrid, doc in zip(missing, fallbacks):
                if isinstance(doc, Exception):
                    if raise_on_error:
                        raise doc
                    self.logger.warning(
                        "Virtual-record mapping fallback failed for %s: %s", vrid, str(doc)
                    )
                    continue
                if doc:
                    resolved[vrid] = self._shape_document_lookup(doc)

        return resolved

    async def get_record_from_storage(
        self,
        virtual_record_id: str,
        org_id: str,
        lookup_result: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """
        Retrieve a record's content from blob storage using the virtual_record_id.

        Args:
            lookup_result: pre-resolved virtual-record → document mapping. Callers
                fetching many records resolve the whole batch in one graph query
                (see ``get_document_ids_by_virtual_record_ids``) and pass the entry
                in, which skips the per-record lookup below.

        Returns:
            str: The content of the record if found, else an empty string.
        """
        try:
            headers, nodejs_endpoint, _ = await self._get_auth_and_config(org_id)

            if lookup_result is None:
                lookup_result = await self.get_document_id_by_virtual_record_id(virtual_record_id)

            if not lookup_result:
                self.logger.debug("No document ID found for virtual record ID: %s", virtual_record_id)
                return None

            document_id = lookup_result.get("record_doc_id")
            file_size_bytes = lookup_result.get("fileSizeBytes")

            if not document_id:
                self.logger.debug("No document ID found for virtual record ID: %s", virtual_record_id)
                return None

            download_url = f"{nodejs_endpoint}{Routes.STORAGE_DOWNLOAD.value.format(documentId=document_id)}"
            session = get_shared_session()

            lease = (
                _DecodeSlotLease(await _acquire_decode_slot())
                if file_size_bytes is not None and file_size_bytes > LARGE_DECODE_BYTES
                else None
            )
            try:
                # A cached signed URL skips the gateway hop entirely. On any failure
                # reading it back, fall through to the gateway and re-sign.
                cached_url = await self._cached_signed_url(org_id, document_id)
                if cached_url:
                    try:
                        record = await self._record_from_signed_url(
                            session, cached_url, file_size_bytes, virtual_record_id, lease
                        )
                        if record is not None:
                            return record
                    except StoredRecordTooLargeError:
                        # Refused after the download, not because of the URL: re-signing
                        # would only download the same record again.
                        raise
                    except Exception as e:
                        self.logger.debug(
                            "Cached signed URL failed for %s, re-signing: %s", document_id, str(e)
                        )

                data = await self._fetch_record_envelope(session, download_url, headers, virtual_record_id)
                if data.get("signedUrl"):
                    await self._store_signed_url(org_id, document_id, data["signedUrl"])

                if data.get("record"):
                    record = await self._decode_record(data, lease)
                    record_name = record.get("record_name")
                    self.logger.debug("✅ Successfully retrieved record %s from storage for virtual_record_id: %s", record_name, virtual_record_id)
                    return record
                elif data.get("signedUrl"):
                    record = await self._record_from_signed_url(
                        session, data["signedUrl"], file_size_bytes, virtual_record_id, lease
                    )
                    if record is not None:
                        return record
                    self.logger.error("❌ No record found for virtual_record_id: %s", virtual_record_id)
                    raise Exception("No record found for virtual_record_id")
                else:
                    self.logger.error("❌ No record found for virtual_record_id: %s", virtual_record_id)
                    raise Exception("No record found for virtual_record_id")
            finally:
                if lease is not None:
                    lease.release()
        except StorageDocumentNotFoundError:
            self.logger.warning(
                "Stored document for virtual_record_id %s no longer exists", virtual_record_id
            )
            raise
        except StoredRecordTooLargeError as e:
            # The reader skips this record; an expected refusal, not a fault worth a traceback.
            self.logger.warning("⚠️ Skipping stored record %s: %s", virtual_record_id, str(e))
            raise
        except Exception as e:
            self.logger.exception(
                "❌ Error retrieving record from storage (virtual_record_id=%s)",
                virtual_record_id,
            )
            raise e

    async def _fetch_record_envelope(
        self, session: aiohttp.ClientSession, download_url: str, headers: dict, virtual_record_id: str
    ) -> dict:
        """GET the record envelope from the gateway; transient failures retried."""
        async def _attempt() -> dict:
            async with session.get(download_url, headers=headers) as resp:
                if resp.status != HttpStatusCode.SUCCESS.value:
                    raise _storage_status_error(
                        resp.status, f"Failed to retrieve record from storage: status {resp.status}"
                    )
                return await resp.json(loads=_decode_json)

        return await self._with_storage_retry(f"record fetch {virtual_record_id}", _attempt)

    async def store_virtual_record_mapping(
        self,
        org_id: str,
        virtual_record_id: str,
        document_id: str,
        file_size_bytes: int | None = None,
        *,
        stored_rev: str | None = _KEEP_STORED_REV,
    ) -> bool:
        """
        Stores the mapping between virtual_record_id and document_id in graph database.
        Args:
            org_id: The organization ID
            virtual_record_id: The virtual record ID
            document_id: The document ID
            file_size_bytes: Optional file size in bytes
            stored_rev: The content revision the stored document now holds, as
                ``storedRev``; None clears it, leaving it out keeps it
        Returns:
            bool: True if successful, False otherwise.
        """

        try:
            collection_name = CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value

            mapping_key = virtual_record_id

            mapping_document = {
                "id": mapping_key,
                "orgId": org_id,
                "virtualRecordId": virtual_record_id,
                "documentId": document_id,
                "record_doc_id": document_id,
                "updatedAt": get_epoch_timestamp_in_ms()
            }

            if file_size_bytes is not None:
                mapping_document["fileSizeBytes"] = file_size_bytes
            if stored_rev is not _KEEP_STORED_REV:
                mapping_document["storedRev"] = stored_rev

            success = await self.graph_provider.batch_upsert_nodes(
                [mapping_document],
                collection_name
            )

            if success:
                size_info = f", file_size={file_size_bytes} bytes" if file_size_bytes is not None else ""
                self.logger.debug("✅ Successfully stored virtual record mapping: virtual_record_id=%s, document_id=%s%s", virtual_record_id, document_id, size_info)
                return True
            else:
                self.logger.error("❌ Failed to store virtual record mapping")
                raise Exception("Failed to store virtual record mapping")

        except Exception as e:
            self.logger.exception(
                "❌ Failed to store virtual record mapping: %s",
                virtual_record_id,
            )
            raise e

    async def upload_next_version(
        self,
        org_id: str,
        record_id: str,
        document_id: str,
        record: dict,
        virtual_record_id: str = None
    ):
        """
        Args:
            org_id: Organization ID
            record_id: Record ID
            document_id: Existing document ID to add version to
            record: Record data to upload
            virtual_record_id: Virtual record ID

        Returns:
            tuple[str | None, int | None]: (document_id, file_size_bytes) if successful
        """
        try:
            headers, nodejs_endpoint, storage_type = await self._get_auth_and_config(org_id)

            compressed_record, use_compression = self._maybe_compress_record(record)

            upload_data = {
                "isCompressed": use_compression,
                "record": compressed_record if use_compression else record,
                "virtualRecordId": virtual_record_id
            }
            # Cloud uploads send `json=upload_data`, which ASCII-escapes; size what is sent.
            json_data = (
                _json_utf8_bytes(upload_data)
                if storage_type == "local"
                else json.dumps(upload_data).encode("utf-8")
            )
            file_size_bytes = len(json_data)

            if storage_type == "local":
                upload_url = f"{nodejs_endpoint}{Routes.STORAGE_UPLOAD_NEXT_VERSION.value.format(documentId=document_id)}"

                async def _attempt() -> None:
                    form_data = aiohttp.FormData()
                    form_data.add_field('file',
                                    json_data,
                                    filename=f'record_{record_id}.json',
                                    content_type='application/json')
                    async with _borrowed_session() as session, session.post(
                        upload_url, data=form_data, headers=headers
                    ) as response:
                        if response.status != HttpStatusCode.SUCCESS.value:
                            error_response = None
                            try:
                                error_response = await response.json()
                                self.logger.error("❌ Failed to upload next version. Status: %d, Error: %s",
                                                response.status, error_response)
                            except aiohttp.ContentTypeError:
                                error_text = await response.text()
                                self.logger.error("❌ Failed to upload next version. Status: %d, Response: %s",
                                                response.status, error_text[:200])
                            if (
                                response.status == HttpStatusCode.BAD_REQUEST.value
                                and isinstance(error_response, dict)
                                and "cannot be versioned"
                                in str(error_response.get("error", {}).get("message", "")).lower()
                            ):
                                raise Exception("This document cannot be versioned")

                            raise Exception(
                                f"Failed to upload next version (status: {response.status})"
                            )

                await self._with_storage_retry("next-version upload", _attempt, idempotent=False)
                self.logger.debug("✅ Successfully uploaded next version for document: %s", document_id)
                return document_id, file_size_bytes
            else:
                async with _borrowed_session() as session:
                    upload_url = f"{nodejs_endpoint}{Routes.STORAGE_DIRECT_UPLOAD.value.format(documentId=document_id)}"
                    upload_result = await self._get_signed_url(session, upload_url, {}, headers)

                    signed_url = upload_result.get('signedUrl')
                    if not signed_url:
                        raise Exception("No signed URL in response for next version upload")

                    await self._upload_to_signed_url(session, signed_url, upload_data)

                    self.logger.debug("✅ Successfully uploaded next version for document: %s", document_id)
                    return document_id, file_size_bytes

        except Exception as e:
            self.logger.error("❌ Error uploading next version: %s", str(e))
            raise e

    async def save_reconciliation_metadata(
        self, org_id: str, record_id: str, virtual_record_id: str, metadata_dict: dict,
        document_path: str | None = None,
        *, connector_id: str | None = None, record_group_id: str | None = None,
        create_if_missing: bool = True,
    ) -> str | None:
        """
        On first call, creates a new document. On subsequent calls, overrides in place.

        The metadata document ID is stored in the same virtual-record-to-doc mapping
        under the field 'record_metadata_doc_id', alongside the record's own 'record_doc_id'.

        Args:
            org_id: Organization ID
            record_id: Record ID
            virtual_record_id: Virtual record ID
            metadata_dict: Reconciliation metadata dictionary
            document_path: Storage path (same as record content path) so metadata
                lives alongside the record. Falls back to records/<vrid> if None.
            create_if_missing: False when *document_path* is not known: an
                existing metadata document is updated in place, none is created
                at a guessed path (no document means no stale state to reset).
                Raises StorageLookupUnavailableError when the existing one could
                not be updated, so the caller retries.

        Returns:
            str | None: metadata document_id if successful
        """
        try:

            effective_path = document_path or f"records/{virtual_record_id}"

            existing_metadata_doc_id = None
            if self.graph_provider:
                # As for the record document: a failed lookup must not create a
                # second metadata document beside the unreferenced first one.
                doc = await self.graph_provider.get_document(
                    virtual_record_id, CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value
                )
                if doc and doc.get("record_metadata_doc_id"):
                    existing_metadata_doc_id = doc["record_metadata_doc_id"]
                else:
                    existing_metadata_doc_id = await self._find_unmapped_record_document(
                        org_id, virtual_record_id, kind="metadata",
                    )

            metadata_document_id = None
            replaced_metadata_doc_id: str | None = None
            if existing_metadata_doc_id:
                try:
                    doc_id, _ = await self._update_metadata_buffer(
                        org_id, existing_metadata_doc_id, metadata_dict, virtual_record_id
                    )
                    metadata_document_id = doc_id
                except StorageDocumentNotFoundError:
                    if not create_if_missing:
                        return None
                    self.logger.warning(
                        "Stored metadata document %s for vrid %s no longer exists; creating a replacement",
                        existing_metadata_doc_id, virtual_record_id,
                    )
                    metadata_document_id = await self._create_metadata_document(
                        org_id, record_id, virtual_record_id, metadata_dict, effective_path,
                        connector_id=connector_id, record_group_id=record_group_id,
                    )
                except Exception as e:
                    self.logger.info(
                        "⚠️ metadata buffer update failed for doc %s, retrying: %s",
                        existing_metadata_doc_id, str(e),
                    )
                    await asyncio.sleep(0.5)
                    try:
                        doc_id, _ = await self._update_metadata_buffer(
                            org_id, existing_metadata_doc_id, metadata_dict, virtual_record_id
                        )
                        metadata_document_id = doc_id
                    except Exception as retry_e:
                        if not create_if_missing:
                            raise StorageLookupUnavailableError(
                                f"Could not update metadata document {existing_metadata_doc_id}: {retry_e}"
                            ) from retry_e
                        self.logger.warning(
                            "⚠️ Retry also failed for metadata doc %s; creating replacement: %s",
                            existing_metadata_doc_id, str(retry_e),
                        )
                        metadata_document_id = await self._create_metadata_document(
                            org_id, record_id, virtual_record_id, metadata_dict, effective_path,
                            connector_id=connector_id, record_group_id=record_group_id,
                        )
                        replaced_metadata_doc_id = existing_metadata_doc_id
            elif not create_if_missing:
                return None
            else:
                metadata_document_id = await self._create_metadata_document(
                    org_id, record_id, virtual_record_id, metadata_dict, effective_path,
                    connector_id=connector_id, record_group_id=record_group_id,
                )

            if metadata_document_id and self.graph_provider:
                mapping_document = {
                    "_key": virtual_record_id,
                    "record_metadata_doc_id": metadata_document_id,
                    "updatedAt": get_epoch_timestamp_in_ms()
                }
                await self.graph_provider.batch_upsert_nodes(
                    [mapping_document],
                    CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value
                )
                self.logger.debug(
                    "✅ Stored metadata mapping: %s -> record_metadata_doc_id=%s",
                    virtual_record_id, metadata_document_id
                )
                if replaced_metadata_doc_id and replaced_metadata_doc_id != metadata_document_id:
                    await self._delete_replaced_document(
                        org_id, replaced_metadata_doc_id, virtual_record_id,
                    )

            return metadata_document_id

        except Exception as e:
            self.logger.error("❌ Error saving reconciliation metadata: %s", str(e))
            raise e

    async def _update_metadata_buffer(
        self,
        org_id: str,
        document_id: str,
        metadata_dict: dict,
        virtual_record_id: str,
    ) -> tuple[str | None, int | None]:
        """Override metadata content in place without compression."""
        try:
            headers, nodejs_endpoint, _ = await self._get_auth_and_config(org_id)

            json_bytes = _json_utf8_bytes(metadata_dict)
            file_size_bytes = len(json_bytes)

            buffer_url = f"{nodejs_endpoint}{Routes.STORAGE_BUFFER.value.format(documentId=document_id)}"
            self.logger.info("📤 Overriding metadata buffer for document: %s", document_id)

            session = get_shared_session()
            form_data = aiohttp.FormData()
            form_data.add_field(
                "file",
                json_bytes,
                filename=f"metadata_{virtual_record_id}.json",
                content_type="application/json",
            )
            async with session.put(buffer_url, data=form_data, headers=headers) as resp:
                if resp.status != HttpStatusCode.SUCCESS.value:
                    error_text = await resp.text()
                    raise _storage_status_error(
                        resp.status, f"Failed to update metadata buffer: {resp.status} {error_text[:200]}"
                    )

            self.logger.info("✅ Successfully overrode metadata buffer for document: %s", document_id)
            return document_id, file_size_bytes

        except StorageDocumentNotFoundError:
            raise
        except Exception as e:
            self.logger.error("❌ Error in _update_metadata_buffer for document %s: %s", document_id, str(e))
            raise

    async def _create_metadata_document(
        self, org_id: str, record_id: str, virtual_record_id: str,
        metadata_dict: dict, document_path: str | None = None,
        *, connector_id: str | None = None, record_group_id: str | None = None,
    ) -> str | None:
        """Create a new metadata document in blob storage (uncompressed plain JSON)."""
        try:
            headers, nodejs_endpoint, storage_type = await self._get_auth_and_config(org_id)

            encoded = _encode_record_json(metadata_dict)
            compressed_metadata, use_compression = self._maybe_compress_record(
                metadata_dict, label="metadata", encoded=encoded
            )

            effective_path = document_path or f"records/{virtual_record_id}"
            upload_data = {
                "isCompressed": use_compression,
                "record": compressed_metadata if use_compression else metadata_dict,
                "virtualRecordId": virtual_record_id,
            }

            if storage_type == "local":
                json_data = (
                    _json_utf8_bytes(upload_data)
                    if use_compression
                    else _plain_envelope_bytes(metadata_dict, encoded, virtual_record_id)
                )
                upload_url = f"{nodejs_endpoint}{Routes.STORAGE_UPLOAD.value}"
                create_headers = _with_idempotency_key(headers)

                async def _attempt() -> str:
                    form_data = _versioned_json_form(
                        json_data, f'metadata_{virtual_record_id}', virtual_record_id, record_id,
                        compressed=use_compression,
                        document_path=effective_path if document_path else None,
                        connector_id=connector_id,
                        record_group_id=record_group_id,
                    )
                    async with _borrowed_session() as session, session.post(
                        upload_url, data=form_data, headers=create_headers
                    ) as response:
                        if response.status == HttpStatusCode.CONFLICT.value:
                            # Our own earlier attempt is still storing it.
                            raise TransientStorageError("Metadata upload still in progress")
                        if response.status != HttpStatusCode.SUCCESS.value:
                            try:
                                error_response = await response.json()
                                self.logger.error("❌ Failed to create metadata. Status: %d, Error: %s",
                                                response.status, error_response)
                            except aiohttp.ContentTypeError:
                                error_text = await response.text()
                                self.logger.error("❌ Failed to create metadata. Status: %d, Response: %s",
                                                response.status, error_text[:200])
                            raise _storage_status_error(response.status, "Failed to create metadata document")

                        response_data = await response.json()
                        document_id = response_data.get('_id')
                        if not document_id:
                            raise Exception("No document ID in metadata upload response")
                        return document_id

                document_id = await self._with_storage_retry("metadata upload", _attempt)
                self.logger.debug("✅ Created metadata document: %s", document_id)
                return document_id
            else:
                metadata_entries: list[dict] = []
                if connector_id:
                    metadata_entries.append({"key": "connectorId", "value": connector_id})
                if record_group_id:
                    metadata_entries.append({"key": "recordGroupId", "value": record_group_id})

                placeholder_data: dict = {
                    "documentName": f"metadata_{virtual_record_id}",
                    "documentPath": effective_path,
                    "extension": "json",
                    "isVersionedFile": False,
                }
                if metadata_entries:
                    placeholder_data["customMetadata"] = metadata_entries

                async with _borrowed_session() as session:
                    placeholder_url = f"{nodejs_endpoint}{Routes.STORAGE_PLACEHOLDER.value}"
                    document = await self._create_placeholder(session, placeholder_url, placeholder_data, headers)

                    document_id = document.get("_id")
                    if not document_id:
                        raise Exception("No document ID in metadata placeholder response")

                    upload_url = f"{nodejs_endpoint}{Routes.STORAGE_DIRECT_UPLOAD.value.format(documentId=document_id)}"
                    upload_result = await self._get_signed_url(session, upload_url, {}, headers)

                    signed_url = upload_result.get('signedUrl')
                    if not signed_url:
                        raise Exception("No signed URL in response for metadata document")

                    await self._upload_to_signed_url(session, signed_url, upload_data)

                    self.logger.debug("✅ Created metadata document: %s", document_id)
                    return document_id

        except Exception as e:
            self.logger.error("❌ Error creating metadata document: %s", str(e))
            raise e


    async def save_conversation_file_to_storage(
        self,
        org_id: str,
        conversation_id: str,
        file_name: str,
        file_bytes: bytes,
        content_type: str = "text/csv",
        custom_metadata: list[CustomMetadataEntry] | None = None,
    ) -> dict:
        """Save a file (CSV, etc.) under a conversation path and return download info.

        Args:
            org_id: Organisation ID (used for auth / routing).
            conversation_id: Conversation this file belongs to.
            file_name: Human-readable file name **with** extension
                       (e.g. ``query_result_1709640000.csv``).
            file_bytes: Raw file content.
            content_type: MIME type for the upload.
            custom_metadata: Optional ``customMetadata`` entries for the
                storage document.

        Returns:
            dict with ``documentId``, ``fileName`` and, when cloud storage issues
            one, ``signedUrl``. There is no ``downloadUrl``: the storage service
            has no user-facing route, so a caller that needs a link must register
            the file as a record and use :meth:`get_record_stream_url`.
        """
        import os

        try:
            headers, nodejs_endpoint, storage_type = await self._get_auth_and_config(org_id)

            document_path = f"conversations/{conversation_id}"
            doc_name_no_ext = os.path.splitext(file_name)[0]
            extension = os.path.splitext(file_name)[1].lstrip(".")

            if storage_type == "local":
                async with _borrowed_session() as session:
                    form_data = aiohttp.FormData()
                    form_data.add_field(
                        "file", file_bytes,
                        filename=file_name,
                        content_type=content_type,
                    )
                    form_data.add_field("documentName", doc_name_no_ext)
                    form_data.add_field("documentPath", document_path)
                    form_data.add_field("isVersionedFile", "false")
                    if custom_metadata:
                        _add_custom_metadata_to_form(form_data, custom_metadata)

                    upload_url = f"{nodejs_endpoint}{Routes.STORAGE_UPLOAD.value}"
                    async with session.post(upload_url, data=form_data, headers=headers) as response:
                        if response.status != HttpStatusCode.SUCCESS.value:
                            try:
                                error_body = await response.json()
                                self.logger.error(
                                    "❌ Conversation file upload failed. Status: %d, Error: %s",
                                    response.status, error_body,
                                )
                            except Exception:
                                error_text = await response.text()
                                self.logger.error(
                                    "❌ Conversation file upload failed. Status: %d, Response: %s",
                                    response.status, error_text[:500],
                                )
                            raise Exception(f"Local upload failed with status {response.status}")
                        response_data = await response.json()
                        document_id = response_data.get("_id")
                        if not document_id:
                            raise Exception("No document ID in local upload response")

                    self.logger.info("✅ Conversation file saved (local): %s", document_id)
                    return {
                        "documentId": document_id,
                        "fileName": file_name,
                    }
            else:
                placeholder_data = {
                    "documentName": doc_name_no_ext,
                    "documentPath": document_path,
                    "extension": extension,
                    "isVersionedFile": False,
                }
                if custom_metadata:
                    placeholder_data["customMetadata"] = custom_metadata

                async with _borrowed_session() as session:
                    placeholder_url = f"{nodejs_endpoint}{Routes.STORAGE_PLACEHOLDER.value}"
                    document = await self._create_placeholder(
                        session, placeholder_url, placeholder_data, headers,
                    )
                    document_id = document.get("_id")
                    if not document_id:
                        raise Exception("No document ID in placeholder response")

                    upload_url = (
                        f"{nodejs_endpoint}"
                        f"{Routes.STORAGE_DIRECT_UPLOAD.value.format(documentId=document_id)}"
                    )
                    upload_result = await self._get_signed_url(session, upload_url, {}, headers)
                    signed_url = upload_result.get("signedUrl")
                    if not signed_url:
                        raise Exception("No signed URL for conversation file upload")

                    await self._upload_raw_to_signed_url(
                        session,
                        signed_url,
                        file_bytes,
                        content_type,
                    )

                    download_api = (
                        f"{nodejs_endpoint}"
                        f"{Routes.STORAGE_DOWNLOAD.value.format(documentId=document_id)}"
                    )
                    async with session.get(download_api, headers=headers) as resp:
                        if resp.status == HttpStatusCode.SUCCESS.value:
                            data = await resp.json()
                            download_signed_url = data.get("signedUrl")
                            if download_signed_url:
                                self.logger.info(
                                    "✅ Conversation file saved (S3): %s", document_id,
                                )
                                return {
                                    "documentId": document_id,
                                    "signedUrl": download_signed_url,
                                    "fileName": file_name,
                                }

                    self.logger.info(
                        "✅ Conversation file saved (no signed URL): %s", document_id,
                    )
                    return {
                        "documentId": document_id,
                        "fileName": file_name,
                    }
        except Exception as e:
            self.logger.exception(
                "❌ Error saving conversation file: %s",
                conversation_id,
            )
            raise

    async def save_versioned_artifact_to_storage(
        self,
        org_id: str,
        conversation_id: str,
        file_name: str,
        file_bytes: bytes,
        content_type: str = "application/octet-stream",
    ) -> dict:
        """Create a NEW, version-enabled document for an agent/LLM artifact.

        Same wire path as :meth:`save_conversation_file_to_storage` (raw
        bytes, ``artifacts/{conversation_id}`` path) but with
        ``isVersionedFile: true`` so a later :meth:`upload_artifact_version`
        call can append version 1, 2, ... to THIS SAME document instead of
        creating a parallel, unrelated file per version — the single
        invariant :class:`~app.services.artifact_registry.versioning.VersionManager`
        depends on.

        Returns dict with ``documentId``, ``fileName`` and, on cloud storage,
        ``signedUrl``.
        """
        import os as _os

        headers, nodejs_endpoint, storage_type = await self._get_auth_and_config(org_id)
        document_path = f"artifacts/{conversation_id}"
        doc_name_no_ext = _os.path.splitext(file_name)[0]
        extension = _os.path.splitext(file_name)[1].lstrip(".")

        if storage_type == "local":
            async with _borrowed_session() as session:
                form_data = aiohttp.FormData()
                form_data.add_field(
                    "file", file_bytes, filename=file_name, content_type=content_type,
                )
                form_data.add_field("documentName", doc_name_no_ext)
                form_data.add_field("documentPath", document_path)
                form_data.add_field("isVersionedFile", "true")

                upload_url = f"{nodejs_endpoint}{Routes.STORAGE_UPLOAD.value}"
                async with session.post(upload_url, data=form_data, headers=headers) as response:
                    if response.status != HttpStatusCode.SUCCESS.value:
                        error_text = (await response.text())[:500]
                        self.logger.error(
                            "❌ Versioned artifact upload failed. Status: %d, Response: %s",
                            response.status, error_text,
                        )
                        raise Exception(f"Local upload failed with status {response.status}")
                    response_data = await response.json()
                    document_id = response_data.get("_id")
                    if not document_id:
                        raise Exception("No document ID in local upload response")

                return {"documentId": document_id, "fileName": file_name}
        else:
            placeholder_data = {
                "documentName": doc_name_no_ext,
                "documentPath": document_path,
                "extension": extension,
                "isVersionedFile": True,
            }
            async with _borrowed_session() as session:
                placeholder_url = f"{nodejs_endpoint}{Routes.STORAGE_PLACEHOLDER.value}"
                document = await self._create_placeholder(session, placeholder_url, placeholder_data, headers)
                document_id = document.get("_id") if document else None
                if not document_id:
                    raise Exception("No document ID in placeholder response")

                upload_url = f"{nodejs_endpoint}{Routes.STORAGE_DIRECT_UPLOAD.value.format(documentId=document_id)}"
                upload_result = await self._get_signed_url(session, upload_url, {}, headers)
                signed_url = (upload_result or {}).get("signedUrl")
                if not signed_url:
                    raise Exception("No signed URL for versioned artifact upload")

                await self._upload_raw_to_signed_url(session, signed_url, file_bytes, content_type)

                download_api = f"{nodejs_endpoint}{Routes.STORAGE_DOWNLOAD.value.format(documentId=document_id)}"
                async with session.get(download_api, headers=headers) as resp:
                    if (
                        resp.status == HttpStatusCode.SUCCESS.value
                        and resp.content_type == "application/json"
                    ):
                        data = await resp.json()
                        if data.get("signedUrl"):
                            return {
                                "documentId": document_id,
                                "signedUrl": data["signedUrl"],
                                "fileName": file_name,
                            }
                return {"documentId": document_id, "fileName": file_name}

    async def get_download_url(
        self, org_id: str, document_id: str, version: int | None = None,
    ) -> str | None:
        """S3/Azure signed download URL for `document_id`, or ``None`` when
        storage cannot issue one (local storage). Callers that still need a
        link fall back to :meth:`get_record_stream_url`, which checks the
        viewer's record permissions; the storage service itself has no
        user-facing route.

        `version` is a storage-layer `versionHistory` index (not a registry
        version number)."""
        headers, nodejs_endpoint, storage_type = await self._get_auth_and_config(org_id)
        # Local storage's download route streams the file bytes rather than
        # returning `{signedUrl}` JSON; calling it would fetch the whole file.
        if storage_type == "local":
            return None
        version_query = f"?version={version}" if version is not None else ""
        download_api = (
            f"{nodejs_endpoint}{Routes.STORAGE_DOWNLOAD.value.format(documentId=document_id)}"
            f"{version_query}"
        )
        async with _borrowed_session() as session:
            async with session.get(download_api, headers=headers) as resp:
                # Any vendor that streams on this route instead of returning
                # JSON has no signed URL to give.
                if (
                    resp.status == HttpStatusCode.SUCCESS.value
                    and resp.content_type == "application/json"
                ):
                    data = await resp.json()
                    signed = data.get("signedUrl")
                    if signed:
                        return str(signed)
        return None

    async def get_record_stream_url(self, record_id: str, version: int | None = None) -> str:
        """User-facing link to a record's bytes through the knowledge-base
        stream route, which enforces the viewer's record permissions.
        `version` is the record's own version number, not a storage index."""
        public_base_url = await self._get_public_download_base_url()
        version_query = f"?version={version}" if version is not None else ""
        return f"{public_base_url}{Routes.KB_STREAM_RECORD.value.format(recordId=record_id)}{version_query}"

    async def get_direct_upload_url(self, org_id: str, document_id: str) -> str:
        """Signed PUT URL for an EXISTING document — the first phase of the
        artifact registry's two-phase upload (`get_upload_grant` in
        `signed_urls.py`). Cloud storage (S3/Azure) only: local storage has
        no equivalent unauthenticated PUT target, since `uploadNextVersion`
        requires the same scoped internal JWT this method itself uses to
        fetch the URL — callers must fall back to the inline-content path
        for local deployments (see `SignedUrlBroker.get_upload_grant`).
        """
        headers, nodejs_endpoint, storage_type = await self._get_auth_and_config(org_id)
        if storage_type == "local":
            raise Exception("Direct signed upload URLs are not supported for local storage")
        async with _borrowed_session() as session:
            upload_url = f"{nodejs_endpoint}{Routes.STORAGE_DIRECT_UPLOAD.value.format(documentId=document_id)}"
            upload_result = await self._get_signed_url(session, upload_url, {}, headers)
            signed_url = (upload_result or {}).get("signedUrl")
            if not signed_url:
                raise Exception("No signed URL returned for direct upload")
            return signed_url

    async def upload_artifact_version(
        self,
        org_id: str,
        document_id: str,
        file_name: str,
        file_bytes: bytes,
        content_type: str = "application/octet-stream",
    ) -> dict:
        """Append a new version of RAW bytes to an existing artifact document.

        Distinct from :meth:`upload_next_version` (which JSON-wraps/compresses
        a KB-record snapshot and, for cloud storage, PUTs straight to the
        CURRENT object key via a `directUpload` signed URL — bypassing
        ``versionHistory`` bookkeeping entirely). ``uploadNextVersionDocument``
        (`storage.controller.ts`) is storage-vendor-agnostic — it reads the
        uploaded buffer via `FileProcessorService` and writes it through the
        SAME adapter abstraction (`adapter.getBufferFromStorageService`/
        `cloneDocument`) regardless of S3/Azure/local — so unlike
        `upload_next_version`, this method always posts multipart bytes to
        that one route for EVERY storage vendor, guaranteeing a real
        ``versionHistory`` entry is appended everywhere.

        Returns dict with ``documentId``, ``sizeBytes``, ``storageVersion``
        (the ``versionHistory`` index Node just wrote these bytes to — the
        LAST entry of the response document's ``versionHistory``), and
        ``priorStorageVersion`` (the entry immediately before it, non-None
        only when Node had to lazily snapshot the previous "current" content
        first — i.e. the very first version bump for this document). Callers
        MUST use these indices rather than deriving them arithmetically —
        see `VersionManager.add_version`.
        """
        headers, nodejs_endpoint, _storage_type = await self._get_auth_and_config(org_id)
        file_size_bytes = len(file_bytes)

        async with _borrowed_session() as session:
            form_data = aiohttp.FormData()
            form_data.add_field(
                "file", file_bytes, filename=file_name, content_type=content_type,
            )
            upload_url = f"{nodejs_endpoint}{Routes.STORAGE_UPLOAD_NEXT_VERSION.value.format(documentId=document_id)}"
            async with session.post(upload_url, data=form_data, headers=headers) as response:
                if response.status != HttpStatusCode.SUCCESS.value:
                    error_text = (await response.text())[:500]
                    self.logger.error(
                        "❌ Artifact version upload failed. Status: %d, Response: %s",
                        response.status, error_text,
                    )
                    if response.status == HttpStatusCode.BAD_REQUEST.value and "cannot be versioned" in error_text.lower():
                        raise Exception("This document cannot be versioned")
                    raise Exception(f"Failed to upload artifact version (status: {response.status})")
                try:
                    response_data = await response.json()
                except aiohttp.ContentTypeError:
                    response_data = {}

            version_history = response_data.get("versionHistory") or []
            storage_version = version_history[-1].get("version") if version_history else None
            prior_storage_version = (
                version_history[-2].get("version") if len(version_history) >= 2 else None
            )
            if storage_version is None:
                self.logger.warning(
                    "⚠️ Artifact version upload for document %s returned no versionHistory; "
                    "version-pinned retrieval for this bump will fall back to latest.",
                    document_id,
                )
            return {
                "documentId": document_id,
                "sizeBytes": file_size_bytes,
                "storageVersion": storage_version,
                "priorStorageVersion": prior_storage_version,
            }

    async def purge_document(self, org_id: str, document_id: str) -> int:
        """Remove a storage document and every stored copy of its file.

        Returns how many documents were removed (0 when it was already gone).
        Raises when storage could not remove it; the document is then kept, so
        calling again is safe.
        """
        return await self._purge(
            org_id, Routes.STORAGE_PURGE_DOCUMENT.value.format(documentId=document_id)
        )

    async def purge_virtual_record_documents(self, org_id: str, virtual_record_id: str) -> int:
        """Remove a virtual record's ``record_``/``metadata_`` storage documents, wherever filed.

        Only for a virtual record no record uses any more: records with identical
        content share it.
        """
        return await self._purge(
            org_id,
            Routes.STORAGE_PURGE_VIRTUAL_RECORD.value.format(virtualRecordId=virtual_record_id),
        )

    async def _purge(self, org_id: str, route: str) -> int:
        headers, nodejs_endpoint, _storage_type = await self._get_auth_and_config(org_id)
        async with _borrowed_session() as session, session.delete(
            f"{nodejs_endpoint}{route}", headers=headers
        ) as response:
            if response.status != HttpStatusCode.SUCCESS.value:
                error_text = (await response.text())[:500]
                raise _storage_status_error(
                    response.status,
                    f"Storage purge {route} failed (status {response.status}): {error_text}",
                )
            return int((await response.json()).get("purged", 0))

    async def get_document_version_history(self, org_id: str, document_id: str) -> list[dict]:
        """Fetch the authoritative ``versionHistory`` array for `document_id`
        straight from the storage document (``GET /internal/{documentId}``).
        Used by `artifact_cleanup.py`'s `PENDING_RECONCILE` repair pass to
        recover the storage index of a version bump whose graph-side write
        failed — the blob write itself already succeeded, so this list is
        the ground truth to reconcile FROM."""
        headers, nodejs_endpoint, _storage_type = await self._get_auth_and_config(org_id)
        url = f"{nodejs_endpoint}{Routes.STORAGE_DOCUMENT.value.format(documentId=document_id)}"
        async with _borrowed_session() as session:
            async with session.get(url, headers=headers) as response:
                if response.status != HttpStatusCode.SUCCESS.value:
                    error_text = (await response.text())[:500]
                    self.logger.error(
                        "❌ Failed to fetch document version history for %s. Status: %d, Response: %s",
                        document_id, response.status, error_text,
                    )
                    raise Exception(f"Failed to fetch document {document_id} (status: {response.status})")
                data = await response.json()
                return data.get("versionHistory") or []

    async def get_reconciliation_metadata(self, virtual_record_id: str, org_id: str) -> dict | None:
        """
        Args:
            virtual_record_id: Virtual record ID
            org_id: Organization ID

        Returns:
            dict | None: Metadata dict (hash_to_block_ids, block_id_to_index with block_id -> index int) if found, None otherwise
        """
        try:
            if not self.graph_provider:
                self.logger.error("❌ ArangoService not initialized")
                return None

            try:
                collection_name = CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value
                doc = await self.graph_provider.get_document(
                    virtual_record_id, collection_name
                )
                if not doc or not doc.get("record_metadata_doc_id"):
                    self.logger.info("No metadata document found for virtual_record_id: %s", virtual_record_id)
                    return None
                metadata_document_id = doc["record_metadata_doc_id"]
            except Exception as e:
                self.logger.warning("Error looking up metadata mapping: %s", str(e))
                return None

            # Download metadata from storage
            headers, nodejs_endpoint, _ = await self._get_auth_and_config(org_id)

            download_url = f"{nodejs_endpoint}{Routes.STORAGE_DOWNLOAD.value.format(documentId=metadata_document_id)}"

            session = get_shared_session()
            async with session.get(download_url, headers=headers) as resp:
                if resp.status == HttpStatusCode.SUCCESS.value:
                    data = await resp.json(loads=_decode_json)
                    if data.get("signedUrl"):
                        signed_url = data.get("signedUrl")
                        async with session.get(URL(signed_url, encoded=True)) as signed_resp:
                            if signed_resp.status == HttpStatusCode.SUCCESS.value:
                                data = await signed_resp.json(content_type=None)
                            else:
                                self.logger.warning(
                                    "⚠️ Failed to fetch metadata from signed URL: status %s, virtual_record_id: %s",
                                    signed_resp.status, virtual_record_id
                                )
                                return None
                    # Handle both compressed (from upload_next_version) and uncompressed formats
                    if data.get("isCompressed"):
                        record = self._process_downloaded_record(data)
                    elif isinstance(data, dict) and "record" in data:
                        record = data.get("record", data)
                    else:
                        record = data
                    self.logger.debug(
                        "✅ Retrieved reconciliation metadata for virtual_record_id: %s",
                        virtual_record_id
                    )
                    return record
                else:
                    self.logger.warning(
                        "⚠️ Failed to retrieve metadata: status %s, virtual_record_id: %s",
                        resp.status, virtual_record_id
                    )
                    return None

        except Exception as e:
            self.logger.error("❌ Error retrieving reconciliation metadata: %s", str(e))
            return None
