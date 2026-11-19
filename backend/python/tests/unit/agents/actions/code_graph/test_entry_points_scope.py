"""entry_points scans the repo it is asked about, whole, in the database.

It paged the connector's blocks in key order (a hash) and stopped at 5,000, so on a large connector it ranked a
random sample, and `repo` filtered that sample afterwards. Now the repo's readable, non-test code files are
chosen first and the database returns their uncalled functions and methods (``find_uncalled_blocks``).
"Uncalled" keeps its meaning: no inbound CALLS from a file the caller can read.
"""
import pytest

from app.agents.actions.code_graph import query as q
from app.agents.actions.code_graph.query import entry_points_impl

from .conftest import CONN, ORG, USER, QueryGraphProvider, _block

pytestmark = pytest.mark.asyncio

BIG = 5100  # more uncalled functions than the old scan read, in another repo, with keys that sort first


class Tracking(QueryGraphProvider):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.scoped: list[set[str] | None] = []
        self.pages = 0

    async def find_uncalled_blocks(self, org_id, connector_id, record_ids, *args,
                                   **kwargs) -> tuple[list[dict], str | None]:
        self.scoped.append(None if record_ids is None else set(record_ids))
        return await super().find_uncalled_blocks(org_id, connector_id, record_ids, *args, **kwargs)

    async def get_documents_paginated(self, *args, **kwargs) -> list[dict]:
        self.pages += 1
        return await super().get_documents_paginated(*args, **kwargs)


def big_connector(**kwargs) -> Tracking:
    g = Tracking(**kwargs)
    g.record_groups["repo-big"] = {"_key": "repo-big", "orgId": ORG, "connectorId": CONN, "groupName": "acme/big"}
    for n in range(BIG):
        rid = f"rec-big{n // 100}"
        block = _block(f"a{n:05d}", rid, "", f"big_{n}", f"job_{n}")
        block.pop("filePath")
        block["recordGroupId"] = "repo-big"
        g.blocks[block["_key"]] = block
        g.code_files[rid] = f"big/jobs_{n // 100}.py"
        g.records[rid] = {"_key": rid, "orgId": ORG, "connectorId": CONN, "virtualRecordId": f"v-{rid}",
                          "recordGroupId": "repo-big"}
    return g


async def _entry(g: QueryGraphProvider, **kwargs) -> dict:
    return await entry_points_impl(graph_provider=g, connector_id=CONN, org_id=ORG, user_id=USER, **kwargs)


def _names(out: dict) -> set[str]:
    return {p["qualified_name"] for p in out["entry_points"]}


async def test_a_repo_is_ranked_whole_whatever_else_its_connector_holds() -> None:
    out = await _entry(big_connector(), repo="acme/shop")
    assert "function:caller" in _names(out)
    assert out["total"] == len(out["entry_points"]) and out["truncated"] is False
    assert not out.get("scan_capped") and "note" not in out


async def test_the_scope_goes_into_the_query() -> None:
    g = big_connector()
    await _entry(g, repo="acme/shop")
    assert g.scoped and all(s == {"rec-a", "rec-b", "rec-c", "rec-d"} for s in g.scoped)
    assert g.pages == 0  # no client-side scan of the connector's blocks


async def test_unreadable_and_test_files_are_left_out_before_the_query() -> None:
    g = big_connector(deny_records=["rec-a"], file_roles={"rec-c": "test"})
    out = await _entry(g, repo="acme/shop")
    assert all(s <= {"rec-b", "rec-d"} for s in g.scoped)
    assert "function:caller" not in _names(out) and "function:renderPanel" not in _names(out)
    shown = await _entry(big_connector(file_roles={"rec-c": "test"}), repo="acme/shop", include_tests=True)
    assert "function:renderPanel" in _names(shown)


async def test_a_function_whose_only_caller_is_hidden_is_an_entry_point() -> None:
    """A caller in an unreadable file does not count: it neither hides the function nor shows itself."""
    g = big_connector(deny_records=["rec-a"])
    out = await _entry(g, repo="acme/shop")
    assert "function:target" in _names(out) and "src/a.py" not in repr(out)


async def test_a_readable_caller_still_makes_a_function_called() -> None:
    assert "function:target" not in _names(await _entry(big_connector(), repo="acme/shop"))


async def test_the_query_failing_is_an_error_not_an_empty_answer() -> None:
    class Failing(QueryGraphProvider):
        async def find_uncalled_blocks(self, *args, **kwargs) -> tuple[list[dict], str | None]:
            raise RuntimeError("store down")

    with pytest.raises(RuntimeError):
        await _entry(Failing())


async def test_a_caller_who_can_read_nothing_gets_no_entry_points() -> None:
    g = big_connector(allow_user="someone-else")
    out = await _entry(g)
    assert out["entry_points"] == [] and out["total"] == 0 and g.scoped == []


async def test_without_repo_the_whole_connector_is_ranked() -> None:
    out = await _entry(big_connector(), limit=200)
    assert out["total"] == BIG + 3 and not out.get("scan_capped")  # + caller, run, renderPanel of acme/shop


async def test_only_a_real_limit_caps_the_scan_and_the_note_says_which(monkeypatch) -> None:
    monkeypatch.setattr(q, "_ENTRY_SCAN_LIMIT", 1000)
    out = await _entry(big_connector(), limit=10)
    assert out["scan_capped"] is True and out["truncated"] is True
    assert "1000" in out["note"] and "repo" in out["note"]
    small = await _entry(big_connector(), repo="acme/shop")
    assert not small.get("scan_capped")


async def test_the_result_marks_the_repos_it_read_for_graph_status() -> None:
    out = await _entry(big_connector(), repo="acme/shop")
    assert out.get("_graph_groups") == ["repo-1"]
