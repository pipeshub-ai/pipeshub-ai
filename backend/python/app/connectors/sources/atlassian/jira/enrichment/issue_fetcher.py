from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from app.connectors.sources.atlassian.jira.enrichment.field_registry import build_search_field_list
from app.config.constants.arangodb import Connectors
from app.config.constants.http_status_code import HttpStatusCode
from app.sources.external.jira.jira import JiraDataSource
from app.utils.logger import create_logger

logger = create_logger("jira_issue_fetcher")

BATCH_ID_CHUNK_SIZE = 50


def _hostname_from_url(url: str) -> str:
    if not url:
        return ""
    normalized = url if "://" in url else f"https://{url}"
    return (urlparse(normalized).hostname or "").lower()


def is_jira_cloud_base_url(base_url: str) -> bool:
    if not base_url:
        return False
    normalized = base_url if "://" in base_url else f"https://{base_url}"
    parsed = urlparse(normalized)
    if (parsed.hostname or "").lower() != "api.atlassian.com":
        return False
    return "/ex/jira" in (parsed.path or "")


def _is_atlassian_cloud_site_url(base_url: str) -> bool:
    hostname = _hostname_from_url(base_url)
    return hostname.endswith(".atlassian.net")


def resolve_is_cloud_api(
    connector_name: Connectors | str | None,
    base_url: str = "",
) -> bool:
    """Pick Cloud v3 vs DC v2 endpoints from connector type, with URL fallback."""
    if connector_name is not None:
        if isinstance(connector_name, str):
            try:
                connector_name = Connectors(connector_name)
            except ValueError:
                connector_name = None
        if connector_name in (Connectors.JIRA, Connectors.JIRA_PERSONAL):
            return True
        if connector_name in (Connectors.JIRA_DATA_CENTER, Connectors.JIRA_DATA_CENTER_PERSONAL):
            return False
    if is_jira_cloud_base_url(base_url):
        return True
    return _is_atlassian_cloud_site_url(base_url)


def _chunk_ids(issue_ids: list[str], chunk_size: int) -> list[list[str]]:
    if not issue_ids:
        return []
    return [issue_ids[i : i + chunk_size] for i in range(0, len(issue_ids), chunk_size)]


def _build_jql_for_ids(issue_ids: list[str]) -> str:
    quoted = ", ".join(str(i) for i in issue_ids)
    return f"id in ({quoted})"


async def batch_fetch_issues(
    data_source: JiraDataSource,
    *,
    is_cloud: bool,
    issue_ids: list[str],
    discovered_custom_ids: dict[str, str],
) -> dict[str, dict[str, Any]]:
    """Fetch issues by numeric id in one (or chunked) JQL search per connector."""
    if not issue_ids:
        return {}

    unique_ids = list(dict.fromkeys(str(i) for i in issue_ids if i))
    fields = build_search_field_list(discovered_custom_ids)
    result: dict[str, dict[str, Any]] = {}

    for id_chunk in _chunk_ids(unique_ids, BATCH_ID_CHUNK_SIZE):
        jql = _build_jql_for_ids(id_chunk)
        try:
            if is_cloud:
                response = await data_source.search_and_reconsile_issues_using_jql_post(
                    jql=jql,
                    fields=fields,
                    maxResults=len(id_chunk),
                )
            else:
                response = await data_source.search_issues_post_v2(
                    jql=jql,
                    fields=fields,
                    maxResults=len(id_chunk),
                    startAt=0,
                )
        except Exception as exc:
            logger.warning("Jira batch search failed: %s", exc)
            continue

        if response.status != HttpStatusCode.OK.value:
            logger.warning("Jira batch search HTTP %s: %s", response.status, response.text())
            continue

        try:
            payload = response.json()
        except Exception as exc:
            logger.warning("Jira batch search JSON parse failed: %s", exc)
            continue

        issues = payload.get("issues") if isinstance(payload, dict) else None
        if not isinstance(issues, list):
            continue

        for issue in issues:
            if isinstance(issue, dict) and issue.get("id"):
                result[str(issue["id"])] = issue

    return result
