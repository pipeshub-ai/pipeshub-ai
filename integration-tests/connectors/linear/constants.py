# pyright: ignore-file

"""Shared constants for Linear connector integration tests."""

import os

LINEAR_TEST_SETTLE_WAIT_SEC = int(os.getenv("LINEAR_TEST_SETTLE_WAIT_SEC", "600"))
LINEAR_INDEXING_WAIT_SEC = int(os.getenv("LINEAR_INDEXING_WAIT_SEC", "180"))

# Reference issue pinned on the primary team for TC-LINEAR-003/004/IDX-001/UPDATE-001.
# Pinned (not "first issue returned by the API") so the reference issue doesn't drift
# across runs based on whichever issue was most recently updated.
LINEAR_REFERENCE_ISSUE_IDENTIFIER = "ENG-2"
