"""Which web search provider a run uses, read from the org's stored config."""

from logging import Logger
from typing import TYPE_CHECKING, Any

from app.config.constants.service import config_node_constants

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService

# Never stored: the config API shows it as the default whenever no stored provider is marked
# default, including for an org that has configured nothing.
DEFAULT_PROVIDER = "duckduckgo"


def _normalized(provider: object) -> str:
    return str(provider or "").strip().lower()


def _configuration(entry: dict[str, Any] | None) -> dict[str, Any]:
    configuration = (entry or {}).get("configuration")
    return configuration if isinstance(configuration, dict) else {}


def _selection(provider: str, entry: dict[str, Any] | None, entries: list[dict[str, Any]]) -> dict[str, Any]:
    """The chosen provider, plus every stored one in stored order (``providers``): the others
    are tried after it, and the ones that read pages back up the built-in fetcher."""
    return {
        "provider": provider,
        "configuration": _configuration(entry),
        "providers": [
            {"provider": _normalized(e.get("provider")), "configuration": _configuration(e)}
            for e in entries
            if e is not entry and _normalized(e.get("provider"))
        ],
    }


async def resolve_web_search(
    config_service: "ConfigurationService",
    logger: Logger,
    *,
    provider: str | None = None,
    use_cache: bool = False,
) -> dict[str, Any] | None:
    """``{"provider", "configuration"}`` for a run, or None when web search can't be set up.

    With ``provider`` (an agent's own attachment, stored as a provider name), the first stored
    configuration of that type, or an empty one when it isn't stored or the store can't be read. Without it, the org default, or
    DuckDuckGo when none is marked default; None when the store can't be read. The provider name
    is passed through even when unknown, so the tool reports it instead of quietly using another.
    """
    wanted = _normalized(provider)
    try:
        stored = await config_service.get_config(
            config_node_constants.WEB_SEARCH.value, default={}, use_cache=use_cache,
        )
    except Exception as e:  # any store failure means "not configured" here
        logger.warning("Failed to load web search configuration: %s", e)
        return _selection(wanted, None, []) if wanted else None

    providers = stored.get("providers") if isinstance(stored, dict) else None
    entries = [p for p in providers if isinstance(p, dict)] if isinstance(providers, list) else []

    if wanted:
        return _selection(wanted, next((p for p in entries if _normalized(p.get("provider")) == wanted), None), entries)

    default = next((p for p in entries if p.get("isDefault")), None)
    if default is None:
        return _selection(DEFAULT_PROVIDER, None, entries)
    return _selection(_normalized(default.get("provider")) or DEFAULT_PROVIDER, default, entries)
