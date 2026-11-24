"""The code-graph contract tests of the other backends (``test_code_graph_real_backends``), run unchanged on one PostgreSQL org.

The functions are imported, so they are collected here with a ``repo`` fixture that is PostgreSQL: code files are written as production
writes them (records, block projection, the real edge builder) and every provider method the code graph uses is checked against them. The two
tests of the other module that read an Arango or Neo4j index are not run here.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import logging
import uuid

import pytest

from tests.integration.graph_db import test_code_graph_real_backends as cg
from tests.integration.graph_db.postgres.pg_support import (
    OrgBound,
    StandInProvider,
    new_org,
)
from tests.integration.graph_db.test_code_graph_real_backends import (  # noqa: F401
    test_a_code_group_is_stamped_with_its_synced_commit_and_read_back,
    test_a_deleted_files_names_are_kept_and_served_once,
    test_a_rebuild_replaces_edges_in_place_and_keeps_their_creation_time,
    test_an_agent_tool_answers_from_the_built_graph,
    test_counts_and_existence,
    test_delete_by_source_keeps_edges_of_the_excluded_build,
    test_deleting_a_files_blocks,
    test_deleting_edges_touching_a_block,
    test_path_prefix_and_name_lookups,
    test_records_updated_since_and_file_paths,
    test_settle_is_a_compare_and_set,
    test_the_builder_writes_the_cross_file_call,
    test_the_edge_rollup_by_file_prefix,
    test_the_name_folded_backfill,
)

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]


@pytest.fixture
async def repo(pg_db) -> cg._Repo:
    graph = OrgBound(StandInProvider(logging.getLogger("pg-graph"), pg_db), new_org())
    run = uuid.uuid4().hex[:10]
    connector_id, group_id = f"conn-cg-{run}", f"g-cg-{run}"
    record_ids, blobs = await cg._write_repo(graph, graph.org, connector_id, group_id)
    return cg._Repo(graph, "postgres", graph.org, connector_id, group_id, record_ids, blobs)
