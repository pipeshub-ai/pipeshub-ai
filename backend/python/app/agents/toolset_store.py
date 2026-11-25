"""Where toolset instances' data lives in the config store, and the rules for personal instances.

- Org instances, which administrators create, are one shared list at ``/services/toolset-instances``
  (read and written by the toolsets routes).
- Personal instances, which a user adds for themselves, get one key each:
  ``/services/toolset-user-instances/{orgId}/{userId}/{instanceId}``. Only their owner sees them,
  signs in to them and uses them; administrators don't manage them.
- ``/services/toolset-personal-owners/{instanceId}`` names a personal instance's org and owner,
  for code that has only its id.
- Sign-ins are at ``/services/toolsets/{instanceId}/{userId}`` and tool approval rules at
  ``/services/toolset-tool-rules/{instanceId}``, for both kinds.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from app.agents.constants.toolset_constants import (
    get_toolset_instance_users_prefix,
    get_toolset_instances_path,
    get_toolset_org_user_instances_prefix,
    get_toolset_personal_owner_path,
    get_toolset_tool_rules_path,
    get_toolset_user_instance_path,
    get_toolset_user_instances_prefix,
)
from app.config.configuration_service import delete_config_confirmed
from app.core.org_context import org_of
from app.utils.concurrency import gather_with_concurrency
from app.utils.env_utils import env_int
from app.utils.url_fetcher import FetchError, resolve_public_http_target

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService

logger = logging.getLogger(__name__)

SCOPE_ORG = "org"
SCOPE_PERSONAL = "personal"

MAX_PERSONAL_INSTANCES_ENV = "TOOLSET_MAX_PERSONAL_INSTANCES"
DEFAULT_MAX_PERSONAL_INSTANCES = 25

# Records read at once when listing a directory of instances.
_READ_CONCURRENCY = 16
# Fields holding a bare host name (a database server), not a URL.
_HOST_FIELDS = frozenset({"host", "hostname"})


def is_personal(instance: dict[str, Any]) -> bool:
    """A record saved before scopes existed has none: it is an org instance."""
    return instance.get("scope") == SCOPE_PERSONAL


def instance_scope(instance: dict[str, Any]) -> str:
    return SCOPE_PERSONAL if is_personal(instance) else SCOPE_ORG


def max_personal_instances() -> int:
    return env_int(MAX_PERSONAL_INSTANCES_ENV, DEFAULT_MAX_PERSONAL_INSTANCES, lo=0)


def personal_instance_path(instance: dict[str, Any]) -> str:
    return get_toolset_user_instance_path(instance["orgId"], instance["createdBy"], instance["_id"])


def _personal_record(record: object, org_id: str | None, owner_id: str | None = None) -> bool:
    """`record` is a personal instance in `org_id` (any org when None), owned by `owner_id` when given.
    A record without an org fails closed: every create path sets it."""
    return (
        isinstance(record, dict)
        and is_personal(record)
        and bool(record.get("orgId"))
        and (org_id is None or record.get("orgId") == org_id)
        and (owner_id is None or record.get("createdBy") == owner_id)
    )


async def _read(config_service: ConfigurationService, key: str) -> dict[str, Any] | None:
    try:
        data = await config_service.get_config(key, default=None, use_cache=False)
    except Exception as e:
        logger.warning(f"Failed to load toolset instance at {key}: {e}")
        return None
    return data if isinstance(data, dict) else None


async def load_user_instances(config_service: ConfigurationService, org_id: str, user_id: str) -> list[dict[str, Any]]:
    """`user_id`'s personal instances. A store failure reads as none, so their org instances still list."""
    prefix = get_toolset_user_instances_prefix(org_id, user_id)
    try:
        keys = await config_service.list_keys_in_directory(prefix)
    except Exception as e:
        logger.error(f"Failed to list personal toolset instances under {prefix}: {e}", exc_info=True)
        return []
    records = await gather_with_concurrency(_READ_CONCURRENCY, *[_read(config_service, key) for key in keys])
    return [r for r in records if _personal_record(r, org_id, user_id)]


async def get_personal_instance(
    config_service: ConfigurationService, org_id: str, user_id: str, instance_id: str,
) -> dict[str, Any] | None:
    """`user_id`'s own personal instance `instance_id`, or None: anyone else's reads as missing."""
    record = await _read(config_service, get_toolset_user_instance_path(org_id, user_id, instance_id))
    return record if _personal_record(record, org_id, user_id) and record.get("_id") == instance_id else None


async def find_personal_instance(
    config_service: ConfigurationService, org_id: str | None, instance_id: str, *, raise_on_error: bool = False,
) -> dict[str, Any] | None:
    """Any user's personal instance by id, for code that runs without its owner at hand: a tool
    client reading its settings, token refresh, the check that keeps one off a shared agent.
    `org_id` None searches every org. `raise_on_error` makes a store failure raise rather than
    read as "none", for a caller that would otherwise let something through on it."""
    async def read(key: str) -> dict[str, Any] | None:
        if not raise_on_error:
            return await _read(config_service, key)
        data = await config_service.get_config(key, default=None, use_cache=False, raise_on_error=True)
        return data if isinstance(data, dict) else None

    def match(record: dict[str, Any] | None) -> dict[str, Any] | None:
        return record if _personal_record(record, org_id) and record.get("_id") == instance_id else None

    owner = await read(get_toolset_personal_owner_path(instance_id))
    if owner and owner.get("orgId") and owner.get("userId"):
        return match(await read(get_toolset_user_instance_path(owner["orgId"], owner["userId"], instance_id)))

    # No owner pointer: list every user's instances, which on Redis scans the whole keyspace.
    prefix = get_toolset_org_user_instances_prefix(org_id)
    try:
        keys = await config_service.list_keys_in_directory(prefix)
    except Exception as e:
        logger.error(f"Failed to list personal toolset instances under {prefix}: {e}", exc_info=True)
        if raise_on_error:
            raise
        return None
    for key in keys:
        if key.endswith(f"/{instance_id}") and (found := match(await read(key))):
            return found
    return None


async def save_personal_instance(config_service: ConfigurationService, instance: dict[str, Any]) -> bool:
    owner = {"orgId": instance["orgId"], "userId": instance["createdBy"]}
    await config_service.set_config(get_toolset_personal_owner_path(instance["_id"]), owner)
    return bool(await config_service.set_config(personal_instance_path(instance), instance))


# ---------------------------------------------------------------------------
# Deleting an instance's data
# ---------------------------------------------------------------------------


def _refresh_service() -> Any:  # noqa: ANN401 - ToolsetTokenRefreshService, imported lazily (it pulls in the connectors)
    """This process's toolset token refresher, when it runs one (the connectors service does)."""
    try:
        from app.connectors.core.base.token_service.startup_service import (
            startup_service,
        )

        return startup_service.get_toolset_token_refresh_service()
    except Exception as e:
        logger.warning(f"Could not reach the toolset token refresher: {e}")
        return None


async def delete_instance_credentials(config_service: ConfigurationService, instance_id: str) -> int:
    """Every sign-in to `instance_id` (users' and service-account agents'), after stopping their
    token refreshes. Returns how many were deleted; a failure is logged, not raised."""
    try:
        refresh_service = _refresh_service()
        if refresh_service:
            cancelled = refresh_service.cancel_refresh_tasks_for_instance(instance_id)
            if cancelled:
                logger.info(f"Cancelled {cancelled} refresh task(s) for toolset instance {instance_id}")
    except Exception as e:
        logger.warning(f"Could not cancel refresh tasks for toolset instance {instance_id}: {e}")

    prefix = get_toolset_instance_users_prefix(instance_id)
    try:
        keys = await config_service.list_keys_in_directory(prefix)
    except Exception as e:
        logger.error(f"Could not list the sign-ins of toolset instance {instance_id}: {e}", exc_info=True)
        return 0
    # Exactly /services/toolsets/{instanceId}/{owner}: a stray key under the prefix is left alone.
    own = [key for key in keys if key.startswith(prefix) and "/" not in key[len(prefix):] and key[len(prefix):]]
    for key in set(keys) - set(own):
        logger.warning(f"Skipping a key that isn't a sign-in of toolset instance {instance_id}: {key}")
    results = await asyncio.gather(*[config_service.delete_config(key) for key in own], return_exceptions=True)
    deleted = 0
    for key, result in zip(own, results):
        if isinstance(result, Exception):
            logger.warning(f"Failed to delete credential {key}: {result}")
        elif result:
            deleted += 1
    return deleted


async def delete_instance_rules(config_service: ConfigurationService, instance_id: str) -> None:
    try:
        await config_service.delete_config(get_toolset_tool_rules_path(instance_id))
    except Exception as e:
        logger.warning(f"Couldn't delete the tool approval rules of toolset instance {instance_id}: {e}")


async def delete_personal_instance(config_service: ConfigurationService, instance: dict[str, Any]) -> int:
    """A personal instance with its sign-ins and tool rules. Returns how many sign-ins went.

    Raises:
        RuntimeError: the instance record itself couldn't be deleted.
    """
    deleted = await delete_instance_credentials(config_service, instance["_id"])
    if not await delete_config_confirmed(config_service, personal_instance_path(instance)):
        raise RuntimeError(f"Couldn't delete personal toolset instance {instance['_id']}")
    await delete_instance_rules(config_service, instance["_id"])
    try:
        await config_service.delete_config(get_toolset_personal_owner_path(instance["_id"]))
    except Exception as e:
        logger.warning(f"Couldn't delete the owner pointer of toolset instance {instance['_id']}: {e}")
    return deleted


async def _remove_all(config_service: ConfigurationService, prefix: str, org_id: str) -> int:
    keys = await config_service.list_keys_in_directory(prefix)
    records = await gather_with_concurrency(_READ_CONCURRENCY, *[_read(config_service, key) for key in keys])
    removed = 0
    for record in records:
        if not _personal_record(record, org_id):
            continue
        try:
            await delete_personal_instance(config_service, record)
            removed += 1
        except Exception as e:
            logger.error(f"Couldn't remove personal toolset instance {record.get('_id')}: {e}")
    return removed


async def remove_user_personal_instances(config_service: ConfigurationService, org_id: str, user_id: str) -> int:
    """A deleted user's personal instances, with their sign-ins and rules, so nothing keeps
    refreshing their tokens. Returns how many instances went."""
    return await _remove_all(config_service, get_toolset_user_instances_prefix(org_id, user_id), org_id)


async def remove_org_personal_instances(config_service: ConfigurationService, org_id: str) -> int:
    """Every personal instance of a deleted org."""
    return await _remove_all(config_service, get_toolset_org_user_instances_prefix(org_id), org_id)


# ---------------------------------------------------------------------------
# Erasing a deleted user's or org's toolset data (C-14 PRINCIPAL_DATA_STORES `personalInstances`)
# ---------------------------------------------------------------------------


class PersonalDataRemainsError(RuntimeError):
    """Keys of an erased user or org are still in the store; the caller retries the erasure."""

    def __init__(self, remaining: list[str]) -> None:
        super().__init__(f"{len(remaining)} key(s) of the erased principal remain")
        self.remaining = remaining


async def _keys_at(config_service: ConfigurationService, path: str) -> list[str]:
    """Every key under `path` when it ends in "/"; otherwise the key itself and any nested under it."""
    keys = await config_service.list_keys_in_directory(path)
    if path.endswith("/"):
        return [key for key in keys if key.startswith(path)]
    return [key for key in keys if key == path or key.startswith(f"{path}/")]


async def _erase_at(config_service: ConfigurationService, path: str) -> None:
    keys = await _keys_at(config_service, path)
    results = await asyncio.gather(*[config_service.delete_config(key) for key in keys], return_exceptions=True)
    for key, result in zip(keys, results):
        if isinstance(result, Exception):
            logger.warning(f"Could not erase {key}: {result}")


async def _personal_instance_paths(config_service: ConfigurationService, key: str, org_id: str) -> list[str]:
    """What a personal instance at `key` stores, keyed by the id its path names (its record may be
    unreadable): the record, every sign-in to it, its tool rules and its owner pointer."""
    instance_id = key.rsplit("/", 1)[-1]
    paths = [key, get_toolset_instance_users_prefix(instance_id), get_toolset_tool_rules_path(instance_id)]
    pointer = get_toolset_personal_owner_path(instance_id)
    owner = await config_service.get_config(pointer, default=None, use_cache=False, raise_on_error=True)
    # Ids are generated, so a pointer naming another org is another instance's, not this erasure's.
    if not isinstance(owner, dict) or org_of(owner) in (None, org_id):
        paths.append(pointer)
    return paths


async def _org_instance_sign_ins(config_service: ConfigurationService, org_id: str, user_id: str | None) -> list[str]:
    """`user_id`'s sign-ins to the org's own instances, or every sign-in to them when `user_id` is None."""
    stored = await config_service.get_config(
        get_toolset_instances_path(org_id), default=[], use_cache=False, raise_on_error=True,
    )
    rows = stored if isinstance(stored, list) else []
    ids = [i["_id"] for i in rows if isinstance(i, dict) and org_of(i) == org_id and isinstance(i.get("_id"), str) and i["_id"]]
    return [f"{get_toolset_instance_users_prefix(i)}{user_id or ''}" for i in ids]


async def _erase_principal(
    config_service: ConfigurationService, org_id: str, user_id: str | None, prefix: str,
) -> dict[str, int]:
    instances = await _keys_at(config_service, prefix)
    paths = [prefix]
    for key in instances:
        paths += await _personal_instance_paths(config_service, key, org_id)
    sign_ins = await _org_instance_sign_ins(config_service, org_id, user_id)
    paths += sign_ins

    refresh_service = _refresh_service()
    if refresh_service:
        for key in instances:
            refresh_service.cancel_refresh_tasks_for_instance(key.rsplit("/", 1)[-1])
        for path in sign_ins:
            for key in await _keys_at(config_service, path):
                refresh_service.cancel_refresh_task(key)
    for path in paths:
        await _erase_at(config_service, path)

    remaining = [key for path in paths for key in await _keys_at(config_service, path)]
    if remaining:
        raise PersonalDataRemainsError(remaining)
    return {"toolsetInstances": len(instances)}


async def erase_user_toolset_data(config_service: ConfigurationService, org_id: str, user_id: str) -> dict[str, int]:
    """A deleted user's toolset data (CLEANUP-G2): their personal instances with every sign-in to them,
    their tool rules and owner pointers, and their own sign-ins to the org's instances. Every key under
    their prefix goes, readable or not; another user's data is never touched. Idempotent.

    Raises:
        PersonalDataRemainsError: a key is still there afterwards (a delete failed); run it again.
        ValueError: no org or user named: the prefixes would then reach other people's data.
    """
    if not org_id or "/" in org_id or not user_id or "/" in user_id:
        raise ValueError("An org and one user are required")
    return await _erase_principal(config_service, org_id, user_id, get_toolset_user_instances_prefix(org_id, user_id))


async def erase_org_toolset_data(config_service: ConfigurationService, org_id: str) -> dict[str, int]:
    """A deleted org's toolset data (CLEANUP-G1): every member's personal instances with their sign-ins,
    rules and owner pointers, and every sign-in to the org's own instances. Another org's is never touched.

    Raises:
        PersonalDataRemainsError: a key is still there afterwards; run it again.
        ValueError: no org named.
    """
    if not org_id or "/" in org_id:
        raise ValueError("An org is required")
    return await _erase_principal(config_service, org_id, None, get_toolset_org_user_instances_prefix(org_id))


# ---------------------------------------------------------------------------
# Addresses a personal instance may reach
# ---------------------------------------------------------------------------


class AddressNotAllowedError(ValueError):
    """A personal instance's setting points at an address it may not reach. The message is safe to
    show; it doesn't say what the name resolved to."""


def connects_by_host(field_names: set[str]) -> bool:
    """The toolset names its server by host (a database), and connects through a driver that
    can't be held to the address that was checked. Only administrators add those."""
    return any(name.lower() in _HOST_FIELDS for name in field_names)


def with_url_schemes(settings: dict[str, Any], url_fields: set[str]) -> dict[str, Any]:
    """`settings` with each URL field given without a scheme written as https, the way its client
    reads it (Salesforce adds one itself). Stored that way, the address check on save and on every
    read sees the host that will be called.

    Raises:
        AddressNotAllowedError: a URL field names a scheme other than http(s).
    """
    out = dict(settings)
    for key in url_fields:
        text = out[key].strip() if isinstance(out.get(key), str) else ""
        if not text:
            continue
        if not text.lower().startswith(("http://", "https://")):
            if "://" in text:
                raise AddressNotAllowedError(f"{key} must be an http or https address.")
            text = f"https://{text}"
        out[key] = text
    return out


async def assert_public_addresses(settings: dict[str, Any]) -> None:
    """Any user can add a personal instance and our servers make its calls, so it may only reach
    public addresses: every http(s) URL among `settings`, and every host field, must resolve to
    public addresses only.

    Raises:
        AddressNotAllowedError: naming the first field that may not be used.
    """
    for key, value in settings.items():
        text = value.strip() if isinstance(value, str) else ""
        if not text:
            continue
        if key.lower() in _HOST_FIELDS:
            url = f"https://{text}"
        elif text.lower().startswith(("http://", "https://")):
            url = text
        else:
            continue
        try:
            await asyncio.to_thread(resolve_public_http_target, url, block_non_global=True, allow_private=False)
        except FetchError as e:
            raise AddressNotAllowedError(
                f"{key} must be a public address that this server can reach. Ask an administrator to add "
                "actions for private or internal servers."
            ) from e
