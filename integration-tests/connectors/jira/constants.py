"""Shared constants for Jira connector integration tests."""

import os

JIRA_TEST_SETTLE_WAIT_SEC = int(os.getenv("JIRA_TEST_SETTLE_WAIT_SEC", "600"))
# Poll timeout for graph ``Record.indexing_status == COMPLETED`` (indexing pipeline). Max 180s unless overridden.
JIRA_INDEXING_WAIT_SEC = int(os.getenv("JIRA_INDEXING_WAIT_SEC", "180"))
