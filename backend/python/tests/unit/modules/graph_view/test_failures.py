"""A failed check or read is a 503, never an empty graph, at every stage.
"""
import pytest

from app.config.constants.arangodb import CollectionNames
from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
from tests.unit.agents.actions.code_graph.conftest import CONN, ORG
from tests.unit.modules.graph_view.world import (
    World,
    client,
    expand,
    personas,
    revalidate,
)

BLOCKS = CollectionNames.BLOCKS.value
CODE_FILES = CollectionNames.CODE_FILES.value
RECORD_GROUPS = CollectionNames.RECORD_GROUPS.value


def failing(world: World, method: str, only_collection: str | None = None, exc: Exception | None = None) -> World:
    real = getattr(world, method)
    boom = exc or RuntimeError("store down: marker-7f3a block:marker-7f3a")

    async def wrapper(*a, **k) -> object:
        if only_collection is None or k.get("collection", a[0] if a else None) == only_collection:
            raise boom
        return await real(*a, **k)

    setattr(world, method, wrapper)
    return world


STAGES = [
    ("neighbour page", "get_neighbors_for_nodes_by_relationship_types", None),
    ("access filter", "filter_accessible_record_ids", None),
    ("anchor and node load", "get_nodes_by_field_in", BLOCKS),
    ("roles", "get_nodes_by_field_in", CODE_FILES),
    ("repo names", "get_nodes_by_field_in", RECORD_GROUPS),
    ("file paths", "get_file_paths_for_records", None),
]


@pytest.mark.parametrize("stage,method,coll", STAGES, ids=[s[0] for s in STAGES])
def test_expand_is_a_503_when_a_stage_fails(stage, method, coll) -> None:
    res = expand(failing(World(personas()), method, coll), "bob", ["main"])
    assert res.status_code == 503, (stage, res.status_code, res.text)
    assert res.json() == {"detail": "Graph unavailable."}
    assert res.headers["cache-control"] == "private, no-store"



@pytest.mark.parametrize("stage,method,coll", STAGES[1:4], ids=[s[0] for s in STAGES[1:4]])
def test_revalidate_is_a_503_when_a_stage_fails(stage, method, coll) -> None:
    res = revalidate(failing(World(personas()), method, coll), "bob", ["block:main"])
    assert res.status_code == 503, (stage, res.status_code, res.text)


def test_the_record_check_failing_in_the_provider_contract_way_is_a_503() -> None:
    world = World(personas())

    async def unavailable(*a, **k) -> None:
        raise PermissionVerificationUnavailableError("down")

    world.filter_accessible_record_ids = unavailable
    assert expand(world, "bob", ["main"]).status_code == 503


# -- the limit check -------------------------------------------------------------------------------------

LIMITED = {"orgId": ORG, "userId": "bob", "knowledgeLimit": {"apps": [CONN]}}




def test_the_provider_missing_is_an_error_with_no_store() -> None:
    world = World(personas())

    class Down:
        async def graph_provider(self) -> None:
            raise ConnectionError("store down")

    c = client(world)
    c.app.container = Down()
    c = type(c)(c.app, raise_server_exceptions=False)
    res = c.post("/api/v1/graph/expand", json={"source": "code", "nodeIds": ["block:main"]})
    assert res.status_code in (500, 503)
    assert res.headers.get("cache-control") == "private, no-store"
