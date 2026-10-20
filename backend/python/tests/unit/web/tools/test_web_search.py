import json
from unittest.mock import AsyncMock, MagicMock, patch

from app.web.chain import ChainFailed
from app.web.errors import AccessDenied, Blocked, RateLimited
from app.web.models import Attempt, CallerContext, WebResult
from app.web.services import SearchOutcome
from app.web.tools.web_search import WebSearchArgs, create_web_search_tool

CALLER = CallerContext(org_id="org-1", user_id="user-1")


def service(outcome: SearchOutcome | Exception) -> MagicMock:
    fake = MagicMock()
    fake.search = AsyncMock(side_effect=outcome) if isinstance(outcome, Exception) else AsyncMock(return_value=outcome)
    return fake


def test_schema() -> None:
    tool = create_web_search_tool(service(Blocked("x")), CALLER)
    assert tool.name == "web_search"
    assert tool.args_schema is WebSearchArgs
    assert "untrusted_web_content" in tool.description


async def test_success_payload_carries_site_date_and_attempts() -> None:
    outcome = SearchOutcome(
        query="q",
        results=(WebResult("https://www.example.com/a", "T", "S", "ddgs", "2026-01-02"),),
        served_by="ddgs",
        attempts=(Attempt("serper", "auth_failed", 0.1), Attempt("ddgs", None, 0.4)),
    )
    fake = service(outcome)
    tool = create_web_search_tool(fake, CALLER)
    with patch("app.web.tools.web_search.record_search") as record:
        payload = json.loads(await tool.ainvoke({"query": "q"}))
    assert payload == {
        "ok": True,
        "result_type": "web_search",
        "query": "q",
        "served_by": "ddgs",
        "attempts": [{"source": "serper", "error_type": "auth_failed"}, {"source": "ddgs", "error_type": None}],
        "web_results": [{
            "title": "T", "link": "https://www.example.com/a", "snippet": "S",
            "domain": "example.com", "published": "2026-01-02", "source": "ddgs",
        }],
    }
    fake.search.assert_awaited_once_with("q", CALLER)
    assert [c.args[:2] for c in record.call_args_list] == [("serper", "auth_failed"), ("ddgs", "ok")]


async def test_every_source_failing_is_reported_with_its_attempts() -> None:  # WP-0-SRCH-01
    attempts = (Attempt("ddgs", "rate_limited", 1.0), Attempt("duckduckgo", "blocked", 0.5))
    error = ChainFailed([RateLimited("slow"), Blocked("bot check")], attempts)
    tool = create_web_search_tool(service(error), CALLER)
    payload = json.loads(await tool.ainvoke({"query": "q"}))
    assert payload["ok"] is False
    assert payload["error_type"] == "blocked"
    assert payload["retryable"] is True
    assert payload["attempts"] == [{"source": "ddgs", "error_type": "rate_limited"}, {"source": "duckduckgo", "error_type": "blocked"}]


async def test_a_refused_search() -> None:
    tool = create_web_search_tool(service(AccessDenied("not allowed")), CALLER)
    payload = json.loads(await tool.ainvoke({"query": "q"}))
    assert payload["error_type"] == "access_denied"
    assert payload["attempts"] == []
