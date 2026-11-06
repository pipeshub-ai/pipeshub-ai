"""Review fixes on the PostgreSQL graph provider's schema wiring: writers that took caller values unchecked, whole-batch checks, merged duplicates,
`_key`-only documents, org-before-validation order, and the bare app stub. Reuses the recorder of ``test_postgres_schema_enforcement``."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("app.services.graph_db.postgres.provider")

from test_postgres_schema_enforcement import (
    ORG,
    Harness,
    _strict_by_default,  # noqa: F401  (autouse fixture)
    assert_nothing_issued,
    rec,
)

from app.core.org_context import MissingOrgContext
from app.schema.node_validator import SchemaValidationError
from app.services.graph_db.postgres.identity import _stub_app
from app.services.graph_db.push_claim import PushExpect, PushSet

# ---- 1. writers that took caller values unchecked --------------------------------------------------------------------------------------


async def test_claim_push_record_update_with_an_empty_document_id_raises_and_issues_nothing() -> None:
    h = Harness()
    with pytest.raises(SchemaValidationError):
        await h.call("claim_push_record", kb_id="kb", org_id=ORG, key="r1", expected=PushExpect("d", "rev", 1), new=PushSet("", 2))
    assert_nothing_issued(h)


async def test_claim_push_record_update_with_valid_values_writes() -> None:
    h = Harness()
    await h.call("claim_push_record", kb_id="kb", org_id=ORG, key="r1", expected=PushExpect("d", "rev", 1), new=PushSet("d2", 2))
    assert h.stmts


async def test_retag_push_record_with_a_non_string_revision_raises_and_issues_nothing() -> None:
    h = Harness()
    with pytest.raises(SchemaValidationError):
        await h.call("retag_push_record", key="r1", kb_id="kb", org_id=ORG, expected_revision="a", new_revision=5)
    assert_nothing_issued(h)


async def test_record_purge_failure_with_a_non_string_error_raises_and_issues_nothing() -> None:
    h = Harness()
    with pytest.raises(SchemaValidationError):
        await h.call("record_purge_failure", ["r1"], ORG, 123)
    assert_nothing_issued(h)


async def test_increment_node_field_validates_the_field_it_sets() -> None:
    h = Harness()
    with pytest.raises(SchemaValidationError):
        await h.call("increment_node_field", "r1", "records", "recordName")      # recordName is a string: {recordName: 1} is invalid
    assert_nothing_issued(h)


async def test_restore_records_validates_the_fields_it_sets() -> None:
    h = Harness()
    with pytest.raises(SchemaValidationError):
        await h.call("restore_records", [{"id": "r1", "set": {"recordName": 5}}], "b1")
    assert_nothing_issued(h)


async def test_soft_delete_records_validates_the_caller_supplied_fields() -> None:
    h = Harness()
    with pytest.raises(SchemaValidationError):
        await h.call("soft_delete_records", ["r1"], "c1", delete_source=5, batch_id="b1")
    assert_nothing_issued(h)


# ---- 2. batch_upsert_app_users: the whole batch before the first statement -------------------------------------------------------------

class _User:
    def __init__(self, uid: str, email: str, *, created_at: Any = 1, guest: bool = False) -> None:  # noqa: ANN401
        self.id, self.email, self.is_guest, self.created_at, self.updated_at = uid, email, guest, created_at, 1
        self.connector_id, self.app_name, self.source_user_id, self.org_id = "c1", None, "src-" + uid, ORG
        self.is_active, self.full_name, self.title = False, "Name", None

    def to_arango_base_user(self) -> dict:
        return {"_key": self.id, "orgId": self.org_id, "email": self.email, "fullName": self.full_name, "userId": self.source_user_id,
                "isActive": False, "createdAtTimestamp": self.created_at, "updatedAtTimestamp": self.updated_at}


@pytest.mark.parametrize("transaction", [None, "t1"])
async def test_batch_upsert_app_users_rejects_a_bad_later_user_before_any_statement(transaction: str | None) -> None:
    h = Harness()
    users = [_User("u1", "a@x.io"), _User("u2", "b@x.io", created_at="not-a-number")]
    with pytest.raises(SchemaValidationError):
        await h.call("batch_upsert_app_users", users, transaction)
    assert_nothing_issued(h)


async def test_batch_upsert_app_users_rejects_a_bad_member_edge_before_any_statement() -> None:
    h = Harness()
    users = [_User("u1", "a@x.io"), _User("u2", "b@x.io")]
    users[1].updated_at = "later"      # the user node and the belongsTo edge both carry it
    with pytest.raises(SchemaValidationError):
        await h.call("batch_upsert_app_users", users, "t1")
    assert_nothing_issued(h)


# ---- 3. a _key-only incomplete document never raises KeyError ---------------------------------------------------------------------------

async def test_a_key_only_incomplete_document_for_an_absent_node_is_a_schema_error() -> None:
    h = Harness(existing=())
    with pytest.raises(SchemaValidationError):
        await h.call("_validate_upserts", "records", [{"_key": "r9", "isDeleted": True}])


async def test_a_key_only_incomplete_document_for_an_existing_node_passes() -> None:
    h = Harness(existing=("r9",))
    await h.call("_validate_upserts", "records", [{"_key": "r9", "isDeleted": True}])
    assert "r9" in h.reads[0].ids()


# ---- 4. a repeated id in one batch: the last one is written, and that is what is checked -------------------------------------------------------------------------------------

async def test_partial_then_full_for_an_absent_id_is_valid_in_batch_upsert_nodes() -> None:
    h = Harness(existing=())
    await h.call("batch_upsert_nodes", [{"id": "r1", "isDeleted": False}, rec("r1")], "records")
    assert h.reads == [] and h.writes


async def test_full_then_partial_for_an_absent_id_is_refused_because_the_last_one_is_written() -> None:
    h = Harness(existing=())
    with pytest.raises(SchemaValidationError):
        await h.call("batch_upsert_nodes", [rec("r1"), {"id": "r1", "isDeleted": True}], "records")
    assert h.writes == []


async def test_full_then_partial_for_an_existing_id_writes_only_the_last_one() -> None:
    h = Harness(existing=("r1",))
    await h.call("batch_upsert_nodes", [rec("r1"), {"id": "r1", "isDeleted": True}], "records")
    doc = next(j for s in h.writes for j in s.json_args() if isinstance(j, list))[0]["doc"]
    assert doc["isDeleted"] is True and "recordName" not in doc


async def test_partial_duplicates_for_an_absent_id_still_raise() -> None:
    h = Harness(existing=())
    with pytest.raises(SchemaValidationError):
        await h.call("batch_upsert_nodes", [{"id": "r1", "isDeleted": False}, {"id": "r1", "indexingStatus": "COMPLETED"}], "records")
    assert h.writes == []


# ---- 5. the org is resolved before the payload is judged -------------------------------------------------------------------------------

@pytest.mark.parametrize(("method", "args", "kwargs"), [
    ("create_node_if_absent", ("r1", "records", {"id": "r1", "recordName": 5}), {}),
    ("batch_update_nodes", ([{"id": "r1", "recordName": 5}], "records"), {}),
    ("update_knowledge_base", ("kb1", {"name": 5}), {}),
    ("ensure_app_membership", ("u1", "users", "c1"), {"is_external": "yes"}),
    ("update_node", ("r1", "records", {"recordName": 5}), {}),
    ("increment_node_field", ("r1", "records", "recordName"), {}),
    ("record_purge_failure", (["r1"], ORG, 5), {}),
    ("retag_push_record", (), {"key": "r1", "kb_id": "kb", "org_id": ORG, "expected_revision": "a", "new_revision": 5}),
])
async def test_a_missing_org_context_is_raised_before_a_schema_error(method: str, args: tuple, kwargs: dict) -> None:
    h = Harness()
    with pytest.raises(MissingOrgContext):
        await getattr(h.p, method)(*args, **kwargs)      # no OrgContext is bound here, and the payload is invalid as well
    assert_nothing_issued(h)


# ---- 6. the bare app stub --------------------------------------------------------------------------------------------------------------

def test_an_unnamed_app_stub_is_inactive_and_stamped() -> None:
    stub = _stub_app("c1")
    assert stub["isActive"] is False and stub["updatedAtTimestamp"] == stub["createdAtTimestamp"]


def test_a_named_app_stub_stays_active() -> None:
    assert _stub_app("c1", "JIRA")["isActive"] is True


# ---- knowledge base grants: a schema rejection is not turned into a failure result ----------------------------------------------------------

async def test_create_kb_permissions_does_not_swallow_a_schema_rejection() -> None:
    # The method answers every other error with {"success": False}; a rejected grant must reach the caller as the rejection it is.
    h = Harness()
    h.p._q = AsyncMock(return_value=[{"coll": "apps", "id": "kb1"}, {"coll": "users", "id": "u1"}])
    with pytest.raises(SchemaValidationError):
        await h.call("create_kb_permissions", "kb1", "requester", ["u1"], [], "NOT_A_ROLE")
