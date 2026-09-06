"""Shared constants for Jira connector integration tests.

Environment carries only site-specific secrets/config — credentials
(``JIRA_TEST_BASE_URL`` / ``JIRA_TEST_EMAIL`` / ``JIRA_TEST_API_TOKEN``) and the
dedicated IT project keys (``JIRA_TEST_PROJECT_KEYS``, comma-separated, primary
first). Fixture issue keys live here, NOT in env: they are tied to the
pre-provisioned IT projects and change only when those tickets change.
"""

import os
import uuid

JIRA_TEST_SETTLE_WAIT_SEC = int(os.getenv("JIRA_TEST_SETTLE_WAIT_SEC", "600"))
# Poll timeout for graph ``Record.indexing_status == COMPLETED`` (indexing pipeline). Max 180s unless overridden.
JIRA_INDEXING_WAIT_SEC = int(os.getenv("JIRA_INDEXING_WAIT_SEC", "180"))

# Summary prefix carried by every ticket the mutation tests create. Every CI leg, every PR
# and the nightly cron share the *same* Jira site, so a run sees other runs' in-flight
# tickets; both the live-Jira baselines and the graph counts skip anything carrying this
# marker so each run only measures data it owns. Keep it a plain substring: the graph-side
# filter (``owned_record_external_ids``) matches on ``prefix in record_name``.
JIRA_IT_ARTIFACT_PREFIX = "PHIT-"

# Identifies this pytest process. Baked into every artifact summary (see
# ``artifact_summary``) so a leaked ticket can be attributed to the run that created it
# and the fixture teardown can delete exactly its own leftovers. Deliberately not
# configurable: two legs of one CI job must never share an id.
JIRA_IT_RUN_ID = uuid.uuid4().hex[:8]

# Artifacts older than this are treated as leaked by a crashed/cancelled run and swept at
# fixture setup. A whole leg takes ~15 minutes and a mutation ticket lives for a few, so
# nothing this old can still belong to a live run.
JIRA_IT_STALE_ARTIFACT_AGE_SEC = 2 * 60 * 60


def artifact_summary(kind: str) -> str:
    """Summary for a ticket created by this run: ``PHIT-<run_id>-<kind>-<8hex>``.

    Prefix first so the existing substring exclusion keeps working; run id second so
    ``sweep_stale_jira_artifacts(only_run_id=...)`` can target one run with a JQL
    ``summary ~`` on the ``PHIT-<run_id>-`` stem.
    """
    return f"{JIRA_IT_ARTIFACT_PREFIX}{JIRA_IT_RUN_ID}-{kind}-{uuid.uuid4().hex[:8]}"

# Frozen blocks expected-snapshot ticket on the primary project (rich ADF description + comments;
# add an inline image in the UI to also cover media embedding). Bootstrap the snapshot once.
JIRA_BLOCKS_ISSUE_KEY = "KAN-13"

# Ticket carrying outward ``issuelinks`` (both ends on the primary project) for
# TC-JIRA-LINKS-001 (seeded with ``blocks`` + ``relates to`` links).
JIRA_LINK_SOURCE_ISSUE_KEY = "KAN-12"

# Reference issue on the primary project for TC-JIRA-004 / IDX-001 / ENTITY-001.
# Read-only: the mutation tests create and delete their own tickets, so nothing edits this one.
# KAN-416 replaced KAN-4 on 2026-08-28 (KAN-4 had been renamed ``PHIT-Edited-...`` by an
# older in-place-editing version of TC-UPDATE-001 and was reaped by an artifact sweep).
JIRA_REFERENCE_ISSUE_KEY = "KAN-416"

# Default site users group (``jira-users-<site>``). TC-JIRA-002 validates that its members
# have User→Group edges. Empty string skips that check.
JIRA_USERS_GROUP_NAME = "jira-users-pipeshub-it"

# Fixed cut between the original fixture batch and later "IT Date Filter New" tickets.
# Used for created after/before partitions in TC-FILTER-DATE-001 (``created`` is immutable).
JIRA_FILTER_DATE_CUT_MS = 1784146637293

# Sub-task on the primary project whose ancestor chain sits outside the created-window
# in TC-JIRA-PH-001. Needs >= 2 ancestors, all created before the child.
# Chain: KAN-229 (Sub-task) → KAN-6 (Story) → KAN-5 (Epic). Cut must sit in a different
# JQL minute than the ancestors (``_jql_datetime`` truncates to ``YYYY-MM-DD HH:MM``).
JIRA_PH_CHILD_KEY = "KAN-229"

# Fixed cut between the chain's ancestors and the child. ``created`` is immutable, so this
# does not decay; recompute only if the chain is reprovisioned.
JIRA_PH_CREATED_CUT_MS = 1785847131576

# Ancestors of JIRA_PH_CHILD_KEY and the attachment ticket (discovered, not pinned) — listed
# here only so the artifact sweep can refuse to touch them.
JIRA_PH_ANCESTOR_KEYS = ("KAN-6", "KAN-5")
JIRA_ATTACHMENT_ISSUE_KEY = "KAN-9"

# Every pre-provisioned ticket the suite depends on. The artifact sweep never deletes these,
# whatever their summary says: an earlier version of TC-UPDATE-001 edited the reference
# issue in place and left it named ``PHIT-Edited-...``, and a marker-only rule reaped it.
JIRA_FROZEN_ISSUE_KEYS = frozenset(
    k for k in (
        JIRA_REFERENCE_ISSUE_KEY, JIRA_LINK_SOURCE_ISSUE_KEY, JIRA_BLOCKS_ISSUE_KEY,
        JIRA_PH_CHILD_KEY, JIRA_ATTACHMENT_ISSUE_KEY, *JIRA_PH_ANCESTOR_KEYS,
    ) if k
)
