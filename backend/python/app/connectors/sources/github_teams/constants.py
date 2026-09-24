"""
Module-level constants for the GitHub Teams connector.

Numeric tuning knobs carry explanatory comments because they encode
operational knowledge about real-world GitHub REST/Search API limits. Do not
change them without reading those comments first.
"""

from app.config.constants.arangodb import ExtensionTypes

# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
#
# GitHub.com cloud only. Enterprise Server is not supported: GitHubClient
# never passes a base_url, and the connector's auth config has no
# instance-URL field to source one from.

# REST "core" budget (authenticated) is 5,000 req/hr. There is no proactive
# pause: like every other connector, a rate-limited call fails fast, the sync
# aborts with its checkpoint withheld, and the scheduler retries later.
# (The timestamp backfill runs on GraphQL's separate point budget.)

# Search API budget is a *separate*, much smaller pool: 30 req/min. Used by
# the repo-picker search (filters.py). Paced with a sliding one-minute window
# rather than a fixed inter-call gap: the budget is per minute, so bursts
# (the picker's scoped+public pair) are fine as long as the window total
# holds — a fixed gap added ~2.1s of dead latency to every picker keystroke.
GITHUB_SEARCH_RATE_LIMIT_PER_MINUTE = 30
GITHUB_SEARCH_CONCURRENCY = 2
GITHUB_SEARCH_WINDOW_SECONDS = 60.0
# 2-call safety margin absorbs clock skew and any Search call issued outside
# this pacer (e.g. a second service instance sharing the token).
GITHUB_SEARCH_WINDOW_BUDGET = GITHUB_SEARCH_RATE_LIMIT_PER_MINUTE - 2

# ---------------------------------------------------------------------------
# Per-operation wall-clock budget
# ---------------------------------------------------------------------------

# Per-logical-call wall-clock budget (seconds). On expiry the op's coroutine
# is cancelled, success=False is returned, and the caller's skip-and-continue
# fallback takes over. Sized for the slowest legitimate ops (recursive tree
# of a huge repo, PR-diff regeneration), not typical calls.
_GITHUB_OP_DEFAULT_TIMEOUT_SECONDS = 300.0

# Concurrency cap for the per-member GET /users/:login enrichment fan-out
# (visible-email phase).
_GITHUB_USER_ENRICHMENT_CONCURRENCY = 4

# ---------------------------------------------------------------------------
# Code indexing
# ---------------------------------------------------------------------------

# GitHub's recursive Git Trees API truncates beyond 100,000 entries or 7MB of
# response body (whichever is hit first). Above this, `GitTree.truncated` is
# True and callers must fall back to a per-subtree (non-recursive) walk.
GIT_TREE_TRUNCATION_ENTRY_HINT = 100_000

# Compare Commits API caps `files[]` at 300 entries for the entire
# comparison (files beyond that are silently missing, not paginated).
COMPARE_COMMITS_FILES_LIMIT = 300

# Compare Commits API only returns up to 250 commits without pagination;
# `total_commits` beyond this is unreliable evidence of the true delta size.
COMPARE_COMMITS_TOTAL_LIMIT = 250

# Content-indexing ceiling for code blobs (bytes). Files above it still get a
# record (visible, name-searchable) but their content is not indexed — the
# record is stamped AUTO_INDEX_OFF at full sync, or refused with 413 at stream
# time on the incremental path where blob size is unknown until first fetch.
CODE_FILE_MAX_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB

# GitHub's maximum page size. Listings carry everything we store, so a full
# page is one API call per 100 items and the page doubles as the batch —
# records start landing after the first page instead of after the last.
PR_PAGE_SIZE = 100
ISSUE_PAGE_SIZE = 100

# A PR's changed-file contents are inlined into the blocks payload and held in
# memory while the container is serialised, so cap what gets inlined. The diff
# itself is always kept — only the full-file body is dropped above this.
PR_FILE_INLINE_CONTENT_MAX_BYTES = 256 * 1024

# GitHub permits attachment uploads up to 25 MB; refuse anything larger so a
# single record cannot stall the stream indefinitely.
ATTACHMENT_MAX_SIZE_BYTES = 25 * 1024 * 1024

PREVIEW_RENDERABLE_EXTENSIONS: frozenset[str] = frozenset(
    ext.value for ext in ExtensionTypes
)

# ---------------------------------------------------------------------------
# Email resolution
# ---------------------------------------------------------------------------

# GitHub's private-email placeholder format: "{id}+{login}@users.noreply.github.com"
# (or the legacy "{login}@users.noreply.github.com"). These confirm identity
# but are not usable as a real email for permission matching.
NOREPLY_EMAIL_SUFFIX = "@users.noreply.github.com"

# Phase B batches GraphQL ``organizationVerifiedDomainEmails`` lookups with
# one aliased ``user(login:)`` field per member — ~1 point per query on
# GraphQL's separate budget regardless of member count. 100 mirrors the REST
# page size and stays far below GitHub's query-size limits.
VERIFIED_DOMAIN_EMAIL_BATCH = 100

# ---------------------------------------------------------------------------
# Filter-options picker limits
# ---------------------------------------------------------------------------

_FILTER_OPTIONS_MAX_PER_PAGE = 100
_FILTER_OPTIONS_MAX_SCAN_PAGES = 20

# GitHub's Search API refuses to page past the first 1,000 results (422),
# regardless of per_page — stop offering more before hitting it.
SEARCH_RESULTS_HARD_CAP = 1_000

# Public-search pages fetched per picker request while filling one result
# page (dedup against scoped rows can consume several). Bounds worst-case
# Search-budget spend per keystroke; an under-filled page just returns short
# with a cursor and the UI loads more.
_SEARCH_PUBLIC_PAGES_PER_REQUEST = 3

# The repo picker resolves the token's org list per request (it has no other
# sync-state to lean on). One keystroke = one request, so cache briefly.
_ORG_SCOPE_CACHE_TTL_SECONDS = 60.0

# ---------------------------------------------------------------------------
# Auth error markers — substring fallback for stringified errors on
# GitHubResponse objects that carry no HTTP status
# ---------------------------------------------------------------------------

# Fallback only — used when GitHubResponse.status_code is None. A bare "401"
# is deliberately absent: it matches repo names, diffs and commit messages.
_AUTH_ERROR_MARKERS: tuple[str, ...] = (
    "bad credentials",
    "unauthorized",
    "invalid_token",
    "invalid_grant",
    "requires authentication",
)

HTTP_UNAUTHORIZED = 401

# Transient statuses worth retrying. 403 is excluded: GitHub uses it for both
# rate limiting and genuine permission denials, and retrying the latter just
# burns budget — check_rate_limit_and_pause handles the former.
_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})
_GITHUB_MAX_RETRIES = 3
_GITHUB_RETRY_BASE_DELAY_SECONDS = 1.0
_GITHUB_RETRY_MAX_DELAY_SECONDS = 30.0

# GitHub collaborator "affiliation" values, used to enumerate collaborators
# without paying for a per-user permission lookup (NamedUser.permissions
# already carries the role for the queried repo).
AFFILIATION_ALL = "all"

# ---------------------------------------------------------------------------
# Deletion safety valve
# ---------------------------------------------------------------------------

# A recursive Git Tree can return HTTP 200 with a truncated set. Deleting a
# large fraction of a repo's code records on a single walk would wipe real
# files, so refuse and leave them for the next complete walk.
REPO_DELETE_VALVE_MAX_FRACTION = 0.5
REPO_DELETE_VALVE_MIN_ABSOLUTE = 5
