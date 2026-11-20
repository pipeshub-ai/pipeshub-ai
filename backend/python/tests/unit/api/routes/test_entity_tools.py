"""Entity tool routes (app.api.routes.entity_tools), the MCP surface of the agent's entity tools.

What is checked: each route is served only where the deployment serves the
agent's tool (entity store, named-entity extraction, ``ENTITY_SEARCH_TOOL``),
the capabilities a client lists from agree with those gates, the caller and
the connected app's limit come from the session, a failure never reads as an
empty answer, and listings carry no Citation IDs.
"""
from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

import app.edition_config  # noqa: F401  (the route modules load cleanly only after this)
from app.agents.actions.knowledge_graph.listing_citations import (
    LISTING_CITATIONS_OFF_KEY,
    cite_listed_records,
)
from app.agents.actions.knowledge_graph.ops.entity_records import LOOKUP_FAILED_MSG
from app.agents.agent_loop.entity_search_tool import (
    ENTITY_SEARCH_OFFERED_STATE_KEY,
    NAMED_ENTITIES_ENABLED_STATE_KEY,
)
from app.api.routes import entity_tools as et
from app.api.routes import knowledge_search as ks
from app.core.org_context import OrgContext, bind

ORG, USER, EMAIL = "org-1", "user-1", "ada@example.com"


@pytest.fixture(autouse=True)
def _signed_in(request: pytest.FixtureRequest) -> Iterator[None]:
    if "no_org_context" in request.fixturenames:
        yield
        return
    with bind(OrgContext(ORG, USER, "user", "jwt", "req-1")):
        yield


@pytest.fixture
def no_org_context() -> None:
    """Marks a test that runs with no org context bound."""


def _request(user: dict | None = None) -> MagicMock:
    request = MagicMock()
    request.state.user = {"orgId": ORG, "userId": USER, "email": EMAIL} if user is None else user
    container = request.app.container
    container.logger.return_value = MagicMock()
    container.config_service.return_value = MagicMock()
    return request


def _state(*, store: bool = True, named: bool = True, offered: bool = True) -> dict:
    return {
        "org_id": ORG, "user_id": USER, "user_email": EMAIL,
        "graph_provider": MagicMock(), "config_service": MagicMock(),
        "entity_vector_store": MagicMock() if store else None,
        "filters": {}, "query": "",
        NAMED_ENTITIES_ENABLED_STATE_KEY: named,
        ENTITY_SEARCH_OFFERED_STATE_KEY: offered,
        LISTING_CITATIONS_OFF_KEY: True,
    }


def _with_state(state: dict) -> object:
    return patch.object(et, "_entity_state", AsyncMock(return_value=state))


def _real_state_inputs(*, store: bool, named: bool, switch: bool) -> object:
    """The three things a deployment decides, with ``_entity_state`` itself left real."""
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(patch(
        "app.api.routes.chatbot.load_entity_vector_store", AsyncMock(return_value=MagicMock() if store else None),
    ))
    stack.enter_context(patch.object(ks, "is_named_entity_extraction_enabled", AsyncMock(return_value=named)))
    stack.enter_context(patch.dict("os.environ", {"ENTITY_SEARCH_TOOL": "true" if switch else "false"}))
    return stack


# ---- capabilities -------------------------------------------------------------
@pytest.mark.parametrize(("store", "named", "switch", "expected"), [
    # Nothing indexed into the entity store, extraction off: only a person's records.
    (False, False, False, {"entitySearch": False, "entityRecords": False, "namedEntities": False, "coMentions": False,
                               "recordsByValue": False, "recordsByEntities": False, "recordsByPerson": True}),
    # The store, extraction off: the taxonomy/record-group/person entity tools.
    (True, False, False, {"entitySearch": True, "entityRecords": True, "namedEntities": False, "coMentions": False,
                              "recordsByValue": False, "recordsByEntities": False, "recordsByPerson": True}),
    # Extraction on, switch off: everything but searching inside the records naming them.
    (True, True, False, {"entitySearch": True, "entityRecords": True, "namedEntities": True, "coMentions": True,
                             "recordsByValue": True, "recordsByEntities": False, "recordsByPerson": True}),
    # Everything on.
    (True, True, True, {"entitySearch": True, "entityRecords": True, "namedEntities": True, "coMentions": True,
                            "recordsByValue": True, "recordsByEntities": True, "recordsByPerson": True}),
    # The switch alone serves nothing extra: the agent needs the store and extraction too.
    (False, False, True, {"entitySearch": False, "entityRecords": False, "namedEntities": False, "coMentions": False,
                              "recordsByValue": False, "recordsByEntities": False, "recordsByPerson": True}),
    (True, False, True, {"entitySearch": True, "entityRecords": True, "namedEntities": False, "coMentions": False,
                             "recordsByValue": False, "recordsByEntities": False, "recordsByPerson": True}),
    # Extraction without the store: values and co-mentions read the graph only; the search cannot run.
    (False, True, True, {"entitySearch": False, "entityRecords": False, "namedEntities": True, "coMentions": True,
                             "recordsByValue": True, "recordsByEntities": False, "recordsByPerson": True}),
])
async def test_capabilities_are_the_gates_the_agent_applies(store, named, switch, expected) -> None:
    with _real_state_inputs(store=store, named=named, switch=switch):
        caps = await et.entity_capabilities(_request(), MagicMock())
    assert caps.model_dump() == expected


async def test_search_by_entities_is_offered_exactly_where_the_agent_offers_it() -> None:
    """The route reads the agent's own decision, not a copy of its rule."""
    from app.agents.agent_loop.entity_search_tool import entity_search_tool_offered

    for store in (False, True):
        for named in (False, True):
            for switch in (False, True):
                with _real_state_inputs(store=store, named=named, switch=switch):
                    state = await ks._entity_state(_request(), MagicMock())
                    caps = await et.entity_capabilities(_request(), MagicMock())
                    assert caps.recordsByEntities is entity_search_tool_offered(state), (store, named, switch)


async def test_the_state_is_the_session_users_with_citations_off() -> None:
    with _real_state_inputs(store=True, named=False, switch=False):
        state = await ks._entity_state(_request(), MagicMock())
    assert (state["org_id"], state["user_id"], state["user_email"]) == (ORG, USER, EMAIL)
    assert state[LISTING_CITATIONS_OFF_KEY] is True
    assert state["filters"] == {}


async def test_a_connected_apps_limit_bounds_every_entity_route() -> None:
    """An app limited to one connector must not reach entities in others: the
    state carries the limit as a strict bound, as a chat turn's does."""
    user = {"orgId": ORG, "userId": USER, "knowledgeLimit": {"apps": ["app-a"]}}
    with _real_state_inputs(store=True, named=True, switch=False):
        state = await ks._entity_state(_request(user), MagicMock())
    assert state["filters"]["strictScope"] is True
    assert state["filters"]["apps"] == ["app-a"]
    assert state["filters"]["connectionApps"] == ["app-a"]


async def test_a_limit_too_large_to_resolve_is_refused_not_widened() -> None:
    from app.modules.retrieval.selection_scope import SelectionTooLargeError

    with _real_state_inputs(store=True, named=True, switch=False), \
         patch.object(ks, "prepare_turn_scope", AsyncMock(side_effect=SelectionTooLargeError())), \
         pytest.raises(HTTPException) as exc:
        await ks._entity_state(_request(), MagicMock())
    assert exc.value.status_code == 400


async def test_no_organisation_context_is_a_401_and_reads_nothing(no_org_context) -> None:
    graph = MagicMock()
    with pytest.raises(HTTPException) as exc:
        await et.entity_capabilities(_request(), graph)
    assert exc.value.status_code == 401
    assert not graph.mock_calls


def test_the_bodies_have_no_place_for_an_organisation_or_a_user() -> None:
    for model in (et.EntityRecordsQuery, et.CoMentionedQuery, et.PersonRecordsQuery,
                  et.ValueRecordsQuery, et.EntityRecordsSearchQuery):
        fields = {name.lower() for name in model.model_fields}
        assert not fields & {"orgid", "userid", "org_id", "user_id", "email"}, model


# ---- gates on every route --------------------------------------------------------
_GATED = [
    ("entity_records", et.EntityRecordsQuery(entityId="e1", entityType="topic"), {"store": False}),
    ("co_mentioned_entities", et.CoMentionedQuery(entityIds=["e1"]), {"named": False}),
    ("records_by_value", et.ValueRecordsQuery(amountMin=50_000), {"named": False}),
    ("records_by_entities", et.EntityRecordsSearchQuery(mentions=[et.Mention(name="Acme")]), {"offered": False}),
]


@pytest.mark.parametrize(("route", "body", "off"), _GATED, ids=[g[0] for g in _GATED])
async def test_a_route_its_deployment_does_not_serve_is_a_404_and_runs_nothing(route, body, off) -> None:
    ops = {name: AsyncMock(return_value=(True, "x")) for name in (
        "execute_find_records_by_entity", "execute_find_co_mentioned_entities", "execute_find_records_by_value",
    )}
    graph = MagicMock()
    args = (_request(), body, graph) + ((MagicMock(),) if route == "records_by_entities" else ())
    with _with_state(_state(**off)), patch.multiple(et, **ops), pytest.raises(HTTPException) as exc:
        await getattr(et, route)(*args)
    assert exc.value.status_code == 404
    assert not any(op.called for op in ops.values())


async def test_a_persons_records_are_served_with_neither_the_store_nor_extraction() -> None:
    op = AsyncMock(return_value=(True, "Records linked to Ada"))
    with _with_state(_state(store=False, named=False, offered=False)), patch.object(et, "execute_find_records_by_person", op):
        response = await et.records_by_person(
            _request(), et.PersonRecordsQuery(person="me", roles=["assignee"], recordTypes=["TICKET"], limit=5, cursor="c1"),
            MagicMock(),
        )
    assert response.status_code == 200
    assert response.body == b'{"text":"Records linked to Ada"}'
    kwargs = op.await_args.kwargs
    assert (kwargs["person"], kwargs["roles"], kwargs["record_types"], kwargs["limit"], kwargs["cursor"]) == (
        "me", ["assignee"], ["TICKET"], 5, "c1",
    )


# ---- an entity's records ---------------------------------------------------------
async def test_a_named_entity_while_extraction_is_off_is_a_400() -> None:
    op = AsyncMock(return_value=(True, "x"))
    with _with_state(_state(named=False)), patch.object(et, "execute_find_records_by_entity", op), \
         pytest.raises(HTTPException) as exc:
        await et.entity_records(_request(), et.EntityRecordsQuery(entityId="e1", entityType="named_entity"), MagicMock())
    assert exc.value.status_code == 400
    assert not op.called


async def test_an_unsupported_entity_type_is_a_400() -> None:
    with _with_state(_state()), pytest.raises(HTTPException) as exc:
        await et.entity_records(_request(), et.EntityRecordsQuery(entityId="e1", entityType="galaxy"), MagicMock())
    assert exc.value.status_code == 400


@pytest.mark.parametrize(("message", "status"), [
    (LOOKUP_FAILED_MSG, 503),
    ("Graph provider not available.", 503),
    ("Invalid cursor: issued for a different listing", 400),
])
async def test_a_failed_operation_is_an_error_never_an_empty_listing(message, status) -> None:
    with _with_state(_state()), patch.object(et, "execute_find_records_by_entity", AsyncMock(return_value=(False, message))), \
         pytest.raises(HTTPException) as exc:
        await et.entity_records(_request(), et.EntityRecordsQuery(entityId="e1", entityType="Topic"), MagicMock())
    assert (exc.value.status_code, exc.value.detail) == (status, message)


async def test_the_entity_type_is_normalised_and_the_listing_passed_through() -> None:
    op = AsyncMock(return_value=(True, "Records tagged with topic"))
    with _with_state(_state()), patch.object(et, "execute_find_records_by_entity", op):
        response = await et.entity_records(
            _request(), et.EntityRecordsQuery(entityId="e1", entityType=" Topic ", recordTypes=["FILE"]), MagicMock(),
        )
    assert response.status_code == 200
    assert op.await_args.kwargs["entity_type"] == "topic"
    assert op.await_args.kwargs["record_types"] == ["FILE"]


# ---- typed values, co-mentions --------------------------------------------------
async def test_a_value_query_reaches_the_operation_with_every_bound() -> None:
    op = AsyncMock(return_value=(True, "Records mentioning amounts"))
    body = et.ValueRecordsQuery(
        kinds=["currency"], name="Acme", entityIds=["n1"], dateFrom="2026-03-01", dateTo="2026-03-31",
        amountMin=1, amountMax=2, currency="USD", quantityMin=3, quantityMax=4, dimension="time",
        percentMin=0.1, percentMax=0.2, recordTypes=["MAIL"], sourceIds=["kb-1"], limit=7, cursor="c",
    )
    with _with_state(_state()), patch.object(et, "execute_find_records_by_value", op):
        await et.records_by_value(_request(), body, MagicMock())
    assert op.await_args.kwargs == {
        "kinds": ["currency"], "name": "Acme", "entity_ids": ["n1"], "date_from": "2026-03-01", "date_to": "2026-03-31",
        "amount_min": 1, "amount_max": 2, "currency": "USD", "quantity_min": 3, "quantity_max": 4, "dimension": "time",
        "percent_min": 0.1, "percent_max": 0.2, "record_types": ["MAIL"], "source_ids": ["kb-1"], "limit": 7, "cursor": "c",
    }


async def test_co_mentions_pass_the_ids_and_kinds() -> None:
    op = AsyncMock(return_value=(True, "Mentioned with Acme"))
    with _with_state(_state()), patch.object(et, "execute_find_co_mentioned_entities", op):
        await et.co_mentioned_entities(_request(), et.CoMentionedQuery(entityIds=["a", "b"], kinds=["person"], limit=3), MagicMock())
    assert op.await_args.kwargs == {"entity_ids": ["a", "b"], "kinds": ["person"], "limit": 3}


# ---- searching inside the records that mention names -----------------------------
def _linking(accepted: bool = True) -> SimpleNamespace:
    result = SimpleNamespace(mention=SimpleNamespace(name="Acme"), accepted=accepted, namesakes=[])
    return SimpleNamespace(results=[result], names={})


def _scope(vrids: list[str]) -> SimpleNamespace:
    return SimpleNamespace(virtual_record_ids=vrids, record_ids=[], together={}, record_names={}, per_name=[], note="")


def _linker(linking: SimpleNamespace | Exception) -> MagicMock:
    linker = MagicMock()
    linker.link = AsyncMock(side_effect=linking) if isinstance(linking, Exception) else AsyncMock(return_value=linking)
    return linker


def _entity_search_patches(linker: MagicMock, scope: SimpleNamespace) -> object:
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(patch.object(et, "load_entity_access_context", AsyncMock(return_value=MagicMock())))
    stack.enter_context(patch.object(et, "load_named_entity_config", AsyncMock(return_value=SimpleNamespace(link_aliases=True))))
    stack.enter_context(patch.object(et, "build_linker", MagicMock(return_value=linker)))
    stack.enter_context(patch.object(et, "readable_records", AsyncMock(return_value=scope)))
    stack.enter_context(patch.object(et, "render_preamble", MagicMock(return_value="Entities:\n- Acme: 2 records\n\n")))
    stack.enter_context(patch.object(et, "limited_scope_note", MagicMock(return_value="")))
    stack.enter_context(patch.object(et, "hide_record_parents_outside_connection", AsyncMock()))
    stack.enter_context(patch.object(et, "hide_record_source_paths", MagicMock()))
    return stack


def _hits() -> dict:
    return {
        "status_code": 200,
        "searchResults": [{
            "content": "Acme renewed for 2027", "score": 0.9, "virtual_record_id": "v1", "block_index": 3,
            "metadata": {"recordId": "r1", "recordName": "Acme renewal", "webUrl": "https://x.test/r1"},
        }],
        "records": [{"_key": "r1", "recordName": "Acme renewal", "connectorName": "DRIVE", "webUrl": "https://x.test/r1"}],
    }


async def test_names_are_searched_only_inside_the_readable_records_that_mention_them() -> None:
    retrieval = MagicMock()
    retrieval.search_with_filters = AsyncMock(return_value=_hits())
    user = {"orgId": ORG, "userId": USER, "knowledgeLimit": {"apps": ["app-a"]}}
    with _with_state(_state()), _entity_search_patches(_linker(_linking()), _scope(["v1", "v2"])):
        response = await et.records_by_entities(
            _request(user), et.EntityRecordsSearchQuery(mentions=[et.Mention(name="Acme", kind="organization")],
                                                         query="renewal"),
            MagicMock(), retrieval,
        )
    kwargs = retrieval.search_with_filters.await_args.kwargs
    assert kwargs["virtual_record_ids_from_tool"] == ["v1", "v2"]
    assert kwargs["filter_groups"]["strictScope"] is True
    assert kwargs["filter_groups"]["connectionApps"] == ["app-a"]
    assert (kwargs["org_id"], kwargs["user_id"]) == (ORG, USER)
    import json
    payload = json.loads(response.body)
    assert payload["entities"].startswith("Entities:")
    assert payload["chunks"][0]["record"]["recordId"] == "r1"
    assert payload["chunks"][0]["text"] == "Acme renewed for 2027"


async def test_without_a_query_the_names_are_what_is_searched() -> None:
    retrieval = MagicMock()
    retrieval.search_with_filters = AsyncMock(return_value=_hits())
    with _with_state(_state()), _entity_search_patches(_linker(_linking()), _scope(["v1"])), \
         patch.object(et, "_queries_for_search", MagicMock(return_value=["Acme"])) as queries:
        await et.records_by_entities(_request(), et.EntityRecordsSearchQuery(mentions=[et.Mention(name="Acme")]),
                                     MagicMock(), retrieval)
    assert queries.call_args.args[0] == "Acme"


async def test_no_readable_record_mentioning_the_names_searches_nothing_and_says_so() -> None:
    retrieval = MagicMock()
    retrieval.search_with_filters = AsyncMock()
    with _with_state(_state()), _entity_search_patches(_linker(_linking(accepted=False)), _scope([])):
        response = await et.records_by_entities(
            _request(), et.EntityRecordsSearchQuery(mentions=[et.Mention(name="Nobody Inc")]), MagicMock(), retrieval,
        )
    assert not retrieval.search_with_filters.called
    import json
    payload = json.loads(response.body)
    assert payload["chunks"] == []
    assert "No readable record mentions these names" in payload["entities"]


@pytest.mark.parametrize(("error", "status"), [
    (TimeoutError(), 503),
    (RuntimeError("graph down"), 503),
])
async def test_a_failed_name_lookup_is_an_error_not_an_empty_answer(error, status) -> None:
    retrieval = MagicMock()
    retrieval.search_with_filters = AsyncMock()
    with _with_state(_state()), _entity_search_patches(_linker(error), _scope(["v1"])), \
         pytest.raises(HTTPException) as exc:
        await et.records_by_entities(_request(), et.EntityRecordsSearchQuery(mentions=[et.Mention(name="Acme")]),
                                     MagicMock(), retrieval)
    assert exc.value.status_code == status
    assert not retrieval.search_with_filters.called


async def test_match_all_needs_two_names() -> None:
    captured = {}

    async def readable(_graph: object, _context: object, _results: object, *, match_all: bool) -> SimpleNamespace:
        captured["match_all"] = match_all
        return _scope([])

    for names, expected in ((["Acme"], False), (["Acme", "Globex"], True)):
        with _with_state(_state()), _entity_search_patches(_linker(_linking()), _scope([])), \
             patch.object(et, "readable_records", readable):
            await et.records_by_entities(
                _request(), et.EntityRecordsSearchQuery(mentions=[et.Mention(name=n) for n in names], match="all"),
                MagicMock(), MagicMock(),
            )
        assert captured["match_all"] is expected


def test_the_mentions_are_bounded() -> None:
    with pytest.raises(ValueError):
        et.EntityRecordsSearchQuery(mentions=[])
    with pytest.raises(ValueError):
        et.EntityRecordsSearchQuery(mentions=[et.Mention(name=f"n{i}") for i in range(et.MAX_MENTIONS + 1)])
    with pytest.raises(ValueError):
        et.EntityRecordsSearchQuery(mentions=[et.Mention(name="Acme")], match="some")


# ---- listings without Citation IDs ------------------------------------------------
async def test_a_listing_for_an_api_caller_mints_no_citation_ids_and_reads_nothing() -> None:
    graph = MagicMock()
    graph.get_records_by_record_ids = AsyncMock(return_value=[{"_key": "r1"}])
    state = {LISTING_CITATIONS_OFF_KEY: True}
    assert await cite_listed_records(state, graph, ORG, {"r1": "Created 2026-01-01"}) == {}
    assert not graph.get_records_by_record_ids.called
    assert "citation_ref_mapper" not in state and "tool_records" not in state


async def test_a_chat_listing_still_mints_citation_ids() -> None:
    graph = MagicMock()
    graph.get_records_by_record_ids = AsyncMock(return_value=[{"_key": "r1", "connectorId": "c1"}])
    with patch("app.agents.actions.knowledge_graph.listing_citations.resolve_frontend_url",
               AsyncMock(return_value="https://pipes.test")), \
         patch("app.agents.actions.knowledge_graph.listing_citations.attach_connector_instance_names", AsyncMock()):
        refs = await cite_listed_records({}, graph, ORG, {"r1": "Created 2026-01-01"})
    assert set(refs) == {"r1"}



# ---- the published spec ---------------------------------------------------------
_SPEC_MODELS = {
    "EntityCapabilities": et.EntityCapabilities,
    "EntityRecordsRequest": et.EntityRecordsQuery,
    "CoMentionedEntitiesRequest": et.CoMentionedQuery,
    "RecordsByPersonRequest": et.PersonRecordsQuery,
    "RecordsByValueRequest": et.ValueRecordsQuery,
    "RecordsByEntitiesRequest": et.EntityRecordsSearchQuery,
}


@pytest.mark.parametrize("name", list(_SPEC_MODELS))
def test_each_body_and_answer_has_exactly_the_published_fields(name: str) -> None:
    from tests.support.openapi_spec import openapi_spec

    schema = openapi_spec()["components"]["schemas"][name]
    model = _SPEC_MODELS[name]
    assert set(schema["properties"]) == set(model.model_fields), name
    required = {field for field, info in model.model_fields.items() if info.is_required()}
    assert set(schema.get("required", [])) == required, name


def test_every_route_is_published_with_its_method_and_the_search_scopes() -> None:
    from tests.support.openapi_spec import openapi_spec

    paths = openapi_spec()["paths"]
    for route in et.router.routes:
        method = next(iter(route.methods)).lower()
        operation = paths[route.path][method]
        assert operation["security"] == [{"bearerAuth": []}, {"oauth2": ["semantic:read"]}], route.path
        assert "semantic:read" in operation["responses"]["403"]["description"], route.path


def test_the_capabilities_answer_names_what_capabilities_of_reports() -> None:
    assert set(et.capabilities_of(_state())) == set(et.EntityCapabilities.model_fields)


async def test_a_value_lookup_runs_bound_to_the_signed_in_caller() -> None:
    """Its name check may call a model; unbound, that call would go out as the service."""
    from contextlib import asynccontextmanager

    seen: dict = {}

    @asynccontextmanager
    async def scope(principal: object, workload: object, **_: object):  # noqa: ANN202
        seen["principal"], seen["workload"] = principal, workload
        seen["inside"] = True
        yield
        seen["inside"] = False

    async def op(*_: object, **__: object) -> tuple[bool, str]:
        seen["op_inside"] = seen.get("inside")
        return True, "ok"

    with _with_state(_state()), patch.object(et, "call_scope", scope), \
         patch.object(et, "principal_from_request_user", MagicMock(return_value="caller")), \
         patch.object(et, "execute_find_records_by_value", op):
        await et.records_by_value(_request(), et.ValueRecordsQuery(name="Acme", amountMin=1), MagicMock())
    assert seen["op_inside"] is True
    assert seen["principal"] == "caller"
    assert seen["workload"] == et.Workload.SEARCH


async def test_a_name_lookup_that_timed_out_is_a_503() -> None:
    from app.agents.actions.knowledge_graph.ops.values import NAME_TOO_SLOW_MSG

    with _with_state(_state()), patch.object(et, "execute_find_records_by_value", AsyncMock(return_value=(False, NAME_TOO_SLOW_MSG))), \
         pytest.raises(HTTPException) as exc:
        await et.records_by_value(_request(), et.ValueRecordsQuery(name="Acme"), MagicMock())
    assert exc.value.status_code == 503


def test_the_route_takes_no_more_names_than_the_linker_looks_up() -> None:
    """parse_mentions keeps the first MAX_MENTIONS: a request with more would be
    answered for fewer names, and match="all" for a different intersection."""
    from app.modules.named_entities.linking.mentions import MAX_MENTIONS

    assert et.MAX_MENTIONS == MAX_MENTIONS
    et.EntityRecordsSearchQuery(mentions=[et.Mention(name=f"n{i}") for i in range(MAX_MENTIONS)], match="all")
    with pytest.raises(ValueError):
        et.EntityRecordsSearchQuery(mentions=[et.Mention(name=f"n{i}") for i in range(MAX_MENTIONS + 1)], match="all")
