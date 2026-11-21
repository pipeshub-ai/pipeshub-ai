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

# Schemas of the extra migration modules above that own SECURITY DEFINER functions. ``db.bootstrap_roles`` creates the vetted
# owner role ``pipeshub_vetted_<schema>`` of each, so the module migrates after the ordinary role setup with no extra flag.
# Keep ``deployment/docker-compose/postgres/extra-vetted-schemas`` (the Compose ``postgres`` profile's role script) in step.
EXTRA_VETTED_SCHEMAS: list[str] = []


def structured_logging_provider() -> object:
    """Logging provider override, or ``None`` for the default text logging.

    A function rather than a constant so that resolving a provider never runs at
    import time; see ``app.utils.structured_logging.get_provider``. Typed
    ``object`` so this module stays import-free; ``get_provider`` checks it.
    """
    return None


def check_runtime_settings() -> None:
    """Raise to refuse, at service start, a combination of settings this build does not run.

    Every Python service main calls it at load, before it builds its container
    or app, so it holds whatever ``edition_services`` supplies. OSS runs every
    combination, so it refuses nothing.
    """


def extra_edge_schemas() -> dict[str, dict]:
    """Arango schemas (collection name -> schema) of edge collections this build adds to the shared
    edge registry in ``app.schema.node_schema_registry``. None by default.

    This covers ArangoDB only: the Neo4j and PostgreSQL stores have no matching hook."""
    return {}
