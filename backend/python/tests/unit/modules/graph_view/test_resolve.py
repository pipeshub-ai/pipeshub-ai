"""Turning what a search hit or a record page has into a node id: hidden and missing are one answer."""
import json

import pytest
from fastapi.responses import JSONResponse

from app.agents.actions.code_graph.repos import CodeRepo
from app.api.routes import graph_view as routes
from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
from tests.unit.agents.actions.code_graph.conftest import CONN, ORG

from .world import OTHER_CONN, Spec, World, client, personas


def resolve(world: World, user: str = "bob", **body: object):  # noqa: ANN201
    return client(world, {"orgId": ORG, "userId": user}).post(
        "/api/v1/graph/resolve", json={"source": "code", **body})


@pytest.fixture(autouse=True)
def one_repo(monkeypatch) -> None:
    async def target(request, graph, org, user, repo, record_id) -> CodeRepo:
        return CodeRepo("acme/shop", CONN, "GITHUB", "grp")

    monkeypatch.setattr(routes, "_target", target)


def spec_of_one_file() -> Spec:
    return Spec(records={"r": CONN}, blocks={"a": "r", "b": "r"}, acl={"bob": {"r"}})


def test_a_record_and_a_name_resolve_to_that_symbol() -> None:
    world = World(spec_of_one_file())
    name = world.blocks["b"]["qualifiedName"]
    res = resolve(world, recordId="r", qualifiedName=name)
    assert res.status_code == 200 and res.json() == {"nodeId": "block:b"}
    assert res.headers["cache-control"] == "private, no-store"


def test_a_file_resolves_to_its_first_definition_and_not_to_a_comment() -> None:
    world = World(spec_of_one_file())
    world.blocks["a"].update(kind="comment", startLine=1)
    world.blocks["b"].update(kind="function", startLine=9)
    assert resolve(world, recordId="r").json() == {"nodeId": "block:b"}


def test_a_path_and_a_name_resolve_to_that_symbol() -> None:
    world = World(spec_of_one_file())
    doc = world.blocks["b"]
    world.records["r"]["recordGroupId"] = "grp"
    res = resolve(world, repo="acme/shop", filePath="src/r.py", qualifiedName=doc["qualifiedName"])
    assert res.json() == {"nodeId": "block:b"}


def test_hidden_missing_and_another_orgs_are_one_answer() -> None:
    spec = Spec(records={"mine": CONN, "secret": CONN, "theirs": CONN},
                blocks={"m": "mine", "s": "secret", "t": "theirs"},
                acl={"bob": {"mine", "theirs"}}, orgs={"t": "other-org"})
    world = World(spec)
    answers = [resolve(world, recordId=r) for r in ("secret", "nope", "theirs")]
    assert {a.status_code for a in answers} == {404}
    assert len({a.content for a in answers}) == 1


def test_a_named_symbol_that_is_not_there_is_the_same_answer() -> None:
    world = World(spec_of_one_file())
    assert resolve(world, recordId="r", qualifiedName="function:nope").content == resolve(world, recordId="nope").content


def test_test_code_needs_asking_for() -> None:
    world = World(Spec(records={"t": CONN}, blocks={"x": "t"}, acl={"bob": {"t"}}, test_records={"t"}))
    assert resolve(world, recordId="t").status_code == 404
    assert resolve(world, recordId="t", includeTests=True).json() == {"nodeId": "block:x"}


def test_a_block_of_another_connector_is_not_found() -> None:
    world = World(Spec(records={"r": OTHER_CONN}, blocks={"a": "r"}, acl={"bob": {"r"}}))
    assert resolve(world, recordId="r").status_code == 404


@pytest.mark.parametrize("body", [{}, {"repo": "acme/shop"}, {"filePath": "src/r.py"}, {"qualifiedName": "function:x"}])
def test_a_body_that_names_no_place_is_refused(body) -> None:
    res = resolve(World(personas()), **body)
    assert res.status_code == 422 and res.headers["cache-control"] == "private, no-store"


def test_a_repo_outside_reach_is_the_same_404_and_an_ambiguous_one_says_so(monkeypatch) -> None:
    world = World(spec_of_one_file())
    missing = resolve(world, recordId="nope")

    async def absent(*a, **k) -> JSONResponse:
        return JSONResponse({"error": "No indexed repository matches secret/repo."}, status_code=404)

    monkeypatch.setattr(routes, "_target", absent)
    gone = resolve(world, repo="secret/repo", filePath="a.py")
    assert gone.status_code == 404 and gone.content == missing.content

    async def two(*a, **k) -> JSONResponse:
        return JSONResponse({"error": "More than one repository matches shop.", "repos": ["a/shop"]}, status_code=409)

    monkeypatch.setattr(routes, "_target", two)
    res = resolve(world, repo="shop", filePath="a.py")
    assert res.status_code == 409 and json.loads(res.content)["detail"] == "More than one repository matches shop."


def test_a_failed_check_is_a_503_not_a_404() -> None:
    world = World(spec_of_one_file())
    world.fail_access = PermissionVerificationUnavailableError("down")
    res = resolve(world, recordId="r")
    assert res.status_code == 503 and res.headers["cache-control"] == "private, no-store"


def test_blocks_that_start_on_the_same_line_resolve_the_same_whatever_order_the_store_answers_in() -> None:
    answers = set()
    for reverse in (False, True):
        world = World(spec_of_one_file())
        for doc in world.blocks.values():
            doc["startLine"], doc["kind"] = 3, "function"
        real = world.get_nodes_by_field_in

        async def ordered(*a, _real=real, _reverse=reverse, **k) -> list:
            rows = await _real(*a, **k)
            return rows[::-1] if _reverse else rows

        world.get_nodes_by_field_in = ordered
        answers.add(resolve(world, recordId="r").content)
    assert len(answers) == 1


def test_a_store_that_cannot_load_the_blocks_is_a_503_not_a_404() -> None:
    world = World(spec_of_one_file())

    async def broken(*a, **k) -> list:
        raise RuntimeError("store down")

    world.get_nodes_by_field_in = broken
    res = resolve(world, recordId="r")
    assert res.status_code == 503 and res.headers["cache-control"] == "private, no-store"


def test_a_bare_file_path_resolves_the_same_whatever_order_the_store_answers_in() -> None:
    answers = set()
    for reverse in (False, True):
        world = World(spec_of_one_file())
        world.records["r"]["recordGroupId"] = "grp"
        for doc in world.blocks.values():
            doc["startLine"], doc["kind"] = 3, "function"
        real = world.get_nodes_by_field_in

        async def ordered(*a, _real=real, _reverse=reverse, **k) -> list:
            rows = await _real(*a, **k)
            return rows[::-1] if _reverse else rows

        world.get_nodes_by_field_in = ordered
        answers.add(resolve(world, repo="acme/shop", filePath="src/r.py").content)
    assert len(answers) == 1


def test_a_file_with_more_tied_definitions_than_the_cut_keeps_resolving_to_the_first_key() -> None:
    answers = set()
    for reverse in (False, True):
        keys = {f"b{i:03d}": "r" for i in range(230)}
        world = World(Spec(records={"r": CONN}, blocks=keys, acl={"bob": {"r"}}))
        world.records["r"]["recordGroupId"] = "grp"
        for doc in world.blocks.values():
            doc["startLine"], doc["kind"] = 1, "function"
        real = world.get_nodes_by_field_in

        async def ordered(*a, _real=real, _reverse=reverse, **k) -> list:
            rows = await _real(*a, **k)
            return rows[::-1] if _reverse else rows

        world.get_nodes_by_field_in = ordered
        answers.add(resolve(world, repo="acme/shop", filePath="src/r.py").content)
    assert answers == {b'{"nodeId":"block:b000"}'}
