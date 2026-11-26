"""What a failed Dropbox call means, read from the error the data source returns.

`DropboxDataSource` turns every SDK exception into ``DropboxResponse(success=False, error=str(exc))``, so the kind of
failure is only in that text. One job: tell apart the failures the connector must handle differently.

  - auth: the token or the refresh token was refused (AuthError: invalid_access_token, expired_access_token after a
    failed refresh, missing_scope, user_suspended ...). The SDK reports a revoked refresh token as
    invalid_access_token (dropbox_client.py, the 400 invalid_grant branch).
  - reset: a listing or event cursor that Dropbox invalidated (ListFolderContinueError.reset,
    GetTeamEventsContinueError.reset / bad_cursor, and the invalid_cursor of the sharing and team lists).
  - not found: LookupError.not_found and SharedFolderAccessError.invalid_id: the item is gone, or this member
    cannot see it.
"""

from __future__ import annotations

_AUTH_MARKERS = ("AuthError", "invalid_access_token", "expired_access_token", "invalid_grant", "missing_scope")
_RESET_MARKERS = ("'reset'", "ListFolderContinueError('reset'", "bad_cursor", "invalid_cursor", "reset/")


def is_auth_error(error: object) -> bool:
    text = str(error or "")
    return any(marker in text for marker in _AUTH_MARKERS)


def is_member_suspended(error: object) -> bool:
    return "user_suspended" in str(error or "")


def is_cursor_reset(error: object) -> bool:
    text = str(error or "")
    return any(marker in text for marker in _RESET_MARKERS)


def missing_scope(error: object) -> str | None:
    """The scope Dropbox says the token lacks, when that is the error."""
    text = str(error or "")
    marker = "required_scope='"
    if "missing_scope" not in text or marker not in text:
        return None
    return text.split(marker, 1)[1].split("'", 1)[0]
