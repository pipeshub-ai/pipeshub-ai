from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any

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
from app.connectors.sources.atlassian.jira.enrichment.value_formatter import (
    enrich_from_issue,
    key_only_refs,
    referenced_issue_ids,
    restrict_cross_issue_refs,
)
from app.models.entities import RecordType, TicketRecord
from app.sources.client.jira.jira import JiraClient, JiraConfigUnavailableError
from app.sources.external.jira.jira import JiraDataSource
from app.utils.logger import create_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

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

_PARENT_ID_LINE = re.compile(r"^External Parent ID: .*$", re.MULTILINE)
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
    readable_issue_ids: Callable[[str, set[str]], Awaitable[set[str]]] | None = None,
) -> str:
    """Fetch curated live Jira fields and merge into ticket LLM context.

    Uses connector service credentials, not the querying user. Jira already
    leaves out fields hidden by the issue's field configuration, but other
    issues named in the response (parent, links, sub-tasks) may not be readable
    by the viewer: ``readable_issue_ids(connector_id, issue_ids)`` says which
    are. Without it, or if it fails, no other issue is named.
    """
    if not is_jira_connector(ticket.connector_name):
        return base_context
    # The base context names the parent by id; show it only if the viewer can read it.
    parent_id = str(ticket.parent_external_record_id or "")
    full_base = base_context
    if parent_id:
        base_context = _PARENT_ID_LINE.sub("External Parent ID: N/A", base_context)
    if not ticket.connector_id or not ticket.external_record_id:
        return base_context

    # Any failure from here returns the redacted context, never the caller's unredacted one.
    try:
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

        readable: set[str] = set()
        # Resolve key-only references (epic link, parent link) to ids in one lookup,
        # so one access check below covers every issue this ticket names.
        key_ids: dict[str, str] = {}
        keys = set(key_only_refs(issue, custom_ids).values())
        if keys:
            try:
                found = await batch_fetch_issues(
                    ds, is_cloud=is_cloud, issue_ids=sorted(keys),
                    discovered_custom_ids={}, by_key=True,
                )
                key_ids = {str(i["key"]): str(i["id"]) for i in found.values() if i.get("key")}
            except Exception as exc:
                logger.warning("Jira key lookup failed; naming no key-only references: %s", exc)
        refs = (
            referenced_issue_ids(issue)
            | ({parent_id} if parent_id else set())
            | set(key_ids.values())
        )
        if refs and readable_issue_ids is not None:
            try:
                readable = set(await readable_issue_ids(connector_id, refs))
            except Exception as exc:
                logger.warning("Jira issue access check failed; naming no other issues: %s", exc)
        if parent_id in readable:
            base_context = full_base
        issue = restrict_cross_issue_refs(issue, readable, custom_ids, key_ids)
        return enrich_from_issue(base_context, issue, custom_ids)
    except Exception as exc:
        logger.warning(
            "Jira enrichment failed for ticket %s: %s",
            ticket.external_record_id,
            exc,
        )
        return base_context
