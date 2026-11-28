"""A skill write that passed its ownership check lands only on the skill that was checked, on PostgreSQL.

The cases of `test_skill_writes_bound_to_creation_real_backends.py` on the PostgreSQL graph provider with schema
validation on: a caller held after its ownership read (and at its first write) while the skill is deleted and the
name created again by another user must leave the new skill, its owner and its agent edges unchanged, and must not
report success; `SkillManager.delete` held after its binding or its usage check likewise. Needs a migrated database: PG_CORE_URL, PG_POOLER=none.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from tests.integration.graph_db.test_skill_writes_bound_to_creation_real_backends import (
    manager_delete_cases,
    race_cases,
    run_case,
    run_manager_delete,
    run_stale_binding_delete,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.integration


@pytest.fixture
async def org(pg_db, org_ctx, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[str]:
    monkeypatch.setenv("GRAPH_SCHEMA_ENFORCEMENT", "strict")
    org_id = uuid.uuid4().hex[:24]
    yield org_id
    with org_ctx(org_id):
        await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.edges WHERE org_id = $1", org_id), write=True)
        await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", org_id), write=True)


@race_cases
async def test_a_held_write_leaves_the_skill_created_again_alone(
    pg_provider, org, org_ctx, monkeypatch, case, same_millisecond, hold_at,
) -> None:
    with org_ctx(org):
        await run_case(pg_provider, org, monkeypatch, case, same_millisecond=same_millisecond, hold_at=hold_at)


@manager_delete_cases
async def test_a_manager_delete_binds_to_the_read_before_its_usage_check(pg_provider, org, org_ctx, admin, detach, hold_at) -> None:
    with org_ctx(org):
        await run_manager_delete(pg_provider, org, admin=admin, detach=detach, hold_at=hold_at)


@pytest.mark.parametrize("admin", [False, True], ids=["owner", "admin"])
async def test_a_store_delete_with_a_stale_binding_is_a_conflict(pg_provider, org, org_ctx, admin) -> None:
    with org_ctx(org):
        await run_stale_binding_delete(pg_provider, org, admin=admin)
