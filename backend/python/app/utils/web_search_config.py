"""Org-level web-search provider resolution, shared by every caller that
needs to build the `web_search`/`fetch_url` tools.

`PipesHubToolLoader._build_dynamic_tools` gates those two tools entirely on
`tool_state["web_search_config"]` being truthy, so whoever assembles an
`AgentContext` must resolve this first. Three call sites did so with
near-identical private copies (the chat route, the chat-mode bridge, and the
provider-specific lookup); the scheduled/headless path had none at all, which
is why a scheduled task could never search the web.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.config.constants.service import config_node_constants

if TYPE_CHECKING:
    from logging import Logger

    from app.config.configuration_service import ConfigurationService

__all__ = [
    "SUPPORTED_WEB_SEARCH_PROVIDERS",
    "resolve_default_web_search_config",
    "resolve_web_search_config_for_provider",
]

SUPPORTED_WEB_SEARCH_PROVIDERS = frozenset({"duckduckgo", "serper", "tavily", "exa"})

_DUCKDUCKGO_CONFIG: dict[str, Any] = {"provider": "duckduckgo", "configuration": {}}


async def _load_providers(
    config_service: "ConfigurationService", logger: "Logger",
) -> list[dict[str, Any]] | None:
    """Stored provider list, or None when the config could not be read.

    None and `[]` mean different things to the callers below: an unreadable
    config must not be mistaken for "this org has configured nothing", which
    would silently hand every org the DuckDuckGo default during an etcd
    outage.
    """
    try:
        stored = await config_service.get_config(
            config_node_constants.WEB_SEARCH.value, default={}, use_cache=False,
        )
    except Exception as exc:
        logger.warning("Failed to load web search configuration: %s", exc)
        return None

    providers = stored.get("providers", []) if isinstance(stored, dict) else []
    if not isinstance(providers, list):
        return []
    return [p for p in providers if isinstance(p, dict)]


def _normalized_config(entry: dict[str, Any]) -> dict[str, Any] | None:
    provider = str(entry.get("provider", "")).strip().lower()
    if not provider or provider not in SUPPORTED_WEB_SEARCH_PROVIDERS:
        return None
    configuration = entry.get("configuration", {})
    return {
        "provider": provider,
        "configuration": configuration if isinstance(configuration, dict) else {},
    }


async def resolve_default_web_search_config(
    config_service: "ConfigurationService", logger: "Logger",
) -> dict[str, Any] | None:
    """The org's active web-search provider, or None if there isn't a usable one.

    Whenever no stored provider carries `isDefault: true` -- whether the org
    has never configured any provider or configured one without marking it
    default -- the Node.js layer treats DuckDuckGo as active (it clears the
    flags rather than inserting a DuckDuckGo entry; see
    `cm_controller.ts::getWebSearchProviders`). Reading the raw stored config
    without that fallback would disable web search for every org that hasn't
    paid for a provider, while the UI says DuckDuckGo is already on.
    """
    providers = await _load_providers(config_service, logger)
    if providers is None:
        return None

    default_provider = next((p for p in providers if p.get("isDefault")), None)
    if not default_provider:
        logger.debug("No explicit default web search provider; falling back to duckduckgo")
        return dict(_DUCKDUCKGO_CONFIG)

    return _normalized_config(default_provider)


async def resolve_web_search_config_for_provider(
    provider: str | None, config_service: "ConfigurationService", logger: "Logger",
) -> dict[str, Any] | None:
    """Config for one explicitly-chosen provider (an agent's `webSearch`
    attachment), rather than the org default.

    An unreadable config or a provider with no stored entry still yields
    `{provider, configuration: {}}`: the caller already decided this provider
    is in use, and providers needing no credentials (DuckDuckGo) work fine
    with an empty configuration.
    """
    if not provider:
        return None
    normalized = str(provider).strip().lower()
    if not normalized:
        return None

    providers = await _load_providers(config_service, logger)
    if providers is None:
        return {"provider": normalized, "configuration": {}}

    entry = next(
        (p for p in providers if str(p.get("provider", "")).strip().lower() == normalized),
        None,
    )
    if not entry:
        return {"provider": normalized, "configuration": {}}

    configuration = entry.get("configuration", {})
    return {
        "provider": normalized,
        "configuration": configuration if isinstance(configuration, dict) else {},
    }
