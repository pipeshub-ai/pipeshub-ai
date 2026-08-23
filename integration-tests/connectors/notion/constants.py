"""Constants for the Notion connector integration tests.

Everything the suite touches in Notion is created per run under a single run-root page and
trashed at teardown, so nothing here pins a pre-existing Notion object except the two ids
supplied via env (``NOTION_TEST_ROOT_PAGE_ID`` and ``NOTION_TEST_ROOT_DATABASE_ID``).
"""

from __future__ import annotations

# Connector registration
NOTION_CONNECTOR_TYPE = "Notion"
NOTION_AUTH_TYPE = "API_TOKEN"
NOTION_SCOPE = "team"
NOTION_API_VERSION = "2025-09-03"

# Timeouts (seconds)
SYNC_TIMEOUT = 600
GRAPH_CLEANUP_TIMEOUT = 300
INDEXING_WAIT_SEC = 180

# Every object the suite creates carries this prefix so a sweep can find leftovers from a
# crashed run. The run root is ``{RUN_ROOT_PREFIX}{run_id}``.
TITLE_PREFIX = "PHIT-"
RUN_ROOT_PREFIX = f"{TITLE_PREFIX}Notion-"

# A run root younger than this may belong to a run that is still going: CI can put the
# neo4j and arango legs on separate runners at the same time, and both sweep before they
# seed. Trashing a live run's tree destroys it mid-sync instead of cleaning up after it.
STALE_RUN_MIN_AGE_SECONDS = 2 * 60 * 60

# Fixture page/database titles (suffixed with the run id so concurrent debugging is legible).
FIXTURE_ROOT_TITLE = f"{TITLE_PREFIX}Fixture"
SCRATCH_ROOT_TITLE = f"{TITLE_PREFIX}Scratch"
NESTED_PARENT_TITLE = f"{TITLE_PREFIX}NestedParent"
NESTED_CHILD_TITLE = f"{TITLE_PREFIX}NestedChild"
RICH_PAGE_TITLE = f"{TITLE_PREFIX}RichPage"
DEEP_PAGE_TITLE = f"{TITLE_PREFIX}DeepPage"
COMMENTED_PAGE_TITLE = f"{TITLE_PREFIX}CommentedPage"
TITLE_STANDARD_TITLE = f"{TITLE_PREFIX}TitleStandard"
TITLE_FALLBACK_TITLE = f"{TITLE_PREFIX}TitleFallback"
CHILD_REF_PAGE_TITLE = f"{TITLE_PREFIX}ChildRefTarget"
CHILD_REF_DB_TITLE = f"{TITLE_PREFIX}ChildRefDB"
FALLBACK_DB_TITLE = f"{TITLE_PREFIX}FallbackTitleDB"
# Title property name on FALLBACK_DB_TITLE: not one of title/Title/Name/name, so the connector
# must fall through to "any title-type property".
FALLBACK_TITLE_PROPERTY = "Heading"
ARCHIVED_PAGE_TITLE = f"{TITLE_PREFIX}ArchivedPage"
ARCHIVED_DB_TITLE = f"{TITLE_PREFIX}ArchivedDB"
FIXTURE_DB_TITLE = f"{TITLE_PREFIX}FixtureDB"
BULK_DB_TITLE = f"{TITLE_PREFIX}BulkRowsDB"

# Database row titles (the "Name" property — the database title-property fallback path).
DB_ROW_NAMED_TITLE = f"{TITLE_PREFIX}DbRowNamed"
DB_ROW_WITH_CHILDREN_TITLE = f"{TITLE_PREFIX}DbRowWithChildren"

# Bulk seeding. Search paginates at page_size=20 (hardcoded in the connector), so the TOTAL
# per object type must exceed 20 for the pagination tests to mean anything. Pages are the
# expensive type — the connector walks blocks and fetches comments per block for every one —
# so the bulk count is kept low and the total is reached via fixtures + database rows.
BULK_PAGE_COUNT = 12
BULK_DATABASE_COUNT = 22
BULK_PAGE_TITLE_FMT = f"{TITLE_PREFIX}Bulk-Page-{{index:02d}}"
BULK_DATABASE_TITLE_FMT = f"{TITLE_PREFIX}Bulk-DB-{{index:02d}}"

# Block-children pagination: the connector fetches children in pages of 50/100.
DEEP_PAGE_BLOCK_COUNT = 55

# Rows in BULK_DB_TITLE, used by the data-source row-pagination assertion.
BULK_DB_ROW_COUNT = 23

# Notion allows ~3 requests/second per integration.
NOTION_MAX_CONCURRENCY = 3
NOTION_MIN_REQUEST_INTERVAL = 0.34
NOTION_MAX_RETRIES = 5

# External URLs used by the LINK / embed-platform / image block fixtures. Only the image URL is
# ever fetched (the connector inlines it as base64); the rest are just recorded or classified.
YOUTUBE_EMBED_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
BOOKMARK_URL = "https://www.pipeshub.com/"
EMBED_URL = "https://example.com/embedded-thing"
# Same host the parser fixtures already use. An external image block exercises the same
# base64-conversion path as a Notion-hosted one, so no binary needs to live anywhere.
IMAGE_PNG_URL = "https://i.ibb.co/Wp7ZyN2s/Screenshot-2026-06-15-171126.png"

# Binaries come from the shared pipeshub-ai/integration-test repo (cloned on demand by
# ``sample-data/sample_data.py``), never from this repo. Paths are relative to
# ``sample-data/entities/files``.
# (relative_path, content_type, notion_block_type)
SAMPLE_FILE_FIXTURES: tuple[tuple[str, str, str], ...] = (
    ("sets/1/employees.csv", "text/csv", "file"),
    ("sets/1/report.pdf", "application/pdf", "pdf"),
)

# Comment attachment source. The same file is attached twice to one comment, which exercises
# the connector's normalized-filename dedup.
COMMENT_ATTACHMENT_SAMPLE_PATH = "sets/1/report.pdf"
COMMENT_ATTACHMENT_FILENAME = "report.pdf"

# Env var names
ENV_TOKEN = "NOTION_TEST_API_TOKEN"
ENV_ROOT_PAGE_ID = "NOTION_TEST_ROOT_PAGE_ID"
ENV_ROOT_DATABASE_ID = "NOTION_TEST_ROOT_DATABASE_ID"
