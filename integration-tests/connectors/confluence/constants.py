"""Shared constants for Confluence connector integration tests."""

import os

# Maximum timeout for Confluence API polling conditions (default 10 min).
# Used as the ceiling for intelligent polling - tests poll Confluence API every 30s
# until the condition is met or this timeout is reached.
CONFLUENCE_TEST_SETTLE_WAIT_SEC = int(os.getenv("CONFLUENCE_TEST_SETTLE_WAIT_SEC", "600"))
