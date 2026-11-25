"""The listed-authenticators marker: the users a connector's source was seen to list.

Kept per connector in the config store, as a list of user keys. A user it holds is never linked to the
account the connector signs in as, nor given its view, even after the source stops listing them.
"""

from logging import Logger
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService

LISTED_AUTHENTICATORS_KEY = "/services/connectors/{connector_id}/listedAuthenticators"


def listed_authenticator_keys(stored: object) -> list[str]:
    """The user keys a stored listed-authenticators marker holds; nothing stored is none. Raises on
    anything else, an entry that is not a non-empty string included: coerced, ``[None]`` would read
    as a key nobody has and let a listed authenticator be linked."""
    if stored is None:
        return []
    if not isinstance(stored, list) or not all(isinstance(k, str) and k.strip() for k in stored):
        raise TypeError(f"expected a list of user keys, got {type(stored).__name__}")
    return list(stored)


async def read_listed_authenticators(config_service: "ConfigurationService", connector_id: str) -> list[str]:
    """The keys the marker holds. Raises when it cannot be read: unreadable is not empty."""
    return listed_authenticator_keys(await config_service.get_config(
        LISTED_AUTHENTICATORS_KEY.format(connector_id=connector_id), use_cache=False, raise_on_error=True,
    ))


async def write_listed_authenticators(
    config_service: "ConfigurationService", logger: Logger, connector_id: str, keys: list[str],
) -> bool:
    """Write the marker. set_config answers False on a store error instead of raising; one retry, then
    a warning. Never raises."""
    error = ""
    for _ in range(2):
        try:
            if await config_service.set_config(LISTED_AUTHENTICATORS_KEY.format(connector_id=connector_id), keys):
                return True
            error = "the store refused the write"
        except Exception as e:
            error = type(e).__name__
    logger.warning(
        "Connector %s: could not record the listed authenticator; a later sync that no longer lists them "
        "may link them: %s", connector_id, error,
    )
    return False


async def add_listed_authenticator(
    config_service: "ConfigurationService", logger: Logger, connector_id: str, user_key: str,
) -> bool:
    """Add one user to the marker. A marker that cannot be read is not rewritten: its other entries
    would be lost. Never raises."""
    try:
        keys = await read_listed_authenticators(config_service, connector_id)
    except Exception as e:
        logger.warning(
            "Connector %s: could not read the listed-authenticators marker, so the listed authenticator is not "
            "recorded; a later sync that no longer lists them may link them: %s", connector_id, type(e).__name__,
        )
        return False
    if user_key in keys:
        return True
    return await write_listed_authenticators(config_service, logger, connector_id, [*keys, user_key])
