"""Unit tests for waiting out a rate-limit window a test exhausted (no network)."""

from __future__ import annotations

import requests

from helper.rate_limit_window import FALLBACK_WAIT_SECONDS, wait_out_rate_limit


def _throttled(headers: dict[str, str] | None = None, body: bytes = b"{}") -> requests.Response:
    resp = requests.Response()
    resp.status_code = 429
    resp.headers.update(headers or {})
    resp._content = body
    return resp


def _waited(resp: requests.Response | None) -> list[float]:
    slept: list[float] = []
    wait_out_rate_limit(resp, sleep=slept.append)
    return slept


def test_waits_for_the_retry_after_the_server_gave_plus_a_margin() -> None:
    slept = _waited(_throttled({"Retry-After": "37"}))
    assert len(slept) == 1
    assert 37 < slept[0] <= 39


def test_falls_back_to_ratelimit_reset_when_retry_after_is_missing() -> None:
    slept = _waited(_throttled({"RateLimit-Reset": "12"}))
    assert 12 < slept[0] <= 14


def test_reads_the_retry_after_in_the_error_body_when_headers_were_stripped() -> None:
    slept = _waited(_throttled(body=b'{"error": {"code": "x", "retryAfter": 20}}'))
    assert 20 < slept[0] <= 22


def test_waits_the_whole_window_when_the_server_gave_no_hint() -> None:
    assert _waited(_throttled(body=b"not json")) == [FALLBACK_WAIT_SECONDS]


def test_an_absurd_hint_is_capped_at_one_window() -> None:
    assert _waited(_throttled({"Retry-After": "86400"})) == [FALLBACK_WAIT_SECONDS]


def test_does_not_wait_when_the_burst_never_hit_the_limit() -> None:
    assert _waited(None) == []
