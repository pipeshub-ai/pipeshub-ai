"""Shared MCP data-access helpers — etcd instance/credential resolution.

Extracted from `api/routes/mcp_servers.py` (Phase 1 duplicated these as module-private
`_`-prefixed helpers). Pure data-access, no FastAPI dependency, so this module can be
imported by the agent-loop runtime (query service) and the connectors-service routes
without pulling in HTTP framework concerns.

Single shared implementation of "which credential record actually backs an instance for
a given caller" — used by routes, the agent-loop's `MCPAccessResolver`/`MCPToolProvider`,
and (via `get_authenticated_mcp_servers`) the assistant/placeholder agent's auto-attach path.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from app.agents.constants.mcp_server_constants import (
    get_mcp_credentials_path,
    get_mcp_instance_path,
    get_mcp_instances_prefix,
    get_mcp_org_user_instances_prefix,
    get_mcp_user_instance_path,
    get_mcp_user_instances_prefix,
)
from app.agents.mcp.models import MCPAuthMode, MCPServerConfig, MCPServerTemplate
from app.agents.mcp.naming import (
    assign_namespaces,
    build_namespaced_tool_name,
    namespace_key,
)
from app.agents.mcp.registry import get_mcp_registry
from app.agents.mcp.runner_fields import runner_fields
from app.config.configuration_service import ConfigurationService
from app.core.org_context import org_of
from app.services.featureflag.platform_settings import is_mcp_enabled
from app.utils.concurrency import gather_with_concurrency
from harness_core.refusals import HarnessRefusal

logger = logging.getLogger(__name__)

__all__ = [
    "MCPInstancesUnavailable",
    "SCOPE_ORG",
    "SCOPE_PERSONAL",
    "belongs_to_org",
    "instance_record_path",
    "instance_scope",
    "is_personal",
    "load_org_instances",
    "load_user_instances",
    "load_visible_instances",
    "load_personal_instances_for_admin",
    "get_instance",
    "find_personal_instance_for_admin",
    "SHARED_CREDENTIAL_OWNER",
    "SHARED_CREDENTIAL_SLOT_MARKER",
    "adopt_legacy_shared_credential",
    "credential_owner_id",
    "legacy_shared_credential",
    "resolve_effective_user_auth",
    "uses_shared_credential",
    "catalog_template",
    "instance_config_from_dict",
    "template_connection_fields",
    "credentials_to_discovery_dict",
    "is_effective_auth_authenticated",
    "get_authenticated_mcp_servers",
    "server_namespace",
    "mcp_tool_full_name",
    "is_mcp_enabled",
]


# ---------------------------------------------------------------------------
# Instance storage
# ---------------------------------------------------------------------------


class MCPInstancesUnavailable(HarnessRefusal):
    """The instance store could not be read: the org's MCP servers are unknown, not absent (CPD-7 FB-3). An empty
    list here made a config-store outage look like "no MCP servers": their tools vanished from runs unannounced."""

    reason_code = "mcp_instances_unavailable"
    http_status = 503


SCOPE_ORG = "org"
SCOPE_PERSONAL = "personal"


def belongs_to_org(instance: dict[str, Any], org_id: Optional[str]) -> bool:
    """Instance keys carry no org, so every read must compare the record's own `orgId`.
    A record without one fails closed — every create path sets it."""
    return bool(org_id) and instance.get("orgId") == org_id


def instance_scope(instance: dict[str, Any]) -> str:
    """`personal` for a user's own instance; anything else — including records saved
    before scopes existed — is an org-wide instance."""
    return SCOPE_PERSONAL if instance.get("scope") == SCOPE_PERSONAL else SCOPE_ORG


def is_personal(instance: dict[str, Any]) -> bool:
    return instance_scope(instance) == SCOPE_PERSONAL


def instance_record_path(instance: dict[str, Any]) -> str:
    if is_personal(instance):
        return get_mcp_user_instance_path(instance["orgId"], instance["createdBy"], instance["_id"])
    return get_mcp_instance_path(instance["_id"])


def _owned_by(instance: dict[str, Any], user_id: Optional[str]) -> bool:
    return bool(user_id) and instance.get("createdBy") == user_id


# Records read at once when listing a directory of instances.
_PREFIX_READ_CONCURRENCY = 16


async def _load_instance_key(
    config_service: ConfigurationService, key: str, failures: Optional[list[Exception]] = None,
) -> Optional[dict[str, Any]]:
    try:
        data = await config_service.get_config(key, default=None, use_cache=False)
    except Exception as e:
        logger.warning(f"Failed to load MCP instance at {key}: {e}")
        if failures is not None:
            failures.append(e)
        return None
    return data if isinstance(data, dict) else None


async def _load_prefix(config_service: ConfigurationService, prefix: str) -> list[dict[str, Any]]:
    """Raises ``MCPInstancesUnavailable`` when the store cannot be listed, or when it lists instances none of
    which can be read; one unreadable record among readable ones is skipped."""
    try:
        keys = await config_service.list_keys_in_directory(prefix)
    except Exception as e:
        logger.error(f"Failed to list MCP instance keys under {prefix}: {e}", exc_info=True)
        raise MCPInstancesUnavailable(f"MCP instances could not be listed: {type(e).__name__}") from e
    failures: list[Exception] = []
    records = await gather_with_concurrency(
        _PREFIX_READ_CONCURRENCY, *[_load_instance_key(config_service, key, failures) for key in keys],
    )
    loaded = [record for record in records if record is not None]
    if failures and not loaded:
        raise MCPInstancesUnavailable(f"No MCP instance could be read: {type(failures[0]).__name__}") from failures[0]
    return loaded


async def load_org_instances(config_service: ConfigurationService, org_id: str) -> list[dict[str, Any]]:
    """Every org-wide MCP instance of `org_id` (no secrets)."""
    records = await _load_prefix(config_service, get_mcp_instances_prefix())
    return [r for r in records if belongs_to_org(r, org_id) and not is_personal(r)]



async def load_user_instances(
    config_service: ConfigurationService, org_id: str, user_id: str,
) -> list[dict[str, Any]]:
    """`user_id`'s own personal instances. Stored under the user, so this reads only theirs."""
    records = await _load_prefix(config_service, get_mcp_user_instances_prefix(org_id, user_id))
    return [r for r in records if belongs_to_org(r, org_id) and is_personal(r) and _owned_by(r, user_id)]


async def load_visible_instances(
    config_service: ConfigurationService, org_id: str, user_id: Optional[str],
) -> list[dict[str, Any]]:
    """What `user_id` may see and use: the org's instances plus their own personal ones."""
    if not user_id:
        return await load_org_instances(config_service, org_id)
    org_instances, own = await asyncio.gather(
        load_org_instances(config_service, org_id), load_user_instances(config_service, org_id, user_id),
    )
    return [*org_instances, *own]


async def load_personal_instances_for_admin(config_service: ConfigurationService, org_id: str) -> list[dict[str, Any]]:
    """Every user's personal instances in `org_id`, for org-wide work: cleanup when the org goes,
    and checks that span users (an agent shared with others mustn't use one)."""
    records = await _load_prefix(config_service, get_mcp_org_user_instances_prefix(org_id))
    return [r for r in records if belongs_to_org(r, org_id) and is_personal(r)]


async def get_instance(
    instance_id: str, config_service: ConfigurationService, org_id: str, user_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """The instance `user_id` can see: an org instance, or their own personal one. None when
    it doesn't exist, belongs to another org, or is someone else's personal instance."""
    data = await config_service.get_config(get_mcp_instance_path(instance_id), default=None, use_cache=False)
    if isinstance(data, dict) and belongs_to_org(data, org_id) and not is_personal(data):
        return data
    if not user_id:
        return None
    data = await config_service.get_config(
        get_mcp_user_instance_path(org_id, user_id, instance_id), default=None, use_cache=False,
    )
    if isinstance(data, dict) and belongs_to_org(data, org_id) and is_personal(data) and _owned_by(data, user_id):
        return data
    return None


async def find_personal_instance_for_admin(
    instance_id: str, config_service: ConfigurationService, org_id: str, *, raise_on_error: bool = False,
) -> Optional[dict[str, Any]]:
    """Any user's personal instance by id, for administrators and for checks that must see
    every user's. `raise_on_error` makes a store failure raise rather than read as "none", for
    a caller that would otherwise let something through on it."""
    prefix = get_mcp_org_user_instances_prefix(org_id)
    try:
        keys = await config_service.list_keys_in_directory(prefix)
    except Exception as e:
        logger.error(f"Failed to list MCP instance keys under {prefix}: {e}", exc_info=True)
        if raise_on_error:
            raise
        return None
    for key in keys:
        if not key.endswith(f"/{instance_id}"):
            continue
        if raise_on_error:
            data = await config_service.get_config(key, default=None, use_cache=False, raise_on_error=True)
            record = data if isinstance(data, dict) else None
        else:
            record = await _load_instance_key(config_service, key)
        if record and record.get("_id") == instance_id and belongs_to_org(record, org_id) and is_personal(record):
            return record
    return None


# ---------------------------------------------------------------------------
# Effective auth resolution (shared by discovery + routes + agent-loop runtime)
# ---------------------------------------------------------------------------


def uses_shared_credential(instance: dict[str, Any]) -> bool:
    """`useAdminAuth` only applies to api_token/headers — OAuth is always per-caller."""
    return bool(instance.get("useAdminAuth")) and instance.get("authMode") in (
        MCPAuthMode.API_TOKEN.value, MCPAuthMode.HEADERS.value,
    )


# The owner id of an instance's one shared admin credential. Not a user id or agent key, so the
# shared credential can never be someone's personal one.
SHARED_CREDENTIAL_OWNER = "_shared"
# Set on records saved since the shared credential got its own slot. Older shared records kept
# it at the creator's credential path; see `adopt_legacy_shared_credential`.
SHARED_CREDENTIAL_SLOT_MARKER = "sharedCredentialSlot"


def credential_owner_id(instance: dict[str, Any], caller_id: str) -> str:
    """Whose credential record backs `instance` for `caller_id`: the instance's shared slot
    for a shared admin credential, otherwise the caller's own. Reads and writes must both use
    this, or a shared credential is saved where nobody reads it."""
    if uses_shared_credential(instance):
        return SHARED_CREDENTIAL_OWNER
    return caller_id


async def _load_credential(
    instance: dict[str, Any], owner_id: str, config_service: ConfigurationService,
) -> Optional[dict[str, Any]]:
    """`owner_id`'s credential record for `instance`; with CONTROL_PLANE_CREDENTIALS on, through its binding
    (CPD-8), where a revoked binding is final: no record."""
    from app.harness.credentials.read_through import credentials_switch_on

    if not credentials_switch_on():
        record = await config_service.get_config(
            get_mcp_credentials_path(instance.get("_id"), owner_id), default=None, use_cache=False,
        )
        return record if isinstance(record, dict) else None
    from app.harness.credentials.legacy_records import legacy_records
    from harness_core.credentials.errors import CredentialRevoked

    try:
        return await legacy_records(config_service).load("mcp", org_of(instance) or "", instance.get("_id"), owner_id)
    except CredentialRevoked:
        return None


async def legacy_shared_credential(
    instance: dict[str, Any], config_service: ConfigurationService,
) -> Optional[dict[str, Any]]:
    """A shared credential saved before it had its own slot, still at the creator's credential path; read only."""
    instance_id, creator = instance.get("_id"), instance.get("createdBy")
    if instance.get(SHARED_CREDENTIAL_SLOT_MARKER) or not instance_id or not creator:
        return None
    # The creator's current material: with CONTROL_PLANE_CREDENTIALS on, its binding, not a frozen old path.
    legacy = await _load_credential(instance, creator, config_service)
    return legacy if isinstance(legacy, dict) else None


async def adopt_legacy_shared_credential(
    instance: dict[str, Any], config_service: ConfigurationService,
) -> Optional[dict[str, Any]]:
    """Move a shared credential saved before it had its own slot (at the creator's credential
    path) into the shared slot, and return it. Moving rather than copying means an admin who
    later disconnects it can't have the old copy found again. A no-op for records saved since
    (they carry the slot marker), so it never adopts anyone's personal credential."""
    legacy = await legacy_shared_credential(instance, config_service)
    if legacy is None:
        return None
    instance_id, creator = instance.get("_id"), instance.get("createdBy")
    from app.harness.credentials.legacy_records import legacy_records

    seam, org_id = legacy_records(config_service), org_of(instance) or ""
    try:
        if await seam.save("mcp", org_id, instance_id, SHARED_CREDENTIAL_OWNER, legacy, user_id=creator):
            await seam.remove("mcp", org_id, instance_id, creator, user_id=creator)
    except Exception as e:
        logger.warning(f"Could not move the legacy shared credential of MCP instance {instance_id}: {e}")
    return legacy


async def resolve_effective_user_auth(
    instance: dict[str, Any],
    user_id: str,
    config_service: ConfigurationService,
    *,
    adopt: bool = True,
) -> Optional[dict[str, Any]]:
    """Resolve which credential record actually backs `instance` for `user_id`.

    `adopt=False` reads a legacy shared credential where it is instead of moving it, for a check that must not write.

    - authMode == none: no record needed; returns {} (trivially authenticated).
    - otherwise the record of `credential_owner_id` — the creator's for a shared admin
      credential, else the caller's own (a plain user, or an agentKey for a
      service-account agent — the credential path treats both as opaque owner ids).
    """
    if instance.get("authMode") == MCPAuthMode.NONE.value:
        return {}

    owner_id = credential_owner_id(instance, user_id)
    record = await _load_credential(instance, owner_id, config_service)
    if isinstance(record, dict):
        return record
    if owner_id == SHARED_CREDENTIAL_OWNER:
        if not adopt:
            return await legacy_shared_credential(instance, config_service)
        return await adopt_legacy_shared_credential(instance, config_service)
    return None


def is_effective_auth_authenticated(effective_auth: Optional[dict[str, Any]]) -> bool:
    """`effective_auth` from `resolve_effective_user_auth` -> whether the instance is
    usable: `{}` (authMode == none) counts as authenticated; a stored record must have
    `isAuthenticated` set."""
    return effective_auth is not None and (effective_auth == {} or bool(effective_auth.get("isAuthenticated")))


def catalog_template(type_id: Optional[str]) -> Optional[MCPServerTemplate]:
    if not type_id:
        return None
    registry = get_mcp_registry()
    registry.auto_discover_templates()
    return registry.get_template(type_id)


def template_connection_fields(template: MCPServerTemplate) -> dict[str, Any]:
    """The stored-record fields a catalog server always takes from its template."""
    return {
        "transport": template.transport.value,
        "command": template.command,
        "args": list(template.args),
        "requiredEnv": list(template.required_env),
        "optionalEnv": list(template.optional_env),
        "url": template.default_url,
        "authorizationUrl": template.authorization_url,
        "tokenUrl": template.token_url,
        "scopes": list(template.default_scopes),
    }


def instance_config_from_dict(instance: dict[str, Any]) -> MCPServerConfig:
    """Build the typed `MCPServerConfig` the discovery/client layer expects, from the stored dict.

    A catalog instance's launch fields come from its template even if the stored record
    says otherwise — records saved before that rule could carry a request-chosen command.

    Uses `.get()` rather than direct indexing for required fields so a malformed/stale etcd
    record surfaces as a normal Pydantic `ValidationError` (missing field) instead of an
    unhandled `KeyError` from this function.
    """
    template = catalog_template(instance.get("typeId"))
    if template:
        launch = template_connection_fields(template)
        locked = ["transport", "command", "args", "requiredEnv", "optionalEnv"]
        if template.url_editable is not True:
            locked.append("url")
        instance = {**instance, **{k: launch[k] for k in locked}}
    runner = runner_fields(instance, template)
    return MCPServerConfig(
        _id=instance.get("_id"),
        org_id=instance.get("orgId"),
        created_by=instance.get("createdBy"),
        name=instance.get("name"),
        type_id=instance.get("typeId"),
        transport=instance.get("transport"),
        auth_mode=instance.get("authMode"),
        use_admin_auth=bool(instance.get("useAdminAuth")),
        description=instance.get("description"),
        command=instance.get("command"),
        args=instance.get("args") or [],
        required_env=instance.get("requiredEnv") or [],
        optional_env=instance.get("optionalEnv") or [],
        egress_hosts=list(runner.egress_hosts),
        runner_image=runner.runner_image,
        url=instance.get("url"),
        header_name=instance.get("headerName"),
        authorization_url=instance.get("authorizationUrl"),
        token_url=instance.get("tokenUrl"),
        scopes=instance.get("scopes") or [],
        is_custom=bool(instance.get("isCustom")),
        scope=instance_scope(instance),
        connect_timeout_seconds=instance.get("connectTimeoutSeconds"),
        call_timeout_seconds=instance.get("callTimeoutSeconds"),
        created_at=instance.get("createdAt") or 0,
        updated_at=instance.get("updatedAt") or 0,
    )


def credentials_to_discovery_dict(auth_mode: str, credential_record: dict[str, Any]) -> dict[str, Any]:
    """Flatten a stored credential record into the shape `discovery.build_auth_env_and_headers` expects."""
    if auth_mode == MCPAuthMode.OAUTH.value:
        tokens = credential_record.get("oauthTokens") or {}
        return {"accessToken": tokens.get("accessToken") or tokens.get("access_token")}
    return credential_record.get("credentials") or {}


# ---------------------------------------------------------------------------
# Assistant / placeholder agent support
# ---------------------------------------------------------------------------


def server_namespace(mcp_server: dict[str, Any]) -> str:
    """The namespace a chat names this server's tools with: the one assigned over the user's
    visible set when the route stamped it, else the untagged key."""
    return mcp_server.get("namespace") or namespace_key(mcp_server.get("typeId"), mcp_server.get("name"))


def mcp_tool_full_name(instance: dict[str, Any], tool_name: str) -> str:
    """The name live discovery gives one of an instance's tools."""
    return build_namespaced_tool_name(server_namespace(instance), tool_name)


async def get_authenticated_mcp_servers(
    owner_id: str,
    config_service: ConfigurationService,
    instances: Optional[list[dict[str, Any]]] = None,
    *,
    org_id: Optional[str] = None,
    resolved: Optional[dict[str, dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """Of ``instances``, those `owner_id` (a user, or an agentKey for a service-account agent) has
    completed authentication for — the MCP analog of
    `api/routes/toolsets.py::get_authenticated_toolsets`.

    `resolved`, when given, receives `{instanceId: {"instance", "auth"}}` for every returned
    server (SENSITIVE: holds credentials), so the chat handler needn't read them again.

    Used by the assistant/placeholder agent (`get_assistant_agent` in `api/routes/agent.py`)
    to auto-attach every authenticated MCP server with no graph edges involved. Two
    instances of one type are both returned; `MCPToolProvider` gives the newer one tagged
    tool names for the request.

    ``instances``, when provided, is used as-is (the caller already org-scoped them);
    otherwise `org_id`'s instances are loaded, and with no `org_id` there are none.
    """
    if instances is None:
        if not org_id:
            return []
        try:
            instances = await load_visible_instances(config_service, org_id, owner_id)
        except MCPInstancesUnavailable:
            raise
        except Exception as e:
            logger.error(f"Failed to load MCP instances: {e}", exc_info=True)
            return []
    if not instances:
        return []

    async def _auth_for(instance: dict[str, Any]) -> Optional[dict[str, Any]]:
        try:
            return await resolve_effective_user_auth(instance, owner_id, config_service)
        except Exception as e:
            logger.warning(f"Failed to resolve MCP auth for instance {instance.get('_id')}: {e}")
            return None

    auths = await asyncio.gather(*[_auth_for(instance) for instance in instances])
    # Over every visible instance, as `/my-mcp-servers` names them.
    namespaces = assign_namespaces(instances)
    authenticated: list[dict[str, Any]] = []
    for instance, effective_auth in zip(instances, auths):
        if not is_effective_auth_authenticated(effective_auth):
            continue
        if resolved is not None:
            resolved[instance["_id"]] = {"instance": instance, "auth": effective_auth or {}}
        authenticated.append({
            "instanceId": instance.get("_id"),
            "name": instance.get("name"),
            "typeId": instance.get("typeId"),
            "displayName": instance.get("name"),
            "transport": instance.get("transport"),
            "authMode": instance.get("authMode"),
            "namespace": namespaces[instance["_id"]],
        })

    return authenticated

