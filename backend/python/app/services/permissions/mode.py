"""`AclFilterMode`: which permission filter a search uses. Legacy is the default and the only one that reads
the serving path today; `shadow` and `scopes` are for the shadow comparator and the pre-filter."""

from __future__ import annotations

import logging
import os
from enum import Enum
from typing import TYPE_CHECKING

from .errors import ViewerScopesUnavailable
from .metrics import SCOPES_UNAVAILABLE

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .scopes import ViewerScopes
    from .service import ViewerScopeService

ENV_ACL_FILTER_MODE = "ACL_FILTER_MODE"
ENV_SCOPE_FIELDS = "ACL_SCOPE_FIELDS"
_logger = logging.getLogger(__name__)


class AclFilterMode(str, Enum):
    LEGACY = "legacy"  # graph check only: container filter plus post-check
    SHADOW = "shadow"  # legacy answers; ViewerScopes are computed and compared on the side
    SCOPES = "scopes"  # ViewerScopes pre-filter, post-check still authoritative

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> AclFilterMode:
        """Unset, `off` or anything unrecognised is legacy: a typo never turns a new filter on."""
        raw = (env if env is not None else os.environ).get(ENV_ACL_FILTER_MODE, "").strip().lower()
        if raw in ("", "off", "legacy"):
            return cls.LEGACY
        try:
            return cls(raw)
        except ValueError:
            _logger.warning("%s=%r is not a mode; using legacy", ENV_ACL_FILTER_MODE, raw)
            return cls.LEGACY


async def scopes_or_legacy(
    mode: AclFilterMode, service: ViewerScopeService, org_id: str, user_key: str
) -> ViewerScopes | None:
    """The viewer's scopes, or None when the caller must use the legacy path: legacy mode (the service is not
    touched) or `ViewerScopesUnavailable` (counted). Never an empty or a wide scope in place of a failure."""
    if mode is AclFilterMode.LEGACY:
        return None
    try:
        return await service.scopes(org_id, user_key)
    except ViewerScopesUnavailable as exc:
        SCOPES_UNAVAILABLE.inc(exc.reason.split(":", 1)[0])
        return None


def scope_fields_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Whether writers stamp scope fields on vector points. Off by default and independent of the filter mode while it is
    legacy: the fields must be able to be written (and back-filled) before any mode other than legacy is switched on. A mode
    other than legacy implies the fields, since a filter that reads them needs them written."""
    source = env if env is not None else os.environ
    if source.get(ENV_SCOPE_FIELDS, "").strip().lower() in ("1", "true", "on", "yes"):
        return True
    return AclFilterMode.from_env(source) is not AclFilterMode.LEGACY
