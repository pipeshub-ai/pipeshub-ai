"""Shared constants for Jira connector integration tests."""

import os

JIRA_TEST_SETTLE_WAIT_SEC = int(os.getenv("JIRA_TEST_SETTLE_WAIT_SEC", "600"))
