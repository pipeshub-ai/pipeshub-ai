"""Which web search provider a run uses, read from the org's stored config."""

from logging import Logger
from typing import TYPE_CHECKING, Any

from app.config.constants.service import config_node_constants

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService

def _normalized(provider: object) -> str:
    return str(provider or "").strip().lower()


# As the Node API stores them: off unless an admin turns them on, 3 per page when on.
DEFAULT_SEND_IMAGES = False
DEFAULT_MAX_IMAGES = 3
MAX_IMAGES_LIMIT = 500
# Images read from one page. The Node API stores up to MAX_IMAGES_LIMIT; past this a page's
# images cost more than its text and slow every read, so a larger stored count is read as this.
MAX_IMAGES_PER_PAGE = 50


def image_settings(stored: object) -> dict[str, Any]:
    """``{"includeImages", "maxImages"}`` from the stored ``settings``, each
    replaced by its default when missing or out of range, as the Node API reads them;
    ``maxImages`` is then at most ``MAX_IMAGES_PER_PAGE``."""
    settings = stored if isinstance(stored, dict) else {}
    include = settings.get("includeImages")
    max_images = _whole_number(settings.get("maxImages"))
    if max_images is None or not 1 <= max_images <= MAX_IMAGES_LIMIT:
        max_images = DEFAULT_MAX_IMAGES
    return {
        "includeImages": include if isinstance(include, bool) else DEFAULT_SEND_IMAGES,
        "maxImages": min(max_images, MAX_IMAGES_PER_PAGE),
    }


def _whole_number(value: object) -> int | None:
    """``value`` as a whole number as the Node API reads it (``Number(value)``): an int, an
    integral float or a string of one. A boolean is not a count."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            return None
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    return value if isinstance(value, int) else None


def _configuration(entry: dict[str, Any] | None) -> dict[str, Any]:
    configuration = (entry or {}).get("configuration")
    return configuration if isinstance(configuration, dict) else {}


def _selection(
    provider: str, entry: dict[str, Any] | None, entries: list[dict[str, Any]], settings: object = None,
) -> dict[str, Any]:
    """The chosen provider, plus every stored one in stored order (``providers``): the others
    are tried after it, and the ones that read pages back up the built-in fetcher. ``settings``
    are the org's web settings (``image_settings``)."""
    return {
        "provider": provider,
        "settings": image_settings(settings),
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
    configuration of that type, or an empty one when it isn't stored or the store can't be read. Without it, the org default;
    None when no provider is marked default (an admin has not set web search up) or the store can't be read. The provider
    name is passed through even when unknown, so the tool reports it instead of quietly using another.
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
    settings = stored.get("settings") if isinstance(stored, dict) else None

    if wanted:
        return _selection(
            wanted, next((p for p in entries if _normalized(p.get("provider")) == wanted), None), entries, settings,
        )

    default = next((p for p in entries if p.get("isDefault")), None)
    name = _normalized(default.get("provider")) if default else ""
    return _selection(name, default, entries, settings) if name else None
