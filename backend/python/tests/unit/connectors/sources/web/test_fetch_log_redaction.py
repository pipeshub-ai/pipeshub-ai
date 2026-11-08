"""The shared fetch chain never logs a URL's query string or user info: a feed or
page URL can carry a token there."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from app.connectors.sources.web.fetch_strategy import (
    _UrlRedactingLogger,
    fetch_url_with_fallback,
)


def test_the_wrapper_cuts_urls_to_scheme_host_and_path() -> None:
    logger = MagicMock(spec=logging.Logger)
    logger.isEnabledFor.return_value = True
    wrapped = _UrlRedactingLogger(logger)

    wrapped.warning("Gave up on %s after %s", "https://u:pw@feeds.example.com/rss?token=s3cret", "timeout")
    wrapped.error("failed for https://feeds.example.com/a?key=s3cret", exc_info=True)

    first, second = logger.log.call_args_list
    assert first.args == (logging.WARNING, "Gave up on https://feeds.example.com/rss after timeout")
    assert second.args == (logging.ERROR, "failed for https://feeds.example.com/a")
    assert second.kwargs == {}


def test_a_traceback_is_redacted_too() -> None:
    logger = MagicMock(spec=logging.Logger)
    logger.isEnabledFor.return_value = True
    wrapped = _UrlRedactingLogger(logger)
    try:
        raise RuntimeError("cannot reach https://u:pw@feeds.example.com/rss?token=s3cret")
    except RuntimeError:
        wrapped.exception("fetch failed")

    (call,) = logger.log.call_args_list
    level, text = call.args
    assert level == logging.ERROR and "exc_info" not in call.kwargs
    assert "Traceback" in text and "RuntimeError: cannot reach https://feeds.example.com/rss" in text
    assert "s3cret" not in text and "pw@" not in text


def test_nothing_is_formatted_below_the_logger_level() -> None:
    logger = MagicMock(spec=logging.Logger)
    logger.isEnabledFor.return_value = False
    _UrlRedactingLogger(logger).debug("%s", "https://x.example.com/?token=s3cret")
    logger.log.assert_not_called()


@pytest.mark.asyncio
async def test_the_strategies_log_through_the_redacting_logger(caplog: pytest.LogCaptureFixture) -> None:
    url = "https://user:pw@feeds.example.com/rss?token=s3cret"

    async def fake_hops(*args: object) -> None:
        walk, logger = args[-3], args[-1]
        logger.warning("⚠️ Timeout fetching %s", walk.url)

    with patch("app.connectors.sources.web.fetch_strategy._hops_aiohttp", fake_hops), \
         patch("app.connectors.sources.web.fetch_strategy._hops_curl_cffi", fake_hops), \
         patch("app.connectors.sources.web.fetch_strategy._hops_cloudscraper", fake_hops), \
         caplog.at_level(logging.DEBUG):
        await fetch_url_with_fallback(url, session=MagicMock(), logger=logging.getLogger("fetch-redaction-test"),
                                      max_retries_per_strategy=1)
    assert "Timeout fetching https://feeds.example.com/rss" in caplog.text
    assert "s3cret" not in caplog.text and "pw@" not in caplog.text
