"""history: the pull requests that changed a file, with their people and tickets.

PRs reach a code file through MODIFIES (written where the PR's file list is read); tickets through the
PR's LINKED_TO (its title and body name them). Both are filtered by what the caller may read: a hidden PR or ticket
is never listed, and a hidden file reads exactly like one that is not indexed.
"""
import pytest

from app.agents.actions.code_graph.history import history_impl
from app.config.constants.arangodb import CollectionNames

from .conftest import CONN, ORG, USER, FakeBlobStore, QueryGraphProvider

pytestmark = pytest.mark.asyncio

RECORDS = CollectionNames.RECORDS.value
PRS = CollectionNames.PULLREQUESTS.value


def _link(a: str, b: str, rel: str) -> dict:
    return {"_from": f"{RECORDS}/{a}", "_to": f"{RECORDS}/{b}", "relationshipType": rel}


class PRGraph(QueryGraphProvider):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.prs: dict[str, dict] = {}

    def pr(self, key: str, title: str, merged_at: int | None, *, connector: str = "GITHUB TEAMS", **fields) -> None:
        self.records[key] = {"_key": key, "orgId": ORG, "connectorId": CONN, "recordName": title,
                             "recordType": "PULL_REQUEST", "connectorName": connector,
                             "webUrl": f"https://example.test/{key}", "virtualRecordId": f"v-{key}"}
        self.prs[key] = {"_key": key, "orgId": ORG, "mergedAtTimestamp": merged_at, **fields}

    def ticket(self, key: str, title: str) -> None:
        self.records[key] = {"_key": key, "orgId": ORG, "connectorId": "conn-jira", "recordName": title,
                             "recordType": "TICKET", "connectorName": "JIRA",
                             "webUrl": f"https://acme.atlassian.net/browse/{key}"}

    async def get_nodes_by_field_in(self, collection, field_name, field_values, return_fields=None,
                                    transaction=None, raise_on_error=False) -> list[dict]:
        if collection == PRS:
            return [d for d in self.prs.values() if d.get(field_name) in set(field_values)]
        return await super().get_nodes_by_field_in(collection, field_name, field_values, return_fields,
                                                   transaction, raise_on_error)


def _graph(**kwargs) -> PRGraph:
    g = PRGraph(**kwargs)
    g.pr("pr-old", "Fix target", 1_000, creatorName="carol", reviewName=["dave"], mergedBy="erin", status="merged")
    g.pr("pr-new", "PA-7: speed up target", 3_000, creatorName="frank", reviewName=[], status="merged")
    g.pr("pr-open", "WIP target", None, creatorName="gina", status="open")
    g.pr("pr-elsewhere", "Unrelated", 2_000)
    g.ticket("PA-7", "Target is slow")
    g.edges += [_link(p, "rec-b", "MODIFIES") for p in ("pr-old", "pr-new", "pr-open")]
    g.edges += [_link("pr-elsewhere", "rec-a", "MODIFIES"), _link("pr-new", "PA-7", "LINKED_TO")]
    return g


async def _history(graph: PRGraph, file_path: str = "src/b.py", **kwargs) -> dict:
    return await history_impl(graph_provider=graph, connector_id=CONN, org_id=ORG, user_id=USER,
                              file_path=file_path, **kwargs)


async def test_the_prs_that_changed_a_file_newest_merge_first_and_open_ones_last() -> None:
    out = await _history(_graph())
    assert [p["title"] for p in out["pull_requests"]] == ["PA-7: speed up target", "Fix target", "WIP target"]
    old = out["pull_requests"][1]
    assert (old["author"], old["merged_by"], old["merged_at"], old["url"]) == (
        "carol", "erin", 1_000, "https://example.test/pr-old")
    assert old["requested_reviewers"] == ["dave"], "GitHub's reviewers are the ones asked, not who reviewed"
    assert out["file_path"] == "src/b.py" and out["total"] == 3


async def test_a_pr_lists_the_tickets_it_names() -> None:
    newest = (await _history(_graph()))["pull_requests"][0]
    assert newest["tickets"] == [{"title": "Target is slow", "url": "https://acme.atlassian.net/browse/PA-7",
                                  "type": "TICKET"}]


async def test_gitlab_reviewers_are_requested_reviewers_too() -> None:
    """GitLab's MR `reviewers` are the assigned ones, not who reviewed (approved_by is not fetched)."""
    g = _graph()
    g.records["pr-old"]["connectorName"] = "GITLAB"
    old = (await _history(g))["pull_requests"][1]
    assert old["requested_reviewers"] == ["dave"] and "reviewers" not in old


async def test_a_hidden_pr_and_a_hidden_ticket_are_never_listed() -> None:
    out = await _history(_graph(deny_records={"pr-old", "PA-7"}))
    assert [p["title"] for p in out["pull_requests"]] == ["PA-7: speed up target", "WIP target"]
    assert out["pull_requests"][0]["tickets"] == []
    assert "Fix target" not in repr(out) and "Target is slow" not in repr(out)
    assert out["total"] == 2, "the count says nothing about what the caller cannot read"


async def test_a_hidden_file_reads_like_one_that_is_not_indexed() -> None:
    hidden = await _history(_graph(deny_records={"rec-b"}))
    missing = await _history(_graph(), file_path="src/nope.py")
    assert hidden["error"] == missing["error"].replace("src/nope.py", "src/b.py")
    assert "Fix target" not in repr(hidden)


async def test_a_file_no_indexed_pr_changed_says_how_prs_reach_it() -> None:
    out = await _history(_graph(), file_path="web/ui/panel.ts")
    assert out["pull_requests"] == [] and out["total"] == 0
    assert "indexed" in out["note"]


async def test_limit_keeps_the_newest_and_says_how_many_more() -> None:
    out = await _history(_graph(), limit=1)
    assert [p["title"] for p in out["pull_requests"]] == ["PA-7: speed up target"]
    assert out["total"] == 3 and out["truncated"] is True


async def test_a_file_with_more_pr_links_than_the_cap_says_the_list_is_cut(monkeypatch) -> None:
    """The MODIFIES lookup is capped; past it the answer is a sample, and says so."""
    from app.agents.actions.code_graph import history

    monkeypatch.setattr(history, "_ROW_LIMIT", 2)
    out = await _history(_graph())
    assert out["truncated"] is True and "2" in out["note"]
    assert len(out["pull_requests"]) == 2


async def test_a_symbol_without_blob_storage_is_answered_at_file_level_and_says_so() -> None:
    out = await _history(_graph(), qualified_name="function:target")
    assert all(p["touches_symbol"] is None for p in out["pull_requests"])
    assert "file" in out["note"]


def _pr_blob(path: str, diff: str) -> dict:
    """A PR's stored blocks as the connectors write them: the file's change as data beside its markdown."""
    return {"block_containers": {"blocks": [], "block_groups": [{
        "index": 0, "type": "text_section", "sub_type": "pr_file_change",
        "data": f"[modified file]\n\nDiff:\n```diff\n{diff}\n```",
        "file_change_metadata": {"path": path, "status": "modified", "additions": 1, "deletions": 1},
    }]}}


async def test_touches_symbol_reads_the_prs_diff_for_this_file() -> None:
    """True when a changed line or hunk header names the symbol; False when the diff for this file does not;
    None when the PR's blocks hold no change for it (indexed before file changes were stored)."""
    blobs = FakeBlobStore({
        "v-pr-new": _pr_blob("src/b.py", "@@ -1,3 +1,3 @@ def target():\n-    return helper()\n+    return fast()"),
        "v-pr-old": _pr_blob("src/b.py", "@@ -40,2 +40,2 @@ class Other:\n-    x = 1\n+    x = 2"),
        "v-pr-open": _pr_blob("src/other.py", "@@ -1 +1 @@\n-target()\n+target(1)"),
    })
    out = await _history(_graph(), qualified_name="function:target", blob_store=blobs)
    touches = {p["title"]: p["touches_symbol"] for p in out["pull_requests"]}
    assert touches == {"PA-7: speed up target": True, "Fix target": False, "WIP target": None}
    assert "heuristic" in out["note"]


def _indexed_pr_blob(path: str, diff: str) -> dict:
    """The same PR once indexed: the group's text is split into blocks, the diff under a nested code group."""
    return {"block_containers": {
        "block_groups": [
            {"index": 0, "type": "text_section", "sub_type": "pr_file_change", "data": None,
             "file_change_metadata": {"path": path, "status": "modified", "additions": 1, "deletions": 1}},
            {"index": 1, "type": "code", "parent_index": 0, "data": None},
            {"index": 2, "type": "text_section", "sub_type": "pr_file_change", "data": None,
             "file_change_metadata": {"path": "src/other.py", "status": "modified"}},
        ],
        "blocks": [
            {"index": 0, "parent_index": 0, "type": "text", "data": "[modified file]"},
            {"index": 1, "parent_index": 1, "type": "text", "data": diff},
            {"index": 2, "parent_index": 2, "type": "text", "data": "@@ -1 +1 @@\n-other()\n+other(1)"},
        ],
    }}


async def test_touches_symbol_reads_the_diff_from_an_indexed_prs_blocks() -> None:
    blobs = FakeBlobStore({
        "v-pr-new": _indexed_pr_blob("src/b.py", "@@ -1,3 +1,3 @@\n-def target():\n+def target(x):"),
        "v-pr-old": _indexed_pr_blob("src/b.py", "@@ -40,2 +40,2 @@\n-    x = 1\n+    x = 2"),
    })
    out = await _history(_graph(), qualified_name="function:target", blob_store=blobs)
    touches = {p["title"]: p["touches_symbol"] for p in out["pull_requests"]}
    assert touches["PA-7: speed up target"] is True
    assert touches["Fix target"] is False


async def test_a_symbol_named_only_in_unchanged_context_is_not_touched() -> None:
    blobs = FakeBlobStore({"v-pr-old": _pr_blob("src/b.py", "@@ -3,3 +3,3 @@\n target()\n-a = 1\n+a = 2")})
    out = await _history(_graph(), qualified_name="function:target", blob_store=blobs)
    old = next(p for p in out["pull_requests"] if p["title"] == "Fix target")
    assert old["touches_symbol"] is False


@pytest.mark.parametrize(("qualified_name", "line"), [
    ("method:Panel.$emit", "-    this.$emit('close')"),
    ("method:Stack.empty?", "+  def empty?"),
])
async def test_touches_symbol_matches_names_that_start_or_end_with_a_non_word_character(qualified_name, line) -> None:
    blobs = FakeBlobStore({"v-pr-new": _pr_blob("src/b.py", f"@@ -1 +1 @@\n{line}")})
    out = await _history(_graph(), qualified_name=qualified_name, blob_store=blobs)
    new = next(p for p in out["pull_requests"] if p["title"] == "PA-7: speed up target")
    assert new["touches_symbol"] is True


async def test_tickets_are_read_for_the_returned_page_only() -> None:
    graph = _graph()
    asked: list[list[str]] = []
    real = graph.get_neighbors_for_nodes_by_relationship_types

    async def spy(**kwargs) -> list[dict]:
        if kwargs["relationship_types"] == ["LINKED_TO"]:
            asked.append(sorted(kwargs["node_keys"]))
        return await real(**kwargs)

    graph.get_neighbors_for_nodes_by_relationship_types = spy
    out = await _history(graph, limit=1)
    assert [p["title"] for p in out["pull_requests"]] == ["PA-7: speed up target"]
    assert out["pull_requests"][0]["tickets"][0]["title"] == "Target is slow"
    assert asked == [["pr-new"]]
