"""The system-scope user lookup is reachable only from the maintenance migration that needs it.

`get_graph_user_keys_by_mongo_user_ids_all_orgs` has no organisation term. Every request path uses the org-bound
`get_graph_user_keys_by_mongo_user_ids`. A new caller of the system-scope one fails this test until it is reviewed
and added to ALLOWED.
"""

from __future__ import annotations

import pathlib

PYTHON = pathlib.Path(__file__).resolve().parents[4]
APP = PYTHON / "app"
# Product code only: the app and the EE tree (its scripts included); tests may name it.
SCANNED = [APP, PYTHON / "ee" / "scripts", *(p for p in (PYTHON / "ee").glob("*") if p.is_dir() and p.name not in {"tests", "vendor", "scripts"})]
NAME = "get_graph_user_keys_by_mongo_user_ids_all_orgs"
ALLOWED = {"migrations/service_account_kind_migration.py"}
DEFINED_IN = {
    "services/graph_db/interface/graph_db_provider.py",
    "services/graph_db/arango/arango_http_provider.py",
    "services/graph_db/neo4j/neo4j_provider.py",
    "services/graph_db/postgres/identity.py",
}


def _users() -> set[str]:
    return {
        str(p.relative_to(PYTHON))
        for root in SCANNED
        if root.is_dir()
        for p in root.rglob("*.py")
        if NAME in p.read_text(encoding="utf-8")
    }


def test_only_the_migration_calls_the_system_scope_lookup() -> None:
    allowed = {f"app/{p}" for p in ALLOWED | DEFINED_IN}
    assert _users() == allowed


def test_no_request_route_or_tool_names_it() -> None:
    for p in (APP / "api").rglob("*.py"):
        assert NAME not in p.read_text(encoding="utf-8"), p
