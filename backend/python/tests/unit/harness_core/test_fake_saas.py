from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.testing import (
    AckLost,
    FakeSaaS,
    IdempotencyConflict,
    ManualClock,
    RateLimit,
    RateLimited,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_k2_lost_ack_records_effect_and_caller_sees_timeout() -> None:
    saas = FakeSaaS(clock=ManualClock(), lose_ack_after_commit=True)
    with pytest.raises(TimeoutError) as info:
        saas.call("jira.create", {"summary": "Login 500"}, idempotency_key="inv-1")
    assert isinstance(info.value, AckLost)
    effect = saas.assert_exactly_once("inv-1")
    assert effect.payload == {"summary": "Login 500"}


def test_keyed_retry_after_lost_ack_does_not_duplicate() -> None:
    saas = FakeSaaS(clock=ManualClock(), lose_ack_after_commit=True)
    with pytest.raises(AckLost):
        saas.call("jira.create", {"summary": "x"}, idempotency_key="inv-1")
    saas.lose_ack_after_commit = False
    receipt = saas.call("jira.create", {"summary": "x"}, idempotency_key="inv-1")
    assert receipt.receipt_id == "jira.create-1"
    assert len(saas.effects) == 1


def test_unkeyed_retry_duplicates_which_is_what_recovery_must_avoid() -> None:
    saas = FakeSaaS(clock=ManualClock())
    saas.call("slack.post", {"text": "hi"})
    saas.call("slack.post", {"text": "hi"})
    assert len(saas.effects_for("slack.post")) == 2


def test_idempotency_key_reuse_with_other_payload_conflicts() -> None:
    saas = FakeSaaS(clock=ManualClock())
    saas.call("jira.create", {"summary": "a"}, idempotency_key="k")
    with pytest.raises(IdempotencyConflict):
        saas.call("jira.create", {"summary": "b"}, idempotency_key="k")


def test_rate_limit_rejects_without_effect_and_reports_retry_after() -> None:
    clock = ManualClock()
    saas = FakeSaaS(clock=clock, rate_limit=RateLimit(calls=2, window_s=10))
    saas.call("op", {})
    clock.advance(4)
    saas.call("op", {})
    with pytest.raises(RateLimited) as info:
        saas.call("op", {})
    assert info.value.retry_after_s == pytest.approx(6)
    assert len(saas.effects) == 2
    clock.advance(6)
    saas.call("op", {})
    assert len(saas.effects) == 3


def test_lookup_lags_commit() -> None:
    clock = ManualClock()
    saas = FakeSaaS(clock=clock, lookup_lag_s=5)
    saas.call("jira.create", {}, idempotency_key="k")
    assert saas.lookup("k") is None
    clock.advance(5)
    found = saas.lookup("k")
    assert found is not None
    assert found.idempotency_key == "k"
    assert saas.lookup("missing") is None


def test_assert_none() -> None:
    saas = FakeSaaS(clock=ManualClock())
    saas.assert_none()
    saas.call("op", {})
    with pytest.raises(AssertionError):
        saas.assert_none()


def test_two_processes_sharing_a_log_dedupe_each_others_keys(tmp_path: Path) -> None:
    # Both live before either commits, as two workers during a rolling deploy (RU-4a).
    log = tmp_path / "saas.jsonl"
    first, second = FakeSaaS(log_path=log), FakeSaaS(log_path=log)
    original = first.call("create_issue", {"title": "x"}, idempotency_key="k")
    replay = second.call("create_issue", {"title": "x"}, idempotency_key="k")
    assert replay.receipt_id == original.receipt_id
    assert len(log.read_text().splitlines()) == 1
