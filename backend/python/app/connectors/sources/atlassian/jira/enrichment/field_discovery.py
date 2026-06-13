from __future__ import annotations

from typing import Any

from app.connectors.sources.atlassian.jira.enrichment.field_registry import (
    CUSTOM_FIELD_SCHEMAS,
    SKIPPED_CUSTOM_SCHEMAS,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.sources.external.jira.jira import JiraDataSource
from app.utils.logger import create_logger

logger = create_logger("jira_field_discovery")

_discovery_cache: dict[str, dict[str, str]] = {}


def _field_schema_custom(field_def: dict[str, Any]) -> str | None:
    schema = field_def.get("schema") or {}
    custom = schema.get("custom")
    return str(custom) if custom else None


def _matches_schema_rule(field_def: dict[str, Any], rule: str | dict[str, Any]) -> bool:
    if isinstance(rule, str):
        return _field_schema_custom(field_def) == rule
    schema_custom = rule.get("schema")
    if _field_schema_custom(field_def) != schema_custom:
        return False
    name_in = rule.get("name_in")
    if name_in:
        name = field_def.get("name") or field_def.get("untranslatedName") or ""
        return name in name_in
    return True


def _parse_fields_response(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [f for f in payload if isinstance(f, dict)]
    return []


async def discover_custom_field_ids(
    data_source: JiraDataSource,
    *,
    is_cloud: bool,
    connector_id: str,
) -> dict[str, str]:
    """Map CUSTOM_FIELD_SCHEMAS keys to ``customfield_XXXXX`` ids for this connector."""
    cached = _discovery_cache.get(connector_id)
    if cached is not None:
        return cached

    if is_cloud:
        response = await data_source.get_fields()
    else:
        response = await data_source.get_fields_v2()

    if response.status != HttpStatusCode.OK.value:
        logger.warning(
            "Jira field discovery failed for connector %s: HTTP %s",
            connector_id,
            response.status,
        )
        return {}

    fields_list = _parse_fields_response(response.json())
    discovered: dict[str, str] = {}

    for registry_key, rules in CUSTOM_FIELD_SCHEMAS.items():
        rule_list = rules if isinstance(rules, list) else [rules]
        for field_def in fields_list:
            schema_custom = _field_schema_custom(field_def)
            if not schema_custom or schema_custom in SKIPPED_CUSTOM_SCHEMAS:
                continue
            if any(_matches_schema_rule(field_def, rule) for rule in rule_list):
                field_id = field_def.get("id") or field_def.get("key")
                if field_id:
                    discovered[registry_key] = str(field_id)
                    break

    _discovery_cache[connector_id] = discovered
    return discovered
