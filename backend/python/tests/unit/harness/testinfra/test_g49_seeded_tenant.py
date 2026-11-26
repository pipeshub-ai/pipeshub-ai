"""G-49: the seeded fixture tenant has the connectors and the access variety the P0 journeys need, and its declared
access is what the record-access rule decides (the rule Neo4j and ArangoDB are held to)."""

from __future__ import annotations

from tests.support.seeded_tenant import load_tenant


def test_g49_the_tenant_spans_four_connectors_with_varied_access() -> None:
    tenant = load_tenant()
    assert {a.connector for a in tenant.apps} == {"SLACK", "JIRA", "DRIVE", "CONFLUENCE"}
    grants = [g for r in tenant.records for g in r.grants]
    assert any(g.group for g in grants) and any(g.user for g in grants)
    assert any(r.deleted for r in tenant.records), "a record in the trash"
    assert any(u.apps is not None for u in tenant.users), "a user limited to some connectors"
    assert any(not tenant.readable(u.id) for u in tenant.users), "a user who reads nothing"
    readers = {frozenset(tenant.readable(u.id)) for u in tenant.users}
    assert len(readers) == len(tenant.users), "every user reads a different set"


async def test_g49_the_declared_access_is_what_the_rule_decides() -> None:
    await load_tenant().check_expectations()


async def test_g49_a_driven_run_acts_as_one_tenant_user() -> None:
    tenant = load_tenant()
    access = tenant.record_access(actor="bob", actor_id="user-1", actor_key="ukey-1")
    ids = [r.record_id for r in tenant.records]
    found = await access.check_access("ukey-1", tenant.org, node_ids=ids)
    assert {rid.removeprefix("rec-") for rid in found.node_ids} == tenant.readable("bob")


def test_g49_connector_toolsets_return_only_what_the_user_reads() -> None:
    tenant = load_tenant()
    alice, bob = tenant.world_fixtures("alice"), tenant.world_fixtures("bob")
    assert {t.key for t in alice.tickets} == {"ATL-101", "ATL-102", "ATL-095"}
    assert bob.tickets == [] and list(bob.slack) == ["#atlas-sales"]
    assert "atlas-design-v1" not in {d.id for d in alice.knowledge}, "the trashed record is not indexed"
