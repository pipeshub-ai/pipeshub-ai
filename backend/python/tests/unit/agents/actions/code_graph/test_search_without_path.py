"""search_code text and regex without `path`: the lexical index proposes files, the stored source decides."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agents.actions.code_graph.search import search_code_impl
from app.modules.retrieval.lexical_search import (
    LexicalHit,
    LexicalSearchResult,
    LexicalSearchUnavailableError,
)

from .conftest import CONN, ORG, USER, FakeBlobStore, QueryGraphProvider

pytestmark = pytest.mark.asyncio


def _source(text: str) -> dict:
    return {"block_containers": {"block_groups": [], "blocks": [
        {"index": 0, "data": {"text": text},
         "code_metadata": {"qualified_name": "function:f", "kind": "function",
                           "start_line": 1, "end_line": text.count("\n")}}]}}


_BLOBS = {
    "v-b": _source('def target():\n    return helper("payments")\n'),
    "v-a": _source("def caller():\n    return target()\n"),
}


class FakeLexical:
    """``search_lexical`` as the retrieval service answers it: the virtual records whose chunks hold the query's
    terms, already scoped and permission-filtered."""

    def __init__(self, vrids=("v-b",), *, capped=False, empty=False, raises=None, failed=0, degraded=False,
                 backfill: object = {"done": True, "failed": 0}) -> None:
        self.vrids, self.capped, self.empty, self.raises = list(vrids), capped, empty, raises
        self.failed, self.degraded = failed, degraded
        self.calls: list[dict] = []
        self.config_service = SimpleNamespace(get_config=AsyncMock(return_value=backfill))

    async def search_lexical(self, queries, user_id, org_id, filter_groups=None, limit=100,
                             virtual_record_ids=None) -> LexicalSearchResult:
        self.calls.append({"queries": queries, "user_id": user_id, "org_id": org_id,
                           "filter_groups": filter_groups, "limit": limit})
        if self.raises:
            raise self.raises
        return LexicalSearchResult(hits=[[LexicalHit(v, "", 1.0) for v in self.vrids]],
                                   capped=[self.capped], empty_query=[self.empty],
                                   failed_collections=self.failed, degraded=self.degraded)


async def _search(graph=None, retrieval=None, **kw) -> dict:
    return await search_code_impl(
        graph_provider=graph or QueryGraphProvider(), blob_store=FakeBlobStore(dict(_BLOBS)),
        retrieval_service=retrieval, connector_id=CONN, org_id=ORG, user_id=USER, **kw)


async def test_text_without_a_path_reads_the_files_the_index_proposes() -> None:
    lexical = FakeLexical(["v-b", "v-a"])
    out = await _search(retrieval=lexical, pattern='helper("payments")', mode="text")
    assert out["matches"] == [{"file_path": "src/b.py", "line": 2, "text": 'return helper("payments")'}]
    assert out["candidates"] == 2 and out["files_searched"] == 2
    assert out["complete"] is True and "note" not in out
    # One lookup, of the rarest word every match holds whole, in this connector only.
    assert lexical.calls == [{"queries": ["payments"], "user_id": USER, "org_id": ORG,
                              "filter_groups": {"apps": [CONN]}, "limit": 300}]


async def test_regex_without_a_path_looks_up_its_literal_run() -> None:
    lexical = FakeLexical()
    out = await _search(retrieval=lexical, pattern=r'return helper\("\w+"\)', mode="regex")
    assert [(m["file_path"], m["line"]) for m in out["matches"]] == [("src/b.py", 2)]
    assert lexical.calls[0]["queries"] == ["helper"]
    assert out["complete"] is True


async def test_regex_without_a_literal_run_needs_a_path() -> None:
    lexical = FakeLexical()
    out = await _search(retrieval=lexical, pattern=r"\w+\(\d+\)", mode="regex")
    assert "path" in out["error"] and "word" in out["error"]
    assert lexical.calls == []


async def test_a_capped_lookup_is_partial_and_says_so() -> None:
    out = await _search(retrieval=FakeLexical(capped=True), pattern='"payments"', mode="text")
    assert [m["line"] for m in out["matches"]] == [2]
    assert out["complete"] is False
    assert "path" in out["note"]


async def test_a_word_that_may_be_part_of_a_longer_one_is_partial() -> None:
    # `target` may sit inside `retarget`: the index finds whole words only.
    out = await _search(retrieval=FakeLexical(["v-a", "v-b"]), pattern="target", mode="text")
    assert {m["file_path"] for m in out["matches"]} == {"src/a.py", "src/b.py"}
    assert out["complete"] is False and "longer word" in out["note"]


async def test_a_short_top_level_line_is_found_through_its_residue_unit() -> None:
    # `LEGACY_TAX_RATE = 0.07` is in no symbol's unit; the file's residue unit is what the index holds it in.
    blobs = {"v-c": {"block_containers": {"block_groups": [], "blocks": [
        {"index": 0, "data": {"text": "LEGACY_TAX_RATE = 0.07\n"},
         "code_metadata": {"kind": "statements", "start_line": 1, "end_line": 1}}]}}}
    graph = QueryGraphProvider()
    graph.code_files["rec-c"] = "src/tax.py"
    graph.records["rec-c"] = {"_key": "rec-c", "orgId": ORG, "connectorId": CONN, "virtualRecordId": "v-c"}

    async def search(pattern: str, mode: str) -> dict:
        return await search_code_impl(
            graph_provider=graph, blob_store=FakeBlobStore(blobs), retrieval_service=FakeLexical(["v-c"]),
            connector_id=CONN, org_id=ORG, user_id=USER, pattern=pattern, mode=mode)

    out = await search(r"\bLEGACY_TAX_RATE\b", "regex")  # a whole word: every match holds it whole
    assert out["matches"] == [{"file_path": "src/tax.py", "line": 1, "text": "LEGACY_TAX_RATE = 0.07"}]
    assert out["complete"] is True and "note" not in out
    # As text, the name can be the tail of a longer one (OLD_LEGACY_TAX_RATE), which only that keeps partial.
    out = await search("LEGACY_TAX_RATE = 0.07", "text")
    assert [m["line"] for m in out["matches"]] == [1] and out["complete"] is False
    assert out["note"].startswith("The index finds whole words")


@pytest.mark.parametrize(("status", "group", "repo", "complete"), [
    ("QUEUED", "repo-1", None, False),         # synced, not yet indexed: its text is in no unit
    ("IN_PROGRESS", "repo-1", "acme/shop", False),
    ("QUEUED", "repo-2", "acme/shop", True),   # another repo's backlog does not touch this repo's search
    ("NOT_STARTED", "repo-1", None, False),
    ("PAUSED", "repo-1", None, False),
    ("FAILED", "repo-1", None, False),         # its blob can be stored without its chunks
    ("COMPLETED", "repo-1", None, True),
    ("EMPTY", "repo-1", None, True),           # nothing to find in it
    # Terminal and never indexed: no stored text, so no search mode finds a line in it. AUTO_INDEX_OFF
    # is on every test file and oversize file by default, so counting it would keep every repo partial.
    ("AUTO_INDEX_OFF", "repo-1", None, True),
    ("FILE_TYPE_NOT_SUPPORTED", "repo-1", None, True),
    ("SKIPPED", "repo-1", None, True),
    ("PASSWORD_PROTECTED", "repo-1", None, True),
])
async def test_a_file_synced_but_not_yet_indexed_makes_the_search_partial(status, group, repo, complete) -> None:
    graph = QueryGraphProvider()
    graph.records["rec-b"]["recordGroupId"] = "repo-1"
    graph.records["rec-n"] = {"_key": "rec-n", "orgId": ORG, "connectorId": CONN, "recordType": "CODE_FILE",
                              "recordGroupId": group, "indexingStatus": status, "virtualRecordId": "v-n"}
    out = await _search(graph, FakeLexical(), pattern='"payments"', mode="text", repo=repo)
    assert [m["file_path"] for m in out["matches"]] == ["src/b.py"]
    assert out["complete"] is complete
    assert ("synced but not indexed yet" in out.get("note", "")) is not complete


async def test_an_indexing_status_check_that_fails_is_partial() -> None:
    graph = QueryGraphProvider()
    graph.has_nodes_by_filters = AsyncMock(side_effect=RuntimeError("graph down"))
    out = await _search(graph, FakeLexical(), pattern='"payments"', mode="text")
    assert out["complete"] is False and "not indexed yet" in out["note"]


@pytest.mark.parametrize("backfill", [None, {"done": False, "after": "r9"}, {"done": True, "failed": 2},
                                      RuntimeError("config store down")])
async def test_until_older_files_have_residue_units_no_hit_is_proof_of_absence(backfill) -> None:
    """Files indexed before residue units gain theirs from the backfill; until it is done with no record
    failed, a short top-level line in one of them is not in the index, so a miss proves nothing."""
    lexical = FakeLexical([])
    if isinstance(backfill, Exception):
        lexical.config_service.get_config.side_effect = backfill
    else:
        lexical.config_service.get_config.return_value = backfill
    out = await _search(retrieval=lexical, pattern="LEGACY_TAX_RATE = 0.07", mode="text")
    assert out["matches"] == [] and out["complete"] is False
    assert out["note"].startswith("Short spans outside function bodies (imports, short assignments and "
                                  "comments, class header lines) of files indexed before")


@pytest.mark.parametrize("lexical", [FakeLexical(failed=1), FakeLexical(degraded=True)])
async def test_a_lookup_that_searched_only_part_of_the_index_is_partial(lexical) -> None:
    out = await _search(retrieval=lexical, pattern='"payments"', mode="text")
    assert [m["line"] for m in out["matches"]] == [2]
    assert out["complete"] is False and "could not be searched" in out["note"]


async def test_a_query_the_index_keeps_no_term_of_is_partial() -> None:
    out = await _search(retrieval=FakeLexical([], empty=True), pattern='"payments"', mode="text")
    assert out["matches"] == [] and out["complete"] is False


async def test_a_denied_file_is_never_returned_even_through_a_readable_copy() -> None:
    # rec-x holds the same bytes as rec-b, so both share v-b; the caller may read only rec-b.
    graph = QueryGraphProvider(deny_records=["rec-x"])
    graph.code_files["rec-x"] = "secret/b.py"
    graph.records["rec-x"] = {"_key": "rec-x", "orgId": ORG, "connectorId": CONN, "virtualRecordId": "v-b"}
    out = await _search(graph, FakeLexical(), pattern='"payments"', mode="text")
    assert [m["file_path"] for m in out["matches"]] == ["src/b.py"]
    assert "secret" not in str(out)

    denied = await _search(QueryGraphProvider(deny_records=["rec-b"]), FakeLexical(),
                           pattern='"payments"', mode="text")
    assert denied["matches"] == [] and denied["candidates"] == 0
    assert "src/b.py" not in str(denied)


async def test_another_connectors_or_orgs_record_is_not_a_candidate() -> None:
    graph = QueryGraphProvider()
    graph.code_files["rec-y"] = "other/b.py"
    graph.records["rec-y"] = {"_key": "rec-y", "orgId": ORG, "connectorId": "conn-2", "virtualRecordId": "v-b"}
    graph.records["rec-z"] = {"_key": "rec-z", "orgId": "org-2", "connectorId": CONN, "virtualRecordId": "v-b"}
    out = await _search(graph, FakeLexical(), pattern='"payments"', mode="text")
    assert [m["file_path"] for m in out["matches"]] == ["src/b.py"]


async def test_tests_are_hidden_from_a_path_free_search_unless_asked() -> None:
    graph = QueryGraphProvider(file_roles={"rec-b": "test"})
    out = await _search(graph, FakeLexical(), pattern='"payments"', mode="text")
    assert out["matches"] == [] and out["hidden_tests"] == 1
    out = await _search(graph, FakeLexical(), pattern='"payments"', mode="text", include_tests=True)
    assert [m["file_path"] for m in out["matches"]] == ["src/b.py"]


async def test_repo_scopes_the_candidate_files() -> None:
    graph = QueryGraphProvider()
    graph.records["rec-b"]["recordGroupId"] = "repo-1"
    graph.code_files["rec-o"] = "fork/b.py"
    graph.records["rec-o"] = {"_key": "rec-o", "orgId": ORG, "connectorId": CONN, "recordGroupId": "repo-2",
                              "virtualRecordId": "v-b"}
    lexical = FakeLexical()
    out = await _search(graph, lexical, pattern='"payments"', mode="text", repo="acme/shop")
    assert [m["file_path"] for m in out["matches"]] == ["src/b.py"] and out["candidates"] == 1
    # A repo scope the store cannot apply yet would read as "no hits", so the lookup is the connector's.
    assert lexical.calls[0]["filter_groups"] == {"apps": [CONN]}
    out = await _search(graph, FakeLexical(), pattern='"payments"', mode="text", repo="nope/none")
    assert "nope/none" in out["error"]


async def test_without_a_lexical_index_text_search_asks_for_a_path() -> None:
    for retrieval in (None, FakeLexical(raises=LexicalSearchUnavailableError("no index"))):
        out = await _search(retrieval=retrieval, pattern='"payments"', mode="text")
        assert "path" in out["error"]


async def test_with_a_path_the_index_is_not_used() -> None:
    lexical = FakeLexical()
    out = await _search(retrieval=lexical, pattern='"payments"', mode="text", path="src/")
    assert [m["file_path"] for m in out["matches"]] == ["src/b.py"]
    assert lexical.calls == [] and "complete" not in out
