"""Sitemap reading (scenarios DI-01 to DI-06, DI-19)."""

import gzip

import pytest

from app.connectors.sources.web import sitemaps
from app.connectors.sources.web.sitemaps import (
    parse_lastmod,
    parse_sitemap,
    read_sitemaps,
)

NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'


def urlset(*entries: tuple[str, str | None]) -> bytes:
    body = "".join(
        f"<url><loc>{loc}</loc>{f'<lastmod>{mod}</lastmod>' if mod else ''}</url>" for loc, mod in entries
    )
    return f'<?xml version="1.0" encoding="UTF-8"?><urlset {NS}>{body}</urlset>'.encode()


def index(*locs: str) -> bytes:
    body = "".join(f"<sitemap><loc>{loc}</loc></sitemap>" for loc in locs)
    return f"<sitemapindex {NS}>{body}</sitemapindex>".encode()


class Site:
    def __init__(self, files: dict[str, bytes | None]) -> None:
        self.files = files
        self.asked: list[str] = []

    async def __call__(self, url: str, limit: int) -> bytes | None:
        self.asked.append(url)
        body = self.files.get(url)
        return body[:limit] if body is not None else None


def _keep_all(_: str) -> bool:
    return True


class TestParse:
    def test_urls_with_and_without_lastmod(self) -> None:
        pages, children, cut = parse_sitemap(urlset(("https://a.test/1", "2026-10-01"), ("https://a.test/2", None)))
        assert [(p.url, p.lastmod_ms is not None) for p in pages] == [("https://a.test/1", True), ("https://a.test/2", False)]
        assert (children, cut) == ([], False)

    def test_an_index_lists_child_sitemaps(self) -> None:
        assert parse_sitemap(index("https://a.test/s1.xml", "https://a.test/s2.xml.gz"))[1] == [
            "https://a.test/s1.xml", "https://a.test/s2.xml.gz",
        ]

    def test_a_plain_text_sitemap(self) -> None:
        pages, _, _ = parse_sitemap(b"https://a.test/1\n\nnot a url\nhttps://a.test/2\n")
        assert [p.url for p in pages] == ["https://a.test/1", "https://a.test/2"]

    def test_entity_expansion_is_refused(self) -> None:
        bomb = (
            b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;">]>'
            b'<urlset><url><loc>&lol2;</loc></url></urlset>'
        )
        with pytest.raises(ValueError):
            parse_sitemap(bomb)

    def test_an_external_entity_is_refused(self) -> None:
        xxe = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><urlset><url><loc>&e;</loc></url></urlset>'
        with pytest.raises(ValueError):
            parse_sitemap(xxe)

    def test_broken_xml(self) -> None:
        with pytest.raises(ValueError):
            parse_sitemap(b"<urlset><url><loc>https://a.test/1</loc>")

    def test_a_file_past_the_protocol_limit_is_cut(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sitemaps, "MAX_URLS_PER_FILE", 3)
        pages, _, cut = parse_sitemap(urlset(*[(f"https://a.test/{i}", None) for i in range(5)]))
        assert len(pages) == 3 and cut


@pytest.mark.parametrize(
    "text, expected_iso",
    [
        ("2026-10-01", "2026-10-02T00:00:00+00:00"),  # a date alone: the end of that day
        ("2026-10-01T09:30:00+02:00", "2026-10-01T07:30:00+00:00"),
        ("2026-10-01T09:30:00Z", "2026-10-01T09:30:00+00:00"),
    ],
)
def test_lastmod_formats(text: str, expected_iso: str) -> None:
    from datetime import datetime

    assert parse_lastmod(text) == int(datetime.fromisoformat(expected_iso).timestamp() * 1000)


@pytest.mark.parametrize("text", [None, "", "yesterday", "2026-13-40"])
def test_unreadable_lastmod(text: str | None) -> None:
    assert parse_lastmod(text) is None


class TestRead:
    async def test_an_index_of_plain_and_gzipped_sitemaps(self) -> None:
        site = Site({
            "https://a.test/index.xml": index("https://a.test/s1.xml", "https://a.test/s2.xml.gz"),
            "https://a.test/s1.xml": urlset(("https://a.test/old", "2025-01-01")),
            "https://a.test/s2.xml.gz": gzip.compress(urlset(("https://a.test/new", "2026-10-01"), ("https://a.test/undated", None))),
        })
        found, report = await read_sitemaps(["https://a.test/index.xml"], site, keep=_keep_all, max_urls=100)
        assert [u.url for u in found] == ["https://a.test/new", "https://a.test/old", "https://a.test/undated"]
        assert (report.files_read, report.files_failed, report.limits_hit) == (3, 0, None)

    async def test_keep_decides_scope(self) -> None:
        site = Site({"https://a.test/s.xml": urlset(("https://a.test/in", None), ("https://b.test/out", None))})
        found, _ = await read_sitemaps(["https://a.test/s.xml"], site, keep=lambda u: u.startswith("https://a.test/"), max_urls=100)
        assert [u.url for u in found] == ["https://a.test/in"]

    async def test_a_url_listed_twice_keeps_its_latest_lastmod(self) -> None:
        site = Site({
            "https://a.test/s.xml": urlset(("https://a.test/p", "2026-01-01"), ("https://a.test/p", "2026-06-01")),
        })
        found, _ = await read_sitemaps(["https://a.test/s.xml"], site, keep=_keep_all, max_urls=100)
        assert found == [sitemaps.SitemapUrl("https://a.test/p", parse_lastmod("2026-06-01"))]

    async def test_index_nesting_stops_at_the_limit(self) -> None:
        files = {f"https://a.test/i{n}.xml": index(f"https://a.test/i{n + 1}.xml") for n in range(6)}
        site = Site(files)
        _, report = await read_sitemaps(["https://a.test/i0.xml"], site, keep=_keep_all, max_urls=100)
        assert len(site.asked) == sitemaps.MAX_INDEX_DEPTH + 1
        assert report.limits_hit == {"max_index_depth"}

    async def test_an_index_that_lists_itself_is_read_once(self) -> None:
        site = Site({"https://a.test/i.xml": index("https://a.test/i.xml")})
        await read_sitemaps(["https://a.test/i.xml"], site, keep=_keep_all, max_urls=100)
        assert site.asked == ["https://a.test/i.xml"]

    async def test_no_more_than_max_files_are_read(self) -> None:
        children = [f"https://a.test/s{n}.xml" for n in range(10)]
        site = Site({"https://a.test/i.xml": index(*children), **{c: urlset() for c in children}})
        _, report = await read_sitemaps(["https://a.test/i.xml"], site, keep=_keep_all, max_urls=100, max_files=4)
        assert len(site.asked) == 4 and "max_files" in report.limits_hit

    async def test_the_newest_urls_are_kept_when_there_are_too_many(self) -> None:
        site = Site({"https://a.test/s.xml": urlset(*[(f"https://a.test/{d}", f"2026-10-0{d}") for d in range(1, 6)])})
        found, report = await read_sitemaps(["https://a.test/s.xml"], site, keep=_keep_all, max_urls=2)
        assert [u.url for u in found] == ["https://a.test/5", "https://a.test/4"]
        assert report.limits_hit == {"max_urls"}

    async def test_a_gzip_bomb_is_dropped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sitemaps, "MAX_FILE_BYTES", 10_000)
        bomb = gzip.compress(b"<urlset>" + b" " * 1_000_000 + b"</urlset>")
        site = Site({"https://a.test/s.xml.gz": bomb})
        found, report = await read_sitemaps(["https://a.test/s.xml.gz"], site, keep=_keep_all, max_urls=100)
        assert found == [] and report.files_failed == 1

    async def test_files_that_cant_be_read_or_parsed_are_counted_and_skipped(self) -> None:
        site = Site({
            "https://a.test/i.xml": index("https://a.test/missing.xml", "https://a.test/broken.xml", "https://a.test/ok.xml"),
            "https://a.test/broken.xml": b"<urlset><url>",
            "https://a.test/ok.xml": urlset(("https://a.test/p", None)),
        })
        found, report = await read_sitemaps(["https://a.test/i.xml"], site, keep=_keep_all, max_urls=100)
        assert [u.url for u in found] == ["https://a.test/p"]
        assert report.files_failed == 2
