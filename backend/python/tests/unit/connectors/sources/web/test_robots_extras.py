"""robots.txt Crawl-delay and Sitemap lines, and the shared cache (scenarios DI-10, DI-11, DI-13)."""

import pytest

from app.connectors.sources.web.robots import RobotsCache, RobotsRules

TEXT = """\
Sitemap: https://a.test/sitemap_index.xml
User-agent: *
Crawl-delay: 5
Disallow: /private/

User-agent: PipesHub
Crawl-delay: 2
Disallow: /drafts/
sitemap: https://a.test/news.xml
"""


class TestParse:
    def test_our_groups_crawl_delay_wins(self) -> None:
        assert RobotsRules.parse(TEXT, "PipesHub").crawl_delay == 2

    def test_another_crawler_gets_the_star_groups_delay(self) -> None:
        assert RobotsRules.parse(TEXT, "OtherBot").crawl_delay == 5

    def test_sitemaps_belong_to_no_group(self) -> None:
        assert RobotsRules.parse(TEXT, "PipesHub").sitemaps == [
            "https://a.test/sitemap_index.xml", "https://a.test/news.xml",
        ]

    @pytest.mark.parametrize("value", ["soon", "-3", ""])
    def test_a_broken_delay_is_ignored(self, value: str) -> None:
        assert RobotsRules.parse(f"User-agent: *\nCrawl-delay: {value}\n", "PipesHub").crawl_delay is None

    def test_no_delay_by_default(self) -> None:
        assert RobotsRules.parse("User-agent: *\nDisallow: /x\n", "PipesHub").crawl_delay is None

    def test_a_delay_does_not_close_the_group(self) -> None:
        rules = RobotsRules.parse("User-agent: *\nCrawl-delay: 1\nDisallow: /x\n", "PipesHub")
        assert not rules.allows("https://a.test/x")


class Reader:
    def __init__(self, *answers: RobotsRules | None) -> None:
        self.answers = list(answers)
        self.reads = 0

    async def __call__(self) -> RobotsRules | None:
        self.reads += 1
        return self.answers.pop(0)


class TestCache:
    def _cache(self) -> tuple[RobotsCache, list[float]]:
        now = [0.0]
        return RobotsCache(now=lambda: now[0], max_sites=2), now

    async def test_a_fresh_copy_is_not_read_again(self) -> None:
        cache, now = self._cache()
        rules = RobotsRules([(False, "/x")])
        read = Reader(rules)
        assert await cache.rules("https://a.test", read) is rules
        now[0] = RobotsCache.FRESH_SECONDS - 1
        assert await cache.rules("https://a.test", read) is rules
        assert read.reads == 1

    async def test_an_old_copy_is_replaced_by_a_new_read(self) -> None:
        cache, now = self._cache()
        old, new = RobotsRules([(False, "/x")]), RobotsRules()
        read = Reader(old, new)
        await cache.rules("https://a.test", read)
        now[0] = RobotsCache.FRESH_SECONDS
        assert await cache.rules("https://a.test", read) is new

    async def test_a_failed_read_falls_back_to_a_copy_under_30_days_old(self) -> None:
        cache, now = self._cache()
        rules = RobotsRules([(False, "/x")])
        read = Reader(rules, None)
        await cache.rules("https://a.test", read)
        now[0] = RobotsCache.STALE_SECONDS - 1
        assert await cache.rules("https://a.test", read) is rules

    async def test_a_failed_read_with_nothing_recent_enough_gives_nothing(self) -> None:
        cache, now = self._cache()
        read = Reader(RobotsRules(), None)
        await cache.rules("https://a.test", read)
        now[0] = RobotsCache.STALE_SECONDS
        assert await cache.rules("https://a.test", read) is None

    async def test_a_failed_read_is_not_remembered(self) -> None:
        cache, _ = self._cache()
        rules = RobotsRules()
        read = Reader(None, rules)
        assert await cache.rules("https://a.test", read) is None
        assert await cache.rules("https://a.test", read) is rules

    async def test_the_least_recently_used_site_is_dropped_past_the_limit(self) -> None:
        cache, _ = self._cache()
        for site in ("https://a.test", "https://b.test", "https://c.test"):
            await cache.rules(site, Reader(RobotsRules()))
        read = Reader(RobotsRules())
        await cache.rules("https://a.test", read)
        assert read.reads == 1
