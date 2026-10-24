"""Two repos under one connector are told apart, never merged (A-3), and a
language without cross-file edges says so (A-5).

A GitHub or GitLab connector syncs many repos and paths are repo-relative, so
`app/main.py` can be two unrelated files. The tools used to merge their
symbols, or read whichever record came first.
"""
import pytest

from app.agents.actions.code_graph.neighbours import get_neighbour_impl
from app.agents.actions.code_graph.ops import AmbiguousRepoError
from app.agents.actions.code_graph.path import find_symbol_path_impl
from app.agents.actions.code_graph.read import read_code_impl
from app.config.constants.arangodb import CollectionNames

from .conftest import CONN, ORG, USER, FakeBlobStore, FakeGraphProvider, _block

pytestmark = pytest.mark.asyncio

GROUPS = CollectionNames.RECORD_GROUPS.value
MAIN = "app/main.py"


def _blob(text: str, qualified_name: str) -> dict:
    return {"block_containers": {"blocks": [{
        "code_metadata": {"qualified_name": qualified_name, "kind": "function",
                          "start_line": 1, "end_line": 2},
        "data": {"text": text},
    }], "block_groups": []}}


class TwoRepos(FakeGraphProvider):
    """acme/shop and acme/admin, each with its own app/main.py, as GitHub files
    them: code under a "Code repository" child of the named repo group."""

    def __init__(self, deny=()) -> None:
        super().__init__(deny_records=deny)
        for key, rid, group, name in (("k_shop", "rec-shop", "g-shop-code", "create_app"),
                                      ("k_admin", "rec-admin", "g-admin-code", "create_app"),
                                      ("k_only", "rec-admin", "g-admin-code", "only_admin")):
            self.blocks[key] = _block(key, rid, MAIN, f"{rid}_{name}", name)
            self.blocks[key].pop("filePath")
            self.blocks[key]["recordGroupId"] = group
            self.code_files[rid] = MAIN
            self.records[rid] = {"_key": rid, "orgId": ORG, "connectorId": CONN,
                                 "recordGroupId": group, "virtualRecordId": f"v-{rid}"}
        for block in self.blocks.values():
            block["connectorId"] = CONN
        self.groups = [
            {"_key": "g-shop-code", "orgId": ORG, "connectorId": CONN,
             "groupName": "Code repository", "parentExternalGroupId": "101"},
            {"_key": "g-admin-code", "orgId": ORG, "connectorId": CONN,
             "groupName": "Code repository", "parentExternalGroupId": "102"},
            {"_key": "g-shop", "orgId": ORG, "connectorId": CONN, "externalGroupId": "101",
             "groupName": "acme/shop"},
            {"_key": "g-admin", "orgId": ORG, "connectorId": CONN, "externalGroupId": "102",
             "groupName": "acme/admin"},
        ]

    async def get_nodes_by_field_in(self, collection, field_name, field_values,
                                    return_fields=None, transaction=None,
                                    raise_on_error=False) -> list[dict]:
        if collection == GROUPS:
            wanted = set(field_values)
            return [g for g in self.groups if g.get(field_name) in wanted]
        return await super().get_nodes_by_field_in(collection, field_name, field_values,
                                                    return_fields, transaction)


BLOBS = FakeBlobStore({
    "v-rec-shop": _blob("def create_app():\n    return 'shop'\n", "function:create_app"),
    "v-rec-admin": _blob("def create_app():\n    return 'admin'\n", "function:create_app"),
})


async def _read(graph, **kwargs) -> dict:
    return await read_code_impl(graph_provider=graph, connector_id=CONN, blob_store=BLOBS,
                                org_id=ORG, user_id=USER, file_path=MAIN, **kwargs)


async def test_a_shared_path_names_the_repos_instead_of_picking_one() -> None:
    with pytest.raises(AmbiguousRepoError) as caught:
        await _read(TwoRepos())
    assert caught.value.repos == ["acme/admin", "acme/shop"]


@pytest.mark.parametrize(("repo", "expected"), [("acme/shop", "'shop'"), ("admin", "'admin'")])
async def test_repo_picks_one_and_the_result_names_it(repo: str, expected: str) -> None:
    out = await _read(TwoRepos(), repo=repo)
    assert expected in out["blocks"][0]["code"]
    assert out["repo"] == ("acme/shop" if "shop" in repo else "acme/admin")


async def test_a_symbol_only_one_repo_defines_needs_no_repo() -> None:
    out = await get_neighbour_impl(
        graph_provider=TwoRepos(), connector_id=CONN, org_id=ORG, user_id=USER,
        file_path=MAIN, qualified_name="function:only_admin",
    )
    assert out["symbol"]["repo"] == "acme/admin"


async def test_a_whole_file_walk_does_not_merge_two_repos() -> None:
    with pytest.raises(AmbiguousRepoError):
        await get_neighbour_impl(graph_provider=TwoRepos(), connector_id=CONN, org_id=ORG,
                                 user_id=USER, file_path=MAIN)


async def test_a_repo_the_caller_cannot_read_is_never_named() -> None:
    out = await _read(TwoRepos(deny={"rec-admin"}))
    assert "'shop'" in out["blocks"][0]["code"]
    assert "admin" not in str(out)


class _RustRepo(FakeGraphProvider):
    def __init__(self) -> None:
        super().__init__()
        for block in self.blocks.values():
            block["connectorId"] = CONN
            block["language"] = "python"
        self.blocks["k_target"]["language"] = "rust"


async def test_a_walk_in_a_language_without_edges_says_coverage_is_missing() -> None:
    graph = _RustRepo()
    rust = await get_neighbour_impl(graph_provider=graph, connector_id=CONN, org_id=ORG,
                                    user_id=USER, file_path="src/b.py",
                                    qualified_name="function:target")
    assert rust["coverage"]["languages"] == ["rust"]
    py = await get_neighbour_impl(graph_provider=graph, connector_id=CONN, org_id=ORG,
                                  user_id=USER, file_path="src/a.py",
                                  qualified_name="function:caller")
    assert "coverage" not in py


async def test_a_missed_path_through_such_a_language_says_so() -> None:
    out = await find_symbol_path_impl(
        graph_provider=_RustRepo(), connector_id=CONN, org_id=ORG, user_id=USER,
        file_path_a="src/b.py", qualified_name_a="function:target",
        file_path_b="web/ui/panel.ts", qualified_name_b="function:renderPanel",
    )
    assert out["found"] is False
    assert out["coverage"]["languages"] == ["rust"]
