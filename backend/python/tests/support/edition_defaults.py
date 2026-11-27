"""Values the shared tests read here instead of hard-coding, where a build of the
product may differ. A build that differs replaces this one file."""

from types import SimpleNamespace

# The keys fetch_full_record gives a SQL table's foreign-key neighbours under.
FK_PARENT_FIELD = "fk_parent_record_ids"
FK_CHILD_FIELD = "fk_child_record_ids"


def postgres_sync_state() -> dict[str, object]:
    """What PostgreSQLConnector keeps for the sync in progress, which its __init__ sets."""
    from app.connectors.sources.postgres.connector import SyncOutcome

    return {"sync_stats": SimpleNamespace(errors=0), "sync_outcome": SyncOutcome()}


def unit_test_fixtures() -> dict[str, object]:
    """Pytest fixtures, by name, that every unit test gets on top of ``tests/unit/conftest.py``.

    The unit conftest binds each one into its own namespace, so pytest finds them as if
    they were defined there; an autouse fixture applies to every unit test. A build that
    needs extra per-test setup or clean-up returns its fixtures here. Here there are none.
    """
    return {}


def worker_stages_extra() -> dict[str, bool]:
    """Labs flags this build pins beside the shared worker stages (Node's ``EDITION_WORKER_STAGES``), at the value
    a rig runs with: a legacy turn's resolved header carries them, so the turn reads none of them from the settings.
    Here there are none."""
    return {}


def agent_turn_live_settings_reads_extra() -> int:
    """How many more times an agent turn without a resolved header reads the settings document live in this build
    (its own flags, read where the shared ones are). Here none."""
    return 0


# The share of the code sandbox slots one org may hold (app.edition.sandbox_org_share).
SANDBOX_ORG_SHARE: float | None = None


def parser_identity_writers_extra() -> dict[tuple[str, str], str]:
    """Writers of ``hasIndexedCopy`` this build adds, ``(file under app/, function) -> why it writes no parser
    identity``, for ``tests/unit/modules/indexing/test_parser_identity_writers.py``. Here there are none."""
    return {}


def optional_plugin_packages() -> tuple[str, ...]:
    """Every package a build may leave out, relative to ``backend/python``: each plug-in and whatever exists only
    for one. The plug-in tests delete them all for the build with no plug-in, and let only them hold a client library."""
    return ("app/plugins/telemetry",)


def default_plugins() -> list[str]:
    """What the checked-in ``app/plugins_manifest.py`` lists."""
    return ["app.plugins.telemetry"]


def extra_platform_events() -> dict[str, object]:
    """Platform events this build's own modules announce besides ``app.platform_events.catalog``: name -> ``Event``.
    ``tests/unit/platform_events/test_catalog.py`` checks every emit against both."""
    return {}


def extra_platform_event_fields() -> dict[str, str]:
    """What the fields of those events mean, where ``catalog.FIELDS`` does not already say: field -> description."""
    return {}


# Catalogued events that no code in this tree announces.
PLATFORM_EVENTS_NOT_EMITTED_HERE: frozenset[str] = frozenset()


def allowed_usage_collection_mentions() -> dict[tuple[str, str], str]:
    """Lines that still say "telemetry" in a tree built with no plug-in, besides the shared ones in
    ``tests/unit/plugins/test_stripped_build.py``: (file, text on the line) -> why it stays."""
    return {}
