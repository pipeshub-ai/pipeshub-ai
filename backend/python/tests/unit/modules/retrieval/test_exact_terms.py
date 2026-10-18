import time

import pytest

from app.modules.retrieval import exact_terms
from app.modules.retrieval.exact_terms import (
    EXACT_TEXT_SEARCH_GATE_ENV_VAR,
    exact_text_search_gated,
    exact_text_search_wanted,
    has_exact_terms,
)


@pytest.mark.parametrize("query", [
    "rollback plan for PAY-4312",
    "status of #8812",
    "what changed in v2.3",
    "upgrade to 3.12",
    "defaults in values.yaml",
    "settings under /etc/app/conf",
    "https://wiki.example.com/x",
    "retryPolicy default",
    "MAX_RETRIES for the uploader",
    "error 503 from the gateway",
    "order 778812",
    "p99 latency budget",
    "SOC2 audit evidence",
    "outage in us-east-1",
    "commit a3f9c21",
    "timeout of 150 ms",
    "99.9% availability",
    "16 GB nodes",
    "a $20k discount",
    'the "warm standby" runbook',
    "the “warm standby” runbook",
    "the `max_conns` setting",
    "the 'blue lane' rollout",
    "the exact error message for login",
    "the policy wording verbatim",
    "TLS_HANDSHAKE_TIMEOUT",
])
def test_identifier_like_queries_ask_for_exact_matching(query: str) -> None:
    assert has_exact_terms(query)


@pytest.mark.parametrize("query", [
    "",
    "   ",
    None,
    "how do we handle refunds for annual plans",
    "who owns the onboarding checklist",
    "SLA for enterprise customers",
    "Q3 roadmap priorities",
    "decisions from the 2026-03-14 planning meeting",
    "retention for the last 2 weeks",
    "a 30-day trial",
    "the team's on-call rotation",
    "incidents in March 2026",
    "the 3rd escalation",
    "FY2025 budget",
    "trends in the 1990s",
    "standup at 9am",
])
def test_plain_language_queries_do_not(query: str | None) -> None:
    assert not has_exact_terms(query)


@pytest.mark.parametrize("query", [
    "x " * 1001,
    "how do we handle refunds " * 100,
])
def test_a_query_too_long_to_read_cheaply_counts_as_an_anchor(query: str) -> None:
    """Fail open: past the cap the gate has not looked, so it must not drop exact text."""
    assert has_exact_terms(query)


@pytest.mark.parametrize("unit", ["a-", "1-", "Ab-", "a."])
def test_reading_a_long_query_takes_linear_time(monkeypatch: pytest.MonkeyPatch, unit: str) -> None:
    """Patterns that restarted at every word of a hyphenated or dotted run took
    seconds on 20k characters and would block the event loop."""
    monkeypatch.setattr(exact_terms, "_MAX_READ_CHARS", 10**6)
    query = (unit * 20000)[:20000]
    started = time.perf_counter()
    has_exact_terms(query)
    assert time.perf_counter() - started < 0.5


@pytest.mark.parametrize(("value", "gated"), [
    (None, False), ("", False), ("false", False), ("yes", False), ("true", True), (" TRUE ", True),
])
def test_the_gate_is_opt_in(monkeypatch: pytest.MonkeyPatch, value: str | None, gated: bool) -> None:
    if value is None:
        monkeypatch.delenv(EXACT_TEXT_SEARCH_GATE_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(EXACT_TEXT_SEARCH_GATE_ENV_VAR, value)
    assert exact_text_search_gated() is gated


def test_high_accuracy_mode_does_not_turn_the_gate_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """That mode never trades the exact-text search's time for a faster answer."""
    monkeypatch.delenv(EXACT_TEXT_SEARCH_GATE_ENV_VAR, raising=False)
    monkeypatch.setenv("HIGH_ACCURACY_MODE", "true")
    assert not exact_text_search_gated()
    assert exact_text_search_wanted("how do we handle refunds")


def test_with_the_gate_off_every_query_wants_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(EXACT_TEXT_SEARCH_GATE_ENV_VAR, raising=False)
    assert exact_text_search_wanted("how do we handle refunds")
    assert exact_text_search_wanted(None)


def test_with_the_gate_on_an_anchor_or_the_agent_asks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EXACT_TEXT_SEARCH_GATE_ENV_VAR, "true")
    assert not exact_text_search_wanted("how do we handle refunds")
    assert exact_text_search_wanted("rollback plan for PAY-4312")
    assert exact_text_search_wanted("how do we handle refunds", exact_match=True)
