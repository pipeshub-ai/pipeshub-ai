"""Shared constants for Confluence connector integration tests."""

import os

# Post-mutation / post-reindex settle before graph assertions or connector resync (default 10 min).
CONFLUENCE_TEST_SETTLE_WAIT_SEC = int(os.getenv("CONFLUENCE_TEST_SETTLE_WAIT_SEC", "600"))
