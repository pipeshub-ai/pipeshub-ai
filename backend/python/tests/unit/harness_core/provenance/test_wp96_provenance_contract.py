"""WP-96 (WP96-TYPE, WP96-RULE, WP96-LEDGER, WP96-REPO): ADR-RUN-01 D3's contract in the harness core.

``ResultProvenance`` has no default; ``SourceRef`` names web pages, repository files (``url@commit:path``), records,
connector items, memories and skills; a tool served without a rule is refused; the session's ``ProvenanceLedger``
folds the stamp's records and replays what has none; a link into a fetched repository at the fetched commit reads
back to its file, and a link at a branch does not.
"""

from __future__ import annotations

import dataclasses
import json
from typing import TYPE_CHECKING, Any, cast

import pytest

from harness_core.effects.types import ToolExecution
from harness_core.journal.types import NewEvent
from harness_core.provenance.ledger import ProvenanceLedger
from harness_core.provenance.repository import (
    RepositoryTree,
    blob_url,
    label,
    repository_file,
)
from harness_core.provenance.rules import (
    CONNECTOR_RULE,
    MCP_RULE,
    NO_CONTENT_RULE,
    REPOSITORY_RULE,
    WEB_RULE,
    UndeclaredProvenance,
    require_rules,
)
from harness_core.provenance.stamp import PROVENANCE_EVENT, UNDECLARED, ProvenanceStamp
from harness_core.provenance.types import (
    NO_CONTENT,
    ConnectorItem,
    MemoryRef,
    RecordRef,
    RepositoryRef,
    ResultProvenance,
    SkillSourceRef,
    WebPage,
    provenance_from_wire,
    source_from_wire,
)
from harness_core.repos.cache import TREE_FILE, RepositoryCache
from harness_core.repos.fetch import MIB, parse_tree_listing
from harness_core.testing.contracts.source_ledger import Stream

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic import JsonValue

COMMIT = "4f83564c1f0e8596be3f71d78f847a96447a3bdd"
ONYX = RepositoryRef("https://github.com/onyx-dot-app/onyx", COMMIT)
RUN = "run-1"


def test_wp96_type_1_provenance_has_no_default() -> None:
    fields = dataclasses.fields(ResultProvenance)
    assert [f.name for f in fields] == ["readers", "integrity", "sources"]
    assert all(f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING for f in fields)
    with pytest.raises(TypeError):
        _ = cast("Any", ResultProvenance)()


@pytest.mark.parametrize(
    "source",
    [
        WebPage("https://onyx.app/", "2026-11-05T10:00:00Z"),
        ONYX,
        ONYX.at("backend/onyx/main.py"),
        RecordRef("rec-1"),
        RecordRef("rec-2", "attachment"),
        RecordRef("rec-3", "indexed", "pub-1"),
        ConnectorItem("DRIVE", "file-1", "inst-1"),
        MemoryRef("mem-1"),
        SkillSourceRef("triage"),
    ],
)
def test_wp96_type_2_every_source_kind_round_trips(source: Any) -> None:  # noqa: ANN401 - each SourceRef kind
    assert source_from_wire(source.to_wire()) == source
    whole = ResultProvenance("principals", "external", (source,))
    assert provenance_from_wire(whole.to_wire()) == whole


@pytest.mark.parametrize(
    "wire",
    [
        {"readers": "everyone", "integrity": "first_party", "sources": []},
        {"readers": "public", "integrity": "internal", "sources": []},
        {"readers": "public", "integrity": "external", "sources": [{"kind": "repository", "url": "https://x/y"}]},
        {"readers": "public", "integrity": "external"},
        "public",
    ],
)
def test_wp96_type_3_an_unreadable_record_is_not_a_provenance(wire: JsonValue) -> None:
    assert provenance_from_wire(wire) is None


def test_wp96_type_4_the_join_is_the_more_held_and_keeps_every_source() -> None:
    web = ResultProvenance("public", "external", (WebPage("https://a/"),))
    record = ResultProvenance("principals", "first_party", (RecordRef("rec-1"),))
    joined = web.join(record)
    assert (joined.readers, joined.integrity) == ("principals", "external")
    assert joined.sources == (WebPage("https://a/"), RecordRef("rec-1"))
    assert joined.citable == (WebPage("https://a/"),) and joined.checked == (RecordRef("rec-1"),)
    assert joined.join(ResultProvenance("unknown", "first_party", ())).readers == "unknown"
    assert NO_CONTENT.join(NO_CONTENT) == NO_CONTENT


def test_wp96_rule_1_a_tool_without_a_rule_is_refused() -> None:
    with pytest.raises(UndeclaredProvenance) as refused:
        _ = require_rules(["search", "mcp_tool", "fetch_url"], {"search": NO_CONTENT_RULE, "fetch_url": WEB_RULE})
    assert refused.value.names == ("mcp_tool",)
    with pytest.raises(UndeclaredProvenance):
        _ = ProvenanceStamp.for_tools(["a", "b"], {"a": NO_CONTENT_RULE, "b": None}, journal=True)


def test_wp96_rule_2_rules_by_server_kind() -> None:
    ok, failed = ToolExecution("ok", "x"), ToolExecution("error", "x")
    stamp = ProvenanceStamp({"mcp": MCP_RULE, "conn": CONNECTOR_RULE, "web": WEB_RULE}, journal=True)
    # An MCP server writes its errors too; a connector action's error is the platform's own text (WP-62).
    assert stamp.provenance("mcp", ok) == stamp.provenance("mcp", failed) == ResultProvenance("unknown", "external", ())
    assert stamp.provenance("conn", ok) == ResultProvenance("unknown", "first_party", ())
    assert stamp.provenance("conn", failed) == NO_CONTENT
    assert stamp.provenance("web", ok) == ResultProvenance("public", "external", ())


def test_wp96_rule_3_only_the_host_claim_makes_a_repository_public() -> None:
    stamp = ProvenanceStamp({"repository_fetch": REPOSITORY_RULE}, journal=True)
    claim = ResultProvenance("public", "external", (ONYX,))
    assert stamp.provenance("repository_fetch", ToolExecution("ok", "{}", provenance=claim)) == claim
    assert stamp.provenance("repository_fetch", ToolExecution("ok", "{}")).readers == "unknown"
    assert ToolExecution("ok", "{}", provenance=claim).public is True


def test_wp96_rule_4_a_tool_registered_mid_run_without_a_rule_is_unknown() -> None:
    stamp = ProvenanceStamp({}, journal=True)
    assert stamp.provenance("late_tool", ToolExecution("ok", "x")) == UNDECLARED
    assert UNDECLARED.readers == "unknown"


def _named(events: object) -> ResultProvenance | None:
    del events
    return ResultProvenance("principals", "first_party", (RecordRef("rec-9"), WebPage("https://b/")))


def test_wp96_rule_5_what_the_call_journaled_is_joined_whatever_the_rule() -> None:
    stamp = ProvenanceStamp({"search": NO_CONTENT_RULE}, journal=True, sources_of=_named, clock=lambda: "T")
    execution = ToolExecution("ok", "x", events=(NewEvent("x.pipeshub.citations.recorded", {}),))
    record = stamp.stamp("search", "pipeshub@kg/search", "inv-1", execution).events[-1]
    assert record.type == PROVENANCE_EVENT
    data = cast("dict[str, Any]", record.data)
    assert data["invocationId"] == "inv-1" and data["rule"] == "no_content"
    assert provenance_from_wire(data["provenance"]) == ResultProvenance(
        "principals", "first_party", (RecordRef("rec-9"), WebPage("https://b/", "T"))
    )
    # A Node that does not keep the records: nothing journaled, the ledger replays.
    quiet = ProvenanceStamp({"search": NO_CONTENT_RULE}, journal=False)
    assert quiet.stamp("search", "t", "inv-1", execution) is execution


def _recorded(stream: Stream, invocation: str, provenance: ResultProvenance) -> None:
    _ = stream.add(
        PROVENANCE_EVENT,
        {"invocationId": invocation, "toolId": "t", "rule": "r", "provenance": provenance.to_wire()},
        run_id=RUN,
    )


def test_wp96_ledger_1_folds_records_and_replays_the_rest() -> None:
    stream = Stream()
    stream.admitted(RUN)
    _recorded(stream, "inv-1", ResultProvenance("public", "external", (WebPage("https://a/"), ONYX)))
    _ = stream.add("tool.outcome", {"invocationId": "inv-1", "status": "ok", "labels": {"integrity": "external"}}, run_id=RUN)
    ledger = ProvenanceLedger()
    ledger.apply(stream.events)
    assert ledger.run_provenance(RUN) == ResultProvenance("public", "external", (WebPage("https://a/"), ONYX))
    assert ledger.citable() == (WebPage("https://a/"), ONYX)
    assert ledger.repositories(public_only=True) == (ONYX,)
    assert ledger.untrusted() is True
    # An outcome with no record: the core replay reads its item labels (none: unknown).
    _ = stream.add("tool.outcome", {"invocationId": "inv-2", "status": "ok", "labels": {"integrity": "internal"}}, run_id=RUN)
    ledger.apply(stream.events)
    assert ledger.run_provenance(RUN).readers == "unknown"
    exported = ledger.export()
    assert [e["recorded"] for e in exported] == [True, False]


def test_wp96_ledger_2_an_unreadable_record_counts_as_unknown() -> None:
    stream = Stream()
    stream.admitted(RUN)
    _ = stream.add(PROVENANCE_EVENT, {"invocationId": "inv-1", "provenance": {"readers": "public"}}, run_id=RUN)
    ledger = ProvenanceLedger()
    ledger.apply(stream.events)
    assert ledger.run_provenance(RUN).readers == "unknown"


class _Trees:
    """The fetched trees by commit, as the fetch's own ``git ls-tree`` listed them."""

    def __init__(self, **trees: RepositoryTree) -> None:
        self._trees = trees

    def tree(self, source: RepositoryRef) -> RepositoryTree | None:
        return self._trees.get(source.commit)


ONYX_TREE = RepositoryTree(
    frozenset({"backend/onyx/main.py", "README.md", "src/x.py"}), frozenset({"backend", "backend/onyx", "src"})
)
TREES = _Trees(**{COMMIT: ONYX_TREE})


def test_wp96_repo_1_a_link_at_the_fetched_commit_is_the_file() -> None:
    link = f"https://github.com/onyx-dot-app/onyx/blob/{COMMIT[:10]}/backend/onyx/main.py#L12"
    found = repository_file(link, [ONYX], TREES)
    assert found == ONYX.at("backend/onyx/main.py")
    assert blob_url(found) == f"https://github.com/onyx-dot-app/onyx/blob/{COMMIT}/backend/onyx/main.py"
    assert label(found) == "onyx-dot-app/onyx · backend/onyx/main.py"
    assert repository_file(f"https://github.com/onyx-dot-app/onyx/commit/{COMMIT}", [ONYX], TREES) == ONYX


def test_wp96_repo_2_a_folder_opens_the_hosts_tree_view() -> None:
    folder = repository_file(f"https://github.com/onyx-dot-app/onyx/blob/{COMMIT}/backend", [ONYX], TREES)
    assert folder == ONYX.at("backend", folder=True)
    assert blob_url(folder) == f"https://github.com/onyx-dot-app/onyx/tree/{COMMIT}/backend"
    lab = RepositoryRef("https://gitlab.com/group/sub/proj", COMMIT, "src", folder=True)
    assert blob_url(lab) == f"https://gitlab.com/group/sub/proj/-/tree/{COMMIT}/src"


@pytest.mark.parametrize(
    ("link", "path"),
    [
        ("https://github.com/onyx-dot-app/onyx/blob/main/backend/onyx/main.py", "backend/onyx/main.py"),
        ("https://github.com/onyx-dot-app/onyx/tree/release/v2/backend", "backend"),
        ("https://github.com/onyx-dot-app/onyx/blob/v1.2.0/README.md", "README.md"),
    ],
)
def test_wp96_repo_3_a_link_at_a_branch_is_the_fetched_commits_permalink_when_the_path_exists(link: str, path: str) -> None:
    """A long research run wrote 38 links at ``main``: each is the fetched commit's file when that path exists there."""
    found = repository_file(link, [ONYX], TREES)
    assert found is not None and (found.commit, found.path) == (COMMIT, path)
    assert COMMIT in blob_url(found)


@pytest.mark.parametrize(
    "link",
    [
        "https://github.com/onyx-dot-app/onyx/blob/main/backend/onyx/gone.py",
        f"https://github.com/onyx-dot-app/onyx/blob/{COMMIT}/backend/onyx/gone.py",
        f"https://github.com/someone/else/blob/{COMMIT}/README.md",
        "https://github.com/onyx-dot-app/onyx/blob/0000000000/README.md",
        f"https://gitlab.com/onyx-dot-app/onyx/blob/{COMMIT}/README.md",
        "https://github.com/onyx-dot-app/onyx/commit/0000000000",
    ],
)
def test_wp96_repo_4_a_path_the_fetched_tree_lacks_or_another_commit_is_no_source(link: str) -> None:
    assert repository_file(link, [ONYX], TREES) is None


def test_wp96_repo_5_an_unreadable_tree_cites_nothing_but_the_commit_page() -> None:
    link = f"https://github.com/onyx-dot-app/onyx/blob/{COMMIT}/README.md"
    assert repository_file(link, [ONYX], _Trees()) is None
    assert repository_file(f"https://github.com/onyx-dot-app/onyx/commit/{COMMIT}", [ONYX], _Trees()) == ONYX


def test_wp96_repo_6_the_fetch_lists_its_tree_and_the_cache_keeps_it(tmp_path: Path) -> None:
    listing = b"100644 blob aa\tREADME.md\x00040000 tree bb\tbackend\x00100644 blob cc\tbackend/main.py\x00"
    tree = parse_tree_listing(listing)
    assert (tree.files, tree.folders) == (frozenset({"README.md", "backend/main.py"}), frozenset({"backend"}))
    cache = RepositoryCache(tmp_path / "cache", max_bytes=64 * MIB, max_age_s=3600)
    staged = cache.temporary()
    (staged / "repo.tar").write_bytes(b"x")
    (staged / TREE_FILE).write_text(json.dumps({"files": sorted(tree.files), "folders": sorted(tree.folders)}))
    _ = cache.put("k", staged, head=COMMIT, ref=None, label="github.com/onyx-dot-app/onyx", public=True)
    assert cache.tree_of("github.com/onyx-dot-app/onyx", COMMIT) == tree
    assert cache.tree_of("github.com/onyx-dot-app/onyx", "0" * 40) is None
    assert cache.tree_of("github.com/other/repo", COMMIT) is None
