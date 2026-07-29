"""Shared constants for Jira connector integration tests.

Environment carries only site-specific secrets/config — credentials
(``JIRA_TEST_BASE_URL`` / ``JIRA_TEST_EMAIL`` / ``JIRA_TEST_API_TOKEN``) and the
dedicated IT project keys (``JIRA_TEST_PROJECT_KEYS``, comma-separated, primary
first). Fixture issue keys live here, NOT in env: they are tied to the
pre-provisioned IT projects and change only when those tickets change.
"""

import os

JIRA_TEST_SETTLE_WAIT_SEC = int(os.getenv("JIRA_TEST_SETTLE_WAIT_SEC", "600"))
# Poll timeout for graph ``Record.indexing_status == COMPLETED`` (indexing pipeline). Max 180s unless overridden.
JIRA_INDEXING_WAIT_SEC = int(os.getenv("JIRA_INDEXING_WAIT_SEC", "180"))

# Frozen blocks expected-snapshot ticket on the primary project (rich ADF description + comments;
# add an inline image in the UI to also cover media embedding). Bootstrap the snapshot once.
JIRA_BLOCKS_ISSUE_KEY = "KAN-13"

# Ticket carrying outward ``issuelinks`` (both ends on the primary project) for
# TC-JIRA-LINKS-001 (seeded with ``blocks`` + ``relates to`` links).
JIRA_LINK_SOURCE_ISSUE_KEY = "KAN-12"

# Reference issue on the primary project for TC-JIRA-004 / IDX-001 / ENTITY-001 / UPDATE-001.
JIRA_REFERENCE_ISSUE_KEY = "KAN-4"

# Default site users group (``jira-users-<site>``). TC-JIRA-002 validates that its members
# have User→Group edges. Empty string skips that check.
JIRA_USERS_GROUP_NAME = "jira-users-pipeshub-it"

# Fixed cut between the original fixture batch and later "IT Date Filter New" tickets.
# Used for created after/before partitions in TC-FILTER-DATE-001 (``created`` is immutable).
JIRA_FILTER_DATE_CUT_MS = 1784146637293
