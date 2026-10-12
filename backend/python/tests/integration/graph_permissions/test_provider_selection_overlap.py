"""``get_selection_nodes`` when the selected roots are nested in one another:
every distinct node comes back once, on both backends.

A root folder holds 18 nodes, its child folder 15 of them, the grandchild
folder 12. Selecting all three reaches each grandchild node three times, so a
limit applied before the repeats are folded would lose nodes or miss the cap.
"""

from __future__ import annotations

import pytest

from .fixture_graph import DRIVE, ORG, app, bt, nr, rec, rg
from .loaders import arango_shape, load_into_arango, load_into_neo4j

pytestmark = pytest.mark.integration

APP, GROUP = "ovl-app", "ovl-g"
ROOT, CHILD, GRAND = "ovl-root", "ovl-child", "ovl-grand"
ROOT_FILES = [f"ovl-r{i}" for i in range(2)]
CHILD_FILES = [f"ovl-c{i}" for i in range(2)]
GRAND_FILES = [f"ovl-g{i:02d}" for i in range(11)]
ALL = {ROOT, CHILD, GRAND, *ROOT_FILES, *CHILD_FILES, *GRAND_FILES}


def _file(node_id: str) -> dict:
    return rec(node_id, node_id, connector_id=APP, virtualRecordId=f"v-{node_id}")


def _folder(node_id: str) -> dict:
    return rec(node_id, node_id, connector_id=APP, mimeType="text/directory")


@pytest.fixture(scope="module")
async def overlapping_graph(neo4j_provider, arango_provider, neo4j_settings, arango_settings) -> None:
    nodes = [
        app(APP, "Overlap drive", connector=DRIVE, app_group="Google Workspace"),
        rg(GROUP, GROUP, group_type="DRIVE", connector=DRIVE, connectorId=APP),
        _folder(ROOT), _folder(CHILD), _folder(GRAND),
        *[_file(f) for f in ROOT_FILES + CHILD_FILES + GRAND_FILES],
    ]
    edges = [
        nr(APP, GROUP), nr(GROUP, ROOT), bt(ROOT, GROUP),
        *[nr(ROOT, f) for f in ROOT_FILES], nr(ROOT, CHILD),
        *[nr(CHILD, f) for f in CHILD_FILES], nr(CHILD, GRAND),
        *[nr(GRAND, f) for f in GRAND_FILES],
    ]
    await load_into_neo4j(neo4j_settings, nodes, edges)
    await load_into_arango(arango_settings, *arango_shape(nodes, edges))


@pytest.fixture(params=["neo4j", "arango"])
def provider(request: pytest.FixtureRequest) -> object:
    return request.getfixturevalue(f"{request.param}_provider")


async def _records(provider, roots, limit) -> set[str]:
    found = await provider.get_selection_nodes(
        ORG, group_ids=[], record_ids=list(roots), exact_record_ids=[], limit=limit,
    )
    return {row["id"] for row in found["records"]}


def test_the_fixture_has_the_sizes_the_case_names() -> None:
    assert len(ALL) == 18
    assert len({CHILD, GRAND, *CHILD_FILES, *GRAND_FILES}) == 15
    assert len({GRAND, *GRAND_FILES}) == 12


async def test_the_root_alone_gives_18(overlapping_graph, provider) -> None:
    assert await _records(provider, [ROOT], 20) == ALL


@pytest.mark.parametrize("limit", [18, 20, 25, 50])
async def test_three_nested_roots_still_give_all_18(overlapping_graph, provider, limit) -> None:
    assert await _records(provider, [ROOT, CHILD, GRAND], limit) == ALL


async def test_two_nested_roots_at_a_limit_just_above_the_set(overlapping_graph, provider) -> None:
    assert await _records(provider, [CHILD, GRAND], 15) == {CHILD, GRAND, *CHILD_FILES, *GRAND_FILES}


async def test_over_the_limit_is_still_signalled_with_nested_roots(overlapping_graph, provider) -> None:
    assert len(await _records(provider, [ROOT, CHILD, GRAND], 10)) == 11
