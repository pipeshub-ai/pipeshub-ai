"""Drupal Wiki vocabulary and errors shared by the connector and its permission reading
(``app.ee.permission_sync.drupal_wiki``)."""

# ---------------------------------------------------------------------------
# Spaces
# ---------------------------------------------------------------------------
SPACE_ACCESS_PRIVATE = "PRIVATE"
SPACE_ACCESS_AUTHENTICATED = "AUTHENTICATED"
SPACE_ACCESS_ANONYMOUS = "ANONYMOUS"
# Everyone on the wiki can read these, so they map to an org-wide permission.
OPEN_SPACE_ACCESS_STATUSES = frozenset({SPACE_ACCESS_AUTHENTICATED, SPACE_ACCESS_ANONYMOUS})

# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------
PAGE_SIZE = 100  # the API allows up to 2000; 100 keeps responses small


class DrupalWikiSyncError(RuntimeError):
    """A sync phase failed; the run is reported as failed and no checkpoint moves."""


class DrupalWikiAuthError(RuntimeError):
    """The wiki rejected the token. Nothing else can be read, so the run fails.

    Not a ``DrupalWikiSyncError``: those are recorded and skipped one item at a time.
    """


def _as_int(value: object) -> int | None:
    """Ids arrive as numbers, strings or nulls depending on the endpoint."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
