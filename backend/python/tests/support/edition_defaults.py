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
    token starts one, and its tools list and run with that person's own credentials (``app.edition``)."""
    from app import edition

    return edition.AGENTS_RUN_AS_THE_CALLER


async def verify_loop_caller(user: dict[str, object]) -> None:
    """What an agent-loop route verifies of the person asking before the loop runs, for a driver that calls the
    route handler or the loop below the route (``tests/harness_replay/legacy_driver.py``). Here nothing."""


def shipped_compose_files_extra() -> tuple[str, ...]:
    """Compose files in ``deployment/docker-compose/`` this build ships besides the shared ones, held to the shipped
    baseline (``tests/unit/harness/security/test_container_baseline.py``). Here none."""
    return ()


def compose_overlays_extra() -> tuple[str, ...]:
    """Compose files, by path from the repository root, this build ships as overlays on
    ``deployment/docker-compose/docker-compose.yml``, read over it as Compose merges them
    (``tests/unit/harness/security/test_sec_d_component_keys.py``). Here none."""
    return ()


def skill_candidates_are_their_learners_alone() -> bool:
    """Whether a learning-loop skill candidate is reached only by the person whose run learned it: no one else, an
    org admin included, lists or decides it, and a candidate with no recorded learner is no one's to decide. Here an
    org admin may decide any candidate."""
    return False


async def create_skill_edges(
    agent_key: str, skill_names: list[str], user_key: str, graph_provider: object, logger: object,
) -> list[str]:
    """``app.api.routes.agent._create_skill_edges`` in the bound org, as an agent create or update calls it."""
    from app.api.routes.agent import _create_skill_edges

    return await _create_skill_edges(agent_key, skill_names, user_key, graph_provider, logger)  # type: ignore[arg-type]


def neo4j_untouched_writers_extra() -> dict[str, str]:
    """Cypher writers of this build's Neo4j provider that cannot change a hierarchy edge's children order stamps, and
    why (``UNTOUCHED`` in ``tests/unit/services/graph_db/test_neo4j_children_order_writers.py``). Here none."""
    return {}


def builtin_pack_digests_extra() -> dict[str, dict[str, str]]:
    """Released versions of the built-in skill packs this build ships besides the shared record
    (``tests/unit/agents/agent_loop/skills/fixtures/builtin_pack_digests.json``), ``{pack: {version: digest}}``.
    Here none."""
    return {}
