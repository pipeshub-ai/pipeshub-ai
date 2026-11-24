import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from tests.support.platform_event_capture import event_calls
from app.utils.html_to_blocks import TextBlock
from app.web.chain import ChainOutcome
from app.web.errors import Blocked, InvalidUrl, TimedOut, UnsafeUrl
from app.web.models import Attempt, CallerContext, WebPage
from app.web.own_files import ISSUED_DOWNLOAD_LINKS_KEY, remember_download_link
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
            "images": [],
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
        with event_calls("web.fetch.completed", "outcome", "duration_seconds") as record:
            await create_fetch_url_tool(service(TimedOut("x")), CALLER).ainvoke({"url": "https://example.com"})
        assert record.call_args.args[0] == "timeout"


# Made-up values in the shape of an S3 presigned link for an uploaded file.
OWN_LINK = (
    "https://files-bucket.s3.us-west-2.amazonaws.com/org-1/KnowledgeBase/private/doc-1/current/report.pdf"
    "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIAEXAMPLE%2F20261117%2Fus-west-2%2Fs3%2Faws4_request"
    "&X-Amz-Date=20261117T000000Z&X-Amz-Expires=3600&X-Amz-Signature=0123abcd&X-Amz-SignedHeaders=host"
)


def issued(url: str = OWN_LINK) -> dict:
    state: dict = {}
    remember_download_link(state, url, record_id="rec-1", file_name="report.pdf")
    return state[ISSUED_DOWNLOAD_LINKS_KEY]


class TestOwnFiles:
    """A download link PipesHub handed out for its own file is sent back to the record, not read as a web page."""

    async def test_issued_link_is_not_fetched(self) -> None:
        fake = service(PAGE)
        with event_calls("web.fetch.completed", "outcome", "duration_seconds") as record:
            payload = json.loads(await create_fetch_url_tool(fake, CALLER, issued_links=issued()).ainvoke({"url": OWN_LINK}))
        fake.fetch.assert_not_called()
        assert payload["ok"] is False and payload["error_type"] == "own_file" and payload["retryable"] is False
        assert "report.pdf" in payload["error"]
        assert "rec-1" in payload["hint"] and "knowledge base" in payload["hint"]
        # The step card shows `error` to people: no record id there, and no link either.
        assert "rec-1" not in payload["error"] and "amazonaws" not in json.dumps(payload)
        assert record.call_args.args[0] == "own_file"

    async def test_reencoded_copy_of_issued_link_is_not_fetched(self) -> None:
        fake = service(PAGE)
        copied = OWN_LINK.replace("%2F", "/")
        payload = json.loads(await create_fetch_url_tool(fake, CALLER, issued_links=issued()).ainvoke({"url": copied}))
        assert payload["error_type"] == "own_file"
        fake.fetch.assert_not_called()

    @pytest.mark.parametrize(
        "copied",
        [
            "https://acct.blob.core.windows.net/c/f.pdf?sv=2023-11-03&sp=r&sig=abc+def=",
            "https://acct.blob.core.windows.net/c/f.pdf?sv=2023-11-03&sp=r&sig=abc%2Bdef%3D&x=1",
            "https://acct.blob.core.windows.net/c/f.pdf?x-amz-signature=decoy&sv=2023-11-03&sp=r&sig=abc%2Bdef%3D",
        ],
    )
    async def test_altered_copy_of_issued_azure_link_is_not_fetched(self, copied: str) -> None:
        """A miss would go on to an outside page reader with a link that still opens the file."""
        fake = service(PAGE)
        links = issued("https://acct.blob.core.windows.net/c/f.pdf?sv=2023-11-03&sp=r&sig=abc%2Bdef%3D")
        payload = json.loads(await create_fetch_url_tool(fake, CALLER, issued_links=links).ainvoke({"url": copied}))
        assert payload["error_type"] == "own_file"
        fake.fetch.assert_not_called()

    async def test_tiny_ref_to_issued_link_is_not_fetched(self) -> None:
        fake = service(PAGE)
        tool = create_fetch_url_tool(fake, CALLER, ref_mapper=mapper({"ref3": OWN_LINK}), issued_links=issued())
        payload = json.loads(await tool.ainvoke({"url": "https://ref3.xyz"}))
        assert payload["error_type"] == "own_file"
        fake.fetch.assert_not_called()

    @pytest.mark.parametrize(
        "url",
        [
            # Someone else's signed link: a vendor's download, a file link from search results.
            "https://vendor-bucket.s3.amazonaws.com/manual.pdf?X-Amz-Expires=600&X-Amz-Signature=feedface",
            # The same file signed again later: this request did not hand it out.
            OWN_LINK.replace("0123abcd", "99998888"),
            "https://example.com/start",
        ],
    )
    async def test_links_not_issued_are_fetched_as_before(self, url: str) -> None:
        fake = service(PAGE)
        payload = json.loads(await create_fetch_url_tool(fake, CALLER, issued_links=issued()).ainvoke({"url": url}))
        assert payload["ok"] is True
        fake.fetch.assert_awaited_once_with(url, CALLER)

    async def test_a_link_whose_signature_was_removed_is_not_fetched(self) -> None:
        """The form an earlier turn's saved step gives back: it opens nothing, and would only hand the
        storage path to an outside page reader."""
        fake = service(PAGE)
        saved = OWN_LINK.replace("X-Amz-Signature=0123abcd", "X-Amz-Signature=[REDACTED]")
        payload = json.loads(await create_fetch_url_tool(fake, CALLER).ainvoke({"url": saved}))
        assert payload["error_type"] == "invalid_url"
        assert "knowledge base" in payload["error"]
        fake.fetch.assert_not_called()

    async def test_without_issued_links_an_own_link_is_fetched_as_before(self) -> None:
        fake = service(PAGE)
        await create_fetch_url_tool(fake, CALLER).ainvoke({"url": OWN_LINK})
        fake.fetch.assert_awaited_once_with(OWN_LINK, CALLER)
