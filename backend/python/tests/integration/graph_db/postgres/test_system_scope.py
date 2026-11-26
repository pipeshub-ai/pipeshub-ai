"""The schedule sweep reads connector instances across orgs under an explicit system scope, on PostgreSQL, through the vetted
``graph.system_*`` functions: the service's own login is enough (no jobs login). Anything else needs a single org.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.api.router import get_all_scheduled_connector_instances_internal
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
            # ``after_key`` continues after the id whichever orgs hold it: neither holder of the shared id comes back, and both holders of
            # the later id do, in order. Other tests' apps may sit between the two ids, so page on from ``shared`` (a bounded number of
            # pages) and look at this test's rows only. A page may end between two holders of an id, so the next one restarts at the
            # id before the last, and a row seen twice counts once.
            mine: dict[tuple[str, str], None] = {}
            cursor, size = shared, 50
            for _ in range(200):
                page = await pg_provider.get_documents_paginated("apps", limit=size, filters={"isActive": True}, after_key=cursor, raise_on_error=True)
                assert shared not in {d["_key"] for d in page}
                mine.update({(d["_key"], d["orgId"]): None for d in page if d["_key"].endswith(tag)})
                if len(mine) == len(holders) or len(page) < size:
                    break
                keys = [d["_key"] for d in page]
                cursor = max((k for k in keys if k < keys[-1]), default=keys[-1])
            assert list(mine) == [(last, org) for org in holders]
    finally:
        for org in (a, b):
            with bind(OrgContext(org, None, "job", "job:test", "t")):
                await pg_provider._db.run(lambda tx, org=org: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", org), write=True)


def _route_request(pg_provider, scheduled: set[str]) -> MagicMock:
    """The request the schedule sweep sends: this provider, and every listed connector's settings say SCHEDULED."""
    async def get_config(path: str, **_kw: object) -> dict | None:
        cid = path.rstrip("/").split("/")[-2]
        return {"sync": {"selectedStrategy": "SCHEDULED", "scheduledConfig": {"intervalMinutes": 30}}} if cid in scheduled else None

    config_service = MagicMock(get_config=AsyncMock(side_effect=get_config))
    request = MagicMock()
    request.app.container.logger.return_value = logging.getLogger("all-scheduled")
    request.app.container.config_service.return_value = config_service
    request.app.state.graph_provider = pg_provider
    return request


async def test_the_schedule_sweep_route_lists_every_orgs_scheduled_connectors(pg_provider, two_orgs, org_ctx) -> None:
    """Under the sweep's own request context (the org-less service token), each org's scheduled connector is listed with its own org."""
    a, b, tag = two_orgs
    for org in (a, b):
        with org_ctx(org):
            await pg_provider.batch_upsert_nodes([{"_key": org, "name": f"org {org}", "isActive": True}], "organizations")
    apps = {f"app-a-{tag}": a, f"app-b-{tag}": b}
    request = _route_request(pg_provider, set(apps))
    with bind(OrgContext(org_context.SYSTEM_ORG_ID, "system", "service", "scoped_jwt", "t")):
        listed, page = [], 1
        while True:
            answer = await get_all_scheduled_connector_instances_internal(request, page=page, limit=200, connector_ids=None)
            listed += answer["items"]
            if not answer["hasMore"]:
                break
            page += 1
        by_id = await get_all_scheduled_connector_instances_internal(request, page=1, limit=50, connector_ids=",".join(apps))
    assert answer["partial"] is False
    assert {i["connectorId"]: i["orgId"] for i in listed if i["connectorId"].endswith(tag)} == apps
    assert {i["connectorId"]: (i["orgId"], i["isActive"]) for i in by_id["items"]} == {k: (v, True) for k, v in apps.items()}


async def test_an_app_with_no_org_field_is_looked_up_and_listed_under_the_org_that_holds_it(pg_provider, org_ctx) -> None:
    """A legacy app stored without orgId names its org by the partition it is in, not by whichever org sorts first."""
    first, holder = f"000000{uuid.uuid4().hex[:18]}", f"ffffff{uuid.uuid4().hex[:18]}"       # org ids are 24 hex digits; the holder sorts last
    key = f"legacy-{uuid.uuid4().hex[:8]}"
    for org in (first, holder):
        with org_ctx(org):
            await pg_provider.batch_upsert_nodes([{"_key": org, "name": f"org {org}", "isActive": True}], "organizations")
    with org_ctx(holder):
        await pg_provider.batch_upsert_nodes([{"_key": key, "name": "Legacy", "type": "JIRA", "isActive": True}], "apps")
        assert "orgId" not in await pg_provider.get_document(key, "apps", raise_on_error=True)
    request = _route_request(pg_provider, {key})
    try:
        with bind(OrgContext(org_context.SYSTEM_ORG_ID, "system", "service", "scoped_jwt", "t")):
            by_id = await get_all_scheduled_connector_instances_internal(request, page=1, limit=50, connector_ids=key)
            listed, page = [], 1
            while True:
                answer = await get_all_scheduled_connector_instances_internal(request, page=page, limit=200, connector_ids=None)
                listed += [i for i in answer["items"] if i["connectorId"] == key]
                if not answer["hasMore"]:
                    break
                page += 1
        assert [(i["connectorId"], i["orgId"]) for i in by_id["items"]] == [(key, holder)]
        assert [i["orgId"] for i in listed] == [holder]
    finally:
        for org in (first, holder):
            with bind(OrgContext(org, None, "job", "job:test", "t")):
                await pg_provider._db.run(lambda tx, org=org: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", org), write=True)
