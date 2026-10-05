"""Plain-language messages for API failures people see in PipesHub.

The web app shows an API error's ``message``/``reason``/``detail`` word for word in
a toast (``frontend/lib/api/api-error.ts``), so these strings must say what failed
in the user's terms and what to do next. Exception text never goes there: it names
internals and is often a repr. Callers log the exception and return one of these.

For a file that failed to index, use :mod:`app.utils.user_errors` instead — those
reasons carry indexing-specific next steps ("Try Reindex …").
"""

from __future__ import annotations

RETRY_HINT = "Please try again; if it keeps failing, contact your admin."

SOMETHING_WENT_WRONG = f"Something went wrong. {RETRY_HINT}"


PEOPLE_GONE = (
    "Some people you picked are no longer in this workspace. "
    "Remove them and try sharing again."
)


EPUB_PREVIEW_UNAVAILABLE = (
    "Preview isn't available for EPUB files. Download the book to read it in an "
    "e-book app; its text is still searchable in PipesHub."
)


def action_failed(action: str) -> str:
    """Why an action failed, in the user's words.

    ``action`` completes "We couldn't …", e.g. ``"delete this folder"``.
    """
    return f"We couldn't {action}. {RETRY_HINT}"


def not_found(thing: str) -> str:
    """A thing the caller asked for is gone or was never theirs.

    Says nothing about whether it exists, so it is safe for records another
    workspace owns. ``thing`` starts the sentence, e.g. ``"This folder"``.
    """
    return f"{thing} was removed, or you no longer have access. Refresh the page and try again."


_CLIENT_ERROR_MIN = 400
_SERVER_ERROR_MIN = 500


def provider_failure(result: object, action: str) -> tuple[int, str]:
    """The status and the words for a graph failure a route is about to raise.

    The providers return their failures rather than raising, so a route's
    ``except`` never sees them. A refusal a provider wrote for a reader carries
    a 4xx ``code`` (Neo4j spells it as a string); ``str(e)`` carries 500, or no
    code at all. Only the first is passed on.
    """
    if isinstance(result, dict):
        try:
            code = int(result["code"])
        except (KeyError, TypeError, ValueError):
            code = _SERVER_ERROR_MIN
        reason = result.get("reason")
        if _CLIENT_ERROR_MIN <= code < _SERVER_ERROR_MIN and reason:
            return code, str(reason)
    return _SERVER_ERROR_MIN, action_failed(action)
