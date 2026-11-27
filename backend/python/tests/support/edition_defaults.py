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


def agents_run_as_the_caller() -> bool:
    """Whether every agent run, a service-account agent's included, is the identified person asking: no service
    token starts one, and its tools list and run with that person's own credentials. Here not: a service-account
    agent runs as its creator, bounded by the person asking."""
    return False


async def verify_loop_caller(user: dict[str, object]) -> None:
    """What an agent-loop route verifies of the person asking before the loop runs, for a driver that calls the
    route handler or the loop below the route (``tests/contracts/golden_support.py``). Here nothing."""


def golden_contract_patches() -> tuple[tuple[str, object], ...]:
    """``(target, value)`` patches the contract recordings (``tests/contracts``) apply while they run a golden
    scenario, for a rule this build adds that the shared goldens do not record. Here none."""
    return ()
