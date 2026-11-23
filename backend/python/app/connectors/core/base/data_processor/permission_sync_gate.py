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
from datetime import datetime, timezone
from typing import Any

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import Connectors
from app.connectors.core.registry.connector_builder import ConnectorScope

# Written by the Node backend once it has checked the licence key: {"allowed": bool, "until": ISO time or null, ...}.
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


# Connectors this process limited since the licence watcher last looked (``note_limited``, ``take_limited``).
# shortcut: in memory, per process; every connectors process runs its own watcher, so the process that limited queues.
# A process that dies before its next check leaves it to the first check after a restart, which scans in full.
_limited_since_last_check: set[str] = set()


def note_limited(connector_id: str) -> None:
    """A connector was limited: the watcher (``licence_lift.watch_licence``) looks again even while the install stays
    licensed, and gives it its queued full syncs back. Without it, a connector limited by a run that ended after a
    licensed one took over was never queued for the full sync that lifts it."""
    _limited_since_last_check.add(connector_id)


def take_limited() -> set[str]:
    """The connectors limited since the last call, and forget them."""
    taken = set(_limited_since_last_check)
    _limited_since_last_check.difference_update(taken)
    return taken


class AccessLimitOwed(RuntimeError):
    """A permission write was skipped inside a store transaction on a connector whose limit is not set yet (its licence
    lapsed during the run). The limit is not written there, which would hold a second connection; the write fails, and
    the limit is set before the next transaction or at the end of the run."""


async def permission_sync_licensed(config_service: ConfigurationService) -> bool:
    """Whether the install is licensed, read at most once per ``LICENCE_MAX_AGE_SECONDS`` (a missing key included).
    Licensed means no key at all, or ``{"allowed": true}``; any other stored value (not a dict, or ``allowed`` not
    exactly ``true``) is not licensed. A failed read raises and is not remembered: answered as "no key", it would
    count as licensed and copy source access again. An ``until`` already behind the clock is not licensed either."""
    now = time.monotonic()
    answer = _licence_answers.get(config_service)
    if answer is not None and now - answer[0] <= LICENCE_MAX_AGE_SECONDS:
        return answer[1]
    value = await config_service.get_config(
        PERMISSION_SYNC_LICENCE_KEY, use_cache=True, raise_on_error=True, max_age_seconds=LICENCE_MAX_AGE_SECONDS
    )
    # shortcut: only a missing key means licensed, so nothing changes before licence keys exist; the major release
    # that turns the check on makes a missing key mean "not licensed". A value that is there but malformed is not.
    licensed = value is None or (
        isinstance(value, dict) and value.get("allowed") is True and not _until_passed(value.get("until"))
    )
    _licence_answers[config_service] = (now, licensed)
    return licensed


def _until_passed(until: object) -> bool:
    """Whether the answer's ``until`` is behind the clock, so an answer left by a Node that stopped checking lapses on
    its own. ``None`` (no end given) has not passed; anything that is not an ISO time with a zone counts as passed."""
    if until is None:
        return False
    try:
        at = datetime.fromisoformat(until) if isinstance(until, str) else None
    except ValueError:
        return True
    return at is None or at.tzinfo is None or at <= datetime.now(timezone.utc)


def gated(app: dict[str, Any] | None) -> bool:
    """For an install without a licence: whether this connector (its app document) must not copy access from its
    source."""
    return (
        app is not None
        and str(app.get("scope") or "").lower() == ConnectorScope.TEAM.value
        and str(app.get("type") or "").upper() not in OPEN_CONNECTOR_TYPES
    )
