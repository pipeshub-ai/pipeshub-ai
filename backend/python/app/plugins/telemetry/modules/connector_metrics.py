"""Connector current-state metrics, refreshed from the graph DB on a tick
(see connectors_main).
"""

import threading
import time

from app.plugins.telemetry.backend import METRICS_BACKEND

# Gauge is current state, so clearing + re-setting each refresh (to drop removed
# connectors) is correct.
CONNECTOR_ACTIVE = METRICS_BACKEND.gauge(
    "pipeshub_connector_active",
    "Number of active connector instances by connector type",
    ["connector", "domain"],
)


def set_connector_active(counts: dict) -> None:
    """Replace the connector_active series with the current per-type counts.

    ``counts`` keys may be a plain ``connector`` string (domain reported as
    ``"unknown"``) or a ``(connector, domain)`` tuple when the org email-domain
    is known, so dashboards can exclude internal (PipesHub) orgs.
    """
    CONNECTOR_ACTIVE.clear()
    for key, count in counts.items():
        if isinstance(key, tuple):
            connector, domain = (key[0] or "unknown"), (key[1] or "unknown")
        else:
            connector, domain = (key or "unknown"), "unknown"
        CONNECTOR_ACTIVE.set(connector, domain, value=count)


# ``pipeshub.usage.records`` of the usage contract. Counts arrive one org at a time, so the last count of each org
# is kept and the series are rebuilt from them: an org past the cap is added into ``other`` rather than given a
# series of its own, and an org not counted again for a day (deleted, or the count switched off) drops out.
RECORDS = METRICS_BACKEND.gauge(
    "pipeshub_usage_records",
    "Records the install holds, by connector type and indexing status",
    ["connector_type", "indexing_status", "org", "domain"],
)
OTHER = "other"
MAX_ORGS = 500
STALE_AFTER_SECONDS = 24 * 60 * 60

_records: dict[str, tuple[float, str, dict[tuple[str, str], int]]] = {}
_records_lock = threading.Lock()


def set_records(org_id: str, domain: str, counts: dict, *, complete: bool = True) -> None:
    """Replace one org's record counts (``(connector type, indexing status) -> count``) and rebuild the gauge.

    A count that is not ``complete`` is still the best known, so it is shown like any other.
    """
    now = time.monotonic()
    with _records_lock:
        _records[org_id] = (now, domain or "unknown", {(str(c), str(s)): int(n) for (c, s), n in counts.items()})
        for stale in [org for org, (seen, _d, _c) in _records.items() if now - seen > STALE_AFTER_SECONDS]:
            del _records[stale]
        series: dict[tuple[str, str, str, str], int] = {}
        # sorted: which orgs keep their own series must not depend on arrival order
        for index, org in enumerate(sorted(_records)):
            _seen, org_domain, org_counts = _records[org]
            labels = (org, org_domain) if index < MAX_ORGS else (OTHER, OTHER)
            for (connector, status), count in org_counts.items():
                key = (connector, status, *labels)
                series[key] = series.get(key, 0) + count
        RECORDS.clear()
        for key, count in series.items():
            RECORDS.set(*key, value=count)


def forget_records() -> None:
    """Drop every org's kept count. For tests."""
    with _records_lock:
        _records.clear()
        RECORDS.clear()
