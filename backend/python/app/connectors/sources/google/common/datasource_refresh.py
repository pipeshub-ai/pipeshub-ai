"""
Keeps a Google OAuth datasource's in-memory credentials in step with the token
saved in the connector's settings (etcd).

Whichever access token expires later wins. A newer saved token (a re-authentication,
or a refresh by another process) replaces the one in memory, and a token that
google-auth refreshed in memory is saved back, so the next check does not swap it
for the stale saved one.
"""

import weakref
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from logging import Logger

from google.oauth2.credentials import Credentials

from app.config.configuration_service import ConfigurationService
from app.connectors.core.base.token_service.oauth_service import OAuthToken
from app.connectors.core.base.token_service.token_refresh_service import (
    connector_refresh_lock,
)
from app.connectors.sources.google.common.connector_google_exceptions import (
    GoogleAuthError,
)
from app.sources.client.google.google import GoogleClient
from app.sources.external.google.drive.drive import GoogleDriveDataSource
from app.sources.external.google.gmail.gmail import GoogleGmailDataSource

GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"

# The refresh token settings and each in-memory credentials object last agreed on.
# It tells a refresh token Google rotated in memory (save it) apart from one a
# re-authentication put in settings (adopt it).
_agreed_refresh_tokens: "weakref.WeakKeyDictionary[object, str]" = weakref.WeakKeyDictionary()


class _Action(Enum):
    KEEP = "keep"
    ADOPT_SAVED = "adopt_saved"
    SAVE_IN_MEMORY = "save_in_memory"


@dataclass(frozen=True)
class _TokenState:
    access_token: str | None
    refresh_token: str | None
    # Naive UTC, as google-auth keeps it; None when unknown.
    expiry: datetime | None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _naive_utc(value: object) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _saved_expiry(credentials: dict) -> datetime | None:
    legacy_expiry_ms = credentials.get("access_token_expiry_time")
    try:
        if isinstance(legacy_expiry_ms, (int, float)):
            expires_at: float | None = legacy_expiry_ms / 1000
        # OAuthToken defaults a missing created_at to now, which would make any saved token look fresh.
        elif all(credentials.get(key) for key in ("access_token", "created_at", "expires_in")):
            expires_at = OAuthToken.from_dict(credentials).expires_at_epoch
        else:
            return None
        if expires_at is None:
            return None
        return datetime.fromtimestamp(expires_at, timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _saved_state(credentials: dict) -> _TokenState:
    return _TokenState(
        access_token=credentials.get("access_token") or None,
        refresh_token=credentials.get("refresh_token") or None,
        expiry=_saved_expiry(credentials),
    )


def _memory_state(credentials: object) -> _TokenState:
    return _TokenState(
        access_token=getattr(credentials, "token", None) or None,
        refresh_token=getattr(credentials, "refresh_token", None) or None,
        expiry=_naive_utc(getattr(credentials, "expiry", None)),
    )


def _choose(memory: _TokenState, saved: _TokenState, agreed_refresh_token: str | None) -> _Action:
    if memory.refresh_token != saved.refresh_token:
        if agreed_refresh_token is not None and saved.refresh_token == agreed_refresh_token:
            return _Action.SAVE_IN_MEMORY
        return _Action.ADOPT_SAVED
    if memory.access_token == saved.access_token:
        # Adopting the saved expiry lets google-auth refresh before the token is rejected.
        if memory.expiry is None and saved.expiry is not None:
            return _Action.ADOPT_SAVED
        return _Action.KEEP
    # google-auth always records an expiry when it refreshes, so a token without one
    # came from settings and the differing saved token is newer.
    if memory.expiry is None:
        return _Action.ADOPT_SAVED
    if saved.expiry is None or memory.expiry > saved.expiry:
        return _Action.SAVE_IN_MEMORY
    if memory.expiry < saved.expiry:
        return _Action.ADOPT_SAVED
    return _Action.KEEP


def _remember_agreement(credentials: object, refresh_token: str | None) -> None:
    if refresh_token:
        _agreed_refresh_tokens[credentials] = refresh_token


def _credentials_from_saved(saved: _TokenState, current: object) -> Credentials:
    replacement = Credentials(
        token=saved.access_token,
        refresh_token=saved.refresh_token,
        token_uri=GOOGLE_TOKEN_URI,
        client_id=getattr(current, "client_id", None),
        client_secret=getattr(current, "client_secret", None),
        scopes=getattr(current, "scopes", None),
    )
    if saved.expiry is not None:
        replacement.expiry = saved.expiry
    return replacement


def _credentials_to_save(saved_credentials: dict, memory: _TokenState) -> dict:
    lifetime_s = None
    if memory.expiry is not None:
        # OAuthToken reads an expires_in of 0 as "never expires".
        lifetime_s = max(int((memory.expiry - _utc_now()).total_seconds()), 1)
    token = OAuthToken.from_dict({
        **saved_credentials,
        "access_token": memory.access_token,
        "refresh_token": memory.refresh_token,
        # OAuthToken's convention: local naive time, as datetime.now() gives it.
        "created_at": datetime.now(),  # noqa: DTZ005
        "expires_in": lifetime_s,
    })
    updated = {**saved_credentials, **token.to_dict()}
    if "access_token_expiry_time" in saved_credentials and memory.expiry is not None:
        updated["access_token_expiry_time"] = int(
            memory.expiry.replace(tzinfo=timezone.utc).timestamp() * 1000
        )
    return updated


async def _save_refreshed_token(
    config_service: ConfigurationService,
    connector_id: str,
    credentials: object,
    memory: _TokenState,
    logger: Logger,
    service_name: str,
) -> None:
    config_path = f"/services/connectors/{connector_id}/config"
    try:
        # Shared with TokenRefreshService so its read-refresh-write and this one don't interleave.
        async with connector_refresh_lock(connector_id):
            latest = await config_service.get_config(config_path)
            if not isinstance(latest, dict):
                logger.warning(
                    "Google %s connector settings are missing; the refreshed token was not saved",
                    service_name,
                )
                return
            saved_credentials = latest.get("credentials") or {}
            agreed = _agreed_refresh_tokens.get(credentials)
            if _choose(memory, _saved_state(saved_credentials), agreed) is not _Action.SAVE_IN_MEMORY:
                return
            updated = {**latest, "credentials": _credentials_to_save(saved_credentials, memory)}
            if not await config_service.set_config(config_path, updated):
                logger.warning(
                    "Could not save the refreshed Google %s token to connector settings; "
                    "it stays in use and saving is retried on the next call",
                    service_name,
                )
                return
        _remember_agreement(credentials, memory.refresh_token)
        logger.info("Saved the refreshed Google %s token to connector settings", service_name)
    except Exception as e:
        logger.warning(
            "Could not save the refreshed Google %s token to connector settings (%s); "
            "it stays in use and saving is retried on the next call",
            service_name,
            type(e).__name__,
        )


async def refresh_google_datasource_credentials(
    google_client: GoogleClient,
    data_source: GoogleDriveDataSource | GoogleGmailDataSource,
    config_service: ConfigurationService,
    connector_id: str,
    logger: Logger,
    service_name: str = "Google"
) -> None:
    """
    Reconcile the datasource's OAuth credentials with the token saved in settings.

    The datasource wraps the Google client by reference, so replacing the
    client's credentials updates the datasource too.

    Raises:
        GoogleAuthError: If config not found or no OAuth credentials available
    """
    config = await config_service.get_config(
        f"/services/connectors/{connector_id}/config"
    )
    if not config:
        raise GoogleAuthError(f"Google {service_name} configuration not found")

    saved = _saved_state(config.get("credentials") or {})
    if not saved.access_token and not saved.refresh_token:
        raise GoogleAuthError("No OAuth credentials available")

    http = getattr(google_client.get_client(), "_http", None)
    if http is None or not hasattr(http, "credentials"):
        return

    in_memory = http.credentials
    if _choose(_memory_state(in_memory), saved, _agreed_refresh_tokens.get(in_memory)) is _Action.KEEP:
        _remember_agreement(in_memory, saved.refresh_token)
        return

    def reconcile() -> tuple[_Action, object, _TokenState]:
        # Runs on the datasource's transport, so no refresh is half-written while we read.
        credentials = http.credentials
        memory = _memory_state(credentials)
        action = _choose(memory, saved, _agreed_refresh_tokens.get(credentials))
        if action is _Action.ADOPT_SAVED:
            http.credentials = _credentials_from_saved(saved, credentials)
            _remember_agreement(http.credentials, saved.refresh_token)
        return action, credentials, memory

    action, credentials, memory = await data_source.execute(reconcile)
    if action is _Action.ADOPT_SAVED:
        logger.info("Using the Google %s token saved in connector settings", service_name)
    elif action is _Action.SAVE_IN_MEMORY:
        await _save_refreshed_token(config_service, connector_id, credentials, memory, logger, service_name)
