"""Maps a connection's forwarding mode to the forwarder that renders it.

New gateways are added by registering a forwarder, not by editing the hook. An
edition adds its own (for example signed-JWT mode) through the
``register_identity_forwarders`` edition hook.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.services.model_identity.forwarders.litellm_headers import (
    LiteLLMHeadersForwarder,
)
from app.services.model_identity.forwarders.template_headers import (
    TemplateHeadersForwarder,
)

if TYPE_CHECKING:
    from app.services.model_identity.forwarders.base import IdentityForwarder
    from app.services.model_identity.settings import ConnectionForwardingSettings

logger = logging.getLogger(__name__)


class ForwarderRegistry:
    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str | None], IdentityForwarder] = {}

    def register(
        self, mode: str, forwarder: IdentityForwarder, *, preset: str | None = None
    ) -> None:
        """Register ``forwarder`` for ``mode``, optionally only for one preset."""
        self._by_key[(mode, preset)] = forwarder

    def get(self, settings: ConnectionForwardingSettings) -> IdentityForwarder | None:
        return self._by_key.get((settings.mode, settings.preset)) or self._by_key.get(
            (settings.mode, None)
        )


def build_default_registry() -> ForwarderRegistry:
    registry = ForwarderRegistry()
    registry.register("headers", LiteLLMHeadersForwarder())
    registry.register("template", TemplateHeadersForwarder())
    return registry


_registry: ForwarderRegistry | None = None


def get_forwarder_registry() -> ForwarderRegistry:
    """The process registry: OSS forwarders plus whatever the edition adds."""
    global _registry  # noqa: PLW0603 - a lazily built process-wide singleton
    if _registry is None:
        registry = build_default_registry()
        _register_edition_forwarders(registry)
        _registry = registry
    return _registry


def reset_forwarder_registry() -> None:
    global _registry  # noqa: PLW0603 - a lazily built process-wide singleton
    _registry = None


def _register_edition_forwarders(registry: ForwarderRegistry) -> None:
    try:
        from app import edition_services

        hook = getattr(edition_services, "register_identity_forwarders", None)
        if hook is not None:
            hook(registry)
    except Exception:
        # An edition hook that fails must not take model calls down: the
        # connections that needed it fall back to no forwarding, which is logged.
        logger.warning("edition identity forwarders could not be registered", exc_info=True)
