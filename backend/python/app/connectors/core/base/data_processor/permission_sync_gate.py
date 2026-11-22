"""Whether a connector may copy access from its source.

Copying who-can-see-what from a source is the paid line: without a licence key, a
company-wide connector is limited to its creator (``AccessLimit``, set by the
processor at sync start and by the registry at startup), and the access it holds is
left as it was until a full sync with a key has rewritten it. Storage and database
connectors read no access from their source, so they stay open to the whole company
either way, and personal connectors are owner-only in both editions.
"""

import time
import weakref
from typing import Any

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import Connectors
from app.connectors.core.registry.connector_builder import ConnectorScope

# Written by the Node backend once it has checked the licence key: {"allowed": bool}.
PERMISSION_SYNC_LICENCE_KEY = "/services/licence/permissionSync"
LICENCE_MAX_AGE_SECONDS = 300
# The answer per config service, with when it was read. The config cache keeps no entry for a missing key, so without
# this every permission write (several per record) would read the store while no licence is configured.
_licence_answers: "weakref.WeakKeyDictionary[ConfigurationService, tuple[float, bool]]" = weakref.WeakKeyDictionary()

OPEN_CONNECTOR_TYPES = frozenset(c.value for c in (
    Connectors.S3, Connectors.MINIO, Connectors.GCS, Connectors.AZURE_BLOB, Connectors.AZURE_FILES,
    Connectors.SMB, Connectors.CIFS, Connectors.POSTGRESQL, Connectors.MARIADB, Connectors.SNOWFLAKE,
    Connectors.WEB, Connectors.RSS,
    # Not a source: collections, whose access is given in PipesHub itself.
    Connectors.KNOWLEDGE_BASE,
))


async def permission_sync_licensed(config_service: ConfigurationService) -> bool:
    """Whether the install is licensed, read at most once per ``LICENCE_MAX_AGE_SECONDS`` (a missing key included).
    A failed read raises and is not remembered: answered as "no key", it would count as licensed and copy source
    access again."""
    now = time.monotonic()
    answer = _licence_answers.get(config_service)
    if answer is not None and now - answer[0] <= LICENCE_MAX_AGE_SECONDS:
        return answer[1]
    value = await config_service.get_config(
        PERMISSION_SYNC_LICENCE_KEY, use_cache=True, raise_on_error=True, max_age_seconds=LICENCE_MAX_AGE_SECONDS
    )
    # shortcut: no key means licensed, so nothing changes before licence keys exist; the major release
    # that turns the check on makes a missing key mean "not licensed".
    licensed = not isinstance(value, dict) or value.get("allowed") is True
    _licence_answers[config_service] = (now, licensed)
    return licensed


def gated(app: dict[str, Any] | None) -> bool:
    """For an install without a licence: whether this connector (its app document) must not copy access from its
    source."""
    return (
        app is not None
        and str(app.get("scope") or "").lower() == ConnectorScope.TEAM.value
        and str(app.get("type") or "").upper() not in OPEN_CONNECTOR_TYPES
    )
