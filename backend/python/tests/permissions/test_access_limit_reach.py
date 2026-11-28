"""Entity search and the team connector list honour a connector's access limit on Neo4j and ArangoDB, for every access-limit
scenario and every generated world (``limit_reach.py``; PostgreSQL: ``graph_db/postgres/test_access_limit_reach_pg.py``).
Needs the throwaway stores of `tests/integration/graph_permissions/docker-compose.yml`."""

from __future__ import annotations

import pytest

from app.config.constants.arangodb import CollectionNames

from . import gen
from .graph import TS
from .limit_reach import ListFn, TeamFn, list_findings, org_apps, reach_findings
from .scenario import load_all

pytestmark = [pytest.mark.integration, pytest.mark.timeout(3600)]

CHUNK = 25


def limit_worlds() -> list:
    return list(dict.fromkeys(s.final_world() for s in load_all() if "access-limit" in s.tags))


def generated_worlds(kind: str) -> list:
    return [w for seed in gen.seeds() for w in gen.generate(kind, seed, gen.world_count())]


def _team(layer, org: str) -> TeamFn:
    """The team list's limited arm as get_filtered_connector_instances asks it: under the request's org."""
    if layer.name == "arango":
        return lambda user: layer._provider._get_user_accessible_team_app_keys(user, org_id=org)
    return lambda user: layer._provider._get_user_accessible_team_app_ids(user, org_id=org)


async def _check(layer, worlds: list, tag: str) -> list[str]:
    found: list[str] = []
    for start in range(0, len(worlds), CHUNK):
        chunk = worlds[start:start + CHUNK]
        await layer.preload(chunk)
        for offset, world in enumerate(chunk):
            ns = await layer._ns(world)

            async def entity(user: str, ns=ns) -> dict | None:
                return await layer._provider.get_entity_access_context(user, ns.org)

            bad = await reach_findings(world, layer._compiled(world), ns, entity, _team(layer, ns.org))
            found += [f"{layer.name} {tag}-{start + offset}: {b}" for b in bad]
        await layer.release(chunk)
    return found


@pytest.mark.parametrize("kind", ["scenarios", *sorted(gen.GENERATORS)])
async def test_entity_search_and_the_team_list_reach_a_limited_connector_exactly_as_its_limit_says(graph_layers, kind: str) -> None:
    worlds = limit_worlds() if kind == "scenarios" else generated_worlds(kind)
    limited = sum(1 for w in worlds for n in w.nodes if n.kind == "app" and n.access_limit is not None)
    assert limited, "no world carries an access limit: nothing was checked"
    for layer in graph_layers:
        found = await _check(layer, worlds, kind)
        assert not found, "\n".join(found[:40])


def _listed(provider, org: str) -> ListFn:
    """The connectors page lists as the routes ask them, for a member (the limit applied) or an admin (not)."""
    async def listed(user: str, limited: bool) -> dict[str, set[str]]:
        mine = await provider.get_user_connector_instances(
            collection=CollectionNames.APPS.value, user_id=user, org_id=org, team_scope="team", personal_scope="personal",
            apply_access_limit=limited)
        page, _ = await provider.get_filtered_connector_instances(
            collection=CollectionNames.APPS.value, edge_collection=CollectionNames.ORG_APP_RELATION.value, org_id=org, user_id=user,
            limit=10000, apply_access_limit=limited)
        return {"instances": {a.get("_key") or a.get("id") for a in mine}, "filtered": {a.get("_key") or a.get("id") for a in page}}
    return listed


@pytest.mark.parametrize("kind", ["scenarios", *sorted(gen.GENERATORS)])
async def test_the_connector_lists_show_a_limited_connector_exactly_as_its_limit_says(graph_layers, kind: str) -> None:
    worlds = limit_worlds() if kind == "scenarios" else generated_worlds(kind)
    for layer in graph_layers:
        found: list[str] = []
        for start in range(0, len(worlds), CHUNK):
            chunk = worlds[start:start + CHUNK]
            await layer.preload(chunk)
            for offset, world in enumerate(chunk):
                ns, graph = await layer._ns(world), layer._compiled(world)
                if apps := org_apps(graph, ns):
                    await layer._provider.batch_create_edges(
                        [{"from_id": ns.org_node, "from_collection": CollectionNames.ORGS.value, "to_id": a,
                          "to_collection": CollectionNames.APPS.value, "createdAtTimestamp": TS} for a in apps],
                        collection=CollectionNames.ORG_APP_RELATION.value)
                bad = await list_findings(world, graph, ns, _listed(layer._provider, ns.org))
                found += [f"{layer.name} {kind}-{start + offset}: {b}" for b in bad]
            await layer.release(chunk)
        assert not found, "\n".join(found[:40])
