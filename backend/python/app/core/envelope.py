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
logged in shadow mode, rejected in enforce mode). That bounds replay to the window; exact
replay rejection needs a durable consumer inbox (``claim``) that records event ids, which
is not wired yet. Nothing keeps seen-ids in Redis.
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

from app.core.org_context import OrgContext, enforcing, spawn_detached, validate_mode
from app.exceptions.non_retryable import NonRetryableError
from app.telemetry.backend import METRICS_BACKEND
from app.utils.request_context import sanitize_root_id

DEFAULT_MAX_AGE_SECONDS = 900  # beyond normal consumer lag plus a retry backoff (<= 300 s); see docs/org-context.md
MAX_FUTURE_SKEW_SECONDS = 300

CTX_MISSING = METRICS_BACKEND.counter(
    "pipeshub_envelope_ctx_missing_total",
    "Broker messages accepted without a signed ctx (upgrade window)",
    ["topic"],
)


UNVERIFIED = METRICS_BACKEND.counter(
    "pipeshub_envelope_unverified_total",
    "Signed broker messages processed without verification because no key is configured",
    ["topic"],
)
STALE = METRICS_BACKEND.counter(
    "pipeshub_envelope_stale_total",
    "Validly signed broker messages older than the accepted signature age",
    ["topic"],
)
STRIPPED = METRICS_BACKEND.counter(
    "pipeshub_envelope_stripped_total",
    "Broker messages without a ctx while signing is enabled (stripped or unsigned producer)",
    ["topic"],
)
UNSIGNED = METRICS_BACKEND.counter(
    "pipeshub_envelope_unsigned_total",
    "Messages sent unsigned while signing is enabled",
    ["topic", "reason"],
)
_logger = logging.getLogger(__name__)
_last_logged: dict[str, float] = {}


def _log_loud(key: str, msg: str, *args: object) -> None:
    """Error log at most once per 30 s per key (the counters carry the exact count)."""
    t = time.monotonic()
    if t - _last_logged.get(key, -1e9) >= 30:
        _last_logged[key] = t
        _logger.error(msg, *args)


class EnvelopeRejected(NonRetryableError):
    """Dead-letter the message (terminal: retrying cannot fix it); ``reason`` is a stable label."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class DuplicateEnvelope(Exception):
    """The eventId was already processed; skip without error."""


class Inbox(Protocol):
    async def claim(self, topic: str, event_id: str, org_id: str) -> bool:
        """True the first time an eventId is seen. Durable, same transaction as the effect."""


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
    """Upgrade-window fallback: the org the producer wrote into ``payload.orgId`` (unsigned)."""
    payload = message.get("payload")
    org_id = payload.get("orgId") if isinstance(payload, dict) else None
    if not (isinstance(org_id, str) and org_id):
        return None
    return OrgContext(org_id, None, "service", "legacy-payload", sanitize_root_id(message.get("requestId")) or "-")


def _decode_payload(payload: object) -> dict[str, Any] | None:
    if not isinstance(payload, str):
        return None
    try:
        body = json.loads(payload)
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


async def open_envelope(
    topic: str,
    message: dict[str, Any],
    *,
    keys: Sequence[bytes] = (),
    required: bool | None = None,
    inbox: Inbox | None = None,
    now: Callable[[], float] = time.time,
) -> Opened:
    """Verify a consumed message and derive its OrgContext. Raises EnvelopeRejected / DuplicateEnvelope."""
    required = ctx_required() if required is None else required
    c = message.get("ctx")
    if c is None:
        if required:
            raise EnvelopeRejected("ctx_missing")
        if sign_enabled():
            # Producers sign, so a message without ctx was stripped or came from an unsigned writer.
            STRIPPED.inc(topic)
            if enforcing():
                raise EnvelopeRejected("ctx_stripped")
            _log_loud(f"stripped:{topic}", "envelope ctx missing while signing is enabled topic=%s: accepted in shadow mode", topic)
        else:
            CTX_MISSING.inc(topic)
        return _unsigned(message)

    payload = message.get("payload")
    if not keys:
        # No key available. Verification configured (signing or required on): never silently skip it.
        if required or (sign_enabled() and enforcing()):
            raise EnvelopeRejected("no_key")
        UNVERIFIED.inc(topic)
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
    except (KeyError, TypeError) as e:
        raise EnvelopeRejected("malformed") from e
    age = now() - c["issuedAt"]
    if age > max_age_seconds():
        STALE.inc(topic)
        if enforcing():
            raise EnvelopeRejected("stale")
        _log_loud(f"stale:{topic}", "validly signed envelope older than %ss topic=%s: accepted in shadow mode", max_age_seconds(), topic)
    if -age > MAX_FUTURE_SKEW_SECONDS:
        raise EnvelopeRejected("future")
    try:
        body = json.loads(payload)
    except json.JSONDecodeError as e:
        raise EnvelopeRejected("malformed") from e
    if not isinstance(body, dict):
        raise EnvelopeRejected("malformed")
    if body.get("orgId") not in (None, c["orgId"]):
        raise EnvelopeRejected("org_mismatch")
    if inbox is not None and not await inbox.claim(topic, c["eventId"], c["orgId"]):
        raise DuplicateEnvelope(c["eventId"])
    ctx = OrgContext(c["orgId"], c.get("userId"), "user" if c.get("userId") else "service", "envelope",
                     sanitize_root_id(c.get("requestId")) or "-")
    return Opened(ctx, {**{k: v for k, v in message.items() if k not in _WIRE_ONLY}, "payload": body})


AsyncKeys = Callable[[], Awaitable[Sequence[bytes]]]
_keys: AsyncKeys | None = None
_inbox: Inbox | None = None
_sign_key: bytes | None = None


def configure(keys: AsyncKeys | None, inbox: Inbox | None = None, sign_key: bytes | None = None) -> None:
    """Startup wiring: key provider (current key first, previous key during rotation), the consumer inbox, the producer key."""
    global _keys, _inbox, _sign_key
    _keys, _inbox, _sign_key = keys, inbox, sign_key


_retry_task: list[asyncio.Task] = []  # at most one background key retry


def _install(key: bytes) -> None:
    async def keys() -> Sequence[bytes]:
        return (key,)

    configure(keys, _inbox, key)


async def configure_from_config(config_service: Any) -> None:  # noqa: ANN401
    """Derive the envelope key from ``scopedJwtSecret`` in the configuration service (no new secret).

    Raises ValueError for an invalid ``ORG_CONTEXT_MODE`` / ``ENVELOPE_MAX_AGE_SECONDS``. A failed key load is
    retried with backoff (briefly at startup, then in the background until it works) and is never silent:
    while it lasts, messages that need verification are rejected in enforce mode and counted and logged in shadow mode.
    """
    from app.config.constants.service import config_node_constants

    validate_mode()
    max_age_seconds()
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
    configure(None, _inbox)
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


async def open_configured(topic: str, message: dict[str, Any], *, claim: bool = True) -> Opened:
    """``claim=False`` for parse paths that may run more than once per delivery (no inbox claim)."""
    keys = await _keys() if _keys else ()
    return await open_envelope(topic, message, keys=keys, inbox=_inbox if claim else None)


def sign_message(topic: str, message: dict[str, Any]) -> dict[str, Any]:
    """Producer hook: sign the message when ``ENVELOPE_SIGN`` is on.

    Signed shape: every top-level field stays, ``payload`` becomes the signed JSON string, ``ctx`` is added.
    With the flag off the message is sent unchanged (v1). With it on, a message that cannot be signed (no org
    bound, payload naming another org) is counted in ``pipeshub_envelope_unsigned_total{reason}`` and sent
    unchanged; with no key it raises in enforce mode and is counted and logged in shadow mode.
    """
    from app.core.org_context import _ctx

    if not sign_enabled():
        return message
    ctx = _ctx.get()
    payload = message.get("payload")
    if not (ctx and ctx.org_id and isinstance(payload, dict)):
        UNSIGNED.inc(topic, "no_org")
        return message
    if payload.get("orgId") not in (None, ctx.org_id):
        UNSIGNED.inc(topic, "org_mismatch")
        return message
    if not _sign_key:
        UNSIGNED.inc(topic, "no_key")
        if enforcing():
            raise RuntimeError(f"envelope signing is enabled but no key is available (topic={topic})")
        _log_loud(f"signkey:{topic}", "envelope signing enabled but no key is available topic=%s: sent unsigned (shadow mode)", topic)
        return message
    event_type = message.get("eventType")
    signed = sign(_sign_key, topic, payload, ctx, event_type=event_type if isinstance(event_type, str) else "")
    return {**{k: v for k, v in message.items() if k not in ("payload", "ctx")}, **signed}


def ctx_of(message: Any) -> OrgContext | None:  # noqa: ANN401
    """The org context of a parsed ``StreamMessage``: the verified one, else the counted payload fallback."""
    return message.org_ctx or legacy_ctx({"payload": message.payload, "requestId": message.requestId})


async def open_stream_message(topic: str, raw: Any, model: type) -> Any:  # noqa: ANN401
    """Parse helper for consumers that build the message model themselves (indexing consumers).

    Verifies like :func:`open_configured` (no inbox claim: parsing may run more than once per
    delivery), builds ``model(**message, org_ctx=ctx)``. Raises EnvelopeRejected.
    """
    if not isinstance(raw, dict):
        return model(**raw)
    opened = await open_configured(topic, raw, claim=False)
    return model(**opened.message, org_ctx=opened.ctx)
