"""
Module-level constants for the GitHub Teams connector.

Numeric tuning knobs carry explanatory comments because they encode
operational knowledge about real-world GitHub REST/Search API limits. Do not
change them without reading those comments first.
"""

from app.config.constants.arangodb import ExtensionTypes

# ---------------------------------------------------------------------------
# GitHub.com cloud endpoints (Enterprise Server base_url is resolved per-connector)
# ---------------------------------------------------------------------------

GITHUB_CLOUD_API_URL = "https://api.github.com"

# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

# REST "core" budget (authenticated): 5,000 req/hr. Pause proactively once
# remaining drops below this floor so a long sweep doesn't run the budget to
# zero and start hard-failing mid-sync.
GITHUB_CORE_RATE_LIMIT_FLOOR = 100

# Search API budget is a *separate*, much smaller pool: 30 req/min. Used only
# by the Phase 4 reverse-lookup (search/commits, search/users). A semaphore
# this size plus an inter-batch delay keeps us well under the ceiling even
# with jitter/retries.
GITHUB_SEARCH_RATE_LIMIT_PER_MINUTE = 30
GITHUB_SEARCH_CONCURRENCY = 2
GITHUB_SEARCH_MIN_INTERVAL_SECONDS = 2.1  # ~28 req/min ceiling, leaves headroom

# ---------------------------------------------------------------------------
# Per-operation wall-clock budget
# ---------------------------------------------------------------------------

# Per-logical-call wall-clock budget (seconds). PyGithub is synchronous;
# calls run in a dedicated executor so a hung request can only stall this
# connector. On expiry we return success=False and let the caller's fallback
# (creator-only permissions, skip-and-continue) take over.
_GITHUB_OP_DEFAULT_TIMEOUT_SECONDS = 300.0

# ---------------------------------------------------------------------------
# Thread-pool sizing
# ---------------------------------------------------------------------------

# Dedicated executor capacity: isolates PyGithub's blocking calls from the
# shared loop executor so a misbehaving org/repo can only stall this
# connector's own work.
_GITHUB_EXECUTOR_MAX_WORKERS = 8

# Concurrency cap for the per-member GET /users/:login enrichment fan-out
# (visible-email phase) and for commit-email extraction sweeps.
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

# Skip blobs above this size when indexing code (bytes). Mirrors GitLab's
# code-file size ceiling to keep block/content payloads bounded.
CODE_FILE_MAX_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB

# Dotfiles/dot-directories (`.git`, `.github`, `.env`, etc.) are skipped by
# default — noise for search, and `.env`-style files often carry secrets.
DOTFILE_PREFIX = "."

PREVIEW_RENDERABLE_EXTENSIONS: frozenset[str] = frozenset(
    ext.value for ext in ExtensionTypes
)

# ---------------------------------------------------------------------------
# Email resolution
# ---------------------------------------------------------------------------

PSEUDO_USER_GROUP_PREFIX = "[Pseudo-User]"

# GitHub's private-email placeholder format: "{id}+{login}@users.noreply.github.com"
# (or the legacy "{login}@users.noreply.github.com"). These confirm identity
# but are not usable as a real email for permission matching.
NOREPLY_EMAIL_SUFFIX = "@users.noreply.github.com"

# Throttle: only run the expensive commit-search reverse-lookup sweep this
# often per connector instance.
USER_EMAIL_RESYNC_INTERVAL_MS = 24 * 60 * 60 * 1000  # 1 day

# ---------------------------------------------------------------------------
# Filter-options picker limits
# ---------------------------------------------------------------------------

_FILTER_OPTIONS_MAX_PER_PAGE = 100
_FILTER_OPTIONS_MAX_SCAN_PAGES = 20

# ---------------------------------------------------------------------------
# Auth error markers (PyGithub raises BadCredentialsException; substring
# match is a fallback for stringified errors coming back from GitHubResponse)
# ---------------------------------------------------------------------------

_AUTH_ERROR_MARKERS: tuple[str, ...] = (
    "401",
    "bad credentials",
    "unauthorized",
    "invalid_token",
    "invalid_grant",
    "requires authentication",
)

# GitHub collaborator "affiliation" values, used to enumerate collaborators
# without paying for a per-user permission lookup (NamedUser.permissions
# already carries the role for the queried repo).
AFFILIATION_ALL = "all"
AFFILIATION_DIRECT = "direct"
AFFILIATION_OUTSIDE = "outside"
