"""Entity routes (app.api.routes.knowledge_search).

The access decisions are the shared ones (``entity_permissions``); these tests
check the routes hand them every result, return nothing the check refuses, and
take the caller from the session, never from the body.
"""
import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import jsonschema
import pytest
import yaml
from fastapi import HTTPException
from fastapi.responses import JSONResponse

import app.edition_config  # noqa: F401  (the route module loads cleanly only after this)
from app.api.routes import knowledge_search as ks
from app.core.org_context import OrgContext, bind
from app.modules.retrieval.entity_permissions import (
    EntityAccessContext,
    EntityRecordPage,
)
from app.services.graph_db.interface.graph_db_provider import AccessibleContainers

ORG, USER, RID, VRID = "org-1", "user-1", "rec-1", "vrid-1"


@pytest.fixture(autouse=True)
def _signed_in(request: pytest.FixtureRequest) -> Iterator[None]:
    """The auth middleware binds the org context before a route runs; a test that asks for ``no_org_context`` runs without it."""
    if "no_org_context" in request.fixturenames:
        yield
        return
    with bind(OrgContext(ORG, USER, "user", "jwt", "req-1")):
        yield


@pytest.fixture
def no_org_context() -> None:
    """Marks a test that runs with no org context bound (see ``_signed_in``)."""


def _request(user: dict | None = None) -> MagicMock:
    request = MagicMock()
    request.state.user = {"orgId": ORG, "userId": USER} if user is None else user
    container = request.app.container
    container.logger.return_value = MagicMock()
    container.config_service.return_value = MagicMock()
    return request


# ---- entities ---------------------------------------------------------------
def _graph(accessible: bool) -> MagicMock:
    graph = MagicMock()
    graph.get_accessible_containers = AsyncMock(return_value=AccessibleContainers())
    graph.filter_accessible_virtual_record_ids = AsyncMock(return_value={VRID: RID} if accessible else {})
    graph.get_records_by_record_ids = AsyncMock(return_value=[{
        "_key": RID, "recordName": "orders.py", "recordType": "FILE", "connectorName": "DRIVE",
        "connectorId": "c1", "webUrl": "https://x.test/orders", "virtualRecordId": VRID,
        "origin": "CONNECTOR", "indexingStatus": "COMPLETED", "version": 0,
    }])
    graph.get_user_by_user_id = AsyncMock(return_value={"id": "user-key"})
    graph.config_service = MagicMock()
    return graph


def _retrieval() -> MagicMock:
    retrieval = MagicMock()
    retrieval.blob_store.get_record_from_storage = AsyncMock(return_value={"block_containers": {"blocks": [
        {"index": 0, "data": "def compute_invoice_total_v2(x):\n    return x",
         "code_metadata": {"start_line": 7, "end_line": 8, "kind": "function",
                           "qualified_name": "function:compute_invoice_total_v2", "language": "python"}},
    ]}})
    return retrieval


async def _fake_grep(**kwargs: dict) -> list[dict]:
    kwargs["found"][VRID] = {"virtual_record_id": VRID, "match_count": "1", "match_preview": "compute_invoice_total_v2"}
    return list(kwargs["found"].values())


async def _grep(accessible: bool, retrieval: MagicMock | None = None, **body) -> dict:
    body.setdefault("pattern", "compute_invoice_total_v2")
    with patch.object(ks, "run_pattern_match_with_llm_grep", _fake_grep), \
         patch.object(ks, "check_pattern_match_eligible", AsyncMock(return_value=True)), \
         patch.object(ks, "resolve_frontend_url", AsyncMock(return_value="https://pipes.test")):
        response = await ks.grep(_request(), ks.GrepQuery(**body), retrieval or _retrieval(), _graph(accessible))
    return json.loads(response.body)


async def test_grep_returns_line_matches_with_symbol_and_url() -> None:
    out = await _grep(True)
    assert out["totalRecords"] == 1
    record = out["records"][0]
    assert record["recordId"] == RID and record["webUrl"] == "https://x.test/orders"
    assert record["language"] == "python"
    match = record["matches"][0]
    assert match["line"] == 7 and match["symbol"]["name"] == "function:compute_invoice_total_v2"
    assert match["citation"] == {"recordId": RID, "blockIndex": 0, "line": 7}


async def test_grep_without_access_returns_no_records_and_never_reads_content() -> None:
    retrieval = _retrieval()
    with patch.object(ks, "run_pattern_match_with_llm_grep", _fake_grep), \
         patch.object(ks, "check_pattern_match_eligible", AsyncMock(return_value=True)):
        response = await ks.grep(_request(), ks.GrepQuery(pattern="compute_invoice_total_v2"), retrieval, _graph(False))
    out = json.loads(response.body)
    assert out["records"] == [] and out["totalRecords"] == 0
    retrieval.blob_store.get_record_from_storage.assert_not_called()


async def test_grep_path_globs_filter_after_the_access_check() -> None:
    assert (await _grep(True, paths=["*.py"]))["totalRecords"] == 1
    assert (await _grep(True, paths=["*.md"]))["totalRecords"] == 0


@pytest.mark.parametrize("pattern,use_regex", [
    (r"(a)\1", True), ("(", True), ("[a", True),
    # every spelling of a reference to an earlier group, and recursion
    ("(?P<n>a)(?P=n)", True), (r"(a)\g<1>", True), (r"(?P<n>a)\k<n>", True), ("(a)(?1)", True), ("(a)(?-1)", True),
    ("(?P<n>a)(?&n)", True), ("(a)(?R)", True),
])
def test_grep_refuses_patterns_it_cannot_run_safely(pattern, use_regex) -> None:
    with pytest.raises(HTTPException) as err:
        ks._grep_command(ks.GrepQuery(pattern=pattern, regex=use_regex))
    assert err.value.status_code == 400


@pytest.mark.parametrize("pattern", [
    "List<String>", "x -> y", "a => b", "a && b", "a || b", "a < b", "a > b", "<div>", "&amp;", "a;b", "$(x)", "`x`", "don't",
])
def test_grep_accepts_code_tokens(pattern) -> None:
    assert ks._grep_command(ks.GrepQuery(pattern=pattern))[0].startswith("grep -rci")


@pytest.mark.parametrize("pattern", ["compute_invoice_total_v2", "a.bcd", r"^\d+$", "ab", "café"])
@pytest.mark.parametrize("use_regex", [False, True])
@pytest.mark.parametrize("case_sensitive", [False, True])
def test_grep_command_is_one_the_explicit_command_rules_accept(pattern, use_regex, case_sensitive) -> None:
    from app.utils.pattern_match import (
        _explicit_command_error,
        validate_pattern_match_command,
    )

    command, _ = ks._grep_command(ks.GrepQuery(pattern=pattern, regex=use_regex, caseSensitive=case_sensitive))
    assert _explicit_command_error(command, readable_only=True) is None  # includes the listing-command checks
    assert _explicit_command_error(command, readable_only=False) is None
    # in the strict shape, so a scan is never put down to an unshaped command (the route passes no word index, and a test below asserts it)
    assert validate_pattern_match_command(command)[0]


def test_grep_command_is_a_fixed_string_of_plain_characters() -> None:
    assert ks._grep_command(ks.GrepQuery(pattern="a.bcd"))[0] == "grep -rciE -e 'bcd|\"isCompressed\": true' ."
    assert ks._grep_command(ks.GrepQuery(pattern="a.bcd", regex=True, caseSensitive=True))[0] == "grep -rcE -e 'bcd|\"isCompressed\": true' ."
    assert ks._grep_command(ks.GrepQuery(pattern=r"(?i)vxy\d+", regex=True, caseSensitive=True))[0] == "grep -rciE -e 'vxy|\"isCompressed\": true' ."
    assert ks._grep_command(ks.GrepQuery(pattern='say "hi"'))[0] == "grep -rciE -e 'ay |\"isCompressed\": true' ."
    assert ks._grep_command(ks.GrepQuery(pattern=r"^\d+$", regex=True))[0] == "grep -rciF -e '' ."
    assert ks._grep_command(ks.GrepQuery(pattern="ab"))[0] == "grep -rciF -e '' ."  # too short to be worth it


# Python matches these decoded lines; the record file holds them as JSON.
_SUPERSET = [
    ("compute_invoice_total_v2", False, "x = compute_invoice_total_v2(a)"),
    (r"\d{4}-\d{2}", True, "date 2026-10 ok"),
    ("café", False, "un café noir"),
    ("CAFÉ", False, "un café noir"),
    ('say "hi"', False, 'print(say "hi")'),
    (r'say "hi"', True, 'print(say "hi")'),
    (r"C:\Users", False, r"path C:\Users\me"),
    (r"C:\\Users", True, r"path C:\Users\me"),
    ("^def ", True, "def main():"),
    ("main\\(\\)$", True, "def main()"),
    (r"\bfoo", True, "first line\nfoo starts the second"),
    ("a\tb", False, "a\tb"),
    (r"a\tb", True, "a\tb"),
    (r"caf\w", True, "café"),
    ("é{2}", True, "éé"),
    ("List<String>", False, "List<String> xs"),
    (r"x\s*->\s*y", True, "x  ->  y"),
    (r"(?:foo|bar)baz", True, "xbarbaz"),
    (r"ab+c", True, "abbbc"),
    (r"ab*c", True, "ac"),
    (r"a{2}b", True, "aab"),
    (r"(?<=x)yz", True, "xyz"),
    (r"[\w.]+@acme\.com", True, "me.me@acme.com"),
    ("a|b", True, "only b here"),
]


def _store(root, text: str) -> None:
    import uuid

    from app.modules.transformers.blob_storage import (
        _encode_record_json,
        _plain_envelope_bytes,
    )

    vrid = uuid.uuid4().hex[:24]
    record = {"block_containers": {"blocks": [{"index": 0, "data": text}]}}
    folder = root / "grp" / "Doc" / vrid
    folder.mkdir(parents=True)
    (folder / f"record_{vrid}.json").write_bytes(_plain_envelope_bytes(record, _encode_record_json(record), vrid))


@pytest.mark.parametrize("pattern,use_regex,text", _SUPERSET)
def test_grep_command_selects_every_record_the_line_matcher_matches(tmp_path, pattern, use_regex, text) -> None:
    """Real grep over a file written by the real blob writer, not over plain text."""
    import shlex
    import subprocess

    command, compiled = ks._grep_command(ks.GrepQuery(pattern=pattern, regex=use_regex))
    assert any(compiled.search(line) for line in text.splitlines()), "the probe line must match in Python"
    _store(tmp_path, text)
    out = subprocess.run(shlex.split(command), cwd=tmp_path, capture_output=True, text=True, check=False)
    assert any(not line.endswith(":0") for line in out.stdout.splitlines()), (command, out.stdout)


def test_grep_ignores_case_of_non_ascii_text_whatever_the_host_locale(tmp_path, monkeypatch) -> None:
    from app.agents.actions.storage_search.storage_search import _run_subprocess

    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("LANG", "C")
    _store(tmp_path, "un CAFÉ noir")
    command, _ = ks._grep_command(ks.GrepQuery(pattern="café"))
    ok, out = asyncio.run(_run_subprocess(command, cwd=str(tmp_path)))
    assert ok and ":1" in out


# ---- entities ---------------------------------------------------------------
def _context() -> EntityAccessContext:
    return EntityAccessContext(
        org_id=ORG, user_key="user-key", app_level_app_ids=frozenset({"c1"}),
        record_level_app_ids=frozenset(), record_group_ids=frozenset(), app_names={"c1": "Drive"},
    )


def _patch_entity_state() -> object:
    return patch.object(ks, "_entity_state", AsyncMock(return_value={
        "org_id": ORG, "user_id": USER, "config_service": MagicMock(), "filters": {},
    }))


async def test_neighbors_lists_only_accessible_records_and_their_shared_entities() -> None:
    graph = MagicMock()
    graph.get_record_taxonomy_links = AsyncMock(return_value=[
        {"recordId": RID, "collection": "topics", "entityId": "t1", "name": "Orders"},
        {"recordId": RID, "collection": "topics", "entityId": "self", "name": "Self"},
    ])
    page = EntityRecordPage(records=[{"_key": RID, "recordName": "Doc", "recordType": "FILE", "connectorId": "c1"}], next_cursor=None)
    with _patch_entity_state(), patch.object(ks, "_named_entities_enabled", AsyncMock(return_value=False)), \
         patch.object(ks, "load_entity_access_context", AsyncMock(return_value=_context())), \
         patch.object(ks, "list_accessible_entity_records", AsyncMock(return_value=page)), \
         patch.object(ks, "resolve_frontend_url", AsyncMock(return_value=None)):
        response = await ks.entity_neighbors(
            _request(), ks.EntityNeighborsQuery(entityId="self", entityType="topic"), graph,
        )
    out = json.loads(response.body)
    assert [r["recordId"] for r in out["records"]] == [RID] and out["records"][0]["app"] == "Drive"
    assert out["relatedEntities"] == [{
        "entityId": "t1", "entityType": "topic", "name": "Orders", "relation": "belongsToTopic", "sharedRecords": 1,
    }]
    assert out["relationTypes"] == ["belongsToTopic"]


async def test_neighbors_without_readable_records_is_empty_and_reads_no_labels() -> None:
    graph = MagicMock()
    graph.get_record_taxonomy_links = AsyncMock()
    with _patch_entity_state(), patch.object(ks, "_named_entities_enabled", AsyncMock(return_value=True)), \
         patch.object(ks, "load_entity_access_context", AsyncMock(return_value=_context())), \
         patch.object(ks, "list_accessible_entity_records", AsyncMock(return_value=EntityRecordPage([], None))), \
         patch.object(ks, "resolve_frontend_url", AsyncMock(return_value=None)):
        response = await ks.entity_neighbors(
            _request(), ks.EntityNeighborsQuery(entityId="secret", entityType="named_entity"), graph,
        )
    out = json.loads(response.body)
    assert out["records"] == [] and out["relatedEntities"] == []
    graph.get_record_taxonomy_links.assert_not_called()


async def test_search_entities_returns_nothing_when_the_access_check_keeps_nothing() -> None:
    state = {"org_id": ORG, "user_id": USER, "config_service": MagicMock(), "filters": {},
             "entity_vector_store": MagicMock(), "graph_provider": MagicMock()}
    with patch.object(ks, "_entity_state", AsyncMock(return_value=state)), \
         patch("app.agents.actions.knowledge_graph.ops.entity_discovery.load_entity_access_context", AsyncMock(return_value=_context())), \
         patch("app.agents.actions.knowledge_graph.ops.entity_discovery.search_entities_for_user", AsyncMock(return_value=[])), \
         patch("app.agents.actions.knowledge_graph.ops.entity_discovery._named_entities_enabled", AsyncMock(return_value=False)):
        response = await ks.search_entities(_request(), ks.EntitySearchQuery(query="payments"), MagicMock())
    assert json.loads(response.body)["results"] == []


async def test_search_entities_without_a_store_is_a_503_that_says_so() -> None:
    state = {"org_id": ORG, "user_id": USER, "config_service": MagicMock(), "filters": {}, "graph_provider": MagicMock()}
    with patch.object(ks, "_entity_state", AsyncMock(return_value=state)):
        with pytest.raises(HTTPException) as err:
            await ks.search_entities(_request(), ks.EntitySearchQuery(query="payments"), MagicMock())
    assert err.value.status_code == 503



def test_the_bodies_have_no_place_for_an_organisation_or_a_user() -> None:
    for model in (ks.EntitySearchQuery, ks.EntityNeighborsQuery):
        assert not {"orgId", "userId", "org_id", "user_id"} & set(model.model_fields)
    # a client that sends them anyway is not obeyed: they are dropped, not read
    body = ks.EntityNeighborsQuery.model_validate(
        {"entityId": "e", "entityType": "topic", "orgId": "someone-else", "userId": "someone-else"},
    )
    assert not hasattr(body, "orgId") and not hasattr(body, "userId")


async def test_no_organisation_context_is_a_401_and_reads_nothing_whatever_the_request_claims(no_org_context) -> None:
    graph = MagicMock()
    # the claims on the request are not read for the org: without a bound context there is no caller
    request = _request({"orgId": "someone-else", "userId": USER})
    with pytest.raises(HTTPException) as err:
        await ks.search_entities(request, ks.EntitySearchQuery(query="payments"), graph)
    assert err.value.status_code == 401
    with pytest.raises(HTTPException) as err:
        await ks.entity_neighbors(request, ks.EntityNeighborsQuery(entityId="e", entityType="topic"), graph)
    assert err.value.status_code == 401
    assert graph.method_calls == []


async def test_a_bound_context_without_a_user_is_a_401() -> None:
    with bind(OrgContext(ORG, None, "user", "jwt", "req-2")):
        with pytest.raises(HTTPException) as err:
            await ks.search_entities(_request({}), ks.EntitySearchQuery(query="payments"), MagicMock())
    assert err.value.status_code == 401


async def test_the_organisation_is_the_bound_one_not_the_one_in_the_claims() -> None:
    assert ks._user(_request({"orgId": "someone-else", "userId": "ignored"})) == (ORG, USER)


async def test_the_access_check_is_built_for_the_session_user() -> None:
    graph = MagicMock()
    seen = {}

    async def context(state: dict) -> EntityAccessContext:
        seen.update(org=state["org_id"], user=state["user_id"])
        return _context()

    state = {"org_id": ORG, "user_id": USER, "config_service": MagicMock(), "filters": {}}
    with patch.object(ks, "_entity_state", AsyncMock(return_value=state)),          patch.object(ks, "_named_entities_enabled", AsyncMock(return_value=False)),          patch.object(ks, "load_entity_access_context", context),          patch.object(ks, "list_accessible_entity_records", AsyncMock(return_value=EntityRecordPage([], None))),          patch.object(ks, "resolve_frontend_url", AsyncMock(return_value=None)):
        await ks.entity_neighbors(_request(), ks.EntityNeighborsQuery(entityId="e", entityType="topic"), graph)
    assert seen == {"org": ORG, "user": USER}


async def test_an_entity_type_the_search_does_not_cover_is_a_400() -> None:
    state = {"org_id": ORG, "user_id": USER, "config_service": MagicMock(), "filters": {}}
    with patch.object(ks, "_entity_state", AsyncMock(return_value=state)),          patch.object(ks, "_named_entities_enabled", AsyncMock(return_value=True)),          patch.object(ks, "SEARCHABLE_ENTITY_TYPES", frozenset({"topic"})):
        with pytest.raises(HTTPException) as err:
            await ks.entity_neighbors(_request(), ks.EntityNeighborsQuery(entityId="e", entityType="department"), MagicMock())
    assert err.value.status_code == 400


@pytest.mark.parametrize("provider", ["neo4j", "arango"])
def test_both_graph_providers_answer_the_calls_the_routes_make(provider) -> None:
    """The routes call only these two provider methods besides the access check."""
    from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
    from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

    cls = Neo4jProvider if provider == "neo4j" else ArangoHTTPProvider
    for name in ("get_record_taxonomy_links", "get_named_entities_for_record"):
        method = getattr(cls, name)
        assert callable(method) and not getattr(method, "__isabstractmethod__", False), (provider, name)
    assert not getattr(cls, "__abstractmethods__", frozenset())


_SPEC = Path(__file__).resolve().parents[4].parent / "nodejs" / "apps" / "src" / "modules" / "api-docs" / "pipeshub-openapi.yaml"


def _validate(schema_name: str, payload: dict) -> None:
    """Check a payload against the published schema (resolved against the spec's own components)."""
    components = yaml.safe_load(_SPEC.read_text(encoding="utf-8"))["components"]
    jsonschema.validate(payload, {"$ref": f"#/components/schemas/{schema_name}", "components": components})


def _neighbors_graph(record: dict, **overrides) -> tuple[MagicMock, EntityRecordPage]:
    graph = MagicMock()
    graph.get_record_taxonomy_links = AsyncMock(return_value=[
        {"recordId": RID, "collection": "topics", "entityId": "t1", "name": "Orders"},
    ])
    graph.get_named_entities_for_record = AsyncMock(return_value=[])
    for name, value in overrides.items():
        setattr(graph, name, value)
    return graph, EntityRecordPage(records=[record], next_cursor="c1")


async def _neighbors(graph: MagicMock, page: EntityRecordPage, *, named: bool = False) -> JSONResponse:
    with _patch_entity_state(), patch.object(ks, "_named_entities_enabled", AsyncMock(return_value=named)), \
         patch.object(ks, "load_entity_access_context", AsyncMock(return_value=_context())), \
         patch.object(ks, "list_accessible_entity_records", AsyncMock(return_value=page)), \
         patch.object(ks, "resolve_frontend_url", AsyncMock(return_value=None)):
        return await ks.entity_neighbors(
            _request(), ks.EntityNeighborsQuery(entityId="self", entityType="topic"), graph,
        )


async def test_the_neighbors_answer_matches_the_published_schema_and_dates_a_record_like_the_search_does() -> None:
    record = {"_key": RID, "recordName": "Doc", "recordType": "FILE", "connectorId": "c1",
              "sourceLastModifiedTimestamp": 1_777_500_000_000, "webUrl": "https://x/y"}
    graph, page = _neighbors_graph(record)
    out = json.loads((await _neighbors(graph, page)).body)
    _validate("EntityNeighborsResponse", out)
    row = out["records"][0]
    dates = {k: v for k, v in row.items() if k in {"modified", "dated", "fileModified", "uploaded"}}
    assert dates == {"modified": "2026-04-29"}
    # the same row, previewed by the search, carries the same key and text
    from app.agents.actions.knowledge_graph.ops.entity_discovery import _preview_row
    preview = _preview_row(record, _context(), None)
    assert {k: v for k, v in preview.items() if k in dates} == dates


async def test_the_search_answer_matches_the_published_schema_with_its_notes() -> None:
    payload = {
        "status": "success", "results": [], "ignoredEntityTypes": ["galaxy"], "kindNotes": ["x is not searched"],
        "scopeNote": "limited",
    }
    _validate("EntitySearchResponse", payload)
    with pytest.raises(jsonschema.ValidationError):
        _validate("EntitySearchResponse", {"results": [{"entityId": 1}]})


@pytest.mark.parametrize("failing", ["get_record_taxonomy_links", "get_named_entities_for_record"])
async def test_a_graph_database_error_while_labelling_is_the_documented_503_not_a_500(failing) -> None:
    graph, page = _neighbors_graph({"_key": RID, "recordName": "Doc", "connectorId": "c1"})
    setattr(graph, failing, AsyncMock(side_effect=ConnectionError("graph database is not connected")))
    with pytest.raises(HTTPException) as err:
        await _neighbors(graph, page, named=True)
    assert err.value.status_code == 503 and "try again" in err.value.detail
    assert "not connected" not in err.value.detail


@pytest.mark.parametrize(("error", "status"), [
    ({"status": "error", "message": "Entity search timed out", "retryable": True}, 503),
    ({"status": "error", "message": "Entity search is not available", "retryable": True}, 503),
    ({"status": "error", "message": "Unsupported entity_types ['x']; failed to match"}, 400),
    ({"status": "error", "message": "Named-entity search is not available on this plan"}, 400),
])
async def test_the_status_follows_what_the_search_reports_not_the_wording_of_its_message(error, status) -> None:
    state = {"org_id": ORG, "user_id": USER, "config_service": MagicMock(), "filters": {}}
    with patch.object(ks, "_entity_state", AsyncMock(return_value=state)), \
         patch.object(ks, "execute_search_entities", AsyncMock(return_value=(False, json.dumps(error)))):
        with pytest.raises(HTTPException) as err:
            await ks.search_entities(_request(), ks.EntitySearchQuery(query="payments"), MagicMock())
    assert err.value.status_code == status


async def test_the_search_marks_the_failures_that_are_retryable() -> None:
    from app.agents.actions.knowledge_graph.ops import entity_discovery as ed

    ok, text = await ed.execute_search_entities({"graph_provider": None}, "x")
    assert not ok and json.loads(text)["retryable"] is True
    ok, text = await ed.execute_search_entities(
        {"graph_provider": MagicMock(), "entity_vector_store": MagicMock(), "config_service": MagicMock(), "filters": {}},
        "x", ["galaxy"],
    )
    assert not ok and "retryable" not in json.loads(text)


# ---- no existence oracle ------------------------------------------------------
def _two_user_graph(readable: set[str]) -> MagicMock:
    graph = _graph(True)
    graph.filter_accessible_virtual_record_ids = AsyncMock(
        side_effect=lambda vrids, *a, **k: {v: f"rec-{v}" for v in vrids if v in readable},
    )
    graph.get_records_by_record_ids = AsyncMock(side_effect=lambda record_ids, org_id: [
        {"_key": r, "recordName": f"{r}.txt", "recordType": "FILE", "connectorName": "DRIVE", "connectorId": "c1",
         "virtualRecordId": r[4:], "origin": "CONNECTOR", "indexingStatus": "COMPLETED", "version": 0}
        for r in record_ids
    ])
    return graph


async def _grep_raw(raw_vrids: list[str], graph: MagicMock, **body) -> bytes:
    async def fake(**kwargs: dict) -> list[dict]:
        for v in raw_vrids:
            kwargs["found"][v] = {"virtual_record_id": v, "match_count": "1", "match_preview": "m"}
        return list(kwargs["found"].values())

    body.setdefault("pattern", "marker")
    with patch.object(ks, "run_pattern_match_with_llm_grep", fake), \
         patch.object(ks, "check_pattern_match_eligible", AsyncMock(return_value=True)), \
         patch.object(ks, "resolve_frontend_url", AsyncMock(return_value=None)):
        return (await ks.grep(_request(), ks.GrepQuery(**body), _retrieval(), graph)).body


@pytest.mark.parametrize("limit", [1, 2, 5])
async def test_grep_response_is_the_same_whether_or_not_an_unreadable_record_matches(limit) -> None:
    graph = _two_user_graph({"v-marker"})  # the caller reads the marker record, not the secret one
    without_secret = await _grep_raw(["v-marker"], graph, limit=limit)
    with_secret = await _grep_raw(["v-secret", "v-marker"], graph, limit=limit)
    many_secrets = await _grep_raw(["v-secret", "v-s2", "v-s3", "v-marker"], graph, limit=limit)
    assert without_secret == with_secret == many_secrets
    assert json.loads(with_secret)["coverage"] == {"complete": True}


async def test_grep_truncation_note_counts_only_readable_records() -> None:
    graph = _two_user_graph({"v-a", "v-b"})
    out = json.loads(await _grep_raw(["v-secret", "v-a", "v-b"], graph, limit=1))
    assert out["totalRecords"] == 1 and out["coverage"]["complete"] is False
    assert "more records matched" in out["coverage"]["note"]


async def test_grep_limit_applies_after_the_access_check() -> None:
    graph = _two_user_graph({"v-last"})
    out = json.loads(await _grep_raw([f"v-{i}" for i in range(30)] + ["v-last"], graph, limit=1))
    assert [r["recordId"] for r in out["records"]] == ["rec-v-last"]


async def test_grep_drops_apps_the_caller_cannot_reach_and_searches_nothing_if_none_remain() -> None:
    graph = _two_user_graph({"v-marker"})
    graph.get_org_apps = AsyncMock(return_value=[{"_key": "mine"}, {"_key": "theirs"}])
    graph.get_accessible_containers = AsyncMock(return_value=AccessibleContainers(app_ids=frozenset({"mine"})))
    seen: list[dict] = []

    async def fake(**kwargs: dict) -> list[dict]:
        seen.append(kwargs["filters"])
        return []

    with patch.object(ks, "run_pattern_match_with_llm_grep", fake), \
         patch.object(ks, "check_pattern_match_eligible", AsyncMock(return_value=True)), \
         patch.object(ks, "resolve_frontend_url", AsyncMock(return_value=None)):
        await ks.grep(_request(), ks.GrepQuery(pattern="x", filters={"apps": ["mine", "theirs"]}), _retrieval(), graph)
        none = await ks.grep(_request(), ks.GrepQuery(pattern="x", filters={"apps": ["theirs"]}), _retrieval(), graph)
        empty = await ks.grep(_request(), ks.GrepQuery(pattern="x", filters={"apps": ["mine"]}), _retrieval(), graph)
    assert seen[0]["apps"] == ["mine"] and len(seen) == 2  # the unreachable-only request never ran a grep
    assert none.body == empty.body  # same answer as a reachable app with no match


# ---- one bounded request ------------------------------------------------------
async def test_grep_with_a_catastrophic_regex_returns_within_the_budget_and_says_it_is_incomplete() -> None:
    import time

    line = ",".join(f"v{j}" for j in range(60))
    retrieval = _retrieval()
    retrieval.blob_store.get_record_from_storage = AsyncMock(return_value={"block_containers": {"blocks": [
        {"index": 0, "data": "\n".join([line] * 400)},
    ]}})
    ticks: list[float] = []

    async def ticker() -> None:
        import asyncio

        while True:
            ticks.append(time.monotonic())
            await asyncio.sleep(0.01)

    import asyncio

    tick = asyncio.ensure_future(ticker())
    start = time.monotonic()
    with patch.object(ks, "run_pattern_match_with_llm_grep", _fake_grep), \
         patch.object(ks, "check_pattern_match_eligible", AsyncMock(return_value=True)), \
         patch.object(ks, "resolve_frontend_url", AsyncMock(return_value=None)):
        response = await ks.grep(
            _request(), ks.GrepQuery(pattern=r"(?:.*?,){40}x|v0_0", regex=True, timeBudgetMs=1000), retrieval, _graph(True),
        )
    tick.cancel()
    assert time.monotonic() - start < 1.5
    assert max(b - a for a, b in zip(ticks, ticks[1:])) < 0.15  # other requests kept being served
    out = json.loads(response.body)
    assert out["coverage"]["complete"] is False and "time limit" in out["coverage"]["note"]


def test_grep_command_also_selects_records_stored_compressed(tmp_path) -> None:
    """Over the compression threshold the writer stores base64 zstd, which no prefilter can see."""
    import shlex
    import subprocess

    from app.modules.transformers.blob_storage import _json_utf8_bytes

    folder = tmp_path / "grp" / "Doc" / "abc"
    folder.mkdir(parents=True)
    (folder / "record_abc.json").write_bytes(
        _json_utf8_bytes({"isCompressed": True, "record": "KLUv/QBYxxxx", "virtualRecordId": "abc"}),
    )
    command, _ = ks._grep_command(ks.GrepQuery(pattern="needle in a haystack"))
    out = subprocess.run(shlex.split(command), cwd=tmp_path, capture_output=True, text=True, check=False)
    assert out.stdout.strip().endswith(":1"), out.stdout



# ---- bounds and identity of the grep route -----------------------------------
@pytest.mark.parametrize("body", [
    {"pattern": "x" * 501},
    {"pattern": "a\nb"},
    {"pattern": "a\x00b"},
    {"pattern": ""},
    {"pattern": "x", "limit": 101},
    {"pattern": "x", "timeBudgetMs": 31000},
    {"pattern": "x", "paths": ["a"] * 21},
])
def test_grep_bounds_the_pattern_and_the_size_of_the_work_and_the_answer(body) -> None:
    with pytest.raises(ValueError):  # pydantic's ValidationError is a ValueError
        ks.GrepQuery(**body)


def test_the_grep_body_has_no_place_for_an_organisation_or_a_user() -> None:
    assert not {"orgId", "userId", "org_id", "user_id"} & set(ks.GrepQuery.model_fields)


async def test_grep_with_no_organisation_context_is_a_401_and_reads_nothing(no_org_context) -> None:
    graph = _graph(True)
    with pytest.raises(HTTPException) as err:
        await ks.grep(_request({}), ks.GrepQuery(pattern="x"), _retrieval(), graph)
    assert err.value.status_code == 401
    assert graph.method_calls == []


async def test_grep_greps_the_readable_view_only_and_for_the_session_user() -> None:
    seen: dict = {}

    async def fake(**kwargs: dict) -> list[dict]:
        seen.update(kwargs)
        return []

    with patch.object(ks, "run_pattern_match_with_llm_grep", fake), \
         patch.object(ks, "check_pattern_match_eligible", AsyncMock(return_value=True)), \
         patch.object(ks, "resolve_frontend_url", AsyncMock(return_value=None)):
        await ks.grep(_request(), ks.GrepQuery(pattern="x"), _retrieval(), _graph(True))
    assert seen["readable_only"] is True and seen["max_records_per_connector"] == ks.MAX_RECORDS_PER_APP
    assert (seen["org_id"], seen["user_id"]) == (ORG, USER) and seen["retrieval_service"] is None


async def test_grep_never_returns_a_line_from_a_record_the_caller_cannot_read() -> None:
    """The storage grep matched a record (raw hit); the access check refuses it, so no line and no count leave."""
    out = await _grep(accessible=False)
    assert out["records"] == [] and out["totalRecords"] == 0
    assert RID not in json.dumps(out) and "return x" not in json.dumps(out)  # no id, no line of text


# ---- patterns that are expensive to compile -----------------------------------
@pytest.mark.parametrize("pattern", [
    "(?:(?:(?:(?:a{99}){99}){99}){99})",    # 33 characters: 23 s and 27 GB to compile
    "(?x)(?:(?:(?:(?:a{99}) {99}) {99}) {99})",   # verbose mode ignores the spaces that hide the nesting
    "(?x)(?:(?:(?:(?:a{99})\t{99})\t{99})\t{99})",
    "(?x:(?:(?:(?:a{99}) {99}) {99}) {99})",
    "(?ix)(?:(?:(?:(?:a{99}) {99}) {99}) {99})",
    "(?V1x)(?:(?:(?:(?:a{99}) {99}) {99}) {99})",
    "(?x)(?#c)a", "(?<n>a)", "(?-x)a", "(?s:a)", "(?(1)a|b)", "(?>a)", "(?|a|b)",
    "((((a{40}){40}){40}){40})",
    "(?:a{2}){3}",                           # a counted repeat inside a counted repeat
    "(a{1,2}b){2,3}",
    "a{101}", "a{1,101}", "a{101,}", "(?:ab){999}", "x{0,100000}",
])
def test_grep_refuses_costly_repeats_before_compiling_and_fast(pattern) -> None:
    import time

    started = time.monotonic()
    with pytest.raises(HTTPException) as err:
        ks._grep_command(ks.GrepQuery(pattern=pattern, regex=True))
    assert err.value.status_code == 400
    assert time.monotonic() - started < 0.5


@pytest.mark.parametrize("pattern", [
    "a{100}", "a{2,100}", "a{3,}", "(?:ab|cd){50}", "(a|b)+c{5}d{5}", r"\\1", r"[{999}]", r"a\{999\}", r"\p{L}{5}",
    "x{,5}", "a{abc}", "(a{2})(b{2})", "(?P<x>a{2})", "(?:x)", r"\(?x", "(?i)abc", "(?i:abc){5}", "(?=a)b", "(?<!a)b", "(?!a)b", "(?<=a)b",
])
def test_grep_still_accepts_bounded_repeats_and_things_that_only_look_like_references(pattern) -> None:
    command, compiled = ks._grep_command(ks.GrepQuery(pattern=pattern, regex=True))
    assert command.startswith("grep ") and compiled is not None


def test_grep_does_not_scan_a_literal_pattern_for_repeats() -> None:
    command, compiled = ks._grep_command(ks.GrepQuery(pattern="a{9999}", regex=False))
    assert compiled.search("a{9999}")


async def test_the_answer_is_cut_at_its_size_limit_and_says_so() -> None:
    vrids = [f"v{i}" for i in range(10)]
    wide = [{"line": n, "text": "x" * 400, "before": [], "after": [], "blockIndex": 0} for n in range(50)]

    async def matcher(*args: object, **kwargs: object) -> tuple[list[dict], int, None, bool]:
        return [dict(m) for m in wide], 50, None, True

    with patch.object(ks, "MAX_RESPONSE_CHARS", 30_000), patch.object(ks, "find_matches_bounded", matcher):
        out = json.loads(await _grep_raw(vrids, _two_user_graph(set(vrids))))
    assert 1 <= len(out["records"]) < 10
    assert out["coverage"]["complete"] is False and "size limit" in out["coverage"]["note"]


async def test_a_record_with_too_much_text_is_not_searched_and_the_answer_says_so() -> None:
    retrieval = _retrieval()
    retrieval.blob_store.get_record_from_storage = AsyncMock(return_value={"block_containers": {"blocks": [
        {"index": 0, "data": "x" * 200},
    ]}})
    with patch.object(ks, "MAX_RECORD_CHARS", 100):
        out = await _grep(True, retrieval=retrieval)
    assert out["records"] == [] and out["coverage"]["complete"] is False
    assert "too large" in out["coverage"]["note"]


def test_the_scan_is_linear_in_the_length_of_the_pattern() -> None:
    import time

    started = time.monotonic()
    with pytest.raises(HTTPException) as err:  # 500 opening parentheses: refused by the scan, never reaching the compiler
        ks._grep_command(ks.GrepQuery(pattern="(" * 500, regex=True))
    assert err.value.status_code == 400 and "deeper" in err.value.detail
    assert time.monotonic() - started < 0.5
