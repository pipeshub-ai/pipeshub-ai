"""Entity search and the team connector list honour a connector's access limit on Neo4j and ArangoDB, for every access-limit
scenario and every generated world (``limit_reach.py``; PostgreSQL: ``graph_db/postgres/test_access_limit_reach_pg.py``).
Needs the throwaway stores of `tests/integration/graph_permissions/docker-compose.yml`."""

from __future__ import annotations

import pytest

from . import gen
from .limit_reach import TeamFn, reach_findings
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
