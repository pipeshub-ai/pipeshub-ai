# ruff: noqa
"""
Example script to demonstrate how to use the Google Sites datasource
for crawling a published Google Site over HTTP.
"""

import asyncio
import logging
from typing import Optional

from app.sources.client.google.google import GoogleClient
from app.sources.client.google.sites import GoogleSitesRESTClient
from app.sources.external.google.sites.sites import (
    GoogleSitesDataSource,
    GoogleSitesPage,
    normalize_published_site_url,
)


async def crawl_published_site(
    published_site_url: str,
    max_pages: Optional[int] = None,
) -> None:
    """
    Crawl a published Google Site starting from the given URL and
    print discovered pages.

    This example uses the same underlying logic as the Google Sites
    connector, but runs standalone.
    """
    logger = logging.getLogger("google-sites-example")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(logging.INFO)
        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    # Normalize and validate the URL using the shared helper
    start_url = normalize_published_site_url(published_site_url)

    google_client = GoogleClient.build_with_client(GoogleSitesRESTClient())
    datasource = GoogleSitesDataSource(client=google_client, logger=logger)
    pages: list[GoogleSitesPage] = await datasource.crawl_site(start_url)

    if max_pages is not None:
        pages = pages[:max_pages]

    logger.info("Discovered %s pages under site %s", len(pages), start_url)
    for idx, page in enumerate(pages, start=1):
        logger.info("[%s] %s (%s)", idx, page.title or "Page", page.url)


async def main() -> None:
    # Replace with your published Google Site URL, for example:
    #   https://sites.google.com/view/your-site
    published_site_url = "https://sites.google.com/view/your-site"

    await crawl_published_site(published_site_url, max_pages=10)


if __name__ == "__main__":
    asyncio.run(main())

