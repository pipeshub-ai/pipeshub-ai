"""CrawlCheckpoint: what an unfinished crawl saves (scenario DI-09)."""

import base64
import zlib

import pytest

from app.connectors.sources.web.checkpoint import (
    MAX_AGE_MS,
    MAX_URLS,
    CrawlCheckpoint,
    settings_fingerprint,
)

FP = settings_fingerprint({"url": "https://a.test/", "depth": 3})


def _checkpoint(**kw: object) -> CrawlCheckpoint:
    return CrawlCheckpoint(
        fingerprint=FP, saved_at_ms=1_000, visited=["https://a.test/"], kept=["https://a.test/"],
        queue=[("https://a.test/b", 1)], **kw,
    )


def test_a_checkpoint_comes_back_as_saved() -> None:
    saved = _checkpoint()
    assert CrawlCheckpoint.decode(saved.encode(), fingerprint=FP, now_ms=2_000) == saved


def test_other_settings_mean_no_checkpoint() -> None:
    other = settings_fingerprint({"url": "https://a.test/", "depth": 4})
    assert CrawlCheckpoint.decode(_checkpoint().encode(), fingerprint=other, now_ms=2_000) is None


def test_a_checkpoint_older_than_a_week_is_dropped() -> None:
    assert CrawlCheckpoint.decode(_checkpoint().encode(), fingerprint=FP, now_ms=1_000 + MAX_AGE_MS + 1) is None


@pytest.mark.parametrize(
    "text",
    [None, "", 42, "not base64!", base64.b64encode(b"not zlib").decode(),
     base64.b64encode(zlib.compress(b'{"fingerprint": 1}')).decode()],
)
def test_anything_else_is_no_checkpoint(text: object) -> None:
    assert CrawlCheckpoint.decode(text, fingerprint=FP, now_ms=2_000) is None


def test_the_fingerprint_ignores_key_order() -> None:
    assert settings_fingerprint({"a": 1, "b": [2]}) == settings_fingerprint({"b": [2], "a": 1})


def test_a_large_crawl_compresses_well() -> None:
    urls = [f"https://docs.example.test/section/{i // 100}/page-{i}.html" for i in range(20_000)]
    encoded = CrawlCheckpoint(fingerprint=FP, saved_at_ms=1, visited=urls, kept=urls, queue=[(u, 2) for u in urls]).encode()
    assert len(encoded) < 1_000_000


@pytest.mark.parametrize("field", ["visited", "kept", "queue"])
def test_a_crawl_over_the_cap_saves_nothing_to_resume(field: str) -> None:
    # A cut-down checkpoint would let the resumed crawl finish as complete without the dropped pages.
    over = [f"https://a.test/{i}" for i in range(MAX_URLS + 1)]
    checkpoint = _checkpoint()
    setattr(checkpoint, field, [(u, 1) for u in over] if field == "queue" else over)
    assert checkpoint.encode() == ""
    assert CrawlCheckpoint.decode(checkpoint.encode(), fingerprint=FP, now_ms=2_000) is None
