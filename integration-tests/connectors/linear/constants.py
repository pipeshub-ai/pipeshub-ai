# pyright: ignore-file

"""Shared constants for Linear connector integration tests."""

import os

LINEAR_TEST_SETTLE_WAIT_SEC = int(os.getenv("LINEAR_TEST_SETTLE_WAIT_SEC", "600"))
LINEAR_INDEXING_WAIT_SEC = int(os.getenv("LINEAR_INDEXING_WAIT_SEC", "180"))

# Reference issue pinned on the primary team for TC-LINEAR-003/004/IDX-001/UPDATE-001.
# Pinned (not "first issue returned by the API") so the reference issue doesn't drift
# across runs based on whichever issue was most recently updated.
LINEAR_REFERENCE_ISSUE_IDENTIFIER = "ENG-2"

# TC-LINEAR-PH-001 chain. Invariant: max(stub ancestor updatedAt) <= cut < child updatedAt,
# with >= 2 ancestors below the cut. ``updatedAt`` is mutable, so editing any issue in the
# chain invalidates the cut — the test's pre-flight names the offending issue and the test
# docstring carries the recompute recipe. Must not be LINEAR_REFERENCE_ISSUE_IDENTIFIER or
# one of its ancestors: TC-UPDATE-001 edits that issue on every run.
LINEAR_PH_CHILD_IDENTIFIER = os.getenv("LINEAR_PH_CHILD_IDENTIFIER", "ENG-328")
LINEAR_PH_MODIFIED_CUT_MS = int(os.getenv("LINEAR_PH_MODIFIED_CUT_MS", "1785787800000"))
