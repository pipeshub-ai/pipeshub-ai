"""Signed broker envelope (v2): binds a message to the org that produced it.

Wire shape: ``{"ctx": {v, eventId, orgId, userId, requestId, issuedAt, sig}, "payload": "<JSON string>"}``.
``sig`` is HMAC-SHA256 over the topic, the message ``eventType``, the ctx fields and the
SHA-256 of the payload bytes exactly as transmitted, so a broker writer can neither pick
an org, swap a payload, change the event type, move a message to another topic nor
refresh its timestamp. Python and Node sign the same canonical string (see the shared
vector in the tests).

While ``ENVELOPE_SIGN`` is off, a message with no ``ctx`` falls back to ``payload.orgId``,
logged and counted. While it is on, a message with no ``ctx`` is a stripped message: counted,
logged and (``ORG_CONTEXT_MODE=enforce``) rejected.

Replay: a signature older than ``ENVELOPE_MAX_AGE_SECONDS`` (default 900) is stale (counted and
logged in shadow mode, rejected in enforce mode). Inside that window a consumer given an
:class:`Inbox` skips an event id it already finished (:func:`open_configured` raises
``DuplicateEnvelope``); the id is recorded only after the handler succeeded
(:func:`record_processed`), so a failed handler leaves the message to be retried. The
inbox is ``platform.consumer_inbox`` (``app/services/messaging/inbox.py``). Nothing keeps
seen-ids in Redis: an evicted id would let a replay through, and under ``noeviction`` the
ids fill the server.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import math
import os
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from app import platform_events
from app.core.org_context import OrgContext, enforcing, spawn_detached, validate_mode
from app.exceptions.non_retryable import NonRetryableError
from app.utils.logging_fields import log_once
from app.utils.request_context import sanitize_root_id

DEFAULT_MAX_AGE_SECONDS = 900  # beyond normal consumer lag plus a retry backoff (<= 300 s); see docs/org-context.md
MAX_FUTURE_SKEW_SECONDS = 300
DEFAULT_ADMITTED_MAX_AGE_SECONDS = 86400  # how long an admission of a delivery counts; re-stamped each time the delivery moves


_logger = logging.getLogger(__name__)


def _log_loud(key: str, msg: str, *args: object) -> None:
    """Error log at most once per 30 s per key (the counters carry the exact count)."""
    if log_once(f"envelope:{key}", 30):
        _logger.error(msg, *args)


class EnvelopeRejected(NonRetryableError):
    """Dead-letter the message (terminal: retrying cannot fix it); ``reason`` is a stable label.

    ``event_type`` and ``record_id`` are set only when the signature already verified (a stale message, say): they
    name what was dropped, so a lost control message can be traced. Never set from an unverified message.
    """

    def __init__(self, reason: str, *, event_type: str = "", record_id: str = "", age_seconds: float | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.event_type = event_type
        self.record_id = record_id
        self.age_seconds = age_seconds

    def facts(self) -> dict[str, str]:
        """``event_type`` / ``record_id`` for the delivery's end line, only those known."""
        return {k: v for k, v in (("event_type", self.event_type), ("record_id", self.record_id)) if v}

    def describe(self) -> str:
        """What was dropped, for the log line; no payload values, only the verified event type and record id."""
        parts = [f"reason={self.reason}"]
        if self.event_type:
            parts.append(f"event_type={self.event_type}")
        if self.record_id:
            parts.append(f"record_id={self.record_id}")
        if self.age_seconds is not None:
            parts.append(f"age_seconds={int(self.age_seconds)}")
        return " ".join(parts)


class DuplicateEnvelope(Exception):
    """The eventId was already processed; skip without error."""


class Inbox(Protocol):
    """Which events a consumer (its group) has finished, per org. ``ctx`` carries the org and the event id."""

    async def seen(self, consumer: str, ctx: OrgContext) -> bool:
        """True when ``consumer`` already finished this event."""

    async def record(self, consumer: str, ctx: OrgContext) -> None:
        """Remember that ``consumer`` finished this event; called only after its handler succeeded."""


def ctx_required() -> bool:
    return os.getenv("ENVELOPE_CTX_REQUIRED", "false").lower() in ("1", "true", "yes")


def sign_enabled() -> bool:
    return os.getenv("ENVELOPE_SIGN", "false").lower() in ("1", "true", "yes")


def max_age_seconds() -> float:
    raw = os.getenv("ENVELOPE_MAX_AGE_SECONDS", "").strip()
    if not raw:
        return float(DEFAULT_MAX_AGE_SECONDS)
    try:
        value = float(raw)
    except ValueError:
        value = 0.0
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"ENVELOPE_MAX_AGE_SECONDS must be a positive number, got {raw!r}")
    return value


def admitted_max_age_seconds() -> float:
    raw = os.getenv("ENVELOPE_ADMITTED_MAX_AGE_SECONDS", "").strip()
    if not raw:
        return float(DEFAULT_ADMITTED_MAX_AGE_SECONDS)
    try:
        value = float(raw)
    except ValueError:
        value = 0.0
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"ENVELOPE_ADMITTED_MAX_AGE_SECONDS must be a positive number, got {raw!r}")
    return value


def assert_may_start(*, writes_org_scoped_tables: bool) -> None:
    """A consumer writing org-scoped tables never runs with the unsigned fallback."""
    if writes_org_scoped_tables and not ctx_required():
        raise RuntimeError("ENVELOPE_CTX_REQUIRED must be true for consumers that write org-scoped tables")


def derive_key(scoped_jwt_secret: str) -> bytes:
    """HKDF-SHA256(secret, salt='pipeshub', info='envelope-ctx:v2'), 32 bytes (single block)."""
    prk = hmac.new(b"pipeshub", scoped_jwt_secret.encode(), hashlib.sha256).digest()
    return hmac.new(prk, b"envelope-ctx:v2" + b"\x01", hashlib.sha256).digest()


def compute_sig(key: bytes, topic: str, c: dict[str, Any], payload: str, event_type: str = "") -> str:
    canon = "\n".join([
        "v2", topic, event_type, c["eventId"], c["orgId"], c.get("userId") or "", c.get("requestId") or "",
        str(c["issuedAt"]), hashlib.sha256(payload.encode()).hexdigest(),
    ])
    return base64.urlsafe_b64encode(hmac.new(key, canon.encode(), hashlib.sha256).digest()).decode().rstrip("=")


def sign(
    key: bytes, topic: str, payload: dict[str, Any], ctx: OrgContext, *, event_type: str = "", issued_at: int | None = None
) -> dict[str, Any]:
    """Producer side: serialise the payload once and sign that exact string (and the event type)."""
    if ctx.org_id is None:
        raise ValueError("cannot sign a system context")
    body = json.dumps(payload, separators=(",", ":"))
    c: dict[str, Any] = {
        "v": 2, "eventId": str(uuid.uuid4()), "orgId": ctx.org_id, "userId": ctx.user_id,
        "requestId": ctx.request_id, "issuedAt": issued_at or int(time.time()),
    }
    c["sig"] = compute_sig(key, topic, c, body, event_type)
    return {"eventType": event_type, "ctx": c, "payload": body} if event_type else {"ctx": c, "payload": body}


def legacy_ctx(message: dict[str, Any]) -> OrgContext | None:
    """Upgrade-window fallback: the org the producer wrote into ``payload.orgId`` (unsigned).

    The event id the producer stamped (top-level ``eventId``) rides along as the context's identity of the event
   ; it is not an authority, and a message from an older producer has none.
    """
    payload = message.get("payload")
    org_id = payload.get("orgId") if isinstance(payload, dict) else None
    if not (isinstance(org_id, str) and org_id):
        return None
    event_id = message.get("eventId")
    return OrgContext(
        org_id, None, "service", "legacy-payload", sanitize_root_id(message.get("requestId")) or "-",
        event_id=event_id if isinstance(event_id, str) and event_id else None,
    )


def decode_json(text: str) -> Any:  # noqa: ANN401
    """``json.loads`` whose every failure on text that can never decode is a ``JSONDecodeError``.

    ``json.loads`` also raises ``RecursionError`` (nesting deeper than the decoder's fixed limit) and a plain
    ``ValueError`` (an integer longer than ``sys.get_int_max_str_digits()``). Both are as permanent as a syntax error.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise
    except (ValueError, RecursionError) as e:
        raise json.JSONDecodeError(str(e), text, 0) from e


def decode_message(value: str) -> Any:  # noqa: ANN401
    """A consumed message body, decoded a second time when a producer JSON-encoded it twice."""
    raw = decode_json(value)
    return decode_json(raw) if isinstance(raw, str) else raw


def _decode_payload(payload: object) -> dict[str, Any] | None:
    if not isinstance(payload, str):
        return None
    try:
        body = decode_json(payload)
    except json.JSONDecodeError:
        return None
    return body if isinstance(body, dict) else None


_WIRE_ONLY = ("ctx", "org_ctx")  # never trusted from the wire


def _unsigned(message: dict[str, Any]) -> "Opened":
    """Unsigned message in the v1 shape (a v2 payload string is decoded, else the message is malformed)."""
    payload = message.get("payload")
    if isinstance(payload, str):
        payload = _decode_payload(payload)
        if payload is None:
            raise EnvelopeRejected("malformed")
    flat = {k: v for k, v in message.items() if k not in _WIRE_ONLY}
    if "payload" in flat:
        flat["payload"] = payload
    return Opened(legacy_ctx(flat), flat)


@dataclass(frozen=True)
class Opened:
    ctx: OrgContext | None  # None: unsigned message with no payload.orgId (stays unbound, fails closed)
    message: dict[str, Any]  # `ctx` removed, `payload` decoded


def _record_id_of(payload: object) -> str:
    """The record a verified payload is about (empty when it names none or does not decode), for the drop's log line."""
    body = _decode_payload(payload) if isinstance(payload, str) else None
    record_id = (body or {}).get("recordId")
    return record_id[:128] if isinstance(record_id, str) else ""


async def open_envelope(
    topic: str,
    message: dict[str, Any],
    *,
    keys: Sequence[bytes] = (),
    required: bool | None = None,
    now: Callable[[], float] | None = None,
    admitted: bool = False,
) -> Opened:
    """Verify a consumed message and derive its OrgContext. Raises EnvelopeRejected.

    ``admitted``: a consumer already opened this delivery with every check passed, and can prove it (see
    :func:`admission_valid`), so the age comparison is skipped. The signature, shape, org and future-skew checks always run.

    Every rejection is counted in ``pipeshub_envelope_rejected_total{topic,reason,event_type}`` here, once, whichever
    consumer called."""
    try:
        return await _open_envelope(topic, message, keys=keys, required=required, now=now, admitted=admitted)
    except EnvelopeRejected as e:
        platform_events.emit("message.envelope.rejected", topic=topic, reason=e.reason, event_type=e.event_type)
        raise


async def _open_envelope(
    topic: str,
    message: dict[str, Any],
    *,
    keys: Sequence[bytes],
    required: bool | None,
    now: Callable[[], float] | None,
    admitted: bool,
) -> Opened:
    required = ctx_required() if required is None else required
    c = message.get("ctx")
    if c is None:
        if required:
            raise EnvelopeRejected("ctx_missing")
        if sign_enabled():
            # Producers sign, so a message without ctx was stripped or came from an unsigned writer.
            platform_events.emit("message.envelope.stripped", topic=topic)
            if enforcing():
                raise EnvelopeRejected("ctx_stripped")
            _log_loud(f"stripped:{topic}", "envelope ctx missing while signing is enabled topic=%s: accepted in shadow mode", topic)
        else:
            platform_events.emit("message.envelope.accepted_without_context", topic=topic)
        return _unsigned(message)

    payload = message.get("payload")
    if not keys:
        # No key available. Verification configured (signing or required on): never silently skip it.
        if required or (sign_enabled() and enforcing()):
            raise EnvelopeRejected("no_key")
        platform_events.emit("message.envelope.processed_unverified", topic=topic)
        if sign_enabled():
            _log_loud(f"nokey:{topic}", "envelope key unavailable topic=%s: processing unverified (shadow mode)", topic)
        else:
            _logger.warning("envelope ctx present but no key configured topic=%s: processing unverified", topic)
        return _unsigned(message)
    try:
        if not (isinstance(c, dict) and c.get("v") == 2 and isinstance(payload, str)):
            raise EnvelopeRejected("malformed")
        if not all(isinstance(c.get(k), str) and c[k] for k in ("eventId", "orgId", "sig")) or not isinstance(c.get("issuedAt"), int):
            raise EnvelopeRejected("malformed")
        event_type = message.get("eventType")
        event_type = event_type if isinstance(event_type, str) else ""
        if not any(hmac.compare_digest(compute_sig(k, topic, c, payload, event_type), c["sig"]) for k in keys):
            raise EnvelopeRejected("bad_signature")
    except (KeyError, TypeError, UnicodeEncodeError) as e:  # a lone surrogate cannot be encoded for the HMAC
        raise EnvelopeRejected("malformed") from e
    age = (now or time.time)() - c["issuedAt"]
    if age > max_age_seconds() and not admitted:
        platform_events.emit("message.envelope.expired", topic=topic, event_type=event_type[:64])
        if enforcing():
            raise EnvelopeRejected("stale", event_type=event_type[:64], record_id=_record_id_of(payload), age_seconds=age)
        _log_loud(f"stale:{topic}", "validly signed envelope older than %ss topic=%s: accepted in shadow mode", max_age_seconds(), topic)
    if -age > MAX_FUTURE_SKEW_SECONDS:
        raise EnvelopeRejected("future", event_type=event_type[:64], record_id=_record_id_of(payload), age_seconds=age)
    try:
        body = decode_json(payload)
    except json.JSONDecodeError as e:
        raise EnvelopeRejected("malformed") from e
    if not isinstance(body, dict):
        raise EnvelopeRejected("malformed")
    if body.get("orgId") not in (None, c["orgId"]):
        raise EnvelopeRejected("org_mismatch")
    ctx = OrgContext(c["orgId"], c.get("userId"), "user" if c.get("userId") else "service", "envelope",
                     sanitize_root_id(c.get("requestId")) or "-", event_id=c["eventId"])
    return Opened(ctx, {**{k: v for k, v in message.items() if k not in _WIRE_ONLY}, "payload": body})


AsyncKeys = Callable[[], Awaitable[Sequence[bytes]]]
_keys: AsyncKeys | None = None
_sign_key: bytes | None = None


def configure(keys: AsyncKeys | None, sign_key: bytes | None = None) -> None:
    """Startup wiring: key provider (current key first, previous key during rotation) and the producer key."""
    global _keys, _sign_key
    _keys, _sign_key = keys, sign_key


_retry_task: list[asyncio.Task] = []  # at most one background key retry


def _install(key: bytes) -> None:
    async def keys() -> Sequence[bytes]:
        return (key,)

    configure(keys, key)


async def configure_from_config(config_service: Any) -> None:  # noqa: ANN401
    """Derive the envelope key from ``scopedJwtSecret`` in the configuration service (no new secret).

    Raises ValueError for an invalid ``ORG_CONTEXT_MODE`` / ``ENVELOPE_MAX_AGE_SECONDS``. A failed key load is
    retried with backoff (briefly at startup, then in the background until it works) and is never silent:
    while it lasts, messages that need verification are rejected in enforce mode and counted and logged in shadow mode.
    """
    from app.config.constants.service import config_node_constants

    validate_mode()
    max_age_seconds()
    admitted_max_age_seconds()
    for task in _retry_task:
        task.cancel()
    _retry_task.clear()

    async def load() -> bytes | None:
        try:
            secrets = await config_service.get_config(config_node_constants.SECRET_KEYS.value, use_cache=True)
            secret = (secrets or {}).get("scopedJwtSecret")
        except Exception:
            _logger.error("envelope: could not read scopedJwtSecret", exc_info=True)
            return None
        return derive_key(secret) if secret else None

    key = None
    for delay in (0, 1, 2, 4) if (sign_enabled() or ctx_required()) else (0,):
        if delay:
            await asyncio.sleep(delay)
        key = await load()
        if key:
            break
    if key:
        _install(key)
        return
    configure(None)
    _logger.error("envelope: no key; retrying in the background (signing and verification are degraded until it loads)")

    async def retry() -> None:
        delay = 2.0
        while True:
            await asyncio.sleep(delay)
            loaded = await load()
            if loaded:
                _install(loaded)
                _logger.warning("envelope: key loaded after retry")
                _retry_task.clear()
                return
            delay = min(delay * 2, 60.0)

    _retry_task.append(spawn_detached(retry(), name="envelope-key-retry"))


async def open_configured(
    topic: str, message: dict[str, Any], *, inbox: Inbox | None = None, consumer: str = "", admitted: bool = False
) -> Opened:
    """Open with the configured keys. With an ``inbox``, raises ``DuplicateEnvelope`` for an event ``consumer`` already finished.

    Only a message naming its event id and org can be matched; an inbox that cannot answer is counted and logged, and the
    message runs as if new (the broker's at-least-once). ``admitted``: see :func:`open_envelope`."""
    keys = await _keys() if _keys else ()
    opened = await open_envelope(topic, message, keys=keys, admitted=admitted)
    ctx = opened.ctx
    if inbox is None or ctx is None or not ctx.event_id:
        return opened
    try:
        seen = await inbox.seen(consumer, ctx)
    except Exception as e:
        platform_events.emit("message.inbox.failed", operation="seen")
        _log_loud(f"inbox-seen:{topic}", "consumer inbox read failed topic=%s: processing as new (%s)", topic, type(e).__name__)
        return opened
    if seen:
        raise DuplicateEnvelope(ctx.event_id)
    return opened


async def record_processed(inbox: Inbox | None, consumer: str, ctx: OrgContext | None) -> None:
    """After a handler succeeded: remember the event so a redelivery or replay of it is skipped. Never raises (the message is
    acknowledged either way; a lost record only means a redelivery runs again)."""
    if inbox is None or ctx is None or not ctx.event_id:
        return
    try:
        await inbox.record(consumer, ctx)
    except Exception as e:
        platform_events.emit("message.inbox.failed", operation="record")
        _log_loud(f"inbox-record:{consumer}", "consumer inbox write failed consumer=%s: a redelivery would run again (%s)", consumer, type(e).__name__)


def replay_window_seconds() -> float:
    """How long the inbox keeps an event id: the longest a copy of a signed envelope passes the age check after it was processed (its
    age, or a stored admission, plus the future skew). That covers every copy only in enforce mode. In shadow mode a stale signature
    is logged and accepted, so a copy replayed after this runs again (counted in ``pipeshub_envelope_stale_total``), and an unsigned
    message has no age at all, in either mode."""
    return max(max_age_seconds(), admitted_max_age_seconds()) + MAX_FUTURE_SKEW_SECONDS


def with_event_id(message: dict[str, Any]) -> dict[str, Any]:
    """The message with a top-level ``eventId`` (a UUID) unless it already names one."""
    if isinstance(message.get("eventId"), str) and message["eventId"]:
        return message
    return {**message, "eventId": str(uuid.uuid4())}


def _admission_canon(topic: str, group_id: str, message_id: int, message: dict[str, Any], admission_ms: int) -> bytes | None:
    """What an admission binds: the message's identity (not its timestamp or signature, so a re-signed copy keeps it), the row and the stamp."""
    c, payload = message.get("ctx"), message.get("payload")
    if not (isinstance(c, dict) and isinstance(payload, str) and isinstance(c.get("eventId"), str) and isinstance(c.get("orgId"), str)):
        return None
    event_type = message.get("eventType")
    parts = [
        "admit1", topic, group_id, str(message_id), c["eventId"], c["orgId"], event_type if isinstance(event_type, str) else "",
        hashlib.sha256(payload.encode("utf-8", "surrogatepass")).hexdigest(), str(admission_ms),
    ]
    # Each field carries its length, so no two different field lists can encode to the same bytes.
    return "".join(f"{len(part)}:{part}" for part in parts).encode("utf-8", "surrogatepass")


def admission_mac(key: bytes, topic: str, group_id: str, message_id: int, message: dict[str, Any], admission_ms: int) -> str | None:
    canon = _admission_canon(topic, group_id, message_id, message, admission_ms)
    if canon is None:
        return None
    return base64.urlsafe_b64encode(hmac.new(key, canon, hashlib.sha256).digest()).decode().rstrip("=")


def admission_valid(
    keys: Sequence[bytes], topic: str, group_id: str, message_id: int, message: dict[str, Any],
    admission_ms: int | None, mac: str | None, *, now: float | None = None,
) -> bool:
    """Whether a stored admission proves a consumer opened this very message for this very row, recently enough.

    False for anything absent, edited, copied to another group, message or topic, made for another message, made with a key that is
    no longer configured, older than ``ENVELOPE_ADMITTED_MAX_AGE_SECONDS`` or stamped in the future. A False answer only means the
    age check applies as it would to a message never seen.
    """
    if admission_ms is None or not mac or not keys:
        return False
    at = (now if now is not None else time.time())
    stamp = admission_ms / 1000.0
    if at - stamp > admitted_max_age_seconds() or stamp - at > MAX_FUTURE_SKEW_SECONDS:
        return False
    try:
        return any(
            (expected := admission_mac(k, topic, group_id, message_id, message, admission_ms)) is not None
            and hmac.compare_digest(expected, mac)
            for k in keys
        )
    except (TypeError, ValueError):
        return False


async def configured_admission_valid(
    topic: str, group_id: str, message_id: int, message: dict[str, Any], admission_ms: int | None, mac: str | None
) -> bool:
    """:func:`admission_valid` with the configured keys; counts a stored admission that does not verify."""
    if admission_ms is None and mac is None:
        return False
    keys = await _keys() if _keys else ()
    ok = admission_valid(keys, topic, group_id, message_id, message, admission_ms, mac)
    if not ok and keys:
        platform_events.emit("message.admission.refused", topic=topic)
        _log_loud(f"admission:{topic}", "a stored queue admission did not verify topic=%s: the message is judged by its age", topic)
    return ok


async def new_admission(topic: str, group_id: str, message_id: int, message: dict[str, Any]) -> tuple[int, str] | None:
    """(stamp, MAC) for a message this process just opened, made with the current key; None without a key or an envelope to bind."""
    keys = await _keys() if _keys else ()
    if not keys or not isinstance(message.get("ctx"), dict):
        return None
    ms = int(time.time() * 1000)
    mac = admission_mac(keys[0], topic, group_id, message_id, message, ms)
    return None if mac is None else (ms, mac)


def within_lifetime(message: dict[str, Any], now: float | None = None) -> bool:
    """Whether the message's own timestamp is inside the envelope lifetime now (the age check on its own, without any admission)."""
    c = message.get("ctx")
    issued = c.get("issuedAt") if isinstance(c, dict) else None
    return isinstance(issued, int) and ((now if now is not None else time.time()) - issued) <= max_age_seconds()


async def refresh_envelope(topic: str, message: dict[str, Any], *, admitted: bool = False) -> dict[str, Any] | None:
    """The same signed message issued again now, for a consumer that keeps it waiting past its lifetime.

    A delivery that is handed back repeatedly keeps its stored envelope, so the time it has waited counts as its age and
    the envelope would be refused as stale. The message is verified first (signature, age, shape), exactly as on receipt;
    only then is ``issuedAt`` renewed and the signature recomputed over the unchanged payload. Raises EnvelopeRejected for a
    message that does not verify, which includes one that is already stale. Returns None when there is nothing to renew: no
    envelope, or no key to verify or sign with.
    """
    if not isinstance(message.get("ctx"), dict) or not _sign_key or _keys is None:
        return None
    keys = await _keys()
    if not keys:
        return None
    await open_envelope(topic, message, keys=keys, required=True, admitted=admitted)
    c = {**message["ctx"], "issuedAt": int(time.time())}
    event_type = message.get("eventType")
    c["sig"] = compute_sig(_sign_key, topic, c, message["payload"], event_type if isinstance(event_type, str) else "")
    return {**message, "ctx": c}


def sign_message(topic: str, message: dict[str, Any]) -> dict[str, Any]:
    """Producer hook: sign the message when ``ENVELOPE_SIGN`` is on, and give every message an event id.

    The event id names the event (a UUID at publish), whether or not the message is signed: signing adds
    integrity, not identity. A signed message carries it in ``ctx.eventId``; any other leaves with a top-level
    ``eventId``. Consumers use it to tell a redelivery of an event from a new event.

    Signed shape: every top-level field stays, ``payload`` becomes the signed JSON string, ``ctx`` is added.
    With the flag off the message is sent as v1, plus ``eventId``. With it on, a message that cannot be signed (no org
    bound, payload naming another org) is counted in ``pipeshub_envelope_unsigned_total{reason}`` and sent
    unsigned (with ``eventId``); with no key it raises in enforce mode and is counted and logged in shadow mode.
    """
    from app.core.org_context import _ctx

    if not sign_enabled():
        return with_event_id(message)
    ctx = _ctx.get()
    payload = message.get("payload")
    if not (ctx and ctx.org_id and isinstance(payload, dict)):
        platform_events.emit("message.sent_unsigned", topic=topic, reason="no_org")
        return with_event_id(message)
    if payload.get("orgId") not in (None, ctx.org_id):
        platform_events.emit("message.sent_unsigned", topic=topic, reason="org_mismatch")
        return with_event_id(message)
    if not _sign_key:
        platform_events.emit("message.sent_unsigned", topic=topic, reason="no_key")
        if enforcing():
            raise RuntimeError(f"envelope signing is enabled but no key is available (topic={topic})")
        _log_loud(f"signkey:{topic}", "envelope signing enabled but no key is available topic=%s: sent unsigned (shadow mode)", topic)
        return with_event_id(message)
    event_type = message.get("eventType")
    signed = sign(_sign_key, topic, payload, ctx, event_type=event_type if isinstance(event_type, str) else "")
    return {**{k: v for k, v in message.items() if k not in ("payload", "ctx")}, **signed}


def ctx_of(message: Any) -> OrgContext | None:  # noqa: ANN401
    """The org context of a parsed ``StreamMessage``: the verified one, else the counted payload fallback."""
    return message.org_ctx or legacy_ctx({"payload": message.payload, "requestId": message.requestId})


async def open_stream_message(topic: str, raw: Any, model: type, *, admitted: bool = False) -> Any:  # noqa: ANN401
    """Parse helper for consumers that build the message model themselves (indexing consumers).

    Verifies like :func:`open_configured` (no inbox: parsing may run more than once per
    delivery, and a record's own state says what was indexed), builds ``model(**message, org_ctx=ctx)``. Raises EnvelopeRejected.
    """
    if not isinstance(raw, dict):
        return model(**raw)
    opened = await open_configured(topic, raw, admitted=admitted)
    return model(**opened.message, org_ctx=opened.ctx)
