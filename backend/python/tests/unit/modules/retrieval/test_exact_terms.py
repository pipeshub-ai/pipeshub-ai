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
    "rollback steps for BILL-2207",
    "status of #4410",
    "what changed in v3.1",
    "upgrade to 3.11",
    "defaults in settings.toml",
    "files under /var/lib/queue",
    "keys in the .env file",
    "who edited .gitignore last",
    "base image in the Dockerfile",
    "lint target in the Makefile",
    "in lib/core/router",
    "manifests in infra/k8s-base",
    "values in helm/web_ui",
    "who maintains infra/k8s-base.",
    "https://docs.example.org/page",
    "retryBackoff value",
    "MAX_WORKERS for the importer",
    "error 429 from the proxy",
    "invoice 553201",
    "p95 response time",
    "ISO27001 certificate",
    "outage in eu-west-2",
    "commit 9be41f0",
    "a delay of 250 ms",
    "99.5% uptime",
    "32 GB workers",
    "a $15k credit",
    'the "cold start" checklist',
    "the “cold start” checklist",
    "the `pool_size` option",
    "the 'green track' launch",
    "the exact error text for signup",
    "the contract clause verbatim",
    "DB_CONNECT_TIMEOUT",
])
def test_identifier_like_queries_ask_for_exact_matching(query: str) -> None:
    assert has_exact_terms(query)


@pytest.mark.parametrize("query", [
    "",
    "   ",
    None,
    "how are refunds handled for monthly plans",
    "who runs the hiring checklist",
    "SLA for premium customers",
    "Q2 planning priorities",
    "notes from the 2025-11-04 review meeting",
    "retention over the last 3 weeks",
    "a 14-day pilot",
    "the team's support rotation",
    "outages in June 2025",
    "the 2nd reminder",
    "FY2024 forecast",
    "music of the 1980s",
    "sync at 10am",
    "there are no 15 spots left",
    "kickoff on 11/02/2025",
    "the 3/4/2025 changelog",
    "rules for and/or filters",
    "the client/server boundary",
    "who owns the client/server.",
    "who owns the client/server...",
    "CI/CD owners",
])
def test_plain_language_queries_do_not(query: str | None) -> None:
    assert not has_exact_terms(query)


@pytest.mark.parametrize("query", [
    "x " * 1001,
    "how are refunds handled " * 100,
])
def test_a_query_too_long_to_read_cheaply_counts_as_an_anchor(query: str) -> None:
    """Fail open: past the cap the gate has not looked, so it must not drop exact text."""
    assert has_exact_terms(query)


@pytest.mark.parametrize("unit", ["a-", "1-", "Ab-", "a.", "a/", "a_/"])
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
    assert exact_text_search_wanted("how are refunds handled")


def test_with_the_gate_off_every_query_wants_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(EXACT_TEXT_SEARCH_GATE_ENV_VAR, raising=False)
    assert exact_text_search_wanted("how are refunds handled")
    assert exact_text_search_wanted(None)


def test_with_the_gate_on_an_anchor_or_the_agent_asks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EXACT_TEXT_SEARCH_GATE_ENV_VAR, "true")
    assert not exact_text_search_wanted("how are refunds handled")
    assert exact_text_search_wanted("rollback steps for BILL-2207")
    assert exact_text_search_wanted("how are refunds handled", exact_match=True)
