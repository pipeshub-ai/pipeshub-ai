"""AI model settings, checked against a real model on the running stack.

CTO list, AI Models: model friendly name, health check, change default model,
indexing model vs query models, reasoning effort, and context length. Runs
against whichever provider the run has (Azure OpenAI in CI; the weekly Ollama
job runs this same file against a local model with ``-m ai_models``).

Model calls are kept to a handful: one pinned chat serves the friendly name
and reasoning effort checks, and one unpinned chat the default switch.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from pipeshub_client import PipeshubClient

from ai_agents.support import (
    AI_MODELS,
    ItModel,
    add_llm,
    default_llm_key,
    delete_conversation,
    delete_llm,
    error_message,
    json_body,
    list_llms,
    model_info_of,
    set_default_llm,
    stream_chat,
)
from helper.agui_run import RunTrace
from helper.clients.conversations_client import ConversationsClient
from helper.clients.kb_client import KBClient
from helper.indexing_progress import wait_until_enriched, wait_until_finished

pytestmark = [pytest.mark.integration, pytest.mark.ai_agents, pytest.mark.ai_models]

# Node refuses a new chat's query past this many characters (es_validators.ts).
_QUERY_MAX_CHARS = 100_000
_PROVIDER_NAMES = {"azureOpenAI": "Azure OpenAI", "ollama": "Ollama"}
_ENRICHMENT_TIMEOUT = 300


def _entry(models: list[dict[str, Any]], model_key: str) -> dict[str, Any]:
    return next((m for m in models if m.get("modelKey") == model_key), {})


def _over_long_query() -> str:
    return ("word " * (_QUERY_MAX_CHARS // 5 + 2))[: _QUERY_MAX_CHARS + 5]


@pytest.fixture(scope="module")
def pinned_chat(conversations_client: ConversationsClient, it_model: ItModel) -> Iterator[RunTrace]:
    """One chat pinned to the suite's model the way the chat's model picker sends it."""
    body: dict[str, Any] = {
        "query": "Reply with the single word: ready.",
        "chatMode": "internal_search",
        "modelKey": it_model.model_key,
        "modelName": it_model.model_name,
        "modelFriendlyName": it_model.friendly_name,
    }
    if it_model.is_reasoning:
        body["reasoningEffort"] = "low"
    trace = stream_chat(conversations_client, body)
    try:
        yield trace
    finally:
        delete_conversation(conversations_client, trace)


class TestModelFriendlyName:
    def test_the_friendly_name_is_in_the_models_list(
        self, pipeshub_client: PipeshubClient, it_model: ItModel
    ) -> None:
        entry = _entry(list_llms(pipeshub_client), it_model.model_key)
        assert entry, f"{it_model.model_key} is not in the configured LLMs"
        assert entry.get("modelFriendlyName") == it_model.friendly_name, entry

    def test_the_friendly_name_is_offered_to_chat_users(
        self, pipeshub_client: PipeshubClient, it_model: ItModel
    ) -> None:
        resp = pipeshub_client.request("GET", f"{AI_MODELS}/available/llm")
        assert resp.status_code == 200, resp.text[:500]
        models = json_body(resp).get("models") or []
        entry = _entry([m for m in models if isinstance(m, dict)], it_model.model_key)
        assert entry.get("modelFriendlyName") == it_model.friendly_name, entry

    def test_the_friendly_name_is_in_the_chat_metadata(self, pinned_chat: RunTrace, it_model: ItModel) -> None:
        assert pinned_chat.finished, pinned_chat.describe()
        infos = model_info_of(pinned_chat)
        assert infos, f"the saved conversation has no modelInfo: {pinned_chat.describe()}"
        assert all(info.get("modelFriendlyName") == it_model.friendly_name for info in infos), infos
        assert all(info.get("modelKey") == it_model.model_key for info in infos), infos


class TestHealthCheck:
    @pytest.fixture(scope="class")
    def bad_key_response(self, pipeshub_client: PipeshubClient, it_model: ItModel):
        if "apiKey" not in it_model.configuration:
            pytest.skip(f"{it_model.provider} takes no API key, so there is no bad key to try")
        friendly = f"IT bad key {uuid.uuid4().hex[:6]}"
        configuration = {**it_model.configuration, "apiKey": f"not-a-key-{uuid.uuid4().hex}", "modelFriendlyName": friendly}
        resp = add_llm(pipeshub_client, configuration, provider=it_model.provider, is_reasoning=False)
        stored = [m for m in list_llms(pipeshub_client) if m.get("modelFriendlyName") == friendly]
        for model in stored:
            delete_llm(pipeshub_client, model["modelKey"])
        return resp, stored

    def test_a_valid_config_passes(self, it_model: ItModel) -> None:
        # it_model was added through the same form, which refuses a config that fails its health check.
        assert it_model.model_key

    def test_a_bad_key_is_refused_with_a_clear_message(self, bad_key_response, it_model: ItModel) -> None:
        resp, stored = bad_key_response
        message = error_message(resp)
        assert resp.status_code >= 400, f"a wrong key was accepted: {resp.status_code} {resp.text[:500]}"
        assert not stored, "a model that failed its health check was saved anyway"
        assert _PROVIDER_NAMES.get(it_model.provider, it_model.provider) in message, message
        assert "key" in message.lower(), f"the message does not point at the key: {message}"
        assert "Traceback" not in message and len(message) < 1500, message

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Product bug: a wrong provider key comes back as HTTP 500, a server fault, although the "
            "admin's input is what is wrong (app/api/routes/health.py maps every provider error to 500)."
        ),
    )
    def test_a_bad_key_is_a_client_error_not_a_server_error(self, bad_key_response) -> None:
        resp, _stored = bad_key_response
        assert 400 <= resp.status_code < 500, f"{resp.status_code} {error_message(resp)}"

    def test_an_unreachable_endpoint_is_refused_with_a_clear_message(
        self, pipeshub_client: PipeshubClient, it_model: ItModel
    ) -> None:
        friendly = f"IT bad endpoint {uuid.uuid4().hex[:6]}"
        # .invalid never resolves (RFC 2606), so no request leaves the stack.
        endpoint = f"https://pipeshub-it-{uuid.uuid4().hex[:8]}.invalid/"
        if it_model.provider == "ollama":
            endpoint = f"http://pipeshub-it-{uuid.uuid4().hex[:8]}.invalid:11434"
        configuration = {**it_model.configuration, "endpoint": endpoint, "modelFriendlyName": friendly}
        resp = add_llm(pipeshub_client, configuration, provider=it_model.provider, is_reasoning=False)
        stored = [m for m in list_llms(pipeshub_client) if m.get("modelFriendlyName") == friendly]
        for model in stored:
            delete_llm(pipeshub_client, model["modelKey"])

        message = error_message(resp)
        assert resp.status_code >= 400, f"an unreachable endpoint was accepted: {resp.status_code}"
        assert not stored, "a model that failed its health check was saved anyway"
        assert any(word in message.lower() for word in ("endpoint", "reach", "connect")), message
        assert "Traceback" not in message and len(message) < 1500, message


class TestDefaultModelSwitch:
    @pytest.fixture(scope="class")
    def switched(self, pipeshub_client: PipeshubClient, it_model: ItModel) -> Iterator[Any]:
        original = default_llm_key(pipeshub_client)
        resp = set_default_llm(pipeshub_client, it_model.model_key)
        try:
            yield resp
        finally:
            if original and original != it_model.model_key:
                restored = set_default_llm(pipeshub_client, original)
                assert restored.status_code == 200, f"could not restore the default model: {restored.text[:300]}"

    @pytest.fixture(scope="class")
    def unpinned_chat(self, switched, conversations_client: ConversationsClient) -> Iterator[RunTrace]:
        """A chat that names no model, as an API or MCP caller sends it."""
        assert switched.status_code == 200, f"switching the default failed: {switched.status_code} {switched.text[:500]}"
        trace = stream_chat(conversations_client, {
            "query": "Reply with the single word: ready.", "chatMode": "internal_search",
        })
        try:
            yield trace
        finally:
            delete_conversation(conversations_client, trace)

    def test_the_switch_moves_the_default_flag(
        self, switched, pipeshub_client: PipeshubClient, it_model: ItModel
    ) -> None:
        assert switched.status_code == 200, f"{switched.status_code} {switched.text[:500]}"
        defaults = [m["modelKey"] for m in list_llms(pipeshub_client) if m.get("isDefault")]
        assert defaults == [it_model.model_key], defaults

    def test_a_chat_without_a_model_answers_after_the_switch(self, unpinned_chat: RunTrace) -> None:
        assert unpinned_chat.finished and not unpinned_chat.error, unpinned_chat.describe()

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Product bug: a chat that names no model saves an empty modelInfo, so neither the "
            "conversation nor its answer says which model (the new default) produced it "
            "(Node extractModelInfo copies only what the request sent)."
        ),
    )
    def test_the_conversation_records_the_default_model_it_used(
        self, unpinned_chat: RunTrace, it_model: ItModel
    ) -> None:
        keys = [info.get("modelKey") for info in model_info_of(unpinned_chat)]
        assert keys and all(key == it_model.model_key for key in keys), keys


class TestIndexingAndQueryModels:
    @pytest.fixture(scope="class")
    def indexing_role(self, pipeshub_client: PipeshubClient, it_model: ItModel) -> Iterator[dict[str, Any]]:
        before = json_body(pipeshub_client.request("GET", f"{AI_MODELS}/roles")).get("modelRoles") or {}
        roles = {**before, "indexing": {"modelType": "llm", "modelKey": it_model.model_key}}
        resp = pipeshub_client.request("PUT", f"{AI_MODELS}/roles", json={"roles": roles})
        assert resp.status_code == 200, f"assigning the indexing model: {resp.status_code} {resp.text[:500]}"
        try:
            yield roles
        finally:
            pipeshub_client.request("PUT", f"{AI_MODELS}/roles", json={"roles": before})

    def test_the_indexing_model_is_saved_apart_from_the_query_default(
        self, indexing_role, pipeshub_client: PipeshubClient, it_model: ItModel
    ) -> None:
        roles = json_body(pipeshub_client.request("GET", f"{AI_MODELS}/roles")).get("modelRoles") or {}
        assert (roles.get("indexing") or {}).get("modelKey") == it_model.model_key, roles
        assert default_llm_key(pipeshub_client) not in (None, it_model.model_key), (
            "the query default should still be the org's own model"
        )

    def test_a_document_indexes_with_the_indexing_model(
        self, indexing_role, kb_client: KBClient, it_document: dict[str, str]
    ) -> None:
        # The stack records no trace of which model indexed a record, so this checks the
        # assigned model does the work end to end (a broken one would fail extraction).
        # Extraction finishes after the record is already searchable, so it gets its own wait.
        token = uuid.uuid4().hex[:8]
        upload = kb_client.upload_file(
            it_document["kb_id"], f"indexing-role-{token}.md",
            f"# Indexing role check {token}\n\nThe cedar {token} ledger closes on Fridays.\n".encode(),
            mimetype="text/markdown",
        )
        record_id = upload["records"][0]["recordId"]
        try:
            final = asyncio.run(wait_until_finished(kb_client, [record_id]))
            assert final.get(record_id) == "COMPLETED", final
            extraction = asyncio.run(wait_until_enriched(kb_client, record_id, timeout=_ENRICHMENT_TIMEOUT))
            assert extraction == "COMPLETED", f"extraction with the indexing model ended {extraction}"
        finally:
            kb_client.delete_record(record_id)


class TestReasoningEffort:
    def test_an_effort_is_passed_through_to_the_conversation(self, pinned_chat: RunTrace, it_model: ItModel) -> None:
        if not it_model.is_reasoning:
            pytest.skip(f"no reasoning effort to pass: {it_model.reasoning_skip_reason}")
        # A provider that refused the effort would fail the run, so finishing is the pass-through check.
        assert pinned_chat.finished and not pinned_chat.error, pinned_chat.describe()
        efforts = [info.get("reasoningEffort") for info in model_info_of(pinned_chat)]
        assert efforts and all(e == "low" for e in efforts), efforts

    def test_an_unknown_effort_is_refused(self, conversations_client: ConversationsClient, it_model: ItModel) -> None:
        resp = conversations_client.stream_conversation(json={
            "query": "hello", "chatMode": "internal_search",
            "modelKey": it_model.model_key, "reasoningEffort": "extreme",
        }, stream=False)
        assert resp.status_code == 400, f"{resp.status_code} {resp.text[:300]}"
        assert (json_body(resp).get("error") or {}).get("code") == "VALIDATION_ERROR", resp.text[:300]


class TestContextLength:
    def test_an_over_long_first_message_is_refused_clearly(
        self, conversations_client: ConversationsClient, it_model: ItModel
    ) -> None:
        resp = conversations_client.stream_conversation(json={
            "query": _over_long_query(), "chatMode": "internal_search", "modelKey": it_model.model_key,
        }, stream=False)
        assert resp.status_code == 400, f"{resp.status_code} {resp.text[:300]}"
        message = error_message(resp)
        assert "maximum length" in message.lower() or "100000" in message.replace(",", ""), message

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Product bug: a follow-up message has no length limit (addMessageBodySchema in "
            "es_validators.ts), so a query a new chat refuses at 100,000 characters goes to the model."
        ),
    )
    def test_an_over_long_follow_up_is_refused_like_a_first_message(
        self, conversations_client: ConversationsClient, pinned_chat: RunTrace, it_model: ItModel
    ) -> None:
        assert pinned_chat.conversation_id, pinned_chat.describe()
        resp = conversations_client.stream_message(pinned_chat.conversation_id, json={
            "query": _over_long_query(), "chatMode": "internal_search", "modelKey": it_model.model_key,
        }, timeout=60)
        # Read only the status: if it was accepted, closing now keeps the model call short.
        status = resp.status_code
        resp.close()
        assert status == 400, f"an over-long follow-up was accepted with {status}"
