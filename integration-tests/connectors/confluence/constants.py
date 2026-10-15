"""Shared constants for Confluence connector integration tests."""

import os
import uuid

# Maximum timeout for Confluence API polling conditions (default 30 min).
# Used as the ceiling for intelligent polling - tests poll Confluence API every 30s
# until the condition is met or this timeout is reached.
CONFLUENCE_TEST_SETTLE_WAIT_SEC = int(os.getenv("CONFLUENCE_TEST_SETTLE_WAIT_SEC", "1800"))

# Concurrent runs (the neo4j and arango jobs, other pull requests) share the IT space. Each
# run titles the pages it creates RUN_PREFIX + ..., and every space-wide snapshot or count
# leaves out other runs' pages, which come and go mid-test.
ARTIFACT_PREFIX = "CFIT-"
RUN_PREFIX = f"{ARTIFACT_PREFIX}{uuid.uuid4().hex[:8]}-"


def is_other_run_artifact(title: str) -> bool:
    return title.startswith(ARTIFACT_PREFIX) and not title.startswith(RUN_PREFIX)
