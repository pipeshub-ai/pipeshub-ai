"""Shared constants for Confluence connector integration tests."""

import os
import re
from urllib.parse import urlsplit

# Maximum timeout for Confluence API polling conditions (default 30 min).
# Used as the ceiling for intelligent polling - tests poll Confluence API every 30s
# until the condition is met or this timeout is reached.
CONFLUENCE_TEST_SETTLE_WAIT_SEC = int(os.getenv("CONFLUENCE_TEST_SETTLE_WAIT_SEC", "1800"))

# Titles the Confluence tests give the pages they create, renames included. Every CI run shares
# one space, so other runs' test pages (and those a cancelled run never deleted) appear,
# change and vanish mid-test; snapshots and space-wide counts must leave them out.
_TEST_PAGE_TITLE = re.compile(
    r"^(?:Renamed-)*(?:Integration Test Page (?:Alpha|Beta)|Parent Page|TC-CF-\d+ Test Page|PipesHub IT matrix) "
)


def is_test_page(title: str) -> bool:
    return bool(_TEST_PAGE_TITLE.match(title))


def connector_base_url() -> str | None:
    """Site URL the connector is configured with.

    The connector runs inside the compose network, so against the simulator it reaches the site
    by service alias while the test process uses the published port (CONFLUENCE_TEST_BASE_URL).
    Against a real site both are the same URL.
    """
    return os.getenv("CONFLUENCE_CONNECTOR_BASE_URL") or os.getenv("CONFLUENCE_TEST_BASE_URL")


def connector_site_origin() -> str:
    """Scheme and host of the connector's site URL, which every synced webUrl starts with."""
    parts = urlsplit(connector_base_url() or "")
    return f"{parts.scheme}://{parts.netloc}"
