# pyright: ignore-file

"""Shared constants for Linear connector integration tests."""

import os
import uuid

LINEAR_TEST_SETTLE_WAIT_SEC = int(os.getenv("LINEAR_TEST_SETTLE_WAIT_SEC", "600"))
LINEAR_INDEXING_WAIT_SEC = int(os.getenv("LINEAR_INDEXING_WAIT_SEC", "180"))

# Title prefix carried by every issue the mutation tests create in the filtered teams.
# The CI matrix runs the arango and neo4j legs — and every open PR — against the *same*
# Linear workspace, so a run sees the other runs' in-flight issues; both the Linear API
# baselines and the graph counts skip anything carrying this marker so each run only
# measures data it owns.
LINEAR_IT_ARTIFACT_PREFIX = "LinearIT-"

# Identity of this pytest process on the shared workspace. Every issue it creates carries
# ``LinearIT-<run_id>-<Kind>-<hex>`` (see ``artifact_title``): teardown can reap exactly its
# own leftovers, and the sweep can attribute a leak to a crashed run.
LINEAR_IT_RUN_ID = uuid.uuid4().hex[:8]

# Artifacts older than this are treated as leaked by a run that no longer exists (a cancelled
# CI job SIGTERMs pytest before any ``finally``). A whole leg takes well under an hour, so
# a two-hour gate never touches a run that is still asserting on its issue.
LINEAR_IT_STALE_ARTIFACT_AGE_SEC = 2 * 60 * 60


def artifact_title(kind: str) -> str:
    """Title for an issue this run creates: ``LinearIT-<run_id>-<kind>-<hex>``.

    ``kind`` must be letters only (``IncrTest``, ``UpdTest``, ``Edited``): the sweep proves
    ownership with a strict regex over this exact shape, and anything else is refused.
    """
    return f"{LINEAR_IT_ARTIFACT_PREFIX}{LINEAR_IT_RUN_ID}-{kind}-{uuid.uuid4().hex[:8]}"


# Reference issue pinned on the primary team for TC-LINEAR-003/004/IDX-001. Read-only:
# the mutation tests create and delete their own issues, so nothing edits this one.
# Pinned (not "first issue returned by the API") so the reference issue doesn't drift
# across runs based on whichever issue was most recently updated.
LINEAR_REFERENCE_ISSUE_IDENTIFIER = "ENG-2"

# TC-LINEAR-PH-001 chain: an issue with >= 2 ancestors, itself updated more recently than
# its two nearest ones. The test derives its own ``modified`` cut from those timestamps, so
# there is no epoch constant here to decay when someone edits the chain.
LINEAR_PH_CHILD_IDENTIFIER = os.getenv("LINEAR_PH_CHILD_IDENTIFIER", "ENG-328")

# Issues no test may ever delete, whatever their title says. The sweep refuses these
# outright; its regex already rejects anything not in this run-id form, and an artifact
# never has a parent or children, so the chain above ``LINEAR_PH_CHILD_IDENTIFIER`` is
# protected by the hierarchy guard as well.
LINEAR_FROZEN_ISSUE_IDENTIFIERS = frozenset({
    LINEAR_REFERENCE_ISSUE_IDENTIFIER,
    LINEAR_PH_CHILD_IDENTIFIER,
})
