"""Edition constants that must stay import-free.

This is the only edition switch that ``app.telemetry`` may import: the metrics
backend is chosen at import time, and ``app.edition_config`` pulls in routers
that import telemetry, so importing it from there would cycle.
"""

# Used when TELEMETRY_COLLECTION_FLAG is unset.
TELEMETRY_DEFAULT_ENABLED = True

# The admin's Data Collection switch before an admin has set it.
METRICS_CONSENT_DEFAULT = True

# Extra migration module directories, relative to ``backend/`` (e.g. ``"db/extra/usage"``), added to the
# ones under ``backend/db/versions/``. Read by ``backend/db`` by file path: keep this module import-free.
EXTRA_MIGRATION_LOCATIONS: list[str] = []


def structured_logging_provider() -> object:
    """Logging provider override, or ``None`` for the default text logging.

    A function rather than a constant so that resolving a provider never runs at
    import time; see ``app.utils.structured_logging.get_provider``. Typed
    ``object`` so this module stays import-free; ``get_provider`` checks it.
    """
    return None


def sandbox_org_share() -> float | None:
    """Share of the code sandbox slots one org may hold at once, or ``None``
    for no per-org cap (one org may use every slot).

    ``SANDBOX_MAX_PER_ORG`` wins when set. Otherwise the cap is this share of
    the current limit, and never below 2 slots.
    """
    return None


def check_runtime_settings() -> None:
    """Raise to refuse, at service start, a combination of settings this build does not run.

    Every Python service main calls it at load, before it builds its container
    or app, so it holds whatever ``edition_services`` supplies. OSS runs every
    combination, so it refuses nothing.
    """


def extra_node_collections() -> dict[str, tuple[tuple[str, ...], ...]]:
    """Document collections this build adds to the graph schema: name -> indexes, each a tuple of field names.
    ``ensure_schema`` creates them on ArangoDB (each collection and its persistent indexes) and on Neo4j (a unique
    ``id`` constraint per collection, which keeps ``MERGE`` an index seek, and each declared index; a node keeps its
    key as ``id`` there, so a declared ``_key`` is indexed as ``id``). Here there are none."""
    return {}
