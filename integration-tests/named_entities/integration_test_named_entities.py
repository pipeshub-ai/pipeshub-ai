"""Named-entity extraction end to end: upload, extract, store, filter, protect, delete.

Documents go in through the knowledge-base API and come back out through the
search API's ``entityFilters``, so every assertion is about what a user of the
product gets. The graph is read only to check what was stored (values exactly as
written, nothing secret), on whichever graph backend the stack runs.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

from helper.clients.kb_client import KBClient
from helper.kb_sharing import grant, revoke
from named_entities.conftest import _wait_for_entities
from named_entities.corpus import SECRETS
from retrieval.ranking import virtual_id_of

pytestmark = [pytest.mark.integration, pytest.mark.ai_agents, pytest.mark.named_entities]

QUERY = "agreement fee settlement refund"
NO_MATCH = "No documents match this search and its entity filter."
MARCH_2026 = {"from": "2026-03-01T00:00:00Z", "to": "2026-04-01T00:00:00Z"}
ANNUAL_FEE = {"min": 1_000_000, "max": 1_500_000, "currency": "USD"}


def _search(search_client, kb_id: str, entity_filters: dict[str, Any]):
    return search_client.search(QUERY, limit=10, filters={"kb": [kb_id], "entityFilters": entity_filters})


def _found(response, corpus) -> set[str]:
    assert response.status_code == 200, f"search failed: {response.status_code}: {response.text[:400]}"
    hits = (response.json().get("searchResponse") or {}).get("searchResults") or []
    by_virtual = {virtual: slug for slug, virtual in corpus.virtual_ids.items()}
    return {by_virtual.get(virtual_id_of(hit), "<other>") for hit in hits}


async def _stored(graph_provider, record_id: str) -> list[dict[str, Any]]:
    return await graph_provider.get_named_entities_for_record(record_id)


def _without_request_id(body: Any) -> Any:
    """Every response names its own request; that is the one field allowed to differ."""
    if isinstance(body, dict):
        return {key: _without_request_id(value) for key, value in body.items() if key != "requestId"}
    if isinstance(body, list):
        return [_without_request_id(item) for item in body]
    return body


def _keys(entities: list[dict[str, Any]]) -> set[str]:
    return {str(entity.get("normKey")) for entity in entities}


@pytest.mark.asyncio(loop_scope="session")
class TestWhatIsStored:
    async def test_values_are_stored_as_the_document_states_them(self, ner_corpus, graph_provider) -> None:
        keys = _keys(await _stored(graph_provider, ner_corpus.record_ids["settlement"]))
        for expected in (
            "money:USD:5000000",       # USD 5 million
            "money:INR:50000000",      # ₹5 crore
            "money:USD:-742",          # a refund of -$742
            "money:USD:-1180",         # ($1,180), an accounting loss
            "pct:-0.03",               # fell -3%
            "email:jane.doe@example.com",
        ):
            assert expected in keys, f"{expected} missing from {sorted(keys)}"
        assert any(key.startswith("date:day:1772496000000:") for key in keys), sorted(keys)  # 3rd March 2026
        wrong = {"money:USD:5100", "money:TOP:10", "money:CAD:3"} & keys
        assert not wrong, f"misread values stored: {wrong}"

    async def test_contacts_and_amounts_of_the_contract(self, ner_corpus, graph_provider) -> None:
        keys = _keys(await _stored(graph_provider, ner_corpus.record_ids["contract"]))
        for expected in (
            "money:USD:1250000",
            "money:EUR:3400",
            "pct:0.125",
            "email:ada@acme-robotics.example",
            "url:https://acme-robotics.example/pricing",
        ):
            assert expected in keys, f"{expected} missing from {sorted(keys)}"

    async def test_the_model_adds_the_names_regexes_cannot_find(self, ner_corpus, graph_provider) -> None:
        entities = await _stored(graph_provider, ner_corpus.record_ids["contract"])
        organizations = {str(e.get("normKey")) for e in entities if e.get("kind") == "organization"}
        assert organizations & {"name:organization:acme robotics", "name:organization:globex"}, (
            f"no organization from the model: {sorted(_keys(entities))}"
        )

    async def test_an_organization_is_keyed_without_its_legal_form(self, ner_corpus, graph_provider) -> None:
        entities = await _stored(graph_provider, ner_corpus.record_ids["settlement"])
        organizations = {str(e.get("normKey")) for e in entities if e.get("kind") == "organization"}
        assert organizations, "the model found no organization in the settlement"
        assert not {key for key in organizations if key.endswith((" ag", " pvt", " ltd", " pvt ltd"))}, organizations

    async def test_nothing_secret_reaches_the_graph(self, ner_corpus, graph_provider) -> None:
        for slug, record_id in ner_corpus.record_ids.items():
            entities = await _stored(graph_provider, record_id)
            stored = repr(entities)
            leaked = [secret for secret in SECRETS if secret in stored]
            assert not leaked, f"{slug} stored {leaked}"
        keys = _keys(await _stored(graph_provider, ner_corpus.record_ids["settlement"]))
        assert "url:https://portal.example.com/view?id=77" in keys, sorted(keys)


@pytest.mark.asyncio(loop_scope="session")
class TestEntityFilters:
    async def test_an_amount_range_finds_the_document_that_states_it(self, ner_corpus, search_client) -> None:
        assert _found(_search(search_client, ner_corpus.kb_id, {"amount": ANNUAL_FEE}), ner_corpus) == {"contract"}

    async def test_constraints_must_hold_in_the_same_document(self, ner_corpus, search_client) -> None:
        both = _search(search_client, ner_corpus.kb_id, {"amount": ANNUAL_FEE, "mentionedDate": MARCH_2026})
        assert _found(both, ner_corpus) == {"contract"}
        later = {"from": "2030-01-01T00:00:00Z", "to": "2030-02-01T00:00:00Z"}
        none = _search(search_client, ner_corpus.kb_id, {"amount": ANNUAL_FEE, "mentionedDate": later})
        assert _found(none, ner_corpus) == set()
        assert NO_MATCH in none.text

    async def test_a_negative_amount_is_found_by_its_sign(self, ner_corpus, search_client) -> None:
        refund = {"amount": {"min": -800, "max": -700, "currency": "usd"}}
        assert _found(_search(search_client, ner_corpus.kb_id, refund), ner_corpus) == {"settlement"}

    async def test_an_iso_date_finds_an_ordinal_day(self, ner_corpus, search_client) -> None:
        day = {"mentionedDate": {"from": "2026-03-03T00:00:00Z", "to": "2026-03-04T00:00:00Z"}}
        assert _found(_search(search_client, ner_corpus.kb_id, day), ner_corpus) == {"settlement"}

    async def test_a_name_finds_the_document_that_names_it(self, ner_corpus, search_client) -> None:
        assert _found(_search(search_client, ner_corpus.kb_id, {"name": "Globex"}), ner_corpus) == {"contract"}

    async def test_a_kind_matches_every_document_with_one(self, ner_corpus, search_client) -> None:
        found = _found(_search(search_client, ner_corpus.kb_id, {"kinds": ["PERCENTAGE"]}), ner_corpus)
        assert found == {"contract", "settlement"}

    @pytest.mark.parametrize(
        "entity_filters",
        [
            {"kinds": ["vehicle"]},
            {"amount": {"min": 1, "max": 2, "currency": "dollars"}},
            {"amount": {"min": 5, "max": 1}},
            {"name": "   "},
            {"where": {"field": "amount"}},
        ],
        ids=["unknown-kind", "bad-currency", "min-above-max", "blank-name", "reserved-where"],
    )
    async def test_an_unusable_filter_is_refused(self, ner_corpus, search_client, entity_filters) -> None:
        filters = {"kb": [ner_corpus.kb_id], "entityFilters": entity_filters}
        if "where" in entity_filters:
            filters = {"kb": [ner_corpus.kb_id], "where": entity_filters["where"]}
        response = search_client.search(QUERY, limit=5, filters=filters)
        assert response.status_code == 400, f"{response.status_code}: {response.text[:300]}"


@pytest.mark.asyncio(loop_scope="session")
class TestPermissions:
    async def test_a_filter_tells_a_reader_nothing_about_documents_they_cannot_read(
        self, ner_corpus, second_user, pipeshub_client,
    ) -> None:
        """A match the user cannot read and no match at all must look the same,
        or the filter becomes an oracle for what other people's documents say."""
        def probe(entity_filters):
            response = second_user.search_filtered(QUERY, {"kb": [ner_corpus.kb_id], "entityFilters": entity_filters})
            return response.status_code, _without_request_id(response.json())

        hidden_match = probe({"amount": ANNUAL_FEE})
        no_match = probe({"amount": {"min": 7, "max": 8, "currency": "USD"}})
        assert hidden_match == no_match

        grant(pipeshub_client, ner_corpus.kb_id, user_ids=[second_user.user_id])
        try:
            for _ in range(24):
                response = second_user.search_filtered(QUERY, {"kb": [ner_corpus.kb_id], "entityFilters": {"amount": ANNUAL_FEE}})
                if response.status_code == 200 and _found(response, ner_corpus) == {"contract"}:
                    break
                await asyncio.sleep(5)
            assert _found(response, ner_corpus) == {"contract"}, response.text[:300]
            shared_no_match = second_user.search_filtered(
                QUERY, {"kb": [ner_corpus.kb_id], "entityFilters": {"amount": {"min": 7, "max": 8, "currency": "USD"}}},
            )
            assert shared_no_match.status_code == 200 and NO_MATCH in shared_no_match.text
        finally:
            revoke(pipeshub_client, ner_corpus.kb_id, user_ids=[second_user.user_id])


@pytest.mark.asyncio(loop_scope="session")
class TestLifecycle:
    async def test_a_deleted_document_is_no_longer_found_by_its_values(
        self, ner_corpus, pipeshub_client, graph_provider, search_client,
    ) -> None:
        kb_client = KBClient(pipeshub_client)
        nonce = uuid.uuid4().hex[:8]
        body = f"# Credit note {nonce}\n\nA refund of -$913 was issued to Initech on 5 May 2026. Reference {nonce}.\n"
        upload = kb_client.upload_file(ner_corpus.kb_id, f"credit-{nonce}.md", body.encode(), mimetype="text/markdown")
        record_id = upload["records"][0]["recordId"]
        virtual_id = await _wait_for_entities(kb_client, graph_provider, record_id, "credit note")
        refund = {"amount": {"min": -920, "max": -910, "currency": "USD"}}

        def hits() -> set[str | None]:
            response = _search(search_client, ner_corpus.kb_id, refund)
            assert response.status_code == 200, response.text[:300]
            return {virtual_id_of(hit) for hit in (response.json().get("searchResponse") or {}).get("searchResults") or []}

        assert virtual_id in hits()
        kb_client.delete_record(record_id)
        for _ in range(24):
            if virtual_id not in hits():
                break
            await asyncio.sleep(5)
        assert virtual_id not in hits()
