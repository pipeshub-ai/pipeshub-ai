"""The schedule sweep reads connector instances across orgs under an explicit system scope, on PostgreSQL, through the vetted
``graph.system_*`` functions: the service's own login is enough (no jobs login). Anything else needs a single org.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING

import pytest

from app.core import org_context
from app.core.org_context import OrgContext, SystemContextNotAllowed, bind

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.integration


@pytest.fixture
async def two_orgs(pg_provider, org_ctx) -> AsyncIterator[tuple[str, str, str]]:
    os.environ.pop("PG_JOBS_URL", None)            # proves the reads need no jobs login
    tag = uuid.uuid4().hex[:8]
    a, b = uuid.uuid4().hex[:24], uuid.uuid4().hex[:24]
    for org, name in ((a, "a"), (b, "b")):
        with org_ctx(org):
            await pg_provider.batch_upsert_nodes(
                [{"_key": f"app-{name}-{tag}", "name": f"Jira {name}", "type": "JIRA", "isActive": True, "orgId": org},
                 {"_key": f"off-{name}-{tag}", "name": f"Off {name}", "type": "JIRA", "isActive": False, "orgId": org}], "apps")
    yield a, b, tag
    for org in (a, b):
        with bind(OrgContext(org, None, "job", "job:test", "t")):
            await pg_provider._db.run(lambda tx, org=org: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", org), write=True)


async def test_system_scope_reads_the_active_apps_of_every_org(pg_provider, two_orgs) -> None:
    a, b, tag = two_orgs
    with org_context.system("test.all-scheduled"):
        page = await pg_provider.get_documents_paginated("apps", skip=0, limit=1000, filters={"isActive": True}, sort_field="_key", raise_on_error=True)
        one = await pg_provider.get_document(f"app-b-{tag}", "apps", raise_on_error=True)
        missing = await pg_provider.get_document("nope", "apps", raise_on_error=True)
    mine = {d["_key"]: d for d in page if d["_key"].endswith(tag)}
    assert set(mine) == {f"app-a-{tag}", f"app-b-{tag}"}                  # active apps of both orgs; the inactive ones are filtered
    assert mine[f"app-a-{tag}"]["orgId"] == a and mine[f"app-b-{tag}"]["orgId"] == b
    assert one and one["orgId"] == b and missing is None


async def test_an_org_scope_still_sees_only_its_own_apps(pg_provider, two_orgs, org_ctx) -> None:
    a, b, tag = two_orgs
    with org_ctx(a):
        page = await pg_provider.get_documents_paginated("apps", limit=100, raise_on_error=True)
        other = await pg_provider.get_document(f"app-b-{tag}", "apps", raise_on_error=True)
    assert {d["_key"] for d in page if d["_key"].endswith(tag)} == {f"app-a-{tag}", f"off-a-{tag}"}
    assert other is None


async def test_system_scope_reads_no_other_collection_across_orgs(pg_provider, two_orgs) -> None:
    with org_context.system("test.other-collection"), pytest.raises(SystemContextNotAllowed):
        await pg_provider.get_document("x", "records", raise_on_error=True)
    with org_context.system("test.other-collection"), pytest.raises(SystemContextNotAllowed):
        await pg_provider.get_documents_paginated("users", raise_on_error=True)
    with org_context.system("test.other-filter"), pytest.raises(ValueError, match="isActive"):
        await pg_provider.get_documents_paginated("apps", filters={"name": "x"}, raise_on_error=True)


async def test_the_app_listing_pages_over_an_id_two_orgs_hold(pg_provider, org_ctx) -> None:
    """An id is unique per org only: a page boundary between its two holders loses neither, and ``after_key`` continues after the id."""
    tag = uuid.uuid4().hex[:8]
    a, b = uuid.uuid4().hex[:24], uuid.uuid4().hex[:24]
    shared = f"dup-{tag}"
    for org in (a, b):
        with org_ctx(org):
            await pg_provider.batch_upsert_nodes([{"_key": shared, "isActive": True, "orgId": org}, {"_key": f"zz-{tag}", "isActive": True, "orgId": org}], "apps")
    try:
        with org_context.system("test.paging"):
            every = await pg_provider.get_documents_paginated("apps", limit=1000, filters={"isActive": True}, sort_field="_key", raise_on_error=True)
            ours = [(d["_key"], d["orgId"]) for d in every if d["_key"].endswith(tag)]
            assert sorted(ours) == sorted([(shared, a), (shared, b), (f"zz-{tag}", a), (f"zz-{tag}", b)])
            # one row per page, from the start: every row appears once, the shared id twice
            seen = []
            for skip in range(len(every)):
                page = await pg_provider.get_documents_paginated("apps", skip=skip, limit=1, filters={"isActive": True}, raise_on_error=True)
                seen += [(d["_key"], d["orgId"]) for d in page if d["_key"].endswith(tag)]
            assert sorted(seen) == sorted(ours)
            after = await pg_provider.get_documents_paginated("apps", limit=1000, filters={"isActive": True}, after_key=shared, raise_on_error=True)
            assert shared not in {d["_key"] for d in after} and {d["orgId"] for d in after if d["_key"] == f"zz-{tag}"} == {a, b}
    finally:
        for org in (a, b):
            with bind(OrgContext(org, None, "job", "job:test", "t")):
                await pg_provider._db.run(lambda tx, org=org: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", org), write=True)
