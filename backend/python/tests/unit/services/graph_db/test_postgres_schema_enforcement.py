"""Schema enforcement on the PostgreSQL graph provider's write paths (graph-schema-validation, provider wiring rules 1-6).

Written tests-first. Status when this file was written:

* RED until the wiring lands: every test that expects ``SchemaValidationError`` (invalid payloads, whole-batch rejection, edges, targeted
  fields, the lazy-required existence query, unknown keys on updates, ``validate_*`` being called at all and before the first statement).
* GREEN today and must stay green: every test of a valid payload (it must still write, so strict mode cannot reject what the app sends), the
  no-schema collections, the nested-object and read-back round trips, the null-stripping behaviour of the SQL layer, and
  ``GRAPH_SCHEMA_ENFORCEMENT=off``.

No database: ``PgBase._q`` is replaced by a recorder that hands the real SQL-building closure a fake transaction, so what is asserted is
which statements the provider issues (never their text, except to tell a read from a write), and whether a ``SchemaValidationError`` stops
them. The backend does not exist in every checkout, hence the ``importorskip``.
"""
from __future__ import annotations

import copy
import json
import logging
import re
from contextlib import contextmanager, suppress
from typing import TYPE_CHECKING, Any, NamedTuple
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("app.services.graph_db.postgres.provider")

from app.core.org_context import OrgContext, bind
from app.schema.node_validator import NodeSchemaValidator, SchemaValidationError
from app.services.graph_db.postgres.base import _arango
from app.services.graph_db.postgres.kg.registry import WRITERS
from app.services.graph_db.postgres.provider import PostgresGraphProvider

if TYPE_CHECKING:
    from collections.abc import Iterator

ORG = "org-1"
VALIDATE_METHODS = ("validate_node", "validate_node_update", "validate_node_upsert", "validate_edge", "validate_edge_update")
_WRITE_SQL = re.compile(r"\b(INSERT|UPDATE|DELETE)\b", re.IGNORECASE)


# ---- the recorder ----------------------------------------------------------------------------------------------------------------------

class Stmt(NamedTuple):
    kind: str
    sql: str
    args: tuple

    @property
    def is_write(self) -> bool:
        return bool(_WRITE_SQL.search(self.sql))

    def ids(self) -> set[str]:
        """Every string the statement carries as a list element or scalar parameter."""
        out: set[str] = set()
        for a in self.args:
            if isinstance(a, (list, tuple)):
                out |= {x for x in a if isinstance(x, str)}
            elif isinstance(a, str):
                out.add(a)
        return out

    def json_args(self) -> list[Any]:
        out = []
        for a in self.args:
            if isinstance(a, str) and a[:1] in "[{":
                with suppress(ValueError):
                    out.append(json.loads(a))
        return out


class FakeTx:
    """Answers a read with the nodes of ``existing`` that the statement names, and a write with a plausible count."""

    def __init__(self, h: Harness) -> None:
        self.h = h

    def _record(self, kind: str, sql: str, args: tuple) -> Stmt:
        s = Stmt(kind, sql, args)
        self.h.stmts.append(s)
        return s

    def _present(self, s: Stmt) -> list[str]:
        return sorted(s.ids() & self.h.existing)

    async def execute(self, sql: str, *args: Any) -> str:  # noqa: ANN401
        self._record("execute", sql, args)
        return "OK"

    async def fetch(self, sql: str, *args: Any) -> list:  # noqa: ANN401
        s = self._record("fetch", sql, args)
        return [] if s.is_write else [{"id": i, "doc": "{}"} for i in self._present(s)]

    async def fetchrow(self, sql: str, *args: Any) -> dict | None:  # noqa: ANN401
        s = self._record("fetchrow", sql, args)
        present = [] if s.is_write else self._present(s)
        return {"id": present[0], "doc": "{}"} if present else None

    async def fetchval(self, sql: str, *args: Any) -> int:  # noqa: ANN401
        s = self._record("fetchval", sql, args)
        if s.is_write:
            return 1
        return len(self._present(s)) if any(isinstance(a, (list, tuple)) for a in args) else 2


class Harness:
    def __init__(self, existing: tuple[str, ...] = ()) -> None:
        self.stmts: list[Stmt] = []
        self.q_calls = 0
        self.existing = set(existing)
        self.validations: list[tuple[str, str, Any, int]] = []   # (method, collection, payload at call time, statements issued so far)
        self.tx = FakeTx(self)
        p = PostgresGraphProvider(logging.getLogger("pg-schema-enforcement"), MagicMock())
        p._q = self._q                                            # the one seam every statement goes through
        p._walk_subtree = AsyncMock(return_value=(["r1"], {"r1": {}}, {}))
        self.p = p

    async def _q(self, fn: Any, transaction: Any = None, *, write: bool = False) -> Any:  # noqa: ANN401
        self.q_calls += 1
        return await fn(self.tx)

    async def call(self, name: str, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        with bind(OrgContext(ORG, "user-1", "job", "job:test", "t")), self._spy():
            return await getattr(self.p, name)(*args, **kwargs)

    @contextmanager
    def _spy(self) -> Iterator[None]:
        """Records every call of the validator and delegates to the real one when it exists (it may not yet: ``create=True``)."""
        patches = []
        for m in VALIDATE_METHODS:
            real = getattr(NodeSchemaValidator, m, None)

            def spy(validator: Any, collection: str, payload: Any, *a: Any, _m: str = m, _real: Any = real, **k: Any) -> Any:  # noqa: ANN401
                self.validations.append((_m, collection, copy.deepcopy(payload), len(self.stmts)))
                return _real(validator, collection, payload, *a, **k) if _real else ([] if _m == "validate_node_upsert" else None)
            patches.append(patch.object(NodeSchemaValidator, m, spy, create=True))
        for p in patches:
            p.start()
        try:
            yield
        finally:
            for p in reversed(patches):
                p.stop()

    @property
    def writes(self) -> list[Stmt]:
        return [s for s in self.stmts if s.is_write]

    @property
    def reads(self) -> list[Stmt]:
        return [s for s in self.stmts if not s.is_write]

    def validated(self, *methods: str) -> list[tuple[str, str, Any, int]]:
        return [v for v in self.validations if v[0] in (methods or VALIDATE_METHODS)]


@pytest.fixture(autouse=True)
def _strict_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRAPH_SCHEMA_ENFORCEMENT", raising=False)


def assert_nothing_issued(h: Harness) -> None:
    assert h.stmts == [] and h.q_calls == 0, f"a rejected payload must not reach the database, but it issued: {[s.sql[:60] for s in h.stmts]}"


# ---- payloads --------------------------------------------------------------------------------------------------------------------------

def rec(key: str = "r1", **over: Any) -> dict:  # noqa: ANN401
    """A record document the records schema accepts in full."""
    doc = {"id": key, "orgId": ORG, "recordName": f"Doc {key}", "externalRecordId": f"ext-{key}", "recordType": "FILE", "origin": "CONNECTOR",
           "connectorId": "c1", "createdAtTimestamp": 1_700_000_000_000}
    doc.update(over)
    return doc


def without(doc: dict, *keys: str) -> dict:
    return {k: v for k, v in doc.items() if k not in keys}


def template(key: str = "t1", **over: Any) -> dict:  # noqa: ANN401
    """An agent template: the schema has an object-typed and array-of-object-typed fields, which are nested documents in jsonb."""
    doc = {"id": key, "name": "T", "description": "d", "startMessage": "hi", "systemPrompt": "sp",
           "tools": [{"name": "search", "description": "find things"}], "memory": {"type": ["CONVERSATIONS"], "enabled": True}}
    doc.update(over)
    return doc


def edge(coll: str, flat: bool, **props: Any) -> dict:  # noqa: ANN401
    """An edge between two nodes, in the Arango shape (``_from``/``_to``) or the flat shape (``from_id`` ...)."""
    ends = ({"from_id": "a1", "from_collection": "records", "to_id": "b1", "to_collection": "recordGroups"} if flat
            else {"_from": "records/a1", "_to": "recordGroups/b1"})
    return {**ends, **props}


GOOD_PROPS = {
    "belongsTo": {"entityType": "KB", "createdAtTimestamp": 1, "updatedAtTimestamp": 1},
    "userAppRelation": {"syncState": "NOT_STARTED", "lastSyncUpdate": 1, "createdAtTimestamp": 1},
    "nodeRelations": {"relationshipType": "PARENT_CHILD", "createdAtTimestamp": 1},
    "recordLinks": {"relationshipType": "LINKED_TO", "createdAtTimestamp": 1},
    "permission": {"type": "USER", "role": "READER", "createdAtTimestamp": 1},
}
BAD_PROPS = {
    "belongsTo": {"entityType": "NOT_AN_ENTITY"},
    "userAppRelation": {"syncState": "NOT_A_STATE", "lastSyncUpdate": 1},
    "nodeRelations": {"relationshipType": "NOT_A_RELATION"},
    "recordLinks": {"relationshipType": "PARENT_CHILD"},        # a hierarchy type can never be a link
    "permission": {"type": "USER", "role": "NOT_A_ROLE"},
}


class _Rec:
    """The part of a ``Record`` ``batch_upsert_records`` reads. Its base document carries ``_key`` as the real model's does."""

    def __init__(self, key: str, **over: Any) -> None:  # noqa: ANN401
        self.record_type = "FILE"
        self._base = {"_key": key, **without(rec(key), "id"), **over}
        self._type = {"_key": key, "name": f"{key}.txt", "isFile": True}

    def to_arango_base_record(self) -> dict:
        return dict(self._base)

    def to_arango_record(self) -> dict:
        return dict(self._type)


# ---- 1. every writer is classified -----------------------------------------------------------------------------------------------------

#: Hand-maintained. create: new node, full validation. upsert: MERGE, partial when the node exists. update: partial. replace: ``SET n = $node``,
#: full. targeted: sets named fields from typed arguments. edge: writes an edge. delegates: only calls other writers. none: no payload a schema
#: could judge (deletes, sync points, counters, projections of data already stored).
CLASSIFICATION: dict[str, str] = {
    **dict.fromkeys(["create_node_if_absent", "ensure_nodes", "stage_state_create"], "create"),
    **dict.fromkeys(["batch_upsert_nodes", "batch_upsert_records"], "upsert"),
    **dict.fromkeys(["update_node", "batch_update_nodes", "update_node_fields_if_match", "update_knowledge_base", "compare_and_set_record_fields"], "update"),
    "update_node_if_match": "replace",
    **dict.fromkeys(["batch_update_connector_status", "compare_and_set_indexing_status", "update_indexing_status_for_record_ids",
                     "stage_state_compare_and_set", "soft_delete_records", "restore_records", "record_purge_failure", "retag_push_record",
                     "claim_push_record", "take_back_kept_record_group", "update_kb_permission"], "targeted"),
    **dict.fromkeys(["batch_create_edges", "create_edges_if_absent", "batch_upsert_node_relations", "ensure_app_membership", "ensure_team_app_edge",
                     "upsert_authenticated_as", "link_record_to_group", "batch_update_team_member_roles", "update_edge"], "edge"),
    "batch_upsert_app_users": "delegates",       # builds users and edges, then calls batch_upsert_nodes and batch_create_edges
    **dict.fromkeys([
        "backfill_block_name_folded", "batch_delete_edges", "copy_document_relationships", "delete_all_edges_for_node",
        "delete_blocks_for_records", "delete_edges_between_collections", "delete_edges_by_relationship_types", "delete_edges_by_source_keys",
        "delete_all_team_permissions", "remove_kb_permission", "delete_kb_hub_app", "delete_connector_sync_edges", "reap_stale_external_app_relations", "delete_edges_from", "delete_edges_to", "delete_edges_to_groups", "delete_edges_touching_nodes", "delete_nodes", "delete_parent_child_edge_to_record",
        "delete_record", "delete_records_and_relations", "delete_records_recursive", "delete_single_record", "delete_team_member_edges",
        "increment_node_field", "purge_trash_kept_record_groups", "purge_trashed_records", "remove_authenticated_as", "remove_nodes_by_field",
        "remove_sync_point", "stage_states_delete_for_virtual_records", "stage_states_delete_unheld_revisions",
        "unlink_nodes_missed_by_full_sync", "update_sync_point_if", "upsert_sync_point",
    ], "none"),
}
#: Targeted writers whose every value comes from a typed argument that no schema narrows (a count, a free string, a constant), so no caller
#: input can be invalid; they are classified for completeness and exercised only by the valid-path matrix where that is cheap.
TYPED_ONLY = {"restore_records", "record_purge_failure", "retag_push_record", "claim_push_record", "take_back_kept_record_group",
              "update_kb_permission"}      # its role is checked against OWNER/WRITER/READER first and its timestamps are generated


def test_every_writer_of_the_registry_is_classified_and_nothing_else_is() -> None:
    unclassified = sorted(set(WRITERS) - set(CLASSIFICATION))
    stale = sorted(set(CLASSIFICATION) - set(WRITERS))
    assert not unclassified, f"new provider writers need a schema-enforcement class in this table: {unclassified}"
    assert not stale, f"classified, but no longer in kg/registry.py WRITERS: {stale}"


def test_every_classified_writer_exists_on_the_provider() -> None:
    assert [n for n in CLASSIFICATION if not callable(getattr(PostgresGraphProvider, n, None))] == []


# ---- 2. the case tables ----------------------------------------------------------------------------------------------------------------

Case = tuple[str, tuple, dict]   # method, args, kwargs

#: payloads a schema rejects: (id, method, args, kwargs)
INVALID: list[tuple[str, str, tuple, dict]] = [
    # create: full validation
    ("create bad enum", "create_node_if_absent", ("r1", "records", rec(recordType="NOT_A_TYPE")), {}),
    ("create missing required", "create_node_if_absent", ("r1", "records", without(rec(), "recordName")), {}),
    ("create unknown key", "create_node_if_absent", ("r1", "records", rec(bogusKey=1)), {}),
    ("create null non-nullable", "create_node_if_absent", ("r1", "records", rec(recordName=None)), {}),
    ("ensure_nodes bad member", "ensure_nodes", ([rec("r1"), rec("r2", origin="NOT_AN_ORIGIN")], "records"), {}),
    ("ensure_nodes missing required", "ensure_nodes", ([rec("r1"), without(rec("r2"), "connectorId")], "records"), {}),
    ("stage_state_create", "stage_state_create", ({"id": "s1", "status": "NOT_A_STATUS"},), {}),
    # upsert
    ("upsert bad member", "batch_upsert_nodes", ([rec("r1"), rec("r2", recordType="NOT_A_TYPE"), rec("r3")], "records"), {}),
    ("upsert complete unknown key", "batch_upsert_nodes", ([rec("r1", bogusKey=1)], "records"), {}),
    ("upsert wrong type", "batch_upsert_nodes", ([rec("r1", version="three")], "records"), {}),
    ("upsert partial bad enum", "batch_upsert_nodes", ([{"id": "r1", "indexingStatus": "NOT_A_STATUS"}], "records"), {"_existing": ("r1",)}),
    ("upsert partial unknown key", "batch_upsert_nodes", ([{"id": "r1", "bogusKey": 1}], "records"), {"_existing": ("r1",)}),
    ("upsert partial null non-nullable", "batch_upsert_nodes", ([{"id": "r1", "recordName": None}], "records"), {"_existing": ("r1",)}),
    ("upsert users missing email", "batch_upsert_nodes", ([{"id": "u1", "email": 12}], "users"), {"_existing": ("u1",)}),
    ("upsert_records bad base", "batch_upsert_records", ([_Rec("r1"), _Rec("r2", recordType="NOT_A_TYPE")],), {}),
    # update: partial
    ("update bad enum", "update_node", ("r1", "records", {"indexingStatus": "NOT_A_STATUS"}), {}),
    ("update unknown key", "update_node", ("r1", "records", {"bogusKey": 1}), {}),
    ("update wrong type", "update_node", ("r1", "records", {"version": "three"}), {}),
    ("update null non-nullable", "update_node", ("r1", "records", {"recordName": None}), {}),
    ("batch_update bad member", "batch_update_nodes", ([{"id": "r1", "indexingStatus": "COMPLETED"}, {"id": "r2", "indexingStatus": "NOT_A_STATUS"}], "records"), {}),
    ("fields_if_match bad", "update_node_fields_if_match", ("r1", "records", {"indexingStatus": "NOT_A_STATUS"}, {"indexingStatus": "NOT_STARTED"}), {}),
    ("update_knowledge_base bad", "update_knowledge_base", ("kb1", {"scope": "NOT_A_SCOPE"}), {}),
    ("batch_update_team_member_roles bad", "batch_update_team_member_roles", ("t1", [{"userId": "u1", "role": "NOT_A_ROLE"}], 1), {}),
    ("update_edge bad role", "update_edge", ("users/u1", "records/r1", {"role": "NOT_A_ROLE"}, "permission"), {}),
    ("cas_record_fields bad", "compare_and_set_record_fields", (["r1"], "rev-1", {"indexingStatus": "NOT_A_STATUS"}), {}),
    # replace: full
    ("replace bad enum", "update_node_if_match", ("r1", "records", rec(recordType="NOT_A_TYPE"), "version", 1), {}),
    ("replace missing required", "update_node_if_match", ("r1", "records", without(rec(), "recordName"), "version", 1), {}),
    ("replace unknown key", "update_node_if_match", ("r1", "records", rec(bogusKey=1), "version", 1), {}),
    # targeted
    ("cas_indexing_status bad", "compare_and_set_indexing_status", (["r1"], "NOT_STARTED", "NOT_A_STATUS"), {}),
    ("stage_state cas bad status", "stage_state_compare_and_set", ("s1", "NOT_STARTED", "NOT_A_STATUS", {}), {}),
    ("stage_state cas bad field", "stage_state_compare_and_set", ("s1", "NOT_STARTED", "IN_PROGRESS", {"attempt": "many"}), {}),
    ("soft_delete bad source", "soft_delete_records", (["r1"], "c1"), {"delete_source": "NOT_A_SOURCE", "batch_id": "b1"}),
    # edges, with the properties of each edge collection
    *[(f"{fn} {coll} {'flat' if flat else 'arango'}", fn, ([edge(coll, flat, **GOOD_PROPS[coll]), edge(coll, flat, **BAD_PROPS[coll])], coll), {})
      for fn in ("batch_create_edges", "create_edges_if_absent") for coll in BAD_PROPS for flat in (False, True)],
    *[(f"batch_upsert_node_relations {'flat' if flat else 'arango'}", "batch_upsert_node_relations", ([edge("nodeRelations", flat, **BAD_PROPS["nodeRelations"])],), {})
      for flat in (False, True)],
    ("ensure_app_membership bad source id", "ensure_app_membership", ("u1", "users", "c1"), {"is_external": False, "source_user_id": 12345}),
    ("upsert_authenticated_as bad connector id", "upsert_authenticated_as", ("u1", "u2", 12345, ORG), {}),
]

#: payloads the app legitimately writes: (id, method, args, kwargs)
VALID: list[tuple[str, str, tuple, dict]] = [
    ("create", "create_node_if_absent", ("r1", "records", rec()), {}),
    ("create with nullables", "create_node_if_absent", ("r1", "records", rec(mimeType=None, externalParentId=None, webUrl=None)), {}),
    ("ensure_nodes", "ensure_nodes", ([rec("r1"), rec("r2")], "records"), {}),
    ("stage_state_create no schema match skipped", "stage_state_create", ({"id": "s1", "orgId": ORG, "virtualRecordId": "v", "rev": "1", "stage": "parse", "status": "NOT_STARTED",
                                                                       "stageVersion": 1, "recordIds": ["r1"], "connectorId": "c1", "tier": "heavy", "priority": "bulk",
                                                                       "trigger": "sync", "textDigest": "d", "blocksDigest": "d", "textChars": 0, "hasTables": False,
                                                                       "hasImages": False, "updatedAtMs": 1},), {}),
    ("upsert complete", "batch_upsert_nodes", ([rec("r1"), rec("r2")], "records"), {}),
    ("upsert partial existing", "batch_upsert_nodes", ([{"id": "r1", "indexingStatus": "COMPLETED"}], "records"), {"_existing": ("r1",)}),
    ("upsert_records", "batch_upsert_records", ([_Rec("r1"), _Rec("r2")],), {}),
    ("update", "update_node", ("r1", "records", {"indexingStatus": "COMPLETED", "version": 2}), {}),
    ("update null on nullable", "update_node", ("r1", "records", {"externalParentId": None, "mimeType": None}), {}),
    ("batch_update", "batch_update_nodes", ([{"id": "r1", "indexingStatus": "COMPLETED"}, {"id": "r2", "isDeleted": True}], "records"), {}),
    ("fields_if_match", "update_node_fields_if_match", ("r1", "records", {"indexingStatus": "COMPLETED"}, {"indexingStatus": "NOT_STARTED"}), {}),
    ("update_knowledge_base", "update_knowledge_base", ("kb1", {"name": "KB", "scope": "personal"}), {}),
    ("batch_update_team_member_roles", "batch_update_team_member_roles", ("t1", [{"userId": "u1", "role": "OWNER"}], 1), {}),
    ("update_edge", "update_edge", ("users/u1", "records/r1", {"role": "WRITER"}, "permission"), {}),
    ("cas_record_fields", "compare_and_set_record_fields", (["r1"], "rev-1", {"indexingStatus": "COMPLETED"}), {}),
    ("replace", "update_node_if_match", ("r1", "records", rec(version=2), "version", 1), {}),
    ("batch_update_connector_status", "batch_update_connector_status", ("apps", ["a1"]), {"is_active": True, "is_agent_active": False}),
    ("cas_indexing_status", "compare_and_set_indexing_status", (["r1"], "NOT_STARTED", "IN_PROGRESS"), {}),
    ("update_indexing_status_for_record_ids", "update_indexing_status_for_record_ids", (["r1"], "COMPLETED"), {}),
    ("stage_state cas", "stage_state_compare_and_set", ("s1", "NOT_STARTED", "IN_PROGRESS", {"attempt": 1}), {}),
    ("soft_delete", "soft_delete_records", (["r1"], "c1"), {"delete_source": "USER", "batch_id": "b1"}),
    *[(f"{fn} {coll} {'flat' if flat else 'arango'}", fn, ([edge(coll, flat, **GOOD_PROPS[coll])], coll), {})
      for fn in ("batch_create_edges", "create_edges_if_absent") for coll in GOOD_PROPS for flat in (False, True)],
    *[(f"batch_upsert_node_relations {'flat' if flat else 'arango'}", "batch_upsert_node_relations", ([edge("nodeRelations", flat, **GOOD_PROPS["nodeRelations"])],), {})
      for flat in (False, True)],
    ("ensure_app_membership", "ensure_app_membership", ("u1", "users", "c1"), {"is_external": False, "source_user_id": "src-1"}),
    ("ensure_team_app_edge", "ensure_team_app_edge", ("c1", ORG), {}),
    ("upsert_authenticated_as", "upsert_authenticated_as", ("u1", "u2", "c1", ORG), {}),
    ("link_record_to_group", "link_record_to_group", ("r1", "g1"), {"inherit": True, "leaving_group_id": "g0", "browse_root": True}),
]


def _run(h_args: tuple[str, str, tuple, dict]) -> tuple[Harness, str, tuple, dict]:
    _id, method, args, kwargs = h_args
    kwargs = dict(kwargs)
    return Harness(existing=kwargs.pop("_existing", ())), method, args, kwargs


def test_every_payload_writer_has_an_invalid_and_a_valid_case() -> None:
    needing = {n for n, c in CLASSIFICATION.items() if c in {"create", "upsert", "update", "replace", "edge", "targeted"} and n not in TYPED_ONLY}
    # the two status writers that swallow errors have their own tests; these two build their edge properties from constants, so no argument
    # can make them invalid (their edges are checked in test_edge_helpers_validate_the_edges_they_build_before_writing)
    exempt = {"batch_update_connector_status", "update_indexing_status_for_record_ids", "link_record_to_group", "ensure_team_app_edge"}
    assert needing - {m for _, m, _, _ in INVALID} - exempt == set(), "writers with no invalid-payload case"
    assert needing - {m for _, m, _, _ in VALID} == set(), "writers with no valid-payload case"
    assert {m for _, m, _, _ in INVALID} <= set(CLASSIFICATION)


# ---- 3. rules 1, 3, 5: invalid payloads are rejected and nothing reaches the database --------------------------------------------------------

@pytest.mark.parametrize("case", INVALID, ids=[c[0] for c in INVALID])
async def test_an_invalid_payload_raises_and_issues_no_statement(case: tuple) -> None:
    """RED until wired. Also covers whole-batch rejection: the batches here hold valid members next to the invalid one."""
    h, method, args, kwargs = _run(case)
    with pytest.raises(SchemaValidationError):
        await h.call(method, *args, **kwargs)
    assert_nothing_issued(h)


@pytest.mark.parametrize("case", VALID, ids=[c[0] for c in VALID])
async def test_a_valid_payload_is_written(case: tuple) -> None:
    """GREEN today and must stay green: strict mode must not reject what the app sends."""
    h, method, args, kwargs = _run(case)
    await h.call(method, *args, **kwargs)
    assert h.writes, "a valid payload must reach the database"


@pytest.mark.parametrize("case", [c for c in VALID if CLASSIFICATION[c[1]] in {"create", "upsert", "update", "replace", "edge"}
                                  and c[1] not in {"ensure_team_app_edge", "link_record_to_group"}], ids=lambda c: c[0])
async def test_validation_runs_before_the_first_statement(case: tuple) -> None:
    """RED until wired: rule 1 is about order, so a valid payload is validated too, and before anything is sent."""
    h, method, args, kwargs = _run(case)
    await h.call(method, *args, **kwargs)
    assert h.validations, f"{method} never called the validator"
    assert h.validations[0][3] == 0, f"{method} validated only after {h.validations[0][3]} statement(s)"


@pytest.mark.parametrize("method,args,kwargs,collections", [
    ("ensure_app_membership", ("u1", "users", "c1"), {"is_external": True, "source_user_id": "s"}, {"userAppRelation"}),
    ("ensure_team_app_edge", ("c1", ORG), {}, {"userAppRelation"}),
    ("upsert_authenticated_as", ("u1", "u2", "c1", ORG), {}, {"authenticatedAs"}),
    ("link_record_to_group", ("r1", "g1"), {"inherit": True, "leaving_group_id": "g0", "browse_root": True}, {"belongsTo", "inheritPermissions", "nodeRelations"}),
], ids=["ensure_app_membership", "ensure_team_app_edge", "upsert_authenticated_as", "link_record_to_group"])
async def test_edge_helpers_validate_the_edges_they_build_before_writing(method: str, args: tuple, kwargs: dict, collections: set) -> None:
    """RED until wired. These writers build their own edge properties, so the check that matters is that they are validated against the right
    edge collection, in full (an edge is created or replaced), and before any statement."""
    h = Harness()
    await h.call(method, *args, **kwargs)
    full = h.validated("validate_edge")
    assert {v[1] for v in full} >= collections
    assert all(v[3] == 0 for v in full)
    assert all(isinstance(v[2], dict) for v in full)
    assert h.writes


# ---- the two writers that swallow their errors ------------------------------------------------------------------------------------------

async def test_connector_status_with_a_non_boolean_issues_no_statement() -> None:
    """RED until wired. ``batch_update_connector_status`` is wrapped in ``_soft(0)``, which turns any exception into ``0``: the contract is that
    nothing is written; whether the error is also raised is the next test."""
    h = Harness()
    with suppress(SchemaValidationError):
        await h.call("batch_update_connector_status", "apps", ["a1"], is_active="yes", is_agent_active=False)
    assert_nothing_issued(h)


async def test_connector_status_with_a_non_boolean_raises_the_schema_error() -> None:
    """RED until wired, and it needs a decision: ``_soft(0)`` would swallow the error, while the plan says strict raises. Either validate
    outside the decorator, or change this test to accept ``0`` for this writer."""
    h = Harness()
    with pytest.raises(SchemaValidationError):
        await h.call("batch_update_connector_status", "apps", ["a1"], is_active="yes", is_agent_active=False)


async def test_indexing_status_for_record_ids_with_a_bad_status_issues_no_statement() -> None:
    """RED until wired. The method documents "a failure is logged, not raised", so only the absence of a write is asserted."""
    h = Harness()
    with suppress(SchemaValidationError):
        await h.call("update_indexing_status_for_record_ids", ["r1"], "NOT_A_STATUS")
    assert_nothing_issued(h)


# ---- rule 3 / create vs upsert: the lazy required check ---------------------------------------------------------------------------------

INCOMPLETE = {"id": "r1", "indexingStatus": "COMPLETED"}


async def test_a_complete_upsert_payload_runs_no_existence_query() -> None:
    """GREEN today and must stay green: a complete payload cannot be a half-created node, so there is nothing to look up."""
    h = Harness()
    await h.call("batch_upsert_nodes", [rec("r1"), rec("r2")], "records")
    assert h.reads == [] and h.writes


async def test_an_incomplete_payload_for_an_existing_node_is_written_after_one_existence_query() -> None:
    """RED until wired (today no query is made)."""
    h = Harness(existing=("r1",))
    await h.call("batch_upsert_nodes", [INCOMPLETE], "records")
    assert len(h.reads) == 1 and h.writes
    assert "r1" in h.reads[0].ids()


async def test_an_incomplete_payload_for_an_absent_node_is_a_create_without_its_required_fields() -> None:
    """RED until wired."""
    h = Harness(existing=())
    with pytest.raises(SchemaValidationError):
        await h.call("batch_upsert_nodes", [INCOMPLETE], "records")
    assert h.writes == [] and len(h.reads) <= 1


async def test_a_mixed_batch_looks_up_only_its_incomplete_members_and_fails_whole_when_one_is_absent() -> None:
    """RED until wired. One existence query for the batch, naming the incomplete ids and not the complete one; nothing written."""
    h = Harness(existing=("r2",))
    batch = [rec("r1"), {"id": "r2", "isDeleted": True}, {"id": "r3", "isDeleted": True}]
    with pytest.raises(SchemaValidationError):
        await h.call("batch_upsert_nodes", batch, "records")
    assert h.writes == [] and len(h.reads) == 1
    assert {"r2", "r3"} <= h.reads[0].ids() and "r1" not in h.reads[0].ids()


async def test_a_mixed_batch_whose_incomplete_members_all_exist_is_written() -> None:
    """RED until wired (the single existence query)."""
    h = Harness(existing=("r2", "r3"))
    await h.call("batch_upsert_nodes", [rec("r1"), {"id": "r2", "isDeleted": True}, {"id": "r3", "isDeleted": True}], "records")
    assert len(h.reads) == 1 and h.writes and "r1" not in h.reads[0].ids()


async def test_a_large_incomplete_batch_costs_one_existence_query() -> None:
    """RED until wired: the lookup is per batch, not per node."""
    ids = [f"r{i}" for i in range(500)]
    h = Harness(existing=tuple(ids))
    await h.call("batch_upsert_nodes", [{"id": i, "isDeleted": False} for i in ids], "records")
    assert len(h.reads) == 1 and set(ids) <= h.reads[0].ids()


@pytest.mark.parametrize("collection,payload", [("groups", {"id": "g1", "anything": [1, {"a": 2}]}), ("syncPoints", {"id": "s1", "cursor": "x"})])
async def test_a_collection_without_a_schema_is_never_looked_up(collection: str, payload: dict) -> None:
    """GREEN today and must stay green: nothing is required there, so a partial payload needs no existence query and nothing is rejected."""
    h = Harness()
    await h.call("batch_upsert_nodes", [payload], collection)
    assert h.reads == [] and h.writes


async def test_the_existence_query_is_not_issued_when_a_complete_batch_is_invalid() -> None:
    """RED until wired: a batch that is already wrong is rejected before anything is read."""
    h = Harness(existing=("r2",))
    with pytest.raises(SchemaValidationError):
        await h.call("batch_upsert_nodes", [rec("r1", recordType="NOT_A_TYPE"), {"id": "r2", "isDeleted": True}], "records")
    assert_nothing_issued(h)


# ---- rule 2: nested documents are validated as the dicts they are, and survive as jsonb ----------------------------------------------------

NESTED_CALLS: list[tuple[str, str, tuple]] = [
    ("batch_upsert_nodes", "upsert", ([template("t1"), template("t2")], "agentTemplates")),
    ("create_node_if_absent", "create", ("t1", "agentTemplates", template())),
    ("ensure_nodes", "create", ([template("t1")], "agentTemplates")),
    ("update_node", "update", ("t1", "agentTemplates", {"tools": [{"name": "x", "description": "y"}], "memory": {"type": ["APPS"], "enabled": False}})),
    ("batch_update_nodes", "update", ([{"id": "t1", "tools": [{"name": "x", "description": "y"}]}], "agentTemplates")),
    ("update_node_fields_if_match", "update", ("t1", "agentTemplates", {"memory": {"type": ["APPS"], "enabled": False}}, {"name": "T"})),
    ("update_node_if_match", "replace", ("t1", "agentTemplates", template(), "name", "T")),
]


@pytest.mark.parametrize("method,kind,args", NESTED_CALLS, ids=[c[0] for c in NESTED_CALLS])
async def test_nested_objects_and_arrays_of_objects_are_written_not_rejected(method: str, kind: str, args: tuple) -> None:
    """GREEN today and must stay green. Neo4j's stringify-before-validate false error must not reappear here: an object field is a dict."""
    h = Harness()
    await h.call(method, *args)
    assert h.writes


@pytest.mark.parametrize("method,kind,args", NESTED_CALLS, ids=[c[0] for c in NESTED_CALLS])
async def test_the_validator_sees_nested_values_as_dicts_and_lists(method: str, kind: str, args: tuple) -> None:
    """RED until wired."""
    h = Harness(existing=("t1", "t2"))
    await h.call(method, *args)
    payloads = [v[2] for v in h.validated("validate_node", "validate_node_update", "validate_node_upsert")]
    assert payloads, f"{method} never validated a node"
    seen = [p[f] for p in payloads for f in ("tools", "memory") if f in p]
    assert seen and all(isinstance(v, (dict, list)) for v in seen), f"a nested value reached the validator as {[type(v).__name__ for v in seen]}"


async def test_nested_objects_reach_the_statement_as_json_objects() -> None:
    """GREEN today: the document column is jsonb, so what is sent is the nested object itself and nothing is stringified twice."""
    h = Harness()
    await h.call("batch_upsert_nodes", [template("t1")], "agentTemplates")
    docs = [item["doc"] for s in h.writes for blob in s.json_args() if isinstance(blob, list) for item in blob]
    assert docs and docs[0]["tools"] == [{"name": "search", "description": "find things"}] and docs[0]["memory"] == {"type": ["CONVERSATIONS"], "enabled": True}


async def test_a_nested_object_of_the_wrong_type_is_rejected() -> None:
    """RED until wired: the schema says ``tools`` is an array of objects, and a string is not one."""
    h = Harness()
    with pytest.raises(SchemaValidationError):
        await h.call("batch_upsert_nodes", [template("t1", tools='[{"name": "search"}]')], "agentTemplates")
    assert_nothing_issued(h)


async def test_an_array_member_of_the_wrong_shape_is_rejected() -> None:
    """RED until wired."""
    h = Harness()
    with pytest.raises(SchemaValidationError):
        await h.call("batch_upsert_nodes", [template("t1", tools=[{"description": "no name"}])], "agentTemplates")
    assert_nothing_issued(h)


# ---- keys and read-back -----------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("method,args", [
    ("batch_upsert_nodes", ([{**without(rec("r1"), "id"), "_key": "r1"}], "records")),
    ("create_node_if_absent", ("r1", "records", {**without(rec("r1"), "id"), "_key": "r1"})),
    ("ensure_nodes", ([{**without(rec("r1"), "id"), "_key": "r1"}], "records")),
    ("batch_upsert_nodes", ([{"_key": "r1", "indexingStatus": "COMPLETED"}], "records")),
], ids=["upsert", "create", "ensure_nodes", "partial upsert"])
async def test_a_node_keyed_by__key_is_not_rejected_for_its_key(method: str, args: tuple) -> None:
    """GREEN today and must stay green. Model converters (``to_arango_base_record`` ...) emit ``_key``, and validation runs before conversion
    (rule 2), so the validator or the provider has to treat ``_key`` as the id: the schemas rename it to ``id`` and forbid unknown keys."""
    h = Harness(existing=("r1",))
    await h.call(method, *args)
    assert h.writes


@pytest.mark.parametrize("method,args", [
    ("batch_upsert_nodes", ([_arango(rec("r1"), "records")], "records")),
    ("update_node_if_match", ("r1", "records", _arango(rec("r1"), "records"), "version", 1)),
    ("create_node_if_absent", ("r1", "records", _arango(rec("r1"), "records"))),
], ids=["upsert", "replace", "create"])
async def test_a_document_read_back_with_key_and_id_can_be_written_back(method: str, args: tuple) -> None:
    """GREEN today and must stay green: ``get_document`` returns ``_key`` and ``_id``, and callers write such documents back."""
    h = Harness()
    await h.call(method, *args)
    assert h.writes


# ---- nulls: stripped by the SQL layer, judged by the validator -------------------------------------------------------------------------------

async def test_a_null_on_a_nullable_field_is_valid_and_still_reaches_the_sql_layer_to_remove_the_field() -> None:
    """GREEN today and must stay green. The validator must not strip the None (the SQL does, and it also uses it to clear the stored field)."""
    h = Harness()
    await h.call("batch_upsert_nodes", [rec("r1", mimeType=None)], "records")
    assert any("batch_upsert_nodes_clear" in s.sql for s in h.writes), "the null must still drive the field removal"
    sent = [item["doc"] for s in h.writes for blob in s.json_args() if isinstance(blob, list) for item in blob if "doc" in item]
    assert sent and sent[0]["mimeType"] is None


async def test_the_validator_is_given_the_nulls_it_has_to_judge() -> None:
    """RED until wired: a None has to be seen by the validator for a non-nullable field to be rejected at all."""
    h = Harness()
    await h.call("update_node", "r1", "records", {"externalParentId": None})
    assert h.validated("validate_node_update")[0][2] == {"externalParentId": None}


@pytest.mark.parametrize("method,args", [
    ("update_node", ("r1", "records", {"recordName": None})),
    ("batch_upsert_nodes", ([rec("r1", recordName=None)], "records")),
    ("create_node_if_absent", ("r1", "records", rec(origin=None))),
], ids=["update", "upsert", "create"])
async def test_a_null_on_a_non_nullable_field_is_rejected_even_though_the_sql_would_drop_it(method: str, args: tuple) -> None:
    """RED until wired: same rule as the graph's other stores (a null on a field the schema does not mark nullable fails)."""
    h = Harness()
    with pytest.raises(SchemaValidationError):
        await h.call(method, *args)
    assert_nothing_issued(h)


# ---- collections without a schema, and sync points ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("method,args", [
    ("create_node_if_absent", ("x1", "groups", {"id": "x1", "whatever": {"nested": [1, 2]}, "n": None})),
    ("ensure_nodes", ([{"id": "x1", "whatever": 1}], "groups")),
    ("update_node", ("x1", "groups", {"anything": "goes"})),
    ("batch_update_nodes", ([{"id": "x1", "anything": "goes"}], "groups")),
    ("update_node_if_match", ("x1", "groups", {"anything": "goes"}, "id", "x1")),
    ("upsert_sync_point", ("k1", {"cursor": {"page": 3}, "opaque": ["a"]}, "syncPoints")),
    ("update_sync_point_if", ("k1", {"cursor": "a"}, {"cursor": {"page": 4}}, "syncPoints")),
], ids=["create", "ensure_nodes", "update", "batch_update", "replace", "sync point", "sync point cas"])
async def test_a_collection_with_no_schema_accepts_any_document(method: str, args: tuple) -> None:
    """GREEN today and must stay green: sync points and other schema-less collections are never rejected."""
    h = Harness()
    await h.call(method, *args)
    assert h.writes


# ---- GRAPH_SCHEMA_ENFORCEMENT=off ------------------------------------------------------------------------------------------------------------

OFF_CASES = [c for c in INVALID if c[1] in {"create_node_if_absent", "batch_upsert_nodes", "update_node", "update_node_if_match", "ensure_nodes",
                                           "batch_create_edges", "create_edges_if_absent", "batch_upsert_node_relations", "compare_and_set_indexing_status"}]


@pytest.mark.parametrize("case", OFF_CASES, ids=[c[0] for c in OFF_CASES])
async def test_enforcement_off_lets_an_invalid_payload_through(case: tuple, monkeypatch: pytest.MonkeyPatch) -> None:
    """GREEN today and must stay green: the emergency switch turns every validate call into a no-op, so the statement is issued as before."""
    monkeypatch.setenv("GRAPH_SCHEMA_ENFORCEMENT", "off")
    h, method, args, kwargs = _run(case)
    await h.call(method, *args, **kwargs)
    assert h.writes


async def test_enforcement_off_does_not_even_run_the_existence_query(monkeypatch: pytest.MonkeyPatch) -> None:
    """RED until wired: with enforcement off there is no required-field question to answer, so no extra read."""
    monkeypatch.setenv("GRAPH_SCHEMA_ENFORCEMENT", "off")
    h = Harness()
    await h.call("batch_upsert_nodes", [INCOMPLETE], "records")
    assert h.reads == [] and h.writes


async def test_enforcement_set_to_strict_is_the_default_behaviour(monkeypatch: pytest.MonkeyPatch) -> None:
    """RED until wired."""
    monkeypatch.setenv("GRAPH_SCHEMA_ENFORCEMENT", "strict")
    h = Harness()
    with pytest.raises(SchemaValidationError):
        await h.call("batch_upsert_nodes", [rec("r1", recordType="NOT_A_TYPE")], "records")
    assert_nothing_issued(h)
