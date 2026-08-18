# pyright: ignore-file

"""Shared constants for Linear connector integration tests."""

import os

LINEAR_TEST_SETTLE_WAIT_SEC = int(os.getenv("LINEAR_TEST_SETTLE_WAIT_SEC", "600"))
LINEAR_INDEXING_WAIT_SEC = int(os.getenv("LINEAR_INDEXING_WAIT_SEC", "180"))

# Title prefix carried by every issue the mutation tests create in the filtered teams.
# The CI matrix runs the arango and neo4j legs against the *same* Linear workspace, so a run
# sees the other leg's in-flight issues; both the Linear API baselines and the graph counts
# skip anything carrying this marker so each run only measures data it owns.
LINEAR_IT_ARTIFACT_PREFIX = "LinearIT-"

# Reference issue pinned on the primary team for TC-LINEAR-003/004/IDX-001. Read-only:
# the mutation tests create and delete their own issues, so nothing edits this one.
# Pinned (not "first issue returned by the API") so the reference issue doesn't drift
# across runs based on whichever issue was most recently updated.
LINEAR_REFERENCE_ISSUE_IDENTIFIER = "ENG-2"

# TC-LINEAR-PH-001 chain: an issue with >= 2 ancestors, itself updated more recently than
# its two nearest ones. The test derives its own ``modified`` cut from those timestamps, so
# there is no epoch constant here to decay when someone edits the chain.
LINEAR_PH_CHILD_IDENTIFIER = os.getenv("LINEAR_PH_CHILD_IDENTIFIER", "ENG-328")
