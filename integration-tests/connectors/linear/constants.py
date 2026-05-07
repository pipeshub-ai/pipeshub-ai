"""Shared constants for Linear connector integration tests."""

import os

LINEAR_TEST_SETTLE_WAIT_SEC = int(os.getenv("LINEAR_TEST_SETTLE_WAIT_SEC", "300"))
