"""WP70-403: a host that refuses or rate-limits this reader is counted, and the engine is told to change course.

A deep research run: 15 of 22 failed reads were api.github.com's anonymous 403s ("it may need a sign-in"), retried
across batches. GitHub is read by cloning (``ph git clone``) or, for one file, through raw.githubusercontent.com; any
other host that refuses twice in a run is a host to stop calling.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

from app.web.errors import Blocked, RateLimited, UpstreamError
from app.web.models import CallerContext
from app.web.tools.fetch_url import REFUSALS_BEFORE_STOP, create_fetch_url_tool

CALLER = CallerContext(org_id="org-1", user_id="user-1")


def _tool(*errors: Exception) -> object:
    service = MagicMock()
    service.fetch = AsyncMock(side_effect=list(errors))
    return create_fetch_url_tool(service, CALLER)


async def _read(tool: object, url: str) -> dict[str, object]:
    return json.loads(await tool.ainvoke({"url": url}))  # type: ignore[attr-defined]


def _refused(url: str, status: int = 403) -> Blocked:
    return Blocked(f"{url} refused the request (HTTP {status}); it may need a sign-in", status=status)


async def test_wp70_403_github_api_refusals_point_to_a_clone_or_a_raw_file() -> None:
    url = "https://api.github.com/repos/onyx-dot-app/onyx/contents/README.md"
    tool = _tool(_refused(url), _refused(url))
    first = await _read(tool, url)
    assert first["error_type"] == "blocked" and first["refused_by_host"] == 1
    hint = str(first["hint"])
    assert "ph git clone https://github.com/<owner>/<repo>" in hint and "raw.githubusercontent.com" in hint
    assert "Do not call api.github.com again" in hint
    second = await _read(tool, url)
    assert second["refused_by_host"] == REFUSALS_BEFORE_STOP and second["retryable"] is False


async def test_wp70_403_a_host_that_refuses_twice_is_one_to_stop_calling() -> None:
    first_url, second_url = "https://www.linkedin.com/posts/a", "https://www.linkedin.com/posts/b"
    tool = _tool(_refused(first_url), _refused(second_url))
    first = await _read(tool, first_url)
    assert first["refused_by_host"] == 1 and "Stop calling it" not in str(first["hint"])
    second = await _read(tool, second_url)
    assert second["refused_by_host"] == 2 and second["retryable"] is False
    assert "www.linkedin.com refuses this reader: it has refused 2 requests in this task. Stop calling it" in str(
        second["hint"]
    )


async def test_wp70_403_rate_limits_are_counted_per_host_and_other_failures_are_not() -> None:
    limited = "https://docs.example.com/a"
    tool = _tool(
        RateLimited(f"{limited} is rate limiting requests (HTTP 429)", status=429),
        UpstreamError("https://other.example.org/x returned HTTP 500", status=500),
        RateLimited(f"{limited} is rate limiting requests (HTTP 429)", status=429),
    )
    first = await _read(tool, limited)
    assert first["refused_by_host"] == 1 and "Do not retry it now" in str(first["hint"])
    other = await _read(tool, "https://other.example.org/x")
    assert "refused_by_host" not in other
    again = await _read(tool, limited)
    assert again["refused_by_host"] == 2 and "is rate limiting this reader" in str(again["hint"])
    assert again["retryable"] is False
