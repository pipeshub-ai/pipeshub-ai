"""
MCP entity tools — integration tests
====================================

``pipeshub_entities`` and ``pipeshub_find_records`` are registered by PipesHub
itself (``modules/mcp/entity.tools.ts``) with only the actions the deployment
serves. What it serves depends on the entity store, named-entity extraction
(``HIGH_ACCURACY_MODE`` or the org's Labs flag) and the ``ENTITY_SEARCH_TOOL``
switch, and the query service reports it at
``GET /api/v1/search/entities/capabilities``.

These tests run against whatever the instance has turned on and check that:

  * the MCP listing serves exactly what the capabilities report, no more;
  * every action served answers (a failure is an error, never an empty listing);
  * every action not served is refused by the REST route too (404), so a
    client holding an old listing cannot reach it;
  * no answer names one of the in-product agent's tools, which a host cannot call.

Set ``MCP_ENTITY_EXPECT`` to a comma-separated list of capability names that
must be on (for example ``recordsByEntities`` on a stack started with
``ENTITY_SEARCH_TOOL=true``), so a deployment that silently serves less fails.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from collections.abc import Iterator

import pytest
import requests

from helper.mcp_client import McpToolResult, call_mcp_tool

pytestmark = [pytest.mark.integration, pytest.mark.mcp]

ENTITIES = "pipeshub_entities"
FIND_RECORDS = "pipeshub_find_records"
CAPABILITIES = (
    "entitySearch", "entityRecords", "namedEntities", "coMentions",
    "recordsByValue", "recordsByEntities", "recordsByPerson",
)
# Capability -> (tool, action) it serves.
ACTIONS = {
    "entitySearch": (ENTITIES, "search"),
    "entityRecords": (ENTITIES, "records"),
    "coMentions": (ENTITIES, "co_mentioned"),
    "recordsByPerson": (FIND_RECORDS, "by_person"),
    "recordsByValue": (FIND_RECORDS, "by_value"),
    "recordsByEntities": (FIND_RECORDS, "by_entities"),
}
# Capability -> the REST route a client could still call with an old listing.
ROUTES = {
    "entityRecords": ("/search/entities/records", {"entityId": "x", "entityType": "topic"}),
    "coMentions": ("/search/entities/co-mentioned", {"entityIds": ["x"]}),
    "recordsByValue": ("/search/records/by-value", {"amountMin": 1}),
    "recordsByEntities": ("/search/records/by-entities", {"mentions": [{"name": "Acme"}]}),
}
logger = logging.getLogger("mcp-entity-tools")
INTERNAL_TOOL = re.compile(r"\bknowledgegraph__\w+")


def _capabilities(mcp_base_url: str, mcp_access_token: str) -> dict[str, bool]:
    response = requests.get(
        f"{mcp_base_url}/api/v1/search/entities/capabilities",
        headers={"Authorization": f"Bearer {mcp_access_token}"},
        timeout=30,
    )
    assert response.status_code == 200, response.text[:500]
    return response.json()


@pytest.fixture(scope="module")
def capabilities(mcp_base_url: str, mcp_access_token: str) -> dict[str, bool]:
    return _capabilities(mcp_base_url, mcp_access_token)


@pytest.fixture(scope="module")
def listing(mcp_surface: dict) -> dict[str, dict]:
    served = {tool["name"]: tool for tool in mcp_surface["tools"]}
    served["__instructions__"] = {"text": mcp_surface["instructions"]}
    return served


def _actions(listing: dict[str, dict], tool: str) -> list[str]:
    if tool not in listing:
        return []
    return listing[tool]["inputSchema"]["properties"]["action"]["enum"]


def _call(mcp_base_url: str, token: str, tool: str, args: dict) -> McpToolResult:
    result = call_mcp_tool(mcp_base_url, token, tool, args)
    assert not INTERNAL_TOOL.search(result.text), f"{tool} named an internal tool:\n{result.text[:2000]}"
    return result


# ---- what is served -----------------------------------------------------------------
def test_the_capabilities_name_every_condition(capabilities: dict[str, bool]) -> None:
    assert set(capabilities) == set(CAPABILITIES)
    assert all(isinstance(v, bool) for v in capabilities.values())
    assert capabilities["recordsByPerson"] is True
    # The switch alone serves nothing: the agent needs the store and extraction too.
    if capabilities["recordsByEntities"]:
        assert capabilities["entitySearch"] and capabilities["namedEntities"]


def test_what_this_deployment_must_serve(capabilities: dict[str, bool]) -> None:
    expected = [name.strip() for name in os.getenv("MCP_ENTITY_EXPECT", "").split(",") if name.strip()]
    missing = [name for name in expected if not capabilities.get(name)]
    assert not missing, f"expected on, but off: {missing} (capabilities: {capabilities})"


def test_the_listing_serves_exactly_the_capabilities(capabilities: dict[str, bool], listing: dict[str, dict]) -> None:
    for capability, (tool, action) in ACTIONS.items():
        assert (action in _actions(listing, tool)) is capabilities[capability], (capability, _actions(listing, tool))
    assert (ENTITIES in listing) is any(capabilities[c] for c in ("entitySearch", "entityRecords", "coMentions"))
    assert FIND_RECORDS in listing


def test_named_entity_inputs_only_with_extraction(capabilities: dict[str, bool], listing: dict[str, dict]) -> None:
    if ENTITIES not in listing or "search" not in _actions(listing, ENTITIES):
        pytest.skip("entity search is not served here")
    props = listing[ENTITIES]["inputSchema"]["properties"]
    assert ("named_entity_kinds" in props) is capabilities["namedEntities"]
    types = json.dumps(props["entity_types"])
    assert ("named_entity" in types) is capabilities["namedEntities"]


def test_the_instructions_name_only_what_is_served(listing: dict[str, dict]) -> None:
    text = listing["__instructions__"]["text"]
    section = text.split("## People, entities and values", 1)[1] if "## People, entities and values" in text else ""
    named = re.findall(r"`(pipeshub_entities|pipeshub_find_records)` `action:\"(\w+)\"`", section)
    unserved = [(tool, action) for tool, action in named if action not in _actions(listing, tool)]
    assert not unserved, f"the instructions name actions that are not served: {unserved}"


@pytest.mark.parametrize("capability", list(ROUTES))
def test_a_lookup_not_served_is_refused_by_its_route(
    capability: str, capabilities: dict[str, bool], mcp_base_url: str, mcp_access_token: str,
) -> None:
    if capabilities[capability]:
        pytest.skip(f"{capability} is served here")
    path, body = ROUTES[capability]
    response = requests.post(
        f"{mcp_base_url}/api/v1{path}", json=body,
        headers={"Authorization": f"Bearer {mcp_access_token}"}, timeout=60,
    )
    assert response.status_code == 404, response.text[:500]


# ---- what each served action does -------------------------------------------------------
def test_my_records_answer(mcp_base_url: str, mcp_access_token: str) -> None:
    result = _call(mcp_base_url, mcp_access_token, FIND_RECORDS, {"action": "by_person", "person": "me", "limit": 5})
    assert not result.is_error, result.text[:2000]
    assert result.text.strip()


def test_an_unknown_person_is_an_answer_not_an_error(mcp_base_url: str, mcp_access_token: str) -> None:
    result = _call(mcp_base_url, mcp_access_token, FIND_RECORDS, {"action": "by_person", "person": "Nobody Zzyzx Quillfeather"})
    assert not result.is_error, result.text[:2000]


def test_a_missing_argument_is_refused_before_any_lookup(mcp_base_url: str, mcp_access_token: str) -> None:
    result = call_mcp_tool(mcp_base_url, mcp_access_token, FIND_RECORDS, {"action": "by_person"})
    assert result.is_error
    assert "person" in result.text


def test_entity_search_finds_the_seeded_document_title_and_lists_its_records(
    capabilities: dict[str, bool], mcp_base_url: str, mcp_access_token: str, mcp_seeded_record: dict[str, str],
) -> None:
    if not capabilities["entitySearch"]:
        pytest.skip("entity search is not served here")
    title = mcp_seeded_record["body"].splitlines()[0].lstrip("# ").strip()
    result = _call(mcp_base_url, mcp_access_token, ENTITIES, {"action": "search", "query": title})
    assert not result.is_error, result.text[:2000]
    hits = result.json().get("results") or []
    if not capabilities["entityRecords"] or not hits:
        return
    first = hits[0]
    listed = _call(mcp_base_url, mcp_access_token, ENTITIES, {
        "action": "records", "entity_id": first["entityId"], "entity_type": first["entityType"], "limit": 5,
    })
    assert not listed.is_error, listed.text[:2000]


def test_records_by_value_answers(capabilities: dict[str, bool], mcp_base_url: str, mcp_access_token: str) -> None:
    if not capabilities["recordsByValue"]:
        pytest.skip("typed values are not served here")
    result = _call(mcp_base_url, mcp_access_token, FIND_RECORDS, {"action": "by_value", "quantity_min": 1, "dimension": "time"})
    assert not result.is_error, result.text[:2000]


def test_records_by_entities_answers_with_a_resolution(
    capabilities: dict[str, bool], mcp_base_url: str, mcp_access_token: str,
) -> None:
    if not capabilities["recordsByEntities"]:
        pytest.skip("searching inside the records naming entities is not served here")
    result = _call(mcp_base_url, mcp_access_token, FIND_RECORDS, {
        "action": "by_entities", "mentions": [{"name": "Zzyzx Quillfeather Holdings", "kind": "organization"}],
    })
    assert not result.is_error, result.text[:2000]
    data = result.json()
    assert data["entities"].startswith("Entities:")
    assert data["chunks"] == []


# ---- with entities extracted from a document this run indexed -------------------------
NER_TIMEOUT = 900
NER_POLL = 10


def _links(mcp_base_url: str, mcp_access_token: str, entity: dict, record_id: str) -> bool:
    """Whether the entity's records, every page of them, include ``record_id``."""
    args = {"action": "records", "entity_id": entity["entityId"], "entity_type": entity["entityType"], "limit": 50}
    for _ in range(20):
        result = call_mcp_tool(mcp_base_url, mcp_access_token, ENTITIES, args)
        if result.is_error:
            return False
        if record_id in result.text:
            return True
        cursor = re.search(r'cursor="([^"]+)"', result.text)
        if not cursor:
            return False
        args = {**args, "cursor": cursor.group(1)}
    return False


@pytest.fixture(scope="module")
def ner_document(
    capabilities: dict[str, bool], kb_client, ai_models_configured, mcp_base_url: str, mcp_access_token: str,
) -> Iterator[dict[str, str]]:
    """A document naming two companies, a person, an amount and a date, indexed
    and with its named entities extracted, so each action has something to find."""
    if not capabilities["namedEntities"]:
        pytest.skip("named-entity extraction is off here")
    token = uuid.uuid4().hex[:8]
    name = f"mcp-entity-{token}.md"
    body = (
        f"# Zentrova Robotics renewal {token}\n\n"
        "Zentrova Robotics renewed its logistics contract with Halvex Logistics on 12 March 2026. "
        "The renewal is worth $75,000 per year and was negotiated by Priya Raman, the account lead "
        "at Zentrova Robotics. Halvex Logistics will expand the warehouse automation pilot to "
        "three more sites under the same terms.\n"
    ).encode()
    kb_id = kb_client.create_kb(f"mcp-entity-{token}")["id"]
    try:
        upload = kb_client.upload_file(kb_id, name, body, mimetype="text/markdown")
        record_id = upload["records"][0]["recordId"]
        deadline = time.monotonic() + NER_TIMEOUT
        found: dict = {}
        while time.monotonic() < deadline:
            # On a new org the entity store, and with it entity search, appears with the first extraction.
            if not _capabilities(mcp_base_url, mcp_access_token)["entitySearch"]:
                time.sleep(NER_POLL)
                continue
            result = call_mcp_tool(mcp_base_url, mcp_access_token, ENTITIES, {
                "action": "search", "query": "Zentrova Robotics", "entity_types": ["named_entity"],
            })
            if not result.is_error:
                hits = [h for h in result.json().get("results") or [] if "zentrova" in str(h.get("name", "")).lower()]
                # An earlier run may have left the entity: it counts only once this run's record is linked to it.
                found = next((h for h in hits if _links(mcp_base_url, mcp_access_token, h, record_id)), {})
                if found:
                    break
            time.sleep(NER_POLL)
        assert found, f"Zentrova Robotics not linked to record {record_id} within {NER_TIMEOUT}s"
        yield {"kb_id": kb_id, "record_id": record_id, "token": token, "entity_id": found["entityId"],
               "entity_type": found["entityType"]}
    finally:
        try:
            kb_client.delete_kb(kb_id)
        except Exception as exc:  # noqa: BLE001 - teardown must not mask a failure
            logger.warning("Could not delete knowledge base %s: %s", kb_id, exc)


def test_records_lists_the_document_naming_the_entity(ner_document: dict, mcp_base_url: str, mcp_access_token: str) -> None:
    result = _call(mcp_base_url, mcp_access_token, ENTITIES, {
        "action": "records", "entity_id": ner_document["entity_id"], "entity_type": ner_document["entity_type"],
    })
    assert not result.is_error, result.text[:2000]
    assert ner_document["record_id"] in result.text, result.text[:2000]
    assert "Citation ID" not in result.text


def test_co_mentioned_names_the_other_company(ner_document: dict, mcp_base_url: str, mcp_access_token: str) -> None:
    result = _call(mcp_base_url, mcp_access_token, ENTITIES, {
        "action": "co_mentioned", "entity_ids": [ner_document["entity_id"]], "kinds": ["organization", "person"],
    })
    assert not result.is_error, result.text[:2000]
    assert "Halvex" in result.text, result.text[:2000]


def test_by_value_finds_the_amount(ner_document: dict, mcp_base_url: str, mcp_access_token: str) -> None:
    result = _call(mcp_base_url, mcp_access_token, FIND_RECORDS, {
        "action": "by_value", "amount_min": 70000, "amount_max": 80000, "currency": "USD",
    })
    assert not result.is_error, result.text[:2000]
    assert ner_document["record_id"] in result.text, result.text[:2000]


def test_by_entities_finds_the_passage_naming_both(ner_document: dict, mcp_base_url: str, mcp_access_token: str) -> None:
    if not _capabilities(mcp_base_url, mcp_access_token)["recordsByEntities"]:
        pytest.skip("searching inside the records naming entities is not served here")
    result = _call(mcp_base_url, mcp_access_token, FIND_RECORDS, {
        "action": "by_entities", "match": "all", "query": "renewal terms",
        "mentions": [{"name": "Zentrova Robotics", "kind": "organization"}, {"name": "Halvex Logistics", "kind": "organization"}],
    })
    assert not result.is_error, result.text[:2000]
    data = result.json()
    assert "Zentrova" in data["entities"] and "Halvex" in data["entities"], data["entities"]
    assert any(c["record"]["recordId"] == ner_document["record_id"] for c in data["chunks"]), json.dumps(data)[:3000]
