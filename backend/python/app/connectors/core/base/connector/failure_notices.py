"""What a connector tells its owner when a sync needs them.

One notice per run, only for something a person can fix. The platform backs off a
repeat of the same title and message, so texts here leave out what changes run to run.
Sending never raises: a notice must not hide the failure it reports.
"""

from __future__ import annotations

import functools
import re
from typing import TYPE_CHECKING, Any

from app.connectors.core.base.token_service.oauth_service import (
    RefreshTokenInvalidError,
)
from app.services.notification.types import NotificationSeverity, NotificationType
from app.utils.credential_refresh_log import EVENT_CHECK_FAILED, log_refresh_failure
from app.utils.url_redaction import redact_urls_in_text

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.connectors.core.base.connector.connector_service import BaseConnector

MAX_LISTED = 10
NO_REFRESH_TOKEN = "no refresh token is stored to renew it"
_VARIABLE_TEXT = re.compile(r"\b(?=[0-9a-f-]*\d)[0-9a-f-]{8,}\b|\d{4,}", re.IGNORECASE)


class ReconnectRequiredError(Exception):
    """The source refused the connector's credentials and they cannot be renewed.

    ``str(error)`` is shown to the user. Steps that carry on past a failed item must
    re-raise it: no later call in the run can succeed."""


class SyncIncompleteError(RuntimeError):
    """The run wrote what it could but must not count as a success. What the user can
    fix goes in ``SyncRunState.problems``; with nothing there, no notice is sent."""


class SyncRunState:
    """What one sync run learned that decides which notice its owner gets."""

    def __init__(self) -> None:
        self.start()

    def start(self) -> None:
        self.rejected: ReconnectRequiredError | None = None
        # The rejected token when nothing can refresh it: counted towards switching the connector off.
        self.unrefreshable_token: str | None = None
        self.rate_limited = False
        self.problems: list[str] = []

    def reject(self, message: str, *, unrefreshable_token: str | None = None) -> ReconnectRequiredError:
        if self.rejected is None:
            self.rejected = ReconnectRequiredError(message)
            self.unrefreshable_token = unrefreshable_token
        return self.rejected

    def raise_if_rejected(self) -> None:
        if self.rejected is not None:
            raise self.rejected

    def note(self, problem: str) -> None:
        if problem not in self.problems:
            self.problems.append(problem)


def stable_reason(error: BaseException | str, limit: int = 200) -> str:
    """The error's text without URLs, ids and timestamps, so a repeat reads the same."""
    text = " ".join(redact_urls_in_text(str(error)).split())
    return _VARIABLE_TEXT.sub("…", text)[:limit]


def listed(names: list[str]) -> str:
    unique = sorted(set(names))
    shown = ", ".join(unique[:MAX_LISTED])
    return f"{shown} and others" if len(unique) > MAX_LISTED else shown


async def _send(
    connector: "BaseConnector", notice_type: NotificationType, severity: NotificationSeverity, event: str, message: str
) -> None:
    try:
        await connector.notify(
            type=notice_type,
            severity=severity,
            title=connector._notification_title(event),
            message=message,
        )
    except Exception as e:
        connector.logger.warning('Could not send the notice "%s": %s', event, e)


async def notify_reconnect(connector: "BaseConnector", message: str) -> None:
    await _send(
        connector, NotificationType.CONNECTOR_AUTH_ERROR, NotificationSeverity.ERROR,
        "needs to be reconnected", message,
    )


async def notify_incomplete(connector: "BaseConnector", message: str) -> None:
    await _send(
        connector, NotificationType.CONNECTOR_WARNING, NotificationSeverity.WARNING,
        "sync finished with problems", message,
    )


async def notify_sync_failed(connector: "BaseConnector", source: str, error: BaseException) -> None:
    await _send(
        connector, NotificationType.CONNECTOR_SYNC_ERROR, NotificationSeverity.ERROR, "sync failed",
        f"The sync stopped: {stable_reason(error)}. Recent {source} changes may not be in search yet. "
        "The next sync tries again; if it keeps failing, check the connector's settings.",
    )


async def notify_init_failed(connector: "BaseConnector", source: str, error: BaseException | None) -> None:
    """A scheduled sync could not start, where nobody sees why."""
    if isinstance(error, ReconnectRequiredError):
        await notify_reconnect(connector, str(error))
        return
    reason = f" ({stable_reason(error)})" if error else ""
    await _send(
        connector, NotificationType.CONNECTOR_SYNC_ERROR, NotificationSeverity.ERROR, "could not start",
        f"PipesHub could not start the {source} connector{reason}. Syncs fail until this is fixed: "
        "check its settings and credentials, and reconnect it if needed.",
    )


async def _switched_off_after_rejection(connector: "BaseConnector", state: SyncRunState) -> bool:
    """Count a token nothing can refresh. True when the connector was turned off and its owner told, by this
    rejection or by a refresh token rejected during the run: that notice replaces the reconnect one."""
    if state.rejected is None:
        return False
    try:
        from app.connectors.core.base.token_service.startup_service import (
            startup_service,
        )

        service = startup_service.get_token_refresh_service()
        if service is None:
            return False
        if service.told_switched_off_recently(connector.connector_id) is True:
            return True
        if not state.unrefreshable_token:
            return False
        return await service.note_access_token_rejected(
            connector.connector_id, state.unrefreshable_token, state.rejected
        )
    except Exception as e:
        connector.logger.warning("Could not record the rejected token: %s", type(e).__name__)
        return False


async def renew_access_token(connector: "BaseConnector") -> str | None:
    """After a 401: renew the stored sign-in. None when it was renewed, else why it cannot be.
    Raises when the refresh failed for a reason that may pass."""
    from app.connectors.core.base.token_service.startup_service import startup_service

    config = await connector.config_service.get_config(f"/services/connectors/{connector.connector_id}/config") or {}
    credentials = config.get("credentials") or {}
    if (config.get("auth") or {}).get("authType") != "OAUTH":
        return "a token entered by hand is not renewed automatically"
    if not credentials.get("refresh_token"):
        return NO_REFRESH_TOKEN
    service = startup_service.get_token_refresh_service()
    if service is None:
        raise RuntimeError("the token refresh service is not available")
    name = getattr(connector.connector_name, "value", None) or str(connector.connector_name)
    try:
        await service.refresh_now(connector.connector_id, name, credentials["refresh_token"])
    except RefreshTokenInvalidError as e:
        log_refresh_failure(connector.logger, EVENT_CHECK_FAILED, e)
        return "the refresh token was also rejected"
    return None


async def stored_unrefreshable_token(connector: "BaseConnector") -> str | None:
    """The stored OAuth access token when no refresh token is stored with it."""
    try:
        config = await connector.config_service.get_config(f"/services/connectors/{connector.connector_id}/config")
        credentials = (config or {}).get("credentials") or {}
        is_oauth = ((config or {}).get("auth") or {}).get("authType") == "OAUTH"
        token = credentials.get("access_token")
        return token if is_oauth and isinstance(token, str) and token and not credentials.get("refresh_token") else None
    except Exception as e:
        connector.logger.warning("Could not read the stored sign-in: %s", type(e).__name__)
        return None


async def switched_off_after_rejection(connector: "BaseConnector", error: Exception) -> bool:
    """For a connector with its own notices: count a stored token nothing can refresh. True when
    that turned the connector off, which already told its owner."""
    token = await stored_unrefreshable_token(connector)
    state = SyncRunState()
    state.reject(str(error), unrefreshable_token=token)
    return await _switched_off_after_rejection(connector, state)


def note_token_accepted(connector: "BaseConnector") -> None:
    try:
        from app.connectors.core.base.token_service.startup_service import (
            startup_service,
        )

        service = startup_service.get_token_refresh_service()
        if service is not None:
            service.note_access_token_accepted(connector.connector_id)
    except Exception as e:
        connector.logger.debug("Could not clear the rejected-token count: %s", type(e).__name__)


def reports_failed_sync(source: str) -> Callable[[Callable[[Any], Awaitable[None]]], Callable[[Any], Awaitable[None]]]:
    """Decorate ``run_sync``: at most one notice per run, and a run whose credentials died raises
    ``ReconnectRequiredError`` even when a step swallowed it. An error marked ``_notification_sent``
    was already told to the user. The connector keeps a ``SyncRunState`` in ``run_state``."""

    def decorate(run_sync: Callable[[Any], Awaitable[None]]) -> Callable[[Any], Awaitable[None]]:
        @functools.wraps(run_sync)
        async def reported(self: BaseConnector) -> None:
            state = getattr(self, "run_state", None)
            if not isinstance(state, SyncRunState):
                state = SyncRunState()
            state.start()
            try:
                await run_sync(self)
                state.raise_if_rejected()
            except Exception as e:
                error = state.rejected or e
                if isinstance(error, ReconnectRequiredError):
                    if not await _switched_off_after_rejection(self, state):
                        await notify_reconnect(self, str(error))
                elif getattr(e, "_notification_sent", False):
                    pass
                elif isinstance(e, SyncIncompleteError):
                    if state.rate_limited:
                        pass  # the next run reads what the limit cut off
                    elif state.problems:
                        await notify_incomplete(self, _problem_text(state))
                    else:
                        await notify_incomplete(
                            self,
                            f"Part of {source} could not be synced: {stable_reason(e)}. The next sync tries again; "
                            "if it keeps failing, check the connector's settings.",
                        )
                elif not state.rate_limited:
                    await notify_sync_failed(self, source, e)
                if error is e:
                    raise
                raise error from e
            note_token_accepted(self)
            if state.problems and not state.rate_limited:
                await notify_incomplete(self, _problem_text(state))

        return reported

    return decorate


def _problem_text(state: SyncRunState) -> str:
    lines = state.problems[:MAX_LISTED]
    return "Some content was not synced:\n" + "\n".join(f"- {line}" for line in lines)
