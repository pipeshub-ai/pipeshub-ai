"""Unit tests for the connector scenario-matrix harness (no live services).

The harness decides, for every connector that plugs in, whether an add, edit,
rename, share, delete, full sync or filter change "worked". The properties worth
pinning without a stack:

* a scenario the source cannot perform is skipped with the source's reason, and
  a known product bug is a strict xfail, both decided at collection;
* record reads work for both shapes the graph providers return (a node dict by
  name, a ``Record`` model by external id), so a lookup cannot silently read
  ``None`` fields and wait forever;
* a round runs once per connector and replays its failure to every test that
  depends on it, rather than re-running a half-applied set of source changes;
* "the vectors hold the new text" means the old text is gone too.

The fake graph and vector store below return exactly what the real ones return:
``get_record_by_name`` gives the node's properties (camelCase, ``id`` on Neo4j,
``_key`` on ArangoDB), ``get_record_by_external_id`` gives a ``FileRecord``
model, and vector texts are the ``page_content`` strings of the chunks.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import connectors.scenario_matrix as sm
from app.config.constants.arangodb import Connectors, OriginTypes
from app.models.entities import FileRecord, RecordType
from connectors.scenario_matrix import (
    Action,
    ConnectorScenarioMatrix,
    MatrixRun,
    RecordView,
    Role,
    ScenarioAdapter,
    SourceItem,
    apply_static_marks,
    static_marks_for,
)
from helper.vector_store import VectorStoreProbe

pytestmark = pytest.mark.unit

CONNECTOR = "conn-1"


class _Matrix(ConnectorScenarioMatrix):
    SOURCE = "Example"
    UNSUPPORTED = {Action.CHANGE_PERMISSION.value: "shares are not synced"}
    KNOWN_BUGS = {"incr_delete": "a delete never reaches the graph"}


class _Scheduled(ConnectorScenarioMatrix):
    SOURCE = "Scheduled"
    SCHEDULED_SYNC = True


def _names(marks: list[pytest.MarkDecorator]) -> list[str]:
    return [m.mark.name for m in marks]


def test_unsupported_action_skips_with_the_sources_reason() -> None:
    marks = static_marks_for(_Matrix, _Matrix.test_matrix_incr_update_permissions)
    assert _names(marks) == ["skip"]
    assert marks[0].mark.kwargs["reason"] == "not supported by Example: shares are not synced"


def test_known_bug_is_a_strict_xfail() -> None:
    marks = static_marks_for(_Matrix, _Matrix.test_matrix_incr_delete)
    assert _names(marks) == ["xfail"]
    assert marks[0].mark.kwargs == {"reason": "a delete never reaches the graph", "strict": True}


def test_supported_scenario_carries_no_mark() -> None:
    assert static_marks_for(_Matrix, _Matrix.test_matrix_incr_add) == []


def test_scheduled_sync_runs_only_where_opted_in() -> None:
    assert _names(static_marks_for(_Matrix, _Matrix.test_matrix_scheduled_sync)) == ["skip"]
    assert static_marks_for(_Scheduled, _Scheduled.test_matrix_scheduled_sync) == []


def test_matrix_runs_on_the_neo4j_leg_unless_widened() -> None:
    assert sm.graph_leg_skip("neo4j", "neo4j") is None
    skip = sm.graph_leg_skip("arango", "neo4j")
    assert skip is not None and "arango leg" in skip.mark.kwargs["reason"]
    assert sm.graph_leg_skip("arango", "neo4j, arango") is None


def test_apply_static_marks_touches_only_matrix_classes(monkeypatch) -> None:
    monkeypatch.setenv("TEST_GRAPH_DB_TYPE", "neo4j")
    monkeypatch.delenv("SCENARIO_MATRIX_GRAPHS", raising=False)
    added: dict[str, list[str]] = {}

    def _item(name: str, cls: type | None, fn: Any) -> SimpleNamespace:
        added[name] = []
        return SimpleNamespace(
            cls=cls, function=fn,
            add_marker=lambda m, name=name: added[name].append(m.mark.name),
        )

    class Other:
        def test_x(self) -> None: ...

    apply_static_marks([
        _item("perm", _Matrix, _Matrix.test_matrix_incr_update_permissions),
        _item("add", _Matrix, _Matrix.test_matrix_incr_add),
        _item("other", Other, Other.test_x),
        _item("plain", None, lambda: None),
    ])
    assert added == {"perm": ["skip"], "add": [], "other": [], "plain": []}


def _node(**overrides: Any) -> dict[str, Any]:
    """A record node's properties as the Neo4j provider returns them."""
    node = {
        "id": "rec-1",
        "recordName": "keep-mx1.txt",
        "virtualRecordId": "vr-1",
        "indexingStatus": "COMPLETED",
        "version": 0,
        "externalRecordId": "ext-1",
        "externalRevisionId": "etag-1",
    }
    node.update(overrides)
    return node


def _model(**overrides: Any) -> FileRecord:
    fields: dict[str, Any] = {
        "id": "rec-1",
        "record_name": "keep-mx1.txt",
        "record_type": RecordType.FILE,
        "external_record_id": "ext-1",
        "external_revision_id": "etag-1",
        "version": 0,
        "origin": OriginTypes.CONNECTOR,
        "connector_name": Connectors.NEXTCLOUD,
        "connector_id": CONNECTOR,
        "virtual_record_id": "vr-1",
        "indexing_status": "COMPLETED",
        "is_file": True,
    }
    fields.update(overrides)
    return FileRecord(**fields)


def test_record_view_reads_every_provider_shape() -> None:
    expected = RecordView(
        id="rec-1", name="keep-mx1.txt", virtual_record_id="vr-1",
        indexing_status="COMPLETED", version=0, revision="etag-1", external_id="ext-1",
    )
    arango = {k: v for k, v in _node().items() if k != "id"} | {"_key": "rec-1"}
    assert RecordView.of(_node()) == expected
    assert RecordView.of(arango) == expected
    assert RecordView.of(_model()) == expected
    assert RecordView.of(None) is None


def test_changed_since_needs_a_new_version_or_revision() -> None:
    before = RecordView.of(_node())
    assert not RecordView.of(_node()).changed_since(before)
    assert RecordView.of(_node(version=1)).changed_since(before)
    assert RecordView.of(_node(externalRevisionId="etag-2")).changed_since(before)


async def test_a_round_runs_once_and_replays_its_failure() -> None:
    rounds = sm._Rounds()
    calls: list[str] = []

    async def ok() -> str:
        calls.append("ok")
        return "done"

    async def boom() -> None:
        calls.append("boom")
        raise AssertionError("sync never finished")

    assert await rounds.run("a", ok) == "done"
    assert await rounds.run("a", ok) == "done"
    for _ in range(2):
        with pytest.raises(AssertionError, match="sync never finished"):
            await rounds.run("b", boom)
    assert calls == ["ok", "boom"]


class _Source:
    """A connector's source and its graph, as far as the harness can see them.

    Changes made at the source reach the graph only on a sync, and only after
    ``lag`` syncs, like a change feed that has not caught up yet.
    """

    def __init__(self, lag: int = 0, *, index_names: bool = False) -> None:
        self.lag = lag
        # Like a connector that indexes a page's heading or a table's DDL with its body.
        self.index_names = index_names
        self.files: dict[str, dict[str, Any]] = {}
        self.graph: dict[str, dict[str, Any]] = {}
        self.vectors: dict[str, list[str]] = {}
        self.syncs = 0

    def sync(self) -> None:
        self.syncs += 1
        if self.syncs <= self.lag:
            return
        for ext, f in self.files.items():
            node = self.graph.get(ext)
            if node is None or node["externalRevisionId"] != f["etag"] or node["recordName"] != f["name"]:
                version = 0 if node is None else node["version"] + 1
                vrid = f"vr-{ext}-{version}"
                self.graph[ext] = _node(
                    id=f"rec-{ext}", recordName=f["name"], virtualRecordId=vrid,
                    version=version, externalRecordId=ext, externalRevisionId=f["etag"],
                )
                self.vectors[vrid] = [f"{f['name']}\n{f['text']}" if self.index_names else f["text"]]
        for ext in [e for e in self.graph if e not in self.files]:
            self.vectors.pop(self.graph.pop(ext)["virtualRecordId"], None)


class _Graph:
    def __init__(self, source: _Source) -> None:
        self.source = source

    async def get_record_by_external_id(self, connector_id: str, ext: str) -> FileRecord | None:
        node = self.source.graph.get(ext)
        if node is None:
            return None
        return _model(
            id=node["id"], record_name=node["recordName"], external_record_id=ext,
            external_revision_id=node["externalRevisionId"], version=node["version"],
            virtual_record_id=node["virtualRecordId"],
        )

    async def get_record_by_name(self, connector_id: str, name: str) -> dict[str, Any] | None:
        return next((dict(n) for n in self.source.graph.values() if n["recordName"] == name), None)


class _Vectors:
    def __init__(self, source: _Source) -> None:
        self.source = source

    async def content_texts(self, vrid: str) -> list[str]:
        return list(self.source.vectors.get(vrid, []))


class _Adapter(ScenarioAdapter):
    source = "Fake"

    def __init__(self, src: _Source) -> None:
        super().__init__(client=None, graph=_Graph(src), connector_id=CONNECTOR)  # type: ignore[arg-type]
        self.src = src

    async def create_item(self, role: Role, text: str, token: str) -> SourceItem:
        ext = f"{role.value}-{token}"
        self.src.files[ext] = {"name": f"{ext}.txt", "etag": "e0", "text": text}
        return SourceItem(role=role, key=ext, record_name=f"{ext}.txt", text=text,
                          token=token, external_id=ext)

    async def update_content(self, item: SourceItem, text: str, token: str) -> SourceItem:
        self.src.files[item.key].update(etag="e1", text=text)
        return SourceItem(role=item.role, key=item.key, record_name=item.record_name,
                          text=text, token=token, external_id=item.external_id)

    async def update_metadata(self, item: SourceItem) -> SourceItem:
        name = f"renamed-{item.token}.txt"
        self.src.files[item.key]["name"] = name
        return SourceItem(role=item.role, key=item.key, record_name=name, text=item.text,
                          token=item.token, external_id=item.external_id)

    async def delete_item(self, item: SourceItem) -> None:
        del self.src.files[item.key]

    async def trigger_incremental_sync(self) -> None:
        self.src.sync()


def _run(src: _Source, unsupported: dict[str, str]) -> MatrixRun:
    run = MatrixRun(_Adapter(src), unsupported=unsupported, vector=_Vectors(src))  # type: ignore[arg-type]

    async def settle() -> None:
        return None

    run.settle = settle  # type: ignore[method-assign]
    return run


_NO_SHARE_OR_FILTER = {
    Action.CHANGE_PERMISSION.value: "no shares",
    Action.SET_FILTER.value: "no filters",
}


async def test_rounds_create_only_what_is_supported_and_retry_a_lagging_sync() -> None:
    src = _Source(lag=1)
    run = _run(src, _NO_SHARE_OR_FILTER)

    await run.add_round()
    assert set(run.items) == {Role.KEEP, Role.CONTENT, Role.METADATA, Role.DELETE}
    assert src.syncs == 2, "the first sync saw nothing, so the harness must sync again"
    assert all(v.indexing_status == "COMPLETED" for v in run.added.values())

    old_token = run.item(Role.CONTENT).token
    await run.mutate_round()
    content = run.item(Role.CONTENT)
    view = await run.wait_vectors_hold(content, content.token, absent=[old_token])
    assert view.changed_since(run.added[Role.CONTENT])
    assert (await run.record(run.item(Role.METADATA))).name == run.item(Role.METADATA).record_name
    assert await run.record(run.item(Role.DELETE)) is None


async def test_sync_until_gives_up_and_names_the_change() -> None:
    src = _Source(lag=10)
    run = _run(src, _NO_SHARE_OR_FILTER)
    with pytest.raises(AssertionError, match="did not happen after 3 incremental syncs"):
        await run.add_round()
    assert src.syncs == sm.SYNC_ATTEMPTS


async def test_vectors_that_still_hold_the_old_text_do_not_count(monkeypatch) -> None:
    monkeypatch.setattr(sm, "POLL_INTERVAL_SEC", 0.05)
    src = _Source()
    run = _run(src, _NO_SHARE_OR_FILTER)
    item = await run.adapter.create_item(Role.CONTENT, "old mxold", "mxold")
    src.sync()
    view = await run.record(item)
    src.vectors[view.virtual_record_id] = ["new mxnew", "leftover mxold"]
    with pytest.raises(AssertionError, match=r"old text still present: \['mxold'\]"):
        await run.wait_vectors_hold(item, "mxnew", absent=["mxold"], timeout=1)


async def test_an_old_token_left_only_in_the_items_name_is_not_stale_text(monkeypatch) -> None:
    monkeypatch.setattr(sm, "POLL_INTERVAL_SEC", 0.05)
    src = _Source(index_names=True)
    run = _run(src, _NO_SHARE_OR_FILTER)
    await run.add_round()
    old_token = run.item(Role.CONTENT).token
    await run.mutate_round()
    content = run.item(Role.CONTENT)
    assert old_token in content.record_name

    view = await run.wait_content_replaced(content)

    assert view.changed_since(run.added[Role.CONTENT])
    # The bare token is still there, in the name, so it cannot be what "old text" means.
    with pytest.raises(AssertionError, match="never matched"):
        await run.wait_vectors_hold(content, content.token, absent=[old_token], timeout=1)


async def test_an_old_body_left_in_the_vectors_still_fails_the_edit(monkeypatch) -> None:
    monkeypatch.setattr(sm, "POLL_INTERVAL_SEC", 0.05)
    src = _Source(index_names=True)
    run = _run(src, _NO_SHARE_OR_FILTER)
    await run.add_round()
    old_text = run.item(Role.CONTENT).text
    await run.mutate_round()
    content = run.item(Role.CONTENT)
    view = await run.record(content)
    src.vectors[view.virtual_record_id].append(old_text)

    with pytest.raises(AssertionError, match=r"old text still present: \['The mx"):
        await run.wait_content_replaced(content, timeout=1)


class _ScrollClient:
    """Answers ``scroll`` the way qdrant does: a page of points and the next offset."""

    def __init__(self, pages: dict[str, list[list[str]]]) -> None:
        self.pages = pages
        self.filters: list[Any] = []

    async def get_collections(self) -> SimpleNamespace:
        return SimpleNamespace(collections=[SimpleNamespace(name=n) for n in self.pages])

    async def scroll(self, *, collection_name: str, scroll_filter: Any, offset: Any, **_: Any):
        self.filters.append(scroll_filter)
        pages = self.pages[collection_name]
        index = offset or 0
        points = [SimpleNamespace(payload={"page_content": t}) for t in pages[index]]
        return points, (index + 1 if index + 1 < len(pages) else None)


async def test_content_texts_pages_through_every_collection_and_skips_the_summary() -> None:
    probe = VectorStoreProbe(host="localhost", api_key="")
    client = _ScrollClient({"a": [["one", "two"], ["three"]], "b": [["four"]]})
    probe._client = client  # type: ignore[assignment]

    assert await probe.content_texts("vr-1") == ["one", "two", "three", "four"]
    only = client.filters[0]
    assert only.must[0].key == "metadata.virtualRecordId"
    assert only.must[0].match.value == "vr-1"
    assert only.must_not[0].key == "metadata.isRecordSummary"


class _Resp:
    """The parts of a ``requests.Response`` the search wait reads."""

    def __init__(self, status: int, body: Any = None) -> None:
        self.status_code = status
        self._body = body

    def json(self) -> Any:
        if self._body is None:
            raise ValueError("no JSON body")
        return self._body


def _hits(*vrids: str) -> dict[str, Any]:
    """A 200 search body as the API sends it: hits under searchResponse.searchResults."""
    return {"searchResponse": {"searchResults": [
        {"content": "chunk", "metadata": {"virtualRecordId": v}} for v in vrids
    ]}}


def test_search_verdict_counts_a_miss_only_after_a_parsed_200() -> None:
    assert sm.search_verdict(200, _hits("vr-1"), "vr-1") is True
    assert sm.search_verdict(200, _hits("vr-2"), "vr-1") is False
    assert sm.search_verdict(200, _hits(), "vr-1") is False
    assert sm.search_verdict(500, None, "vr-1") is None
    assert sm.search_verdict(200, {"searchResponse": {}}, "vr-1") is None
    assert sm.search_verdict(200, "oops", "vr-1") is None
    assert sm.search_verdict(401, None, "vr-1") is None
    assert sm.search_verdict(404, None, "vr-1") is None
    assert sm.search_verdict(404, None, "vr-1", denial_is_miss=True) is False
    assert sm.search_verdict(500, None, "vr-1", denial_is_miss=True) is None
    assert sm.search_verdict(200, {"searchResults": [None]}, "vr-1") is None
    assert sm.search_verdict(200, {"searchResults": [{"content": "no id"}]}, "vr-1") is None
    assert sm.search_verdict(200, {"searchResults": [{"virtual_record_id": "vr-1"}]}, "vr-1") is True


def _search_run(monkeypatch, responses: list[_Resp]) -> MatrixRun:
    monkeypatch.setattr(sm, "POLL_INTERVAL_SEC", 0.01)
    run = _run(_Source(), _NO_SHARE_OR_FILTER)
    queue = list(responses)
    run._search = lambda query, as_user: queue.pop(0) if len(queue) > 1 else queue[0]  # type: ignore[method-assign]
    return run


async def test_search_down_never_passes_as_a_miss(monkeypatch) -> None:
    run = _search_run(monkeypatch, [_Resp(500)])
    with pytest.raises(AssertionError, match=r"last search: HTTP 500, no usable answer"):
        await run.wait_search("q", "vr-1", expect=False, timeout=0.3)


async def test_a_found_needs_a_200_hit(monkeypatch) -> None:
    run = _search_run(monkeypatch, [_Resp(500), _Resp(200, {"searchResponse": {}})])
    with pytest.raises(AssertionError, match=r"HTTP 200, no usable answer"):
        await run.wait_search("q", "vr-1", expect=True, timeout=0.3)

    run = _search_run(monkeypatch, [_Resp(500), _Resp(200, _hits("vr-1"))])
    await run.wait_search("q", "vr-1", expect=True, timeout=5)


async def test_a_miss_settles_on_a_200_without_the_record(monkeypatch) -> None:
    run = _search_run(monkeypatch, [_Resp(503), _Resp(200, _hits("vr-2"))])
    await run.wait_search("q", "vr-1", expect=False, timeout=5)


async def test_a_refusal_counts_as_a_miss_only_when_asked(monkeypatch) -> None:
    """Only the sharee's searches accept a 403/404; for the owner it is a fault, not a miss."""
    owner = object()
    run = _search_run(monkeypatch, [_Resp(404)])
    with pytest.raises(AssertionError, match=r"HTTP 404, no usable answer"):
        await run.wait_search("q", "vr-1", expect=False, as_user=owner, timeout=0.3)  # type: ignore[arg-type]

    run = _search_run(monkeypatch, [_Resp(404)])
    await run.wait_search("q", "vr-1", expect=False, as_user=owner, denial_is_miss=True, timeout=5)  # type: ignore[arg-type]


async def test_github_adapter_searches_as_the_admin_and_commits_as_the_org(monkeypatch) -> None:
    """The org login drives commits; ``owner`` stays None so searches run as the admin."""
    import connectors.github_teams.github_scenario_adapter as gh

    commits: list[str] = []

    async def fake_commit(rest, owner, repo, branch, changes, message, **kwargs) -> None:
        commits.append(owner)

    monkeypatch.setattr(gh, "commit_changes", fake_commit)
    adapter = gh.GitHubCodeAdapter(
        rest=object(), repo_owner="acme", repo="repo", branch="main",
        client=None, graph=None, connector_id=CONNECTOR,  # type: ignore[arg-type]
    )
    assert adapter.owner is None
    await adapter.create_item(Role.CONTENT, "text", "tok")
    assert commits == ["acme"]

    searched: list[str] = []
    monkeypatch.setattr(sm, "search_connector_as_admin", lambda *a: searched.append("admin"))
    monkeypatch.setattr(sm, "search_connector_as", lambda *a: searched.append("user"))
    MatrixRun(adapter, unsupported={}, vector=None)._search("q", adapter.owner)  # type: ignore[arg-type]
    assert searched == ["admin"]


class _Mongo:
    """Storage documents as MongoStoreProbe finds them: by name, under the given folder or the flat one."""

    def __init__(self, documents: dict[str, str]) -> None:
        self.documents = documents
        self.vendor_asked: list[str] = []

    async def envelope_path(self, org_id: str, vrid: str, *, within: str) -> str:
        flat = f"{org_id}/PipesHub/records/{vrid}"
        paths = [p for name, p in self.documents.items()
                 if name == f"record_{vrid}" and p.startswith((within + "/", flat))]
        assert len(paths) == 1, f"record_{vrid} is not under {within!r} or {flat!r}"
        return paths[0]

    async def storage_vendor_under_path(self, prefix: str) -> str | None:
        self.vendor_asked.append(prefix)
        return "s3"


async def test_filter_footprint_looks_where_indexing_filed_the_record() -> None:
    src = _Source()
    run = _run(src, _NO_SHARE_OR_FILTER)
    run.org_id = "org-1"
    item = await run.adapter.create_item(Role.FILTERED, "filtered mxf", "mxf")
    src.sync()
    view = await run.record(item)
    filed = f"org-1/PipesHub/records/{CONNECTOR}/bucket/{item.record_name}"
    run.mongo = _Mongo({f"record_{view.virtual_record_id}": filed})  # type: ignore[assignment]

    footprint, vendor = await run.footprint(item, view)

    assert footprint.storage_prefix == filed
    assert footprint.virtual_record_id == view.virtual_record_id
    assert footprint.connector_id == CONNECTOR
    assert (vendor, run.mongo.vendor_asked) == ("s3", [filed])
