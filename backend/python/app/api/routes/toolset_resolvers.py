"""Toolset edition resolvers — own-org lookups; EE-only seams are no-ops."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import HTTPException, Request

from app.config.configuration_service import ConfigurationService
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import DefaultEndpoints

logger = logging.getLogger(__name__)

DEFAULT_TOOLSET_INSTANCES_PATH = "/services/toolset-instances"

REDACTED_PLACEHOLDER: str | None = None


def _oauth_config_path(toolset_type: str) -> str:
    return f"/services/oauths/toolsets/{toolset_type.lower()}"


async def get_oauth_credentials_for_toolset(
    toolset_config: dict[str, Any],
    config_service: ConfigurationService,
    logger: logging.Logger | None = None,
    raw_config_service: Any | None = None,
    oauth_config_resolver: Any | None = None,
) -> dict[str, Any]:
    """Fetch OAuth config for a toolset."""
    del raw_config_service, oauth_config_resolver

    if not toolset_config:
        raise ValueError("Toolset configuration is required")

    auth_config = toolset_config.get("auth", {})
    if auth_config and isinstance(auth_config, dict):
        has_client_id = auth_config.get("clientId") or auth_config.get("client_id")
        has_client_secret = auth_config.get("clientSecret") or auth_config.get("client_secret")
        if has_client_id and has_client_secret:
            if logger:
                logger.debug("Using OAuth credentials from toolset auth config (legacy or override)")
            return dict(auth_config)

    oauth_config_id = toolset_config.get("oauthConfigId")
    toolset_type = toolset_config.get("toolsetType")
    instance_id = toolset_config.get("instanceId")

    if not toolset_type:
        raise ValueError(
            f"Toolset type not found in config. "
            f"Config keys: {list(toolset_config.keys())}. "
            f"This indicates a corrupted toolset configuration."
        )

    if not oauth_config_id and instance_id:
        if logger:
            logger.warning(
                f"No oauthConfigId in user config for instance {instance_id}. "
                f"Fetching current instance's OAuth config (admin may have updated it)."
            )
        try:
            instances = await config_service.get_config(DEFAULT_TOOLSET_INSTANCES_PATH, default=[])
            if isinstance(instances, list):
                current_instance = next(
                    (inst for inst in instances if inst.get("_id") == instance_id),
                    None,
                )
                if current_instance:
                    oauth_config_id = current_instance.get("oauthConfigId")
                    if logger:
                        logger.info(
                            f"Retrieved current oauthConfigId '{oauth_config_id}' from instance {instance_id}"
                        )
        except Exception as e:
            if logger:
                logger.warning(f"Could not fetch instance to get oauthConfigId: {e}")

    if not oauth_config_id:
        raise ValueError(
            f"No oauthConfigId found in toolset config or instance. "
            f"Config keys: {list(toolset_config.keys())}. "
            f"Please reauthenticate or ask an administrator to configure OAuth for this toolset."
        )

    try:
        oauth_config_path = _oauth_config_path(toolset_type)
        oauth_configs = await config_service.get_config(oauth_config_path, default=[], use_cache=False)
        if not isinstance(oauth_configs, list):
            raise ValueError(f"Invalid OAuth config format for toolset type '{toolset_type}'")

        oauth_config = next(
            (cfg for cfg in oauth_configs if cfg.get("_id") == oauth_config_id),
            None,
        )
        if not oauth_config:
            if logger:
                logger.error(
                    f"OAuth configuration '{oauth_config_id}' not found for toolset '{toolset_type}'. "
                    f"Available configs: {[c.get('_id') for c in oauth_configs]}"
                )
            raise ValueError(
                f"OAuth configuration '{oauth_config_id}' not found for toolset '{toolset_type}'. "
                f"This can happen if:\n"
                f"  1. The admin deleted the OAuth configuration\n"
                f"  2. The admin switched the instance to use a different OAuth config\n"
                f"  3. There's a configuration mismatch\n"
                f"Please reauthenticate this toolset to use the current OAuth configuration."
            )

        config_data = oauth_config.get("config", {})
        if not config_data or not isinstance(config_data, dict):
            raise ValueError(
                f"OAuth configuration '{oauth_config_id}' has invalid or empty config data. "
                f"Please ask an administrator to update the OAuth configuration."
            )

        client_id = config_data.get("clientId") or config_data.get("client_id")
        client_secret = config_data.get("clientSecret") or config_data.get("client_secret")
        if not client_id or not client_secret:
            raise ValueError(
                f"OAuth configuration '{oauth_config_id}' is missing clientId or clientSecret. "
                f"Available config keys: {list(config_data.keys())}. "
                f"Please ask an administrator to update the OAuth configuration."
            )

        if logger:
            logger.debug(
                f"✅ Fetched complete OAuth config '{oauth_config_id}' "
                f"for toolset type '{toolset_type}' with fields: {list(config_data.keys())}"
            )
        return dict(config_data)
    except ValueError:
        raise
    except Exception as e:
        if logger:
            logger.error(f"Failed to fetch OAuth credentials: {e}", exc_info=True)
        raise ValueError(
            f"Failed to retrieve OAuth credentials for toolset: {str(e)}"
        ) from e


async def get_toolset_by_id(
    instance_id: str,
    config_service: ConfigurationService,
    org_id: str | None = None,
) -> dict[str, Any] | None:
    """Fetch a toolset instance by ID, optionally scoped to an org."""
    try:
        instances = await config_service.get_config(DEFAULT_TOOLSET_INSTANCES_PATH, default=[])
        if isinstance(instances, list):
            for inst in instances:
                if inst.get("_id") == instance_id:
                    if org_id is not None and inst.get("orgId") != org_id:
                        return None
                    return inst
        return None
    except Exception as e:
        logger.error(f"Failed to fetch toolset instance '{instance_id}': {e}", exc_info=True)
        return None


async def check_user_is_admin(
    user_id: str,
    org_id: str | None,
    request: Request | None,
    config_service: ConfigurationService,
) -> bool:
    """Admin check via Node.js CM backend."""
    del org_id
    if request is None:
        return False
    try:
        try:
            endpoints = await config_service.get_config("/services/endpoints", use_cache=False)
            nodejs_url = (
                endpoints.get("nodejs", {}).get("endpoint")
                if isinstance(endpoints, dict)
                else None
            ) or DefaultEndpoints.NODEJS_ENDPOINT.value
        except Exception:
            nodejs_url = DefaultEndpoints.NODEJS_ENDPOINT.value

        auth_headers: dict[str, str] = {}
        for header_name in ("authorization", "x-organization-id", "cookie"):
            val = request.headers.get(header_name)
            if val:
                auth_headers[header_name] = val

        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{nodejs_url}/api/v1/users/{user_id}/adminCheck",
                headers=auth_headers,
            )
            return resp.status_code == HttpStatusCode.OK.value
    except Exception as e:
        logger.warning(
            f"Admin check via REST API failed for user {user_id}: {e}. Defaulting to non-admin."
        )
        return False


async def resolve_inherited_from_org_id(
    *,
    toolset_type: str,
    oauth_config_id: str,
    org_id: str,
    config_service: ConfigurationService,
    oauth_config_resolver: Any | None = None,
) -> str | None:
    """No org inheritance."""
    del toolset_type, oauth_config_id, org_id, config_service, oauth_config_resolver
    return None


def mask_oauth_secrets(
    cfg_data: dict[str, Any],
    *,
    is_inherited: bool = False,
) -> dict[str, Any]:
    """Return config unchanged."""
    del is_inherited
    return dict(cfg_data)


def is_redacted_placeholder(value: Any) -> bool:
    """Never redacts."""
    del value
    return False


async def load_instances_for_mutation(
    org_id: str,
    config_service: ConfigurationService,
) -> list[dict[str, Any]]:
    """Load instances for create/update/delete."""
    try:
        instances_data = await config_service.get_config(DEFAULT_TOOLSET_INSTANCES_PATH, default=[])
        if not isinstance(instances_data, list):
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail="Invalid toolset instances data.",
            )
        return [i for i in instances_data if isinstance(i, dict) and i.get("orgId") == org_id]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to load toolset instances: {e}", exc_info=True)
        raise HTTPException(
            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
            detail="Failed to access toolset instances. Please try again or contact support."
        ) from e
