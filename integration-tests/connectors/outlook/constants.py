"""Constants for Outlook connector integration tests.

Seed resource ids/topics come from ``OUTLOOK_TEST_*`` env vars (resolved in ``conftest.py``);
this module holds only static/pinned values and tunables.
"""

from __future__ import annotations

# Product connector identity (backend: OutlookConnectorNames.TEAM / AuthType.OAUTH_ADMIN_CONSENT).
OUTLOOK_CONNECTOR_TYPE = "Outlook"
OUTLOOK_AUTH_TYPE = "OAUTH_ADMIN_CONSENT"
OUTLOOK_SCOPE = "team"

# Timeouts / polling (seconds).
SYNC_TIMEOUT = 1200
# The indexer re-processes a reindexed record (parse + embed) before it stamps it again.
REINDEX_TIMEOUT = 300
GRAPH_CLEANUP_TIMEOUT = 300

# Test-data prefixes so seed mailboxes stay identifiable and cleanable.
TEST_RESOURCE_PREFIX = "PHIT"  # PipesHub Integration Test

# A small default HTML mail body.
DEFAULT_MAIL_BODY_HTML = "<html><body><p>PipesHub Outlook integration test message.</p></body></html>"

# A tiny text attachment payload (bytes) for attachment tests.
DEFAULT_ATTACHMENT_BYTES = b"PipesHub Outlook IT attachment payload.\n"
DEFAULT_ATTACHMENT_NAME = "phit_attachment.txt"
DEFAULT_ATTACHMENT_CONTENT_TYPE = "text/plain"
EXCHANGE_ATTACHMENT_NAME = "phit_exchange.txt"
GROUP_ATTACHMENT_NAME = "phit_group.txt"

# Markers the stream tests look for in the returned HTML.
MAIL_BODY_MARKER = "PHIT-MAIL-BODY-MARKER"
GROUP_BODY_MARKER = "PHIT-GROUP-BODY-MARKER"

# A recipient outside the tenant: must never become a grantee.
EXTERNAL_ADDRESS = "phit-external@example.com"

# Shapes that cross Graph page boundaries: childFolders pages at 10 by default, so 12 children
# need a second page; 12 attachments exercise the attachment listing beyond a handful.
WIDE_CHILD_FOLDERS = 12
MANY_ATTACHMENTS = 12
# The connector reads message deltas 100 per page (OutlookSyncConfig.MESSAGE_PAGE_SIZE), so 105
# messages in one folder need a second page. Override with OUTLOOK_TEST_BULK_MESSAGES.
BULK_MESSAGES = 105
# Attachment names that trip naming code: unicode, no extension, and two attachments sharing a name.
ODD_ATTACHMENT_NAMES = {
    "name_unicode": "résumé – 日本 (1).txt",
    "name_noext": "README",
    "name_dup_a": "same-name.txt",
    "name_dup_b": "same-name.txt",
}
# Just over the connector's 25 MiB attachment indexing cap (OutlookSyncConfig.MAX_INDEXED_ATTACHMENT_BYTES).
# Seeded only with OUTLOOK_TEST_LARGE_ATTACHMENT=1: the upload and the stream move ~25 MB each way.
LARGE_ATTACHMENT_SIZE = 25 * 1024 * 1024 + 64 * 1024
LARGE_ATTACHMENT_NAME = "phit_large.bin"


def large_attachment_bytes() -> bytes:
    """Deterministic payload of LARGE_ATTACHMENT_SIZE bytes, rebuilt by the test to compare."""
    return (bytes(range(256)) * (LARGE_ATTACHMENT_SIZE // 256 + 1))[:LARGE_ATTACHMENT_SIZE]


# Exchange allows 4 concurrent requests per mailbox per app.
MAILBOX_CONCURRENCY = 4

# Baseline received-date cutoff = run start minus this slack (clock skew between the test host
# and Exchange). Mail the run creates is newer; the mailboxes' real history is older.
RECEIVED_CUTOFF_SLACK_MINUTES = 10

# 1x1 transparent PNG for the inline-image case.
PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)
