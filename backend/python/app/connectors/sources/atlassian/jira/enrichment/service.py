from __future__ import annotations

import time
from typing import Any

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import Connectors
from app.connectors.sources.atlassian.jira.enrichment.field_discovery import discover_custom_field_ids
from app.connectors.sources.atlassian.jira.enrichment.record_identifiers import (
    is_jira_connector,
)
from app.connectors.sources.atlassian.jira.enrichment.issue_fetcher import (
    batch_fetch_issues,
    resolve_is_cloud_api,
)
from app.connectors.sources.atlassian.jira.enrichment.value_formatter import enrich_from_issue
from app.models.entities import RecordType, TicketRecord
from app.sources.client.jira.jira import JiraClient, JiraConfigUnavailableError
from app.sources.external.jira.jira import JiraDataSource
from app.utils.logger import create_logger

logger = create_logger("jira_ticket_enrichment")

_data_sources: dict[str, JiraDataSource] = {}
_is_cloud: dict[str, bool] = {}
_auth_types: dict[str, str] = {}
# Connector ids whose configuration cannot make a Jira client, and when to try
# again. A record can say JIRA without coming from a Jira connector (the Demo
# connector imitates one); without this, every answer citing such a record
# retried the client and logged the failure.
_unavailable_until: dict[str, float] = {}
_RETRY_UNAVAILABLE_AFTER_S = 600.0

_CONNECTOR_CONFIG_PATH = "/services/connectors/{connector_id}/config"


async def _sync_oauth_token_if_needed(
    ds: JiraDataSource,
    config_service: ConfigurationService,
    connector_id: str,
) -> None:
    """Update cached client token from etcd when OAuth credentials were rotated."""
    try:
        config = await config_service.get_config(
            _CONNECTOR_CONFIG_PATH.format(connector_id=connector_id),
        )
        if not config:
            return
        fresh_token = (config.get("credentials") or {}).get("access_token", "")
        if not fresh_token:
            return
        inner = ds._client
        if not hasattr(inner, "get_token") or not hasattr(inner, "set_token"):
            return
        if inner.get_token() != fresh_token:
            inner.set_token(fresh_token)
    except Exception as exc:
        logger.warning(
            "Could not sync OAuth token for connector %s: %s",
            connector_id,
            exc,
        )


async def _get_data_source(
    config_service: ConfigurationService,
    connector_id: str,
    connector_name: Connectors | str | None = None,
) -> JiraDataSource | None:
    if connector_id in _data_sources:
        ds = _data_sources[connector_id]
        if _auth_types.get(connector_id) == "OAUTH":
            await _sync_oauth_token_if_needed(ds, config_service, connector_id)
        return ds
    if _unavailable_until.get(connector_id, 0.0) > time.monotonic():
        return None
    try:
        jira_client = await JiraClient.build_from_services(
            logger,
            config_service,
            connector_instance_id=connector_id,
        )
    except JiraConfigUnavailableError as exc:
        # The config store did not answer; that can pass by the next answer.
        logger.warning("Could not read the Jira config for connector %s: %s", connector_id, exc)
        return None
    except ValueError as exc:
        # Its configuration cannot make a Jira client at all, which does not
        # fix itself between two answers: e.g. the record came from the Demo
        # connector, which only imitates Jira.
        _unavailable_until[connector_id] = time.monotonic() + _RETRY_UNAVAILABLE_AFTER_S
        logger.warning(
            "No Jira client for connector %s: %s (not retried for %d minutes)",
            connector_id, exc, int(_RETRY_UNAVAILABLE_AFTER_S // 60),
        )
        return None
    except Exception as exc:
        # Anything else may be a blip (network, Jira itself); try again next time.
        logger.warning("Failed to build Jira client for connector %s: %s", connector_id, exc)
        return None
    try:
        inner = jira_client.get_client()
        base_url = getattr(inner, "base_url", "") or getattr(inner, "url", "") or ""
        ds = JiraDataSource(jira_client)
        config = await config_service.get_config(
            _CONNECTOR_CONFIG_PATH.format(connector_id=connector_id),
        )
        auth_config = (config or {}).get("auth") or {}
        _auth_types[connector_id] = auth_config.get("authType", "OAUTH")
        _data_sources[connector_id] = ds
        _is_cloud[connector_id] = resolve_is_cloud_api(connector_name, str(base_url))
        _unavailable_until.pop(connector_id, None)
        return ds
    except Exception as exc:
        logger.warning("Failed to set up the Jira client for connector %s: %s", connector_id, exc)
        return None


async def _discover_custom_fields(
    config_service: ConfigurationService,
    connector_id: str,
    connector_name: Connectors | str | None,
    ds: JiraDataSource | None = None,
) -> dict[str, str]:
    if ds is None:
        ds = await _get_data_source(config_service, connector_id, connector_name)
    if not ds:
        return {}
    return await discover_custom_field_ids(
        ds,
        is_cloud=_is_cloud.get(connector_id, False),
        connector_id=connector_id,
    )


async def enrich_ticket_llm_context(
    base_context: str,
    ticket: TicketRecord,
    config_service: ConfigurationService,
) -> str:
    """Fetch curated live Jira fields and merge into ticket LLM context.

    Uses connector service credentials (not the querying user) — may expose
    fields the user cannot see in the Jira UI.
    """
    if not ticket.connector_id or not ticket.external_record_id:
        return base_context
    if not is_jira_connector(ticket.connector_name):
        return base_context

    connector_id = str(ticket.connector_id)
    ds = await _get_data_source(config_service, connector_id, ticket.connector_name)
    if not ds:
        return base_context

    is_cloud = _is_cloud.get(connector_id, resolve_is_cloud_api(ticket.connector_name))
    custom_ids = await _discover_custom_fields(
        config_service, connector_id, ticket.connector_name, ds=ds,
    )
    issues_map = await batch_fetch_issues(
        ds,
        is_cloud=is_cloud,
        issue_ids=[str(ticket.external_record_id)],
        discovered_custom_ids=custom_ids,
    )
    issue = issues_map.get(str(ticket.external_record_id))
    if not issue:
        return base_context

    try:
        return enrich_from_issue(base_context, issue, custom_ids)
    except Exception as exc:
        logger.warning(
            "Jira enrichment format failed for ticket %s: %s",
            ticket.external_record_id,
            exc,
        )
        return base_context
