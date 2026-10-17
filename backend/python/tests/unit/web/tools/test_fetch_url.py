import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from app.utils.html_to_blocks import TextBlock
from app.web.chain import ChainOutcome
from app.web.errors import Blocked, InvalidUrl, TimedOut, UnsafeUrl
from app.web.models import Attempt, CallerContext, WebPage
from app.web.tools.fetch_url import (
    FetchUrlArgs,
    checked_url,
    create_fetch_url_tool,
    resolve_tiny_ref_url,
)

CALLER = CallerContext(org_id="org-1", user_id="user-1")


def mapper(ref_to_url: dict[str, str]) -> MagicMock:
    fake = MagicMock()
    fake.ref_to_url = ref_to_url
    return fake


def service(result: WebPage | Exception) -> MagicMock:
    fake = MagicMock()
    if isinstance(result, Exception):
        fake.fetch = AsyncMock(side_effect=result)
    else:
        fake.fetch = AsyncMock(return_value=ChainOutcome(result, "builtin", (Attempt("builtin", None, 0.2),)))
    return fake


PAGE = WebPage(url="https://example.com/final", title="Final page", blocks=(TextBlock(content="Hello"),), fetched_by="builtin")


class TestArgs:
    def test_valid(self) -> None:
        assert FetchUrlArgs(url="https://example.com").url == "https://example.com"

    def test_missing(self) -> None:
        with pytest.raises(ValidationError):
            FetchUrlArgs()


class TestResolveTinyRefUrl:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("", ""),
            ("https://example.com/page", "https://example.com/page"),
            ("https://example.com/page#:~:text=hello%20world", "https://example.com/page"),
            ("https://example.com/page#section:~:text=e%2Dmail%2C%20x", "https://example.com/page#section"),
        ],
    )
    def test_plain_urls(self, url: str, expected: str) -> None:
        assert resolve_tiny_ref_url(url, None) == expected

    def test_ref_resolved_and_fragment_stripped(self) -> None:
        assert resolve_tiny_ref_url("https://ref1.xyz", mapper({"ref1": "https://real.com/page#:~:text=hello"})) == "https://real.com/page"

    def test_unknown_ref_left_as_is(self) -> None:
        assert resolve_tiny_ref_url("https://ref99.xyz", mapper({})) == "https://ref99.xyz"
        assert resolve_tiny_ref_url("https://ref1.xyz", None) == "https://ref1.xyz"


class TestCheckedUrl:
    @pytest.mark.parametrize("url", ["https://preferred.xyz/docs", "https://ref.example.xyz/a", "https://xyz.com/reference"])
    def test_real_urls_containing_ref_and_xyz_are_allowed(self, url: str) -> None:  # WP-0-FETCH-08
        assert checked_url(url) == url

    @pytest.mark.parametrize("url", ["https://ref3.xyz", "ftp://example.com/file", "https:///no-host", "javascript:alert(1)"])
    def test_rejected(self, url: str) -> None:
        with pytest.raises(InvalidUrl):
            checked_url(url)


class TestTool:
    def test_description_warns_about_untrusted_text(self) -> None:
        assert "untrusted_web_content" in create_fetch_url_tool(service(PAGE), CALLER).description

    async def test_success_payload(self) -> None:
        fake = service(PAGE)
        tool = create_fetch_url_tool(fake, CALLER)
        payload = json.loads(await tool.ainvoke({"url": "https://example.com/start"}))
        assert payload == {
            "ok": True,
            "result_type": "url_content",
            "url": "https://example.com/final",
            "title": "Final page",
            "fetched_by": "builtin",
            "blocks": [{"type": "text", "content": "Hello"}],
        }
        fake.fetch.assert_awaited_once_with("https://example.com/start", CALLER)

    async def test_resolves_ref_before_fetching(self) -> None:
        fake = service(PAGE)
        await create_fetch_url_tool(fake, CALLER, ref_mapper=mapper({"ref2": "https://real.com/doc"})).ainvoke({"url": "https://ref2.xyz"})
        assert fake.fetch.call_args.args[0] == "https://real.com/doc"

    async def test_unresolved_ref_is_reported_without_fetching(self) -> None:
        fake = service(PAGE)
        payload = json.loads(await create_fetch_url_tool(fake, CALLER, ref_mapper=mapper({})).ainvoke({"url": "https://ref7.xyz"}))
        assert payload["error_type"] == "invalid_url"
        fake.fetch.assert_not_called()

    @pytest.mark.parametrize(("error", "error_type"), [(UnsafeUrl("x"), "unsafe_url"), (TimedOut("x"), "timeout"), (Blocked("x"), "blocked")])
    async def test_failures_carry_type_and_hint(self, error: Exception, error_type: str) -> None:
        payload = json.loads(await create_fetch_url_tool(service(error), CALLER).ainvoke({"url": "https://example.com"}))
        assert payload["ok"] is False
        assert payload["error_type"] == error_type
        assert "fetch those URLs next" in payload["hint"]
        assert "fetch those URLs next" not in payload["error"]  # the step card shows `error` to people

    async def test_unexpected_exception_is_contained(self) -> None:
        payload = json.loads(await create_fetch_url_tool(service(RuntimeError("boom")), CALLER).ainvoke({"url": "https://example.com"}))
        assert payload["ok"] is False and payload["retryable"] is False
        assert "boom" not in payload["error"]
        assert "fetch those URLs next" in payload["hint"]

    async def test_records_metrics(self) -> None:
        with patch("app.web.tools.fetch_url.record_fetch") as record:
            await create_fetch_url_tool(service(TimedOut("x")), CALLER).ainvoke({"url": "https://example.com"})
        assert record.call_args.args[0] == "timeout"
