"""Organisations a document names (KG-13 slice 3b) resolve through the same
tiers as taxonomy: their key against the tenant's organisations, then the
nearest organisation points and the record's one model call. The record then
links to each with an EXTRACTED edge, replacing only its own."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors
from app.models.entities import EntityRecord, EntityType
from app.modules.entity_resolution.keys import taxonomy_node_key
from app.modules.transformers.sink_orchestrator import SinkOrchestrator

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.modules.entity_resolution.models import EntityResolution

ORG = "acme-tenant"
ORGS = CollectionNames.ORGS.value


async def _resolve(make_resolver, ctx_factory, metadata, **ctx: object) -> tuple[Any, EntityResolution]:
    context = ctx_factory("r1", ORG, metadata, **ctx)
    return context, await make_resolver().resolve(context)


async def test_a_new_name_becomes_a_new_organisation_of_the_tenant(make_resolver, ctx_factory, metadata_factory) -> None:
    meta = metadata_factory(organizations=["Globex Corp."])
    _, resolution = await _resolve(make_resolver, ctx_factory, meta)
    (entity,) = resolution.entries.values()
    # Trailing punctuation goes, as for every resolved name.
    assert (entity.key, entity.name, entity.is_new) == (taxonomy_node_key(ORG, ORGS, "globex"), "Globex Corp", True)
    assert meta.organizations == ["Globex Corp"]


async def test_a_name_finds_the_account_by_its_key(make_resolver, ctx_factory, metadata_factory, fake_graph) -> None:
    fake_graph.add_account(ORG, "acc-globex", "Globex Corporation")
    meta = metadata_factory(organizations=["GLOBEX corp"])
    _, resolution = await _resolve(make_resolver, ctx_factory, meta)
    (entity,) = resolution.entries.values()
    assert (entity.key, entity.decision, entity.is_new) == ("acc-globex", "exact", False)
    # The record links to the account under its own name.
    assert meta.organizations == ["Globex Corporation"]


async def test_the_tenant_and_the_source_app_are_not_organisations_of_the_record(
    make_resolver, ctx_factory, metadata_factory, fake_graph,
) -> None:
    fake_graph.tenant_names[ORG] = "Northwind Analytics"
    meta = metadata_factory(organizations=["Northwind", "Slack", "Initech"])
    _, resolution = await _resolve(make_resolver, ctx_factory, meta, connector_name=Connectors.SLACK)
    assert [e.name for e in resolution.entries.values()] == ["Initech"]


async def test_a_failed_tenant_lookup_still_resolves(make_resolver, ctx_factory, metadata_factory, fake_graph) -> None:
    fake_graph.tenant_names[ORG] = "Northwind"
    fake_graph.fail_tenant_lookup = True
    _, resolution = await _resolve(make_resolver, ctx_factory, metadata_factory(organizations=["Initech"]))
    assert [e.name for e in resolution.entries.values()] == ["Initech"]


async def test_a_spelling_the_key_misses_is_offered_to_the_model(
    make_resolver, ctx_factory, metadata_factory, fake_store, scripted_model,
) -> None:
    await fake_store.upsert_entities_batch([EntityRecord.for_linked(
        EntityType.ORGANIZATION, "acc-ibm", "International Business Machines", ORG, "conn-sf", None,
    )])
    model = scripted_model({"IBM business machines": ("same", "International Business Machines")})
    meta = metadata_factory(organizations=["IBM business machines"])
    _, resolution = await _resolve(make_resolver, ctx_factory, meta)
    (entity,) = resolution.entries.values()
    assert (entity.key, entity.decision) == ("acc-ibm", "merge")
    (items,) = model.calls
    assert items[0]["kind"] == "organization"


async def test_another_tenants_organisation_is_never_offered(
    make_resolver, ctx_factory, metadata_factory, fake_store, fake_graph, scripted_model,
) -> None:
    await fake_store.upsert_entities_batch([EntityRecord.for_linked(
        EntityType.ORGANIZATION, "theirs", "Globex Holdings", ORG, "conn-sf", None,
    )])
    fake_graph.nodes[(ORGS, "theirs")]["parentOrgId"] = "another-tenant"
    model = scripted_model()
    _, resolution = await _resolve(make_resolver, ctx_factory, metadata_factory(organizations=["Globex"]))
    assert [e.is_new for e in resolution.entries.values()] == [True]
    assert model.calls == []


async def test_several_new_organisations_cost_no_model_call(
    make_resolver, ctx_factory, metadata_factory, scripted_model,
) -> None:
    """Spellings of one organisation already share a key, so new names alone
    have nothing for the model to group."""
    model = scripted_model()
    meta = metadata_factory(organizations=["Initech", "Hooli", "Globex"])
    _, resolution = await _resolve(make_resolver, ctx_factory, meta)
    assert len(resolution.entries) == 3 and model.calls == []


# ---------------------------------------------------------------------------
# Through the graph write and the entity index
# ---------------------------------------------------------------------------

@pytest.fixture
def run(
    make_resolver, make_transformer, fake_graph, fake_store, ctx_factory, metadata_factory, scripted_model,
) -> Callable[..., Awaitable[Any]]:
    scripted_model()
    sink = SinkOrchestrator(
        graphdb=make_transformer(), blob_storage=MagicMock(), vector_store=MagicMock(),
        graph_provider=MagicMock(), logger=MagicMock(), config_service=MagicMock(),
        entity_vector_store=fake_store, entity_resolver=make_resolver(),
    )

    async def _run(record_id: str, organizations: list[str], **meta: object) -> object:
        if record_id not in fake_graph.records:
            fake_graph.add_record(record_id, ORG)
        ctx = ctx_factory(record_id, ORG, metadata_factory(organizations=organizations, **meta))
        await sink.resolve_entities(ctx)
        await sink.enrich(ctx)
        return ctx

    return _run


def _org_points(fake_store) -> list[str]:
    return sorted(p["name"] for p in fake_store.points_of(ORG, "organization"))


async def test_the_record_links_to_each_organisation_it_names(run, fake_graph) -> None:
    fake_graph.add_account(ORG, "acc-globex", "Globex Corporation")
    await run("r1", ["Globex corp.", "Initech"])
    initech = taxonomy_node_key(ORG, ORGS, "initech")
    assert fake_graph.organization_edges("r1") == sorted([
        ("acc-globex", "MENTIONS", "EXTRACTED"), (initech, "MENTIONS", "EXTRACTED"),
    ])
    edge = next(e for e in fake_graph.entity_relations if e["_to"].endswith("acc-globex"))
    assert (edge["extractedName"], edge["source"]) == ("Globex corp", "extraction")
    created = fake_graph.node(ORGS, initech)
    assert (created["name"], created["parentOrgId"], created["isExternal"]) == ("Initech", ORG, True)


async def test_a_re_extraction_replaces_only_its_own_links(run, fake_graph) -> None:
    fake_graph.add_account(ORG, "acc-globex", "Globex Corporation")
    fake_graph.add_record("r1", ORG)
    fake_graph.account_edge("r1", "acc-globex")
    await run("r1", ["Initech"])
    await run("r1", ["Hooli"])
    assert fake_graph.organization_edges("r1") == sorted([
        ("acc-globex", "FOR_ACCOUNT", "INFERRED"), (taxonomy_node_key(ORG, ORGS, "hooli"), "MENTIONS", "EXTRACTED"),
    ])
    await run("r1", [])
    assert fake_graph.organization_edges("r1") == [("acc-globex", "FOR_ACCOUNT", "INFERRED")]


async def test_an_extracted_organisation_is_searchable_once_two_records_name_it(run, fake_store) -> None:
    await run("r1", ["Initech"])
    assert _org_points(fake_store) == []
    await run("r1", ["Initech"])  # the same record again is still one record
    assert _org_points(fake_store) == []
    await run("r2", ["initech"])
    assert _org_points(fake_store) == ["Initech"]


async def test_an_account_a_connector_knows_is_searchable_from_the_first_mention(run, fake_graph, fake_store) -> None:
    fake_graph.add_account(ORG, "acc-globex", "Globex Corporation")
    fake_graph.nodes[(ORGS, "acc-globex")]["account"] = True  # its record group's dealOf edge
    await run("r1", ["Globex"])
    assert _org_points(fake_store) == ["Globex Corporation"]


async def test_a_rejected_organisation_write_keeps_the_rest_of_the_record(run, fake_graph) -> None:
    """MENTIONS and normalizedName are new; an older pod's schema rejects them
    during a rolling deploy."""
    fake_graph.fail_organization_writes = True
    ctx = await run("r1", ["Initech"], topics=["Pricing"])
    assert fake_graph.edges_from("r1", CollectionNames.BELONGS_TO_TOPIC.value)
    assert fake_graph.organization_edges("r1") == []
    assert ctx.record.extraction_status == "COMPLETED"


async def test_the_model_is_told_what_makes_two_organisations_one(
    make_resolver, ctx_factory, metadata_factory, fake_store, scripted_model,
) -> None:
    from unittest.mock import patch

    from app.modules.entity_resolution import resolver as resolver_module

    await fake_store.upsert_entities_batch([EntityRecord.for_linked(
        EntityType.ORGANIZATION, "acc", "Globex Holdings", ORG, "conn", None,
    )])
    seen = []
    original = resolver_module.build_prompt
    with patch.object(resolver_module, "build_prompt", side_effect=lambda *a: seen.append(original(*a)) or seen[-1]):
        scripted_model()
        await make_resolver().resolve(ctx_factory("r1", ORG, metadata_factory(organizations=["Globex"])))
    assert "subsidiary" in seen[0]


async def test_a_connectors_account_wins_over_an_extracted_namesake(make_resolver, ctx_factory, metadata_factory, fake_graph) -> None:
    """INFERRED wins identity: whatever the keys sort to."""
    extracted = taxonomy_node_key(ORG, ORGS, "globex")
    fake_graph.nodes[(ORGS, extracted)] = {
        "name": "Globex Corp", "normalizedName": "globex", "isExternal": True, "parentOrgId": ORG,
    }
    for account in ("0-account", "zz-account"):
        fake_graph.add_account(ORG, account, "Globex")
        _, resolution = await _resolve(make_resolver, ctx_factory, metadata_factory(organizations=["Globex"]))
        assert [e.key for e in resolution.entries.values()] == [account]
        del fake_graph.nodes[(ORGS, account)]


async def test_the_threshold_check_stops_counting_at_the_threshold(run, fake_graph) -> None:
    """An organisation named in 100k records is not walked for each record."""
    await run("r1", ["Initech"])
    caps = [args[2] for name, args in fake_graph.calls if name == "get_organization_record_reach"]
    assert caps == [2]


async def test_the_point_made_when_the_threshold_is_crossed_has_every_records_reach(
    make_resolver, make_transformer, fake_graph, fake_store, ctx_factory, metadata_factory, scripted_model,
) -> None:
    """The first record wrote no point, so the crossing record cannot be the
    only membership: users of the first record's connector would miss it."""
    scripted_model()
    sink = SinkOrchestrator(
        graphdb=make_transformer(), blob_storage=MagicMock(), vector_store=MagicMock(),
        graph_provider=MagicMock(), logger=MagicMock(), config_service=MagicMock(),
        entity_vector_store=fake_store, entity_resolver=make_resolver(),
    )
    for record, connector, group in (("r1", "drive", "rg-d"), ("r2", "slack", "rg-s")):
        fake_graph.add_record(record, ORG, connector, group)
        ctx = ctx_factory(record, ORG, metadata_factory(organizations=["Initech"]), connector_id=connector,
                          record_group_id=group)
        await sink.resolve_entities(ctx)
        await sink.enrich(ctx)
    (point,) = fake_store.points_of(ORG, "organization")
    assert sorted(point["connectorIds"]) == ["drive", "slack"]
    assert sorted(point["recordGroupIds"]) == ["rg-d", "rg-s"]
