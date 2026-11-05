"""The schedule sweep reads connector instances across orgs under an explicit system scope, on PostgreSQL, through the vetted
``graph.system_*`` functions: the service's own login is enough (no jobs login). Anything else needs a single org.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from app.core import org_context
from app.core.org_context import OrgContext, SystemContextNotAllowed, bind

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _no_jobs_login(monkeypatch: pytest.MonkeyPatch) -> None:
    """Before ``pg_db`` starts: the reads of a system scope need no jobs login, and these tests prove it."""
    monkeypatch.delenv("PG_JOBS_URL", raising=False)


@pytest.fixture
async def two_orgs(pg_provider, org_ctx) -> AsyncIterator[tuple[str, str, str]]:
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
    """An id is unique per org only: a page boundary between its two holders loses neither, and ``after_key`` continues after the id.

    The database is shared: tests that run beside this one add and remove apps, so a page of the whole listing at a fixed offset can
    move. Each id is paged from just before it (``after_key`` is a prefix of the id, which sorts first), where only a row of this test
    can be, and the checks are on the rows of this test."""
    tag = uuid.uuid4().hex[:8]
    a, b = uuid.uuid4().hex[:24], uuid.uuid4().hex[:24]
    shared, last = f"dup-{tag}", f"zz-{tag}"
    for org in (a, b):
        with org_ctx(org):
            await pg_provider.batch_upsert_nodes([{"_key": shared, "isActive": True, "orgId": org}, {"_key": last, "isActive": True, "orgId": org}], "apps")
    holders = sorted([a, b])                                  # the order of the listing: by id, then by org
    try:
        with org_context.system("test.paging"):
            for key in (shared, last):
                # one row per page from just before the id: both holders appear, in order, one on each side of the page boundary
                for skip, org in enumerate(holders):
                    page = await pg_provider.get_documents_paginated(
                        "apps", skip=skip, limit=1, filters={"isActive": True}, after_key=key[:-1], raise_on_error=True)
                    assert [(d["_key"], d["orgId"]) for d in page] == [(key, org)]
                # two rows in one page, and the page after them does not begin with either
                both = await pg_provider.get_documents_paginated(
                    "apps", limit=2, filters={"isActive": True}, after_key=key[:-1], raise_on_error=True)
                assert sorted((d["_key"], d["orgId"]) for d in both) == sorted((key, org) for org in holders)
            # ``after_key`` continues after the id whichever orgs hold it: neither holder of the shared id comes back
            after = await pg_provider.get_documents_paginated("apps", limit=1, filters={"isActive": True}, after_key=shared, raise_on_error=True)
            assert after and after[0]["_key"] != shared
    finally:
        for org in (a, b):
            with bind(OrgContext(org, None, "job", "job:test", "t")):
                await pg_provider._db.run(lambda tx, org=org: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", org), write=True)
