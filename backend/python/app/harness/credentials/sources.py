"""Read-only secret sources over the records the platform already keeps.

Their owners write them (the toolset OAuth callback and
``ToolsetTokenRefreshService``, the MCP credential routes, the AI Models
settings); the credential service only hands them out by handle. Each record
lives in etcd/Redis through ``ConfigurationService``, encrypted at rest by the
platform's encrypted key-value store. They keep no history: a refreshed token
is a new version, and a handle issued earlier resolves the current record
(the provider honours the earlier token until it expires).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from typing import TYPE_CHECKING, Any, cast

from app.harness.credentials.read_through import (
    BindingReadThrough,
    credentials_switch_on,
)
from harness_core.credentials.errors import CredentialReadOnly
from harness_core.credentials.types import Secret

if TYPE_CHECKING:
    from pydantic import JsonValue

    from app.harness.credentials.bindings import BindingDocuments
    from harness_core.credentials.types import CredentialScope

TOOLSET_KIND = "toolset"
MCP_KIND = "mcp"
MODEL_KIND = "model"


# The version is logged with every handle issued. An unkeyed digest of the material would let anyone with the logs
# test guesses of a weak stored password offline; the key lives only in this process, which is where the issued
# handles and the versions they are compared with live.
_VERSION_KEY = secrets.token_bytes(32)


def _version(material: object) -> str:
    text = json.dumps(material, sort_keys=True, default=str).encode()
    return hmac.new(_VERSION_KEY, text, hashlib.sha256).hexdigest()[:16]


async def configured_llm_entries(config: Any, *, fresh: bool = False) -> list[dict[str, Any]]:  # noqa: ANN401
    """The deployment's configured LLM entries: how a pinned model is reached (its endpoint and key), read when
    it is used. Which model a run calls is pinned and checked against the entry by every caller; this never
    chooses one. Absent and unreadable both answer none: the caller refuses, never guesses."""
    from app.config.constants.service import config_node_constants

    document: object = await config.get_config(
        config_node_constants.AI_MODELS.value, use_cache=not fresh, raise_on_error=False
    )
    entries = cast("dict[str, Any]", document).get("llm") if isinstance(document, dict) else None
    return [cast("dict[str, Any]", e) for e in cast("list[object]", entries) if isinstance(e, dict)] if isinstance(entries, list) else []


def owner_scoped(name: str) -> tuple[str, str]:
    """``{instanceId}/{owner}`` → (instanceId, owner)."""
    instance, _, owner = name.partition("/")
    if not instance or not owner:
        raise ValueError("expected {instanceId}/{owner}")
    return instance, owner


class _ReadOnly:
    async def write(self, tenant: str, scope: CredentialScope, material: JsonValue) -> str:
        raise CredentialReadOnly(f"{scope.kind} credentials are written by their owner, not by the service")


class _ReadsBindingsFirst:
    """With ``CONTROL_PLANE_CREDENTIALS`` on, the binding is read first (CPD-8, ``read_through``)."""

    def __init__(self, config: Any, bindings: BindingDocuments | None = None) -> None:  # noqa: ANN401
        self._config = config
        self._through = BindingReadThrough(bindings) if bindings is not None else None

    async def _binding(
        self, tenant: str, scope: CredentialScope, instance: str, owner: str, old_record: object
    ) -> Secret | None:
        if self._through is None or not credentials_switch_on():
            return None
        return await self._through.read(tenant, scope, instance, owner, old_record)


class ToolsetSecretSource(_ReadOnly, _ReadsBindingsFirst):
    """``/services/toolsets/{instanceId}/{userId|agentKey}``, when authenticated; its binding first when switched on."""

    async def read(self, tenant: str, scope: CredentialScope, version: str | None = None) -> Secret | None:
        from app import edition_config
        from app.agents.constants.toolset_constants import get_toolset_config_path

        instance, owner = owner_scoped(scope.name)
        # The credential path names no org: only an instance of the run's org may resolve (QA19-O2).
        if await edition_config.get_toolset_by_id(instance, self._config, org_id=tenant) is None:
            return None
        # Absent and unreadable both answer "no secret": the credential service refuses credential_unavailable.
        record: object = await self._config.get_config(get_toolset_config_path(instance, owner), raise_on_error=False)
        bound = await self._binding(tenant, scope, instance, owner, record)
        if bound is not None:
            authenticated = isinstance(bound.material, dict) and bound.material.get("isAuthenticated", False)
            return bound if authenticated else None
        if not isinstance(record, dict):
            return None
        found = cast("dict[str, Any]", record)
        return Secret(_version(found), cast("JsonValue", found)) if found.get("isAuthenticated", False) else None


class McpSecretSource(_ReadOnly, _ReadsBindingsFirst):
    """The effective MCP credential record of ``{instanceId}/{owner}`` (``useAdminAuth`` honoured); its binding first
    when switched on."""

    async def read(self, tenant: str, scope: CredentialScope, version: str | None = None) -> Secret | None:
        from app.agents.mcp import service as mcp_service
        from app.agents.mcp.instance_scope import get_instance_for_org

        instance_id, owner = owner_scoped(scope.name)
        # As for toolsets: another org's instance id resolves nothing (QA19-O2).
        instance = await get_instance_for_org(instance_id, self._config, tenant, owner)
        if not instance:
            return None
        auth = await mcp_service.resolve_effective_user_auth(instance, owner, self._config)
        if auth != {}:
            holder = mcp_service.credential_owner_id(instance, owner)
            bound = await self._binding(tenant, scope, instance_id, holder, auth)
            if bound is not None:
                material = bound.material if isinstance(bound.material, dict) else None
                return bound if mcp_service.is_effective_auth_authenticated(cast("dict[str, Any] | None", material)) else None
        if not mcp_service.is_effective_auth_authenticated(auth):
            return None
        material = cast("JsonValue", auth or {})
        return Secret(_version(material), material)


class ModelKeySource(_ReadOnly):
    """``configuration.apiKey`` of the ``/services/aiModels`` LLM entry named ``modelKey``."""

    def __init__(self, config: Any) -> None:  # noqa: ANN401
        self._config = config

    async def read(self, tenant: str, scope: CredentialScope, version: str | None = None) -> Secret | None:
        # The cached document first, as the query service reads it; one fresh read for a key it lacks.
        for fresh in (False, True):
            for entry in await configured_llm_entries(self._config, fresh=fresh):
                if entry.get("modelKey") == scope.name:
                    configuration = entry.get("configuration")
                    key = cast("dict[str, Any]", configuration).get("apiKey") if isinstance(configuration, dict) else None
                    return Secret(_version(key), key) if isinstance(key, str) and key else None
        return None


__all__ = [
    "MCP_KIND",
    "MODEL_KIND",
    "TOOLSET_KIND",
    "McpSecretSource",
    "ModelKeySource",
    "ToolsetSecretSource",
    "configured_llm_entries",
    "owner_scoped",
]
