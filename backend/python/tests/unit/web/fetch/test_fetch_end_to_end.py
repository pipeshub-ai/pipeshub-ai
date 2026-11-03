"""fetch_public and read_page against in-memory websites, through the real web connector
fetcher over real HTTP (a Unix socket), with no network."""

import gzip

import pytest
from web_behaviour_fakes import INTRANET_HOST, FakeWeb, Page

from app.connectors.sources.web.address_guard import ALLOWED_HOSTS_ENV
from app.connectors.sources.web.fetch_strategy import MAX_GET_REDIRECTS
from app.web.deadline import Deadline
from app.web.errors import (
    Blocked,
    TooLarge,
    TooManyRedirects,
    Unreachable,
    UnsafeUrl,
    UpstreamError,
)
from app.web.fetch.builtin import BuiltinFetcher
from app.web.fetch.http import MAX_PAGE_MB, fetch_public
from tests.unit.web.fetch.test_parsers import make_pdf

SITE = "http://site.test"
TEXT = "Readable paragraph text. " * 40


def _deadline() -> Deadline:
    return Deadline.after(15)


async def read_page(url: str, deadline: Deadline) -> list:
    return list((await BuiltinFetcher().fetch(url, deadline)).blocks)


async def test_reads_a_page(fake_web: FakeWeb) -> None:
    fake_web.html(f"{SITE}/doc", "Doc", text=TEXT)
    page = await BuiltinFetcher().fetch(f"{SITE}/doc", _deadline())
    assert "Readable paragraph text." in " ".join(b.content for b in page.blocks)
    assert page.title == "Doc"
    assert page.url == f"{SITE}/doc"
    assert page.fetched_by == "builtin"


async def test_a_caller_can_refuse_a_redirect(fake_web: FakeWeb) -> None:
    fake_web.redirect(f"{SITE}/go", f"{SITE}/elsewhere").html(f"{SITE}/elsewhere", "Elsewhere")

    async def refuse(url: str) -> bool:
        return False

    with pytest.raises(UnsafeUrl):
        await BuiltinFetcher().fetch(f"{SITE}/go", _deadline(), allow_hop=refuse)
    assert fake_web.gets(f"{SITE}/elsewhere") == 0


async def test_reads_a_pdf(fake_web: FakeWeb) -> None:  # WP-0-FETCH-01
    fake_web.add(f"{SITE}/paper.pdf", Page(body=make_pdf(["One", "Two", "Three"]), content_type="application/pdf"))
    blocks = await read_page(f"{SITE}/paper.pdf", _deadline())
    assert [b.content.splitlines()[0] for b in blocks] == ["[Page 1]", "[Page 2]", "[Page 3]"]


async def test_follows_redirects(fake_web: FakeWeb) -> None:
    fake_web.redirect(f"{SITE}/old", f"{SITE}/new").html(f"{SITE}/new", "New", text=TEXT)
    body = await fetch_public(f"{SITE}/old", _deadline())
    assert body.url == f"{SITE}/new"
    assert body.status == 200


async def test_too_many_redirects(fake_web: FakeWeb) -> None:  # WP-0-FETCH-06
    hops = MAX_GET_REDIRECTS + 2
    for i in range(hops):
        fake_web.redirect(f"{SITE}/r{i}", f"{SITE}/r{i + 1}")
    fake_web.html(f"{SITE}/r{hops}", "End")
    with pytest.raises(TooManyRedirects):
        await fetch_public(f"{SITE}/r0", _deadline())


@pytest.mark.parametrize("allowlisted", [False, True])
async def test_redirect_to_an_internal_host_is_refused(
    fake_web: FakeWeb, monkeypatch: pytest.MonkeyPatch, allowlisted: bool,
) -> None:  # WP-0-FETCH-07
    """Even a host the operator lets the web connector crawl is off limits to chat."""
    if allowlisted:
        monkeypatch.setenv(ALLOWED_HOSTS_ENV, INTRANET_HOST)
    fake_web.redirect(f"{SITE}/go", f"http://{INTRANET_HOST}/secret").html(f"http://{INTRANET_HOST}/secret", "Secret")
    with pytest.raises(UnsafeUrl):
        await fetch_public(f"{SITE}/go", _deadline())
    assert fake_web.gets(f"http://{INTRANET_HOST}/secret") == 0


async def test_internal_host_is_refused_before_any_request(fake_web: FakeWeb, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ALLOWED_HOSTS_ENV, INTRANET_HOST)
    fake_web.html(f"http://{INTRANET_HOST}/secret", "Secret")
    with pytest.raises(UnsafeUrl):
        await fetch_public(f"http://{INTRANET_HOST}/secret", _deadline())
    assert fake_web.requests == []


@pytest.mark.parametrize("chunked", [False, True])
async def test_oversized_body(fake_web: FakeWeb, chunked: bool) -> None:  # WP-0-FETCH-03
    fake_web.add(f"{SITE}/big", Page(body=b"a" * (MAX_PAGE_MB * 1024 * 1024 + 1), content_type="text/plain", chunked=chunked))
    with pytest.raises(TooLarge):
        await fetch_public(f"{SITE}/big", _deadline())


async def test_compression_bomb_stops_at_the_decompressed_cap(fake_web: FakeWeb) -> None:  # WP-0-FETCH-04
    bomb = gzip.compress(b"\0" * (MAX_PAGE_MB * 1024 * 1024 * 3))
    assert len(bomb) < 100 * 1024
    fake_web.add(f"{SITE}/bomb", Page(body=bomb, content_type="text/html", headers={"Content-Encoding": "gzip"}))
    with pytest.raises(TooLarge):
        await fetch_public(f"{SITE}/bomb", _deadline())


async def test_site_that_hangs_up(fake_web: FakeWeb) -> None:
    fake_web.add(f"{SITE}/gone", Page(hang_up=True))
    with pytest.raises(Unreachable):
        await fetch_public(f"{SITE}/gone", _deadline())


async def test_refusal_and_missing_page(fake_web: FakeWeb) -> None:
    fake_web.add(f"{SITE}/private", Page(status=403, body=b"no"))
    with pytest.raises(Blocked):
        await read_page(f"{SITE}/private", _deadline())
    with pytest.raises(UpstreamError) as info:
        await read_page(f"{SITE}/missing", _deadline())
    assert info.value.status == 404
