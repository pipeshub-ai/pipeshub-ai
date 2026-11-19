"""search_code(mode="external"): who calls into a library.

`pattern` names the module (a prefix on dotted or slashed paths: `django.db` covers
`django.db.models`, never `django.dbx`), `name` narrows it to one of the module's
names or members. Matches are blocks the caller may read, each with the refs that
matched.
"""
import pytest

from app.agents.actions.code_graph.search import search_code_impl

from .conftest import CONN, ORG, USER, QueryGraphProvider

pytestmark = pytest.mark.asyncio

REFS = {
    "k_caller": ["requests:get", "requests:Session.get"],
    "k_target": ["django.db.models:CharField", "django.dbx:thing"],
    "k_panel": ["node:fs:readFileSync", "react:useState"],
    "k_client": ["axios:default.get"],
}


def graph(**kwargs) -> QueryGraphProvider:
    g = QueryGraphProvider(**kwargs)
    for key, refs in REFS.items():
        g.blocks[key]["externalRefs"] = refs
    return g


async def _external(g: QueryGraphProvider, pattern: str, **kwargs) -> dict:
    return await search_code_impl(graph_provider=g, blob_store=None, connector_id=CONN, org_id=ORG,
                                  user_id=USER, pattern=pattern, mode="external", **kwargs)


def _hits(out: dict) -> dict[str, list[str]]:
    return {m["qualified_name"]: m["external_refs"] for m in out["matches"]}


async def test_a_module_finds_every_block_calling_into_it() -> None:
    out = await _external(graph(), "requests")
    assert _hits(out) == {"function:caller": ["requests:get", "requests:Session.get"]}
    assert out["mode"] == "external" and out["total"] == 1 and out["truncated"] is False


async def test_a_name_narrows_to_it_and_its_members() -> None:
    assert _hits(await _external(graph(), "requests", name="Session")) == {
        "function:caller": ["requests:Session.get"]}


async def test_a_name_also_matches_a_member_reached_through_a_binding() -> None:
    assert _hits(await _external(graph(), "requests", name="get")) == {
        "function:caller": ["requests:get", "requests:Session.get"]}
    # A default import's member: `axios.get` is `axios:default.get`.
    assert _hits(await _external(graph(), "axios", name="get")) == {"function:fetchTarget": ["axios:default.get"]}


async def test_a_module_prefix_stops_at_a_segment_boundary() -> None:
    assert _hits(await _external(graph(), "django.db")) == {"function:target": ["django.db.models:CharField"]}
    assert (await _external(graph(), "django.d"))["matches"] == []


async def test_a_specifier_with_a_colon_is_one_module() -> None:
    assert _hits(await _external(graph(), "node:fs")) == {"function:renderPanel": ["node:fs:readFileSync"]}
    assert (await _external(graph(), "node"))["matches"] == []


async def test_a_rust_crate_and_its_modules_are_found_through_double_colons() -> None:
    g = graph()
    g.blocks["k_caller"]["externalRefs"] = ["tokio::sync::Mutex:Mutex.new", "tokio:spawn", "tokio_util::codec:Framed.new"]
    tokio = ["tokio::sync::Mutex:Mutex.new", "tokio:spawn"]
    assert _hits(await _external(g, "tokio")) == {"function:caller": tokio}                         # crate
    assert _hits(await _external(g, "tokio::sync")) == {"function:caller": tokio[:1]}               # submodule
    assert _hits(await _external(g, "tokio::sync::Mutex", name="new")) == {"function:caller": tokio[:1]}  # item
    assert (await _external(g, "tokio::sy"))["matches"] == []


async def test_a_php_namespace_and_its_classes_are_found_through_backslashes() -> None:
    g = graph()
    g.blocks["k_caller"]["externalRefs"] = ["Illuminate\\Http\\Request:Request.create"]
    hit = {"function:caller": ["Illuminate\\Http\\Request:Request.create"]}
    assert _hits(await _external(g, "Illuminate\\Http")) == hit
    assert _hits(await _external(g, "Illuminate\\Http\\Request", name="create")) == hit
    assert (await _external(g, "Illuminate\\Ht"))["matches"] == []


async def test_a_block_the_caller_cannot_read_is_never_named() -> None:
    out = await _external(graph(deny_records=["rec-a"]), "requests")
    assert out["matches"] == [] and "caller" not in repr(out)
    assert "hint" in out


async def test_test_files_are_left_out_unless_asked() -> None:
    g = graph(file_roles={"rec-a": "test"})
    out = await _external(g, "requests")
    assert out["matches"] == [] and out["hidden_tests"] == 1
    assert _hits(await _external(g, "requests", include_tests=True)) == {
        "function:caller": ["requests:get", "requests:Session.get"]}


async def test_path_narrows_the_matches() -> None:
    g = graph()
    g.blocks["k_client"]["externalRefs"] = ["react:useEffect"]
    assert set(_hits(await _external(g, "react"))) == {"function:renderPanel", "function:fetchTarget"}
    assert set(_hits(await _external(g, "react", path="web/ui/"))) == {"function:renderPanel"}


async def test_more_matches_than_the_limit_say_so() -> None:
    g = graph()
    g.blocks["k_client"]["externalRefs"] = ["react:useEffect"]
    out = await _external(g, "react", limit=1)
    assert len(out["matches"]) == 1 and out["total"] == 2 and out["truncated"] is True


async def test_the_repo_filters_in_the_query_so_another_repos_callers_cannot_use_up_the_cap(monkeypatch) -> None:
    from app.agents.actions.code_graph import search

    monkeypatch.setattr(search, "_EXTERNAL_ROWS", 1)
    g = graph()
    g.record_groups["repo-2"] = {"_key": "repo-2", "orgId": ORG, "connectorId": CONN, "groupName": "acme/admin"}
    g.blocks["k_client"].update(recordGroupId="repo-2", externalRefs=["react:useEffect"])  # sorts first
    out = await _external(g, "react", repo="acme/shop")
    assert set(_hits(out)) == {"function:renderPanel"} and out["truncated"] is False
    assert g.external_groups == [["repo-1"]]
    assert set(_hits(await _external(g, "react", repo="acme/admin"))) == {"function:fetchTarget"}


async def test_an_unknown_repo_is_an_error_naming_it() -> None:
    out = await _external(graph(), "react", repo="acme/nowhere")
    assert "acme/nowhere" in out["error"]


async def test_more_rows_than_the_cap_is_reported_as_truncated(monkeypatch) -> None:
    from app.agents.actions.code_graph import search

    monkeypatch.setattr(search, "_EXTERNAL_ROWS", 1)
    g = graph()
    g.blocks["k_client"]["externalRefs"] = ["react:useEffect"]
    assert (await _external(g, "react"))["truncated"] is True


async def test_blocks_without_refs_are_never_matched_and_one_query_is_made() -> None:
    g = graph()
    out = await _external(g, "react")
    assert set(_hits(out)) == {"function:renderPanel"}
    assert len(g.external_groups) == 1


async def test_an_empty_answer_says_why_a_name_tests_or_the_cap_left_nothing(monkeypatch) -> None:
    from app.agents.actions.code_graph import search

    g = graph(file_roles={"rec-a": "test"})
    plain = (await _external(graph(), "nowhere"))["hint"]
    assert plain.startswith("No block you can read calls that module") and "include_tests" not in plain
    named = (await _external(graph(), "react", name="useEffect"))["hint"]
    assert named.startswith("No block you can read calls `useEffect` of that module")
    assert "include_tests" in (await _external(g, "requests"))["hint"]
    monkeypatch.setattr(search, "_EXTERNAL_ROWS", 1)
    g = graph()
    g.blocks["k_client"]["externalRefs"] = ["react:useEffect"]  # sorts first and holds no useState call
    capped = await _external(g, "react", name="useState")
    assert capped["matches"] == [] and capped["truncated"] is True
    assert "among the first 1 blocks" in capped["hint"]
