"""Page URLs a site lists in its sitemaps (sitemaps.org protocol 0.9).

Sitemaps come from robots.txt ``Sitemap:`` lines or the usual addresses, may be gzipped, and may
be indexes of further sitemaps. Everything here is site input, so every limit is hard: how deep
indexes nest, how many files are read, how many bytes each may decompress to, how many URLs are
kept. XML is parsed with defusedxml, which refuses entity expansion and external entities.
"""

import zlib
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
from xml.etree.ElementTree import Element

from defusedxml.ElementTree import iterparse

# Protocol limits are 50,000 URLs and 50 MB uncompressed per file.
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_URLS_PER_FILE = 50_000
MAX_FILES = 50
MAX_INDEX_DEPTH = 3
GZIP_MAGIC = b"\x1f\x8b"


@dataclass(frozen=True)
class SitemapUrl:
    url: str
    # When the site says the page last changed, in epoch ms; None when it doesn't say.
    lastmod_ms: int | None = None


@dataclass
class SitemapReport:
    files_read: int = 0
    files_failed: int = 0
    # Limits that cut the listing short, e.g. "max_files", "max_index_depth".
    limits_hit: set[str] | None = None

    def hit(self, limit: str) -> None:
        self.limits_hit = (self.limits_hit or set()) | {limit}


# Fetches a sitemap URL and returns at most ``limit`` bytes of its body, or None if it couldn't.
Fetch = Callable[[str, int], Awaitable[bytes | None]]


def parse_lastmod(text: str | None) -> int | None:
    """W3C datetime (``2026-10-01``, ``2026-10-01T09:30:00+02:00``, ``...Z``) as epoch ms. A date alone
    reads as the end of that day (UTC): the page may have changed after a crawl earlier that day."""
    if not text:
        return None
    value = text.strip().replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    if "T" not in value:
        moment += timedelta(days=1)
    return int(moment.timestamp() * 1000)


def _decompressed(body: bytes) -> bytes | None:
    """The body, gunzipped when it is gzip; None if gzip is broken or inflates past MAX_FILE_BYTES."""
    if not body.startswith(GZIP_MAGIC):
        return body
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        out = inflater.decompress(body, MAX_FILE_BYTES + 1)
    except zlib.error:
        return None
    return None if len(out) > MAX_FILE_BYTES or inflater.unconsumed_tail else out


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(element: Element, name: str) -> str | None:
    for child in element:
        if _local(child.tag) == name:
            return (child.text or "").strip() or None
    return None


def parse_sitemap(body: bytes) -> tuple[list[SitemapUrl], list[str], bool]:
    """(page URLs, child sitemap URLs, whether the file was cut at MAX_URLS_PER_FILE).

    Plain-text sitemaps (one URL per line) are accepted too. Raises ValueError for XML that
    isn't well formed or that defusedxml refuses.
    """
    stripped = body.lstrip()
    if not stripped.startswith(b"<"):
        lines = [line.strip() for line in body.decode("utf-8", "replace").splitlines()]
        urls = [SitemapUrl(line) for line in lines if line.startswith(("http://", "https://"))]
        return urls[:MAX_URLS_PER_FILE], [], len(urls) > MAX_URLS_PER_FILE
    pages: list[SitemapUrl] = []
    children: list[str] = []
    try:
        for _, element in iterparse(BytesIO(body), events=("end",)):
            kind = _local(element.tag)
            if kind not in ("url", "sitemap"):
                continue
            loc = _child_text(element, "loc")
            if loc:
                if kind == "sitemap":
                    children.append(loc)
                elif len(pages) < MAX_URLS_PER_FILE:
                    pages.append(SitemapUrl(loc, parse_lastmod(_child_text(element, "lastmod"))))
                else:
                    return pages, children, True
            element.clear()
    except Exception as e:  # ParseError and defusedxml's refusals both mean: not a usable sitemap
        raise ValueError(f"not a usable sitemap: {e}") from e
    return pages, children, False


async def read_sitemaps(
    roots: Iterable[str],
    fetch: Fetch,
    *,
    keep: Callable[[str], bool],
    max_urls: int,
    max_files: int = MAX_FILES,
    max_depth: int = MAX_INDEX_DEPTH,
) -> tuple[list[SitemapUrl], SitemapReport]:
    """Every page URL the sitemaps under ``roots`` list that ``keep`` accepts, newest first.

    Each URL is listed once, with the latest lastmod any sitemap gave it. ``keep`` decides scope
    (same site, URL filters); ``fetch`` decides what may be requested at all (the address guard).
    """
    report = SitemapReport()
    seen_files: set[str] = set()
    found: dict[str, int | None] = {}
    pending: list[tuple[str, int]] = [(root, 0) for root in dict.fromkeys(roots)]
    while pending:
        url, depth = pending.pop(0)
        if url in seen_files:
            continue
        if len(seen_files) >= max_files:
            report.hit("max_files")
            break
        seen_files.add(url)
        body = await fetch(url, MAX_FILE_BYTES)
        content = _decompressed(body) if body is not None else None
        if content is None:
            report.files_failed += 1
            continue
        try:
            pages, children, cut = parse_sitemap(content)
        except ValueError:
            report.files_failed += 1
            continue
        report.files_read += 1
        if cut:
            report.hit("max_urls_per_file")
        for page in pages:
            if keep(page.url):
                previous = found.get(page.url)
                if page.url not in found or (page.lastmod_ms or 0) > (previous or 0):
                    found[page.url] = page.lastmod_ms
        if children:
            if depth + 1 > max_depth:
                report.hit("max_index_depth")
            else:
                pending.extend((child, depth + 1) for child in children)
    ordered = sorted(found.items(), key=lambda item: -(item[1] or 0))
    if len(ordered) > max_urls:
        report.hit("max_urls")
    return [SitemapUrl(url, lastmod) for url, lastmod in ordered[:max_urls]], report
