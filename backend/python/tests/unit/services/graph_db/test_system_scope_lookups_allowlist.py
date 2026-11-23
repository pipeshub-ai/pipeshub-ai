"""The system-scope user lookup is reachable only from the maintenance migration that needs it.

`get_graph_user_keys_by_mongo_user_ids_all_orgs` has no organisation term. Every request path uses the org-bound
`get_graph_user_keys_by_mongo_user_ids`. A new caller of the system-scope one fails this test until it is reviewed
and added to ALLOWED.
"""

from __future__ import annotations

import pathlib

APP = pathlib.Path(__file__).resolve().parents[4] / "app"
NAME = "get_graph_user_keys_by_mongo_user_ids_all_orgs"
ALLOWED = {"migrations/service_account_kind_migration.py"}
DEFINED_IN = {
    "services/graph_db/interface/graph_db_provider.py",
    "services/graph_db/arango/arango_http_provider.py",
    "services/graph_db/neo4j/neo4j_provider.py",
    "services/graph_db/postgres/identity.py",
}


def test_only_the_migration_calls_the_system_scope_lookup() -> None:
    users = {str(p.relative_to(APP)) for p in APP.rglob("*.py") if NAME in p.read_text(encoding="utf-8")}
    assert users == ALLOWED | DEFINED_IN


def test_no_request_route_or_tool_names_it() -> None:
    for p in (APP / "api").rglob("*.py"):
        assert NAME not in p.read_text(encoding="utf-8"), p
