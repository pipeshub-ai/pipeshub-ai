"""
Module-level constants for the GitLab connector.

All numeric tuning knobs carry long explanatory comments because they encode
hard-won operational knowledge about real-world GitLab EE behaviour.  Do not
change them without reading those comments first.
"""

import re

from app.config.constants.arangodb import ExtensionTypes

# ---------------------------------------------------------------------------
# Instance URLs
# ---------------------------------------------------------------------------

GITLAB_CLOUD_URL = "https://gitlab.com"

# ---------------------------------------------------------------------------
# Search behaviour
# ---------------------------------------------------------------------------

# GitLab's REST search= parameter only does substring (LIKE %term%) matching
# when the query is >= 3 characters.  Below this threshold we drop the server-
# side search and filter client-side instead.
GITLAB_SEARCH_MIN_PARTIAL_CHARS = 3

# Maximum number of diff entries shown per compare-commits call.  Anything
# beyond this is truncated to keep block payloads bounded.
GITLAB_COMPARE_DIFF_LIMIT = 1000

# ---------------------------------------------------------------------------
# Per-operation wall-clock budget
# ---------------------------------------------------------------------------

# Per-logical-call wall-clock budget (seconds).  GitLabClientViaToken already
# passes a per-HTTP-request timeout=30 to python-gitlab, but a single
# get_all=True materialisation fans out into many sequential HTTP calls; the
# per-request timeout does nothing to bound the *total* call duration.
# Without this ceiling, a misbehaving EE instance can keep the event loop
# blocked inside asyncio.to_thread for hours.  When the budget fires we return
# success=False and the caller's fallback (creator-only permissions,
# next-page retry, etc.) takes over.
_GITLAB_OP_DEFAULT_TIMEOUT_SECONDS = 300.0

# Per-batch wall-clock budget for _paged_list.  _GITLAB_OP_DEFAULT_TIMEOUT_SECONDS
# wraps the *first* call that constructs the GitlabList iterator; every
# subsequent next() inside the drain loop fans out into a fresh HTTP request
# (one per page boundary).  Without a budget around each drain batch a single
# stuck page fetch leaves the worker thread blocked on recv() indefinitely.
#
# Sized for the default progress_every=500 against per_page=100 callers
# (5 page requests per batch) including python-gitlab's retry_transient_errors
# exponential backoff: ~30s per request best case, more under retry.  300s
# matches the precedent set by _GITLAB_OP_DEFAULT_TIMEOUT_SECONDS and leaves
# headroom for slow-but-functional EE deployments.
#
# CAVEAT: asyncio.wait_for cancels the awaiting coroutine but CANNOT interrupt
# the worker thread.  The orphaned thread is left to unwind via python-gitlab's
# per-request timeout=30 and is not joined.
_GITLAB_PAGE_BATCH_TIMEOUT_SECONDS = 300.0

# ---------------------------------------------------------------------------
# Thread-pool sizing
# ---------------------------------------------------------------------------

# Per-connector dedicated executor capacity.  Isolating GitLab on its own pool
# means a misbehaving instance can only stall its own connector, never the rest
# of the service.  8 workers keeps cross-connector concurrency bounded while
# leaving headroom for the enrichment fan-out.
_GITLAB_EXECUTOR_MAX_WORKERS = 8

# Concurrency cap for the per-user GET /users/:id enrichment fan-out.  Half of
# _GITLAB_EXECUTOR_MAX_WORKERS so picker requests, paged sweeps, and other
# GitLab calls still get half the pool while enrichment runs.
_GITLAB_USER_ENRICHMENT_CONCURRENCY = 4

# ---------------------------------------------------------------------------
# Attachment / repo constants
# ---------------------------------------------------------------------------

PSEUDO_USER_GROUP_PREFIX = "[Pseudo-User]"

IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {"png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"}
)

# Extensions for documents/media that the UI can render as a preview (i.e. not
# raw source code).  Anything outside this set is treated as a code file.
PREVIEW_RENDERABLE_EXTENSIONS: frozenset[str] = frozenset(
    ext.value for ext in ExtensionTypes
)

# Regex matching GitLab upload links embedded in Markdown.
# Examples:
#   ![screenshot](/uploads/abc123.../image.png)
#   [attachment](/uploads/abc123.../file.pdf)
UPLOAD_PATTERN = re.compile(
    r"""
    (?P<full>
        (?:!\[.*?\]|\[.*?\])      # Image or link markdown prefix
        \(
        (?P<href>
            /uploads/
            [a-f0-9]{32}/         # 32-char GitLab upload hash
            (?P<filename>[^)\s]+) # filename (no closing paren or whitespace)
        )
        \)
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Auth error markers for GitLabResponse.error strings (substring match).
# Used as a fallback when the original exception object is not in scope.
# When the original exception IS available, prefer
# isinstance(exc, GitlabAuthenticationError) instead.
_AUTH_ERROR_MARKERS: tuple[str, ...] = (
    "401",
    "unauthorized",
    "invalid_token",
    "invalid_grant",
    "authentication",
)

# Filter-options picker limits
_FILTER_OPTIONS_MAX_PER_PAGE = 100   # GitLab caps per_page at 100
_FILTER_OPTIONS_MAX_SCAN_PAGES = 20  # Stop local scanning after this many upstream pages
