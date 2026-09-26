"""Keep a Slack connector working when its app has token rotation turned on.

With rotation, Slack access tokens expire after about 12 hours. Each comes with a
single-use refresh token, and every refresh returns a new pair. The shared
``TokenRefreshService`` renews them on a schedule; this module covers the cases
the schedule can miss: a token found about to expire when the connector is about
to use it, and a token Slack has already refused. All renewals go through
``TokenRefreshService.refresh_now``, which lets one refresh run at a time per
connector and saves the new access and refresh token together. A retried call
uses the token the renewal produced, never a cached read: a read that was in
flight during the save can leave the old document in the config cache.
"""

import asyncio
import inspect
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from app.config.configuration_service import ConfigurationService
from app.connectors.core.base.token_service.oauth_service import (
    OAuthToken,
    RefreshTokenInvalidError,
)
from app.sources.client.slack.slack import SlackClient
from app.sources.external.slack.slack import SlackDataSource

# What Slack answers when an access token has expired or was replaced.
RENEWABLE_ERRORS = frozenset({"token_expired", "invalid_auth"})

# Shorter than the refresh service's own 10-minute lead, so the schedule normally
# wins and this only runs when the schedule was missed.
RENEW_BEFORE_EXPIRY = timedelta(minutes=5)

# If Slack calls a token invalid this soon after issuing it, a new one won't fare
# better, and renewing on every refused call would hammer oauth.v2.access.
RENEWAL_COOLDOWN_SECONDS = 60

_ROTATING_TOKEN_PREFIX = "xoxe."

RECONNECT_MESSAGE = (
    "Slack no longer accepts PipesHub's saved sign-in for this connector, so its access "
    "could not be renewed. The sign-in was revoked, or the Slack app was reinstalled or "
    "removed. Reconnect Slack from the connector's settings to resume syncing."
)
# Slack doesn't let an app turn token rotation off again, hence the new-app advice.
PASTED_ROTATING_TOKEN_MESSAGE = (
    "The Slack token pasted into this connector has expired. It comes from a Slack app "
    "with token rotation turned on, and a pasted token can't be renewed automatically. "
    "Connect with Slack sign-in (OAuth) where the connector offers it, or create a Slack "
    "app with token rotation left off and paste its token instead."
)

TokenSource = Callable[[], Awaitable[tuple[dict[str, Any], str]]]


def _datasource_for(external_client: SlackClient, token: str) -> SlackDataSource:
    client = external_client.get_client()
    if getattr(client, "get_token", lambda: None)() != token and hasattr(client, "set_token"):
        client.set_token(token)
    return SlackDataSource(external_client)


class RenewingSlackDataSource:
    """A ``SlackDataSource`` that renews the token and retries once when Slack refuses it."""

    def __init__(
        self,
        datasource: SlackDataSource,
        token: str,
        renewal: "SlackTokenRenewal",
        rebuild: Callable[[str], SlackDataSource],
    ) -> None:
        self._datasource = datasource
        self._token = token
        self._renewal = renewal
        self._rebuild = rebuild

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401 - proxies any datasource member
        member = getattr(self._datasource, name)
        if not inspect.iscoroutinefunction(member):
            return member

        async def call(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            response = await member(*args, **kwargs)
            if getattr(response, "success", True) is not False:
                return response
            error = getattr(response, "error", None)
            if error not in RENEWABLE_ERRORS:
                return response
            renewed_token = await self._renewal.renew(self._token, error)
            if renewed_token is None:
                return response
            return await getattr(self._rebuild(renewed_token), name)(*args, **kwargs)

        return call


class SlackTokenRenewal:
    """Renews one Slack connector's rotating token through the shared refresh service."""

    def __init__(
        self,
        connector_id: str,
        connector_type: str,
        config_service: ConfigurationService,
        logger: logging.Logger,
    ) -> None:
        self._connector_id = connector_id
        self._connector_type = connector_type
        self._config_service = config_service
        self._logger = logger
        # A refresh token Slack has refused is not sent again, so a sync with
        # hundreds of calls makes one attempt rather than one per call.
        self._rejected_refresh_token: str | None = None
        self._explained_token: str | None = None
        self._last_renewed_at: float | None = None
        # Concurrent calls that all find the token refused must reach one decision:
        # a rejected refresh token rotates nothing, so every caller queued behind
        # the refresh service's lock would otherwise send it again and add a strike.
        self._lock = asyncio.Lock()

    async def datasource(self, external_client: SlackClient, current_token: TokenSource) -> RenewingSlackDataSource:
        """A datasource for the connector's current token, renewed first if it is about to expire."""
        config, token = await current_token()
        if self._expiring(config, token):
            token = await self.renew(token, "token_expiring") or token

        def rebuild(renewed_token: str) -> SlackDataSource:
            return _datasource_for(external_client, renewed_token)

        return RenewingSlackDataSource(_datasource_for(external_client, token), token, self, rebuild)

    @staticmethod
    def _expiring(config: dict[str, Any], token: str) -> bool:
        credentials = config.get("credentials") or {}
        if token != credentials.get("access_token") or not credentials.get("refresh_token"):
            return False
        try:
            expires_at = OAuthToken.from_dict(credentials).expires_at_epoch
        except (TypeError, ValueError):
            return False
        if expires_at is None:
            return False
        return time.time() + RENEW_BEFORE_EXPIRY.total_seconds() >= expires_at

    async def renew(self, token_in_use: str, reason: str) -> str | None:
        """A newer access token than ``token_in_use``, or None when there is none to use."""
        if not token_in_use.startswith(_ROTATING_TOKEN_PREFIX):
            return None
        async with self._lock:
            return await self._renew_locked(token_in_use, reason)

    async def _renew_locked(self, token_in_use: str, reason: str) -> str | None:
        config = await self._config_service.get_config(
            f"/services/connectors/{self._connector_id}/config"
        ) or {}
        auth = config.get("auth") or {}
        credentials = config.get("credentials") or {}
        refresh_token = credentials.get("refresh_token")

        pasted = token_in_use in (auth.get("apiToken"), auth.get("accessToken"))
        if pasted or not refresh_token:
            self._explain_unrenewable(token_in_use, reason)
            return None

        stored_token = credentials.get("access_token")
        if stored_token and stored_token != token_in_use:
            return stored_token
        if refresh_token == self._rejected_refresh_token:
            return None
        if reason == "invalid_auth" and self._renewed_recently():
            return None

        from app.connectors.core.base.token_service.startup_service import (
            startup_service,
        )
        refresh_service = startup_service.get_token_refresh_service()
        if refresh_service is None:
            self._logger.warning("Slack token refresh service is not running; cannot renew the Slack token")
            return None

        try:
            new_token = await refresh_service.refresh_now(self._connector_id, self._connector_type, refresh_token)
        except RefreshTokenInvalidError:
            self._rejected_refresh_token = refresh_token
            self._logger.error(RECONNECT_MESSAGE)
            return None
        except Exception as exc:
            self._logger.warning("Could not renew the Slack token (%s); the next call will try again", exc)
            return None
        self._last_renewed_at = time.monotonic()
        self._logger.info("Renewed the Slack token for connector %s (%s)", self._connector_id, reason)
        return new_token.access_token

    def _renewed_recently(self) -> bool:
        return (
            self._last_renewed_at is not None
            and time.monotonic() - self._last_renewed_at < RENEWAL_COOLDOWN_SECONDS
        )

    def _explain_unrenewable(self, token: str, reason: str) -> None:
        if reason != "token_expired":
            return
        if token == self._explained_token:
            return
        self._explained_token = token
        self._logger.error(PASTED_ROTATING_TOKEN_MESSAGE)
