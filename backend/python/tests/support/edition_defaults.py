"""Values the shared tests read here instead of hard-coding, where a build of the
product may differ. A build that differs replaces this one file."""

from types import SimpleNamespace

# The keys fetch_full_record gives a SQL table's foreign-key neighbours under.
FK_PARENT_FIELD = "fk_parent_record_ids"
FK_CHILD_FIELD = "fk_child_record_ids"


# The extra migration modules the shipped edition file registers (app/edition.py: EXTRA_MIGRATION_LOCATIONS, relative to backend/) and the
# schemas among them that own SECURITY DEFINER functions (EXTRA_VETTED_SCHEMAS). The database tests compare them with the file.
SHIPPED_MIGRATION_LOCATIONS: list[str] = []
SHIPPED_VETTED_SCHEMAS: list[str] = []


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


# The share of the code sandbox slots one org may hold (app.edition.sandbox_org_share).
SANDBOX_ORG_SHARE: float | None = None


def org_context_baseline_extra() -> dict[str, dict[str, int]]:
    """Per-file counts of the org-context guards (``{guard: {file: count}}``) that a build adds to
    ``tests/unit/core/org_context_baseline.json``: files only that build has, and shared files it extends.
    A listed count replaces the shared one. None by default."""
    return {}


def hand_kept_file_type_lists_extra() -> dict[str, tuple[int, str]]:
    """Entries a build adds to ``ALLOWED`` in ``tests/unit/config/file_types/test_single_source.py``
    (path under ``app/`` -> (count, reason)). None by default."""
    return {}


def record_diagnostic_exemptions_extra() -> frozenset[str]:
    """Node source files (paths under ``backend/nodejs/apps/src``) that may name a record processing diagnostic
    field for an unrelated purpose, which ``test_no_node_code_names_the_fields_yet`` then skips. None by default."""
    return frozenset()


def connector_service_keeps_an_entity_store() -> bool:
    """Whether the connector service's container defines ``entity_vector_store``."""
    return False


def structural_writer_exemptions_extra() -> dict[tuple[str, str], str]:
    """Callers outside the graph store that a build adds to ``_OUTSIDE_THE_STORE`` in
    ``tests/unit/connectors/test_structural_writers.py`` ((path, call) -> reason it may skip the structure
    generation). None by default."""
    return {}


def graph_provider_dirs_extra() -> tuple[str, ...]:
    """Directories under ``app/`` (with a trailing slash) holding a build's own graph providers, which
    ``tests/unit/services/permissions/test_permission_writers_bump.py`` leaves out as it does the shared ones.
    None by default."""
    return ()
