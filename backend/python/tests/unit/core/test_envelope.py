"""Signed broker envelope v2 and the consumer wrapper."""

import json
import time
import uuid

import pytest

from app.core import envelope
from app.core.envelope import (
    DuplicateEnvelope,
    EnvelopeRejected,
    assert_may_start,
    compute_sig,
    derive_key,
    open_envelope,
    sign,
)
from app.core.org_context import OrgContext
from app.exceptions.non_retryable import NonRetryableError

KEY = derive_key("test-scopedJwtSecret-from-config-service")
OTHER_KEY = derive_key("another-secret")
CTX_B = OrgContext("org_b", "u1", "user", "jwt", "req-1")
TOPIC = "record-events"


def _msg(payload=None, ctx=CTX_B, **kw) -> dict:
    return sign(KEY, TOPIC, payload or {"orgId": "org_b", "recordId": "r1"}, ctx, **kw)


async def _open(msg, topic=TOPIC, keys=(KEY,), **kw) -> envelope.Opened:
    return await open_envelope(topic, msg, keys=keys, required=kw.pop("required", False), **kw)


def test_key_and_signature_match_the_shared_vector() -> None:
    # the same vector is asserted by the Node suite (tests/libs/context/envelope.test.ts)
    key = derive_key("vector-secret")
    assert key.hex() == "1477ea7d774181ea88495e8e9203338d6b9472d957b1671cbc08266e574576fa"
    ctx = {"eventId": "evt-1", "orgId": "org_b", "userId": "u1", "requestId": "req-1", "issuedAt": 1700000000}
    body = '{"orgId":"org_b","n":1}'
    assert compute_sig(key, "record-events", ctx, body, "record.created") == "bf5S2YKNK43EZq5bjcjbFTxu7s8h3JddJbQ311Nw7UU"
    assert compute_sig(key, "record-events", ctx, body) == "1FlNqHKd4JVp7RXiOotLTUVSLd2YKzG6S5S3Ned6row"


async def test_valid_message_binds_signed_org() -> None:
    opened = await _open(_msg())
    assert opened.ctx.org_id == "org_b" and opened.ctx.source == "envelope"
    assert opened.message["payload"] == {"orgId": "org_b", "recordId": "r1"} and "ctx" not in opened.message


async def test_payload_swap_under_valid_ctx_is_rejected() -> None:
    m = _msg()
    m["payload"] = json.dumps({"orgId": "org_b", "recordId": "attacker", "action": "delete"}, separators=(",", ":"))
    with pytest.raises(EnvelopeRejected) as e:
        await _open(m)
    assert e.value.reason == "bad_signature" and isinstance(e.value, NonRetryableError)


async def test_forged_consistent_org_rewrite_is_rejected() -> None:
    m = _msg()
    m["ctx"]["orgId"] = "org_x"
    m["payload"] = json.dumps({"orgId": "org_x"})
    with pytest.raises(EnvelopeRejected, match="bad_signature"):
        await _open(m)


async def test_bad_signature_with_a_key_configured_is_rejected() -> None:
    with pytest.raises(EnvelopeRejected, match="bad_signature"):
        await _open(_msg(), keys=(OTHER_KEY,))


async def test_v2_message_with_no_key_configured_is_processed_counted_and_logged(caplog) -> None:
    before = envelope.UNVERIFIED._metric.labels(TOPIC)._value.get()
    with caplog.at_level("WARNING"):
        opened = await _open(_msg(), keys=())
    assert (opened.ctx.org_id, opened.ctx.source) == ("org_b", "legacy-payload")
    assert opened.message["payload"] == {"orgId": "org_b", "recordId": "r1"} and "ctx" not in opened.message
    assert envelope.UNVERIFIED._metric.labels(TOPIC)._value.get() == before + 1
    assert "no key configured" in caplog.text


async def test_v2_message_with_no_key_is_dead_lettered_once_the_flag_is_on() -> None:
    with pytest.raises(EnvelopeRejected, match="no_key"):
        await _open(_msg(), keys=(), required=True)


async def test_producer_signs_only_when_the_flag_and_a_key_are_on(monkeypatch) -> None:
    from app.core.org_context import bind

    flat = {"eventType": "T", "payload": {"orgId": "org_b", "n": 1}, "requestId": "r"}
    envelope.configure(None, None, KEY)
    try:
        with bind(CTX_B):
            assert envelope.sign_message(TOPIC, flat) == flat  # flag off: v1
            monkeypatch.setenv("ENVELOPE_SIGN", "true")
            signed = envelope.sign_message(TOPIC, flat)
            assert isinstance(signed["payload"], str) and signed["eventType"] == "T" and signed["ctx"]["v"] == 2
            assert (await _open(signed)).message["payload"] == flat["payload"]  # round trip
            other_org = {**flat, "payload": {"orgId": "org_x"}}
            assert envelope.sign_message(TOPIC, other_org) == other_org  # never sign a payload for another org
        assert envelope.sign_message(TOPIC, flat) == flat  # no org bound: v1
    finally:
        envelope.configure(None)


async def test_previous_key_accepted_during_rotation() -> None:
    assert (await _open(_msg(), keys=(OTHER_KEY, KEY))).ctx.org_id == "org_b"


async def test_new_event_id_on_old_signature_and_other_topic_are_rejected() -> None:
    m = _msg()
    m["ctx"]["eventId"] = str(uuid.uuid4())
    with pytest.raises(EnvelopeRejected, match="bad_signature"):
        await _open(m)
    with pytest.raises(EnvelopeRejected, match="bad_signature"):
        await _open(_msg(), topic="entity-events")


async def test_payload_org_must_match_ctx_org() -> None:
    m = _msg(payload={"orgId": "org_a"})  # signed correctly, but names another org
    with pytest.raises(EnvelopeRejected, match="org_mismatch"):
        await _open(m)


async def test_stale_and_future_issued_at_are_rejected(monkeypatch) -> None:
    monkeypatch.setenv("ORG_CONTEXT_MODE", "enforce")
    with pytest.raises(EnvelopeRejected, match="stale"):
        await _open(_msg(issued_at=int(time.time()) - 3600))
    with pytest.raises(EnvelopeRejected, match="future"):
        await _open(_msg(issued_at=int(time.time()) + 3600))


async def test_malformed_ctx_is_rejected() -> None:
    for bad in ({"v": 1}, "x", {"v": 2, "eventId": "e", "orgId": "o", "sig": "s", "issuedAt": "1"}):
        with pytest.raises(EnvelopeRejected, match="malformed"):
            await _open({"ctx": bad, "payload": "{}"})
    m = _msg()
    m["payload"] = {"orgId": "org_b"}  # v2 payload must be the signed string
    with pytest.raises(EnvelopeRejected, match="malformed"):
        await _open(m)


class _Inbox:
    def __init__(self) -> None:
        self.ids = set()

    async def claim(self, topic, event_id, org_id) -> bool:
        if (topic, event_id) in self.ids:
            return False
        self.ids.add((topic, event_id))
        return True


async def test_replay_is_skipped_via_the_durable_inbox_not_redis() -> None:
    inbox, m = _Inbox(), _msg()
    await _open(m, inbox=inbox)
    with pytest.raises(DuplicateEnvelope):
        await _open(m, inbox=inbox)


async def test_n_minus_1_message_without_ctx_is_accepted_and_counted_before_the_flag() -> None:
    before = envelope.CTX_MISSING._metric.labels(TOPIC)._value.get()
    legacy = {"eventType": "x", "payload": {"orgId": "org_b"}, "requestId": "req-9"}
    opened = await _open(legacy)
    assert (opened.ctx.org_id, opened.ctx.source) == ("org_b", "legacy-payload")
    assert envelope.CTX_MISSING._metric.labels(TOPIC)._value.get() == before + 1
    # no org anywhere: stays unbound, so current() fails closed
    assert (await _open({"eventType": "x", "payload": {}})).ctx is None


async def test_missing_ctx_is_dead_lettered_once_the_flag_is_on() -> None:
    with pytest.raises(EnvelopeRejected, match="ctx_missing"):
        await _open({"payload": {"orgId": "org_b"}}, required=True)


def test_org_scoped_writer_refuses_to_start_with_the_flag_off(monkeypatch) -> None:
    monkeypatch.delenv("ENVELOPE_CTX_REQUIRED", raising=False)
    assert_may_start(writes_org_scoped_tables=False)
    with pytest.raises(RuntimeError):
        assert_may_start(writes_org_scoped_tables=True)
    monkeypatch.setenv("ENVELOPE_CTX_REQUIRED", "true")
    assert_may_start(writes_org_scoped_tables=True)


async def test_indexing_parse_helper_uses_a_valid_ctx_else_the_counted_fallback() -> None:
    from app.services.messaging.config import StreamMessage

    async def keys() -> tuple[bytes, ...]:
        return (KEY,)

    envelope.configure(keys)
    try:
        signed = _msg(event_type="T")
        m = await envelope.open_stream_message(TOPIC, signed, StreamMessage)
        assert m.org_ctx.source == "envelope" and m.payload["recordId"] == "r1"
        v1 = await envelope.open_stream_message(TOPIC, {"eventType": "T", "payload": {"orgId": "org_z"}}, StreamMessage)
        assert v1.org_ctx.source == "legacy-payload" and v1.org_ctx.org_id == "org_z"
        bad = _msg(event_type="T")
        bad["ctx"]["orgId"] = "org_x"
        with pytest.raises(EnvelopeRejected):
            await envelope.open_stream_message(TOPIC, bad, StreamMessage)
    finally:
        envelope.configure(None)


# --- event type, window, stripped ctx, key failures, wire keys ------------------------------


def _typed(event_type="record.created", **kw) -> dict:
    m = _msg(event_type=event_type, **kw)
    assert m["eventType"] == event_type
    return m


async def test_event_type_is_signed() -> None:
    assert (await _open(_typed())).message["eventType"] == "record.created"
    for tampered in ("deleteRecord", ""):
        m = _typed()
        m["eventType"] = tampered
        with pytest.raises(EnvelopeRejected, match="bad_signature"):
            await _open(m)
    m = _msg()  # signed with no event type, then given one
    m["eventType"] = "deleteRecord"
    with pytest.raises(EnvelopeRejected, match="bad_signature"):
        await _open(m)


async def test_producer_signs_the_event_type(monkeypatch) -> None:
    from app.core.org_context import bind

    envelope.configure(None, None, KEY)
    monkeypatch.setenv("ENVELOPE_SIGN", "true")
    try:
        with bind(CTX_B):
            signed = envelope.sign_message(TOPIC, {"eventType": "T", "payload": {"orgId": "org_b"}})
        signed["eventType"] = "other"
        with pytest.raises(EnvelopeRejected, match="bad_signature"):
            await _open(signed)
    finally:
        envelope.configure(None)


async def test_stale_signature_follows_the_mode(monkeypatch, caplog) -> None:
    old = _msg(issued_at=int(time.time()) - 901)
    monkeypatch.delenv("ORG_CONTEXT_MODE", raising=False)
    envelope._last_logged.clear()
    before = envelope.STALE._metric.labels(TOPIC)._value.get()
    with caplog.at_level("ERROR"):
        assert (await _open(old)).ctx.org_id == "org_b"  # shadow: counted and logged, accepted
    assert envelope.STALE._metric.labels(TOPIC)._value.get() == before + 1 and "older than" in caplog.text
    monkeypatch.setenv("ORG_CONTEXT_MODE", "enforce")
    with pytest.raises(EnvelopeRejected, match="stale"):
        await _open(old)
    assert (await _open(_msg(issued_at=int(time.time()) - 600))).ctx.org_id == "org_b"  # inside the default window
    monkeypatch.setenv("ENVELOPE_MAX_AGE_SECONDS", "1200")
    assert (await _open(old)).ctx.org_id == "org_b"  # the window is configurable
    for bad in ("soon", "nan", "inf", "0"):
        monkeypatch.setenv("ENVELOPE_MAX_AGE_SECONDS", bad)
        with pytest.raises(ValueError):
            envelope.max_age_seconds()


async def test_stripped_ctx_is_counted_as_stripped_while_signing_is_on(monkeypatch) -> None:
    legacy = {"eventType": "x", "payload": {"orgId": "org_b"}}
    stripped, missing = envelope.STRIPPED._metric.labels(TOPIC), envelope.CTX_MISSING._metric.labels(TOPIC)
    monkeypatch.delenv("ENVELOPE_SIGN", raising=False)
    s0, m0 = stripped._value.get(), missing._value.get()
    await _open(legacy)  # producer flag off: plain v1
    assert (stripped._value.get(), missing._value.get()) == (s0, m0 + 1)
    monkeypatch.setenv("ENVELOPE_SIGN", "true")
    monkeypatch.delenv("ORG_CONTEXT_MODE", raising=False)
    assert (await _open(legacy)).ctx.source == "legacy-payload"  # shadow: counted, accepted
    assert (stripped._value.get(), missing._value.get()) == (s0 + 1, m0 + 1)
    monkeypatch.setenv("ORG_CONTEXT_MODE", "enforce")
    with pytest.raises(EnvelopeRejected, match="ctx_stripped"):
        await _open(legacy)


async def test_stripped_v2_message_has_the_v1_shape() -> None:
    m = _msg()
    del m["ctx"]
    opened = await _open(m)
    assert opened.message["payload"] == {"orgId": "org_b", "recordId": "r1"}
    assert opened.ctx.org_id == "org_b" and opened.ctx.source == "legacy-payload"
    m["payload"] = "not json"
    with pytest.raises(EnvelopeRejected, match="malformed"):
        await _open(m)


async def test_wire_org_ctx_key_is_dropped() -> None:
    m = _msg()
    m["org_ctx"] = "forged"
    assert "org_ctx" not in (await _open(m)).message
    assert "org_ctx" not in (await _open({"payload": {"orgId": "org_b"}, "org_ctx": "forged"})).message


async def test_no_key_while_signing_is_never_silent(monkeypatch, caplog) -> None:
    monkeypatch.setenv("ENVELOPE_SIGN", "true")
    monkeypatch.delenv("ORG_CONTEXT_MODE", raising=False)
    before = envelope.UNVERIFIED._metric.labels(TOPIC)._value.get()
    envelope._last_logged.clear()
    with caplog.at_level("ERROR"):
        assert (await _open(_msg(), keys=())).ctx.source == "legacy-payload"
    assert envelope.UNVERIFIED._metric.labels(TOPIC)._value.get() == before + 1 and "key unavailable" in caplog.text
    monkeypatch.setenv("ORG_CONTEXT_MODE", "enforce")
    with pytest.raises(EnvelopeRejected, match="no_key"):
        await _open(_msg(), keys=())


async def test_producer_without_a_key_is_counted_and_fails_closed_in_enforce(monkeypatch) -> None:
    from app.core.org_context import bind

    envelope.configure(None)
    monkeypatch.setenv("ENVELOPE_SIGN", "true")
    flat = {"eventType": "T", "payload": {"orgId": "org_b"}}
    counter = envelope.UNSIGNED._metric.labels(TOPIC, "no_key")
    before = counter._value.get()
    with bind(CTX_B):
        monkeypatch.delenv("ORG_CONTEXT_MODE", raising=False)
        assert envelope.sign_message(TOPIC, flat) == flat and counter._value.get() == before + 1
        monkeypatch.setenv("ORG_CONTEXT_MODE", "enforce")
        with pytest.raises(RuntimeError, match="no key"):
            envelope.sign_message(TOPIC, flat)
    assert envelope.sign_message(TOPIC, flat) == flat  # no org bound: counted, sent as v1
    assert envelope.UNSIGNED._metric.labels(TOPIC, "no_org")._value.get() >= 1


class _Config:
    def __init__(self, fail_times) -> None:
        self.fail_times, self.calls = fail_times, 0

    async def get_config(self, key, use_cache=True) -> dict[str, str]:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ConnectionError("config store down")
        return {"scopedJwtSecret": "s3cret"}


async def test_failed_key_load_is_retried_in_the_background(monkeypatch) -> None:
    import asyncio

    sleeps = []
    real_sleep = asyncio.sleep

    async def fast_sleep(delay) -> None:
        sleeps.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(envelope.asyncio, "sleep", fast_sleep)
    monkeypatch.setenv("ENVELOPE_SIGN", "true")
    cfg = _Config(fail_times=5)  # all four startup attempts fail, the second retry succeeds
    await envelope.configure_from_config(cfg)
    assert envelope._keys is None and envelope._retry_task  # degraded, not silently off
    for _ in range(50):
        await real_sleep(0)
        if envelope._keys is not None:
            break
    try:
        assert envelope._sign_key == derive_key("s3cret") and sleeps[:3] == [1, 2, 4]
    finally:
        envelope.configure(None)


async def test_invalid_settings_stop_startup(monkeypatch) -> None:
    monkeypatch.setenv("ORG_CONTEXT_MODE", "enforced")
    with pytest.raises(ValueError):
        await envelope.configure_from_config(_Config(0))
    monkeypatch.delenv("ORG_CONTEXT_MODE")
    monkeypatch.setenv("ENVELOPE_MAX_AGE_SECONDS", "-1")
    with pytest.raises(ValueError):
        await envelope.configure_from_config(_Config(0))
