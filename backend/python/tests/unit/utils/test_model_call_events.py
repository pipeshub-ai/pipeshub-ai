"""``llm.call.completed``: every call of a model built by the model factory is announced, with what it
cost and how it ended and never what was said."""

from __future__ import annotations

import pytest
from langchain_core.embeddings import Embeddings
from langchain_core.language_models.fake_chat_models import (
    FakeMessagesListChatModel,
    GenericFakeChatModel,
)
from langchain_core.messages import AIMessage

from app import platform_events
from app.core import org_context
from app.platform_events import catalog
from app.utils import aimodels, model_call_events
from app.utils.embedding_server_client import EmbeddingServerEmbeddings
from tests.support.platform_event_capture import captured_events, facts

SECRET = "SEEDED-prompt-text"
REPLY = "SEEDED-completion-text"
CONFIG = {"configuration": {"model": "gpt-4o, gpt-4o-mini"}}
FIELDS = set(catalog.EVENTS["llm.call.completed"].fields)


def _chat(reply: AIMessage | None = None) -> FakeMessagesListChatModel:
    reply = reply or AIMessage(content=REPLY, usage_metadata={"input_tokens": 11, "output_tokens": 7, "total_tokens": 18})
    model = FakeMessagesListChatModel(responses=[reply])
    return model_call_events.announce_chat_model(model, "openAI", "gpt-4o", CONFIG)


class _Failing(GenericFakeChatModel):
    def _generate(self, *args: object, **kwargs: object):  # noqa: ANN202
        raise TimeoutError("provider timed out " + SECRET)


class _Embeddings(Embeddings):
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.model = "text-embedding-3-small"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self.error:
            raise self.error
        return [[0.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.0]


class TestChat:
    def test_a_call_is_announced_with_its_tokens_and_no_content(self) -> None:
        with captured_events("llm.call.completed") as events, org_context.for_org("org_a", job="test"):
            _chat().invoke(SECRET)

        (call,) = facts(events, "llm.call.completed")
        assert set(call) == FIELDS
        assert (call["provider"], call["model"], call["operation"], call["outcome"]) == ("openAI", "gpt-4o", "chat", "success")
        assert (call["tokens_in"], call["tokens_out"], call["org_id"], call["error_class"]) == (11, 7, "org_a", None)
        assert 0 <= call["duration_seconds"] < 5
        assert SECRET not in repr(call) and REPLY not in repr(call)

    async def test_an_async_call_and_a_stream_are_each_announced_once(self) -> None:
        with captured_events("llm.call.completed") as events:
            model = _chat()
            await model.ainvoke(SECRET)
            _ = [chunk async for chunk in model.astream(SECRET)]

        assert [call["outcome"] for call in facts(events, "llm.call.completed")] == ["success", "success"]

    def test_a_provider_that_reports_no_usage_gives_no_token_counts(self) -> None:
        with captured_events("llm.call.completed") as events:
            _chat(AIMessage(content=REPLY)).invoke(SECRET)

        (call,) = facts(events, "llm.call.completed")
        assert (call["tokens_in"], call["tokens_out"]) == (None, None)

    def test_token_usage_of_the_result_is_read_when_the_message_has_none(self) -> None:
        from langchain_core.outputs import ChatGeneration, LLMResult

        result = LLMResult(
            generations=[[ChatGeneration(message=AIMessage(content=REPLY))]],
            llm_output={"token_usage": {"prompt_tokens": 5, "completion_tokens": 2}},
        )
        assert model_call_events._tokens(result) == (5, 2)

    def test_a_failed_call_is_announced_with_its_class_and_not_its_text(self) -> None:
        model = model_call_events.announce_chat_model(_Failing(messages=iter(())), "anthropic", None, CONFIG)
        with captured_events("llm.call.completed") as events, pytest.raises(TimeoutError):
            model.invoke(SECRET)

        (call,) = facts(events, "llm.call.completed")
        assert (call["outcome"], call["error_class"], call["model"]) == ("failure", "timeout", "gpt-4o")
        assert (call["tokens_in"], call["tokens_out"]) == (None, None)
        assert SECRET not in repr(call)

    def test_the_callback_is_put_on_a_model_once(self) -> None:
        model = _chat()
        model_call_events.announce_chat_model(model, "openAI", "gpt-4o", CONFIG)

        assert [type(callback) for callback in model.callbacks] == [model_call_events.ModelCallAnnouncer]

    def test_nothing_is_built_when_nobody_listens(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(model_call_events, "_tokens", lambda _response: pytest.fail("built a payload nobody hears"))
        assert not platform_events.has_listeners("llm.call.completed")

        _chat().invoke(SECRET)

    def test_the_factory_puts_it_on_every_chat_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(aimodels, "_get_generator_model_impl", lambda *_a, **_k: FakeMessagesListChatModel(responses=[AIMessage(content=REPLY)]))
        monkeypatch.setattr("app.services.model_identity.transport.instrument.install_wire_hooks", lambda *_a, **_k: None)

        model = aimodels.get_generator_model("openAI", CONFIG, "gpt-4o-mini")

        (announcer,) = [c for c in model.callbacks if isinstance(c, model_call_events.ModelCallAnnouncer)]
        assert (announcer.provider, announcer.model) == ("openAI", "gpt-4o-mini")


class TestConfiguredModel:
    @pytest.mark.parametrize(
        ("asked", "expected"),
        [("gpt-4o", "gpt-4o"), ("gpt-4o-mini", "gpt-4o-mini"), (None, "gpt-4o"), ("typed by a person", "other"), ("", "other")],
    )
    def test_only_a_configured_name_is_kept(self, asked: str | None, expected: str) -> None:
        assert model_call_events.configured_model(asked, CONFIG) == expected

    def test_no_configuration_is_other(self) -> None:
        assert [model_call_events.configured_model(None, c) for c in (None, {}, {"configuration": None})] == ["other"] * 3


class TestEmbeddings:
    def test_a_wrapped_client_announces_each_call_without_the_texts(self) -> None:
        with captured_events("llm.call.completed") as events:
            wrapped = model_call_events.announce_embeddings(_Embeddings(), "openAI", None, {"configuration": {"model": "text-embedding-3-small"}})
            wrapped.embed_documents([SECRET, SECRET])
            wrapped.embed_query(SECRET)

        calls = facts(events, "llm.call.completed")
        assert [(c["operation"], c["count"], c["outcome"], c["model"]) for c in calls] == [
            ("embedding", 2, "success", "text-embedding-3-small"), ("embedding", 1, "success", "text-embedding-3-small"),
        ]
        assert all(set(c) == FIELDS for c in calls) and SECRET not in repr(calls)
        assert wrapped.model == "text-embedding-3-small"  # everything else is the wrapped client's

    async def test_async_calls_and_failures_are_announced(self) -> None:
        with captured_events("llm.call.completed") as events:
            wrapped = model_call_events.announce_embeddings(_Embeddings(ConnectionError(SECRET)), "cohere", "embed-v4", {"configuration": {"model": "embed-v4"}})
            with pytest.raises(ConnectionError):
                await wrapped.aembed_documents([SECRET])

        (call,) = facts(events, "llm.call.completed")
        assert (call["provider"], call["outcome"], call["error_class"], call["count"]) == ("cohere", "failure", "unavailable", 1)
        assert SECRET not in repr(call)

    def test_a_client_is_left_as_it_is_when_nobody_listens(self) -> None:
        inner = _Embeddings()

        assert model_call_events.announce_embeddings(inner, "openAI", None, CONFIG) is inner

    def test_a_client_is_wrapped_once_and_one_that_announces_itself_not_at_all(self) -> None:
        with captured_events("llm.call.completed"):
            wrapped = model_call_events.announce_embeddings(_Embeddings(), "openAI", None, CONFIG)
            own = EmbeddingServerEmbeddings.__new__(EmbeddingServerEmbeddings)

            assert model_call_events.announce_embeddings(wrapped, "openAI", None, CONFIG) is wrapped
            assert model_call_events.announce_embeddings(own, "default", None, CONFIG) is own

    def test_the_embedding_server_client_announces_its_own_calls(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.utils.embedding_server_client.call_with_retry", lambda call, **_k: [[0.0]])
        client = EmbeddingServerEmbeddings.__new__(EmbeddingServerEmbeddings)
        client.model, client.max_retries, client._backpressure_coordinator = "bge-small", 1, None

        with captured_events("llm.call.completed") as events:
            client.embed_documents([SECRET])

        (call,) = facts(events, "llm.call.completed")
        assert (call["provider"], call["model"], call["operation"], call["count"]) == ("default", "bge-small", "embedding", 1)


def test_the_event_has_no_field_for_content() -> None:
    assert not {"prompt", "messages", "completion", "content", "text", "arguments", "input", "output"} & FIELDS


class _Reporting(Embeddings):
    """An embeddings client that sees its provider's responses, as the application's own clients do."""

    def __init__(self, usages: list[object]) -> None:
        self.usages = usages

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        for usage in self.usages:
            model_call_events.note_usage({"data": [], "usage": usage})
        return [[0.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.0]


class TestEmbeddingTokens:
    def test_the_tokens_a_provider_reported_are_on_the_call_and_add_up_over_its_requests(self) -> None:
        with captured_events("llm.call.completed") as events:
            wrapped = model_call_events.announce_embeddings(
                _Reporting([{"prompt_tokens": 40, "total_tokens": 40}, {"total_tokens": 2}]), "voyage", None, CONFIG)
            wrapped.embed_documents([SECRET, SECRET])
            wrapped.embed_query(SECRET)

        first, second = facts(events, "llm.call.completed")
        assert (first["tokens_in"], first["tokens_out"], first["count"]) == (42, None, 2)
        assert second["tokens_in"] is None  # that call's response was not seen: nothing is estimated

    @pytest.mark.parametrize("usage", [None, {}, {"prompt_tokens": "12"}, {"prompt_tokens": -1}, {"prompt_tokens": True}, "12"])
    def test_anything_but_a_count_is_not_a_token_count(self, usage: object) -> None:
        with captured_events("llm.call.completed") as events:
            model_call_events.announce_embeddings(_Reporting([usage]), "voyage", None, CONFIG).embed_documents([SECRET])

        assert facts(events, "llm.call.completed")[0]["tokens_in"] is None

    def test_a_count_noted_outside_an_announced_call_goes_nowhere(self) -> None:
        assert model_call_events.awaits_tokens() is False
        model_call_events.note_tokens(7)
        with captured_events("llm.call.completed") as events:
            with model_call_events.embedding_call("p", "m", 1):
                assert model_call_events.awaits_tokens() is True
            assert model_call_events.awaits_tokens() is False

        assert facts(events, "llm.call.completed")[0]["tokens_in"] is None

    async def test_requests_sent_from_tasks_of_the_call_are_added_to_it(self) -> None:
        import asyncio

        async def one(count: int) -> None:
            model_call_events.note_tokens(count)

        with captured_events("llm.call.completed") as events:
            with model_call_events.embedding_call("p", "m", 3):
                await asyncio.gather(one(1), one(2), asyncio.to_thread(model_call_events.note_tokens, 4))

        assert facts(events, "llm.call.completed")[0]["tokens_in"] == 7

    def test_the_embedding_server_response_is_read_for_its_count_without_parsing_the_vectors(self) -> None:
        import httpx

        from app.utils import embedding_server_client as server_client

        body = b'{"object":"list","data":[{"embedding":[' + b"0.1," * 5000 + b'0.1]}],"model":"m","usage":{"prompt_tokens":17,"total_tokens":17}}'
        answers = {"/v1/embeddings": httpx.Response(200, content=body), "/down": httpx.Response(503, content=body)}
        transport = httpx.MockTransport(lambda request: answers[request.url.path])
        with captured_events("llm.call.completed") as events:
            with httpx.Client(transport=transport, event_hooks={"response": [server_client._note_response]}) as http:
                with model_call_events.embedding_call("default", "m", 1):
                    assert http.post("http://embedding.invalid/v1/embeddings").json()["model"] == "m"
                    http.post("http://embedding.invalid/down")
                http.post("http://embedding.invalid/v1/embeddings")  # outside a call: not read

        assert [c["tokens_in"] for c in facts(events, "llm.call.completed")] == [17]

    async def test_the_async_embedding_server_response_is_read_the_same_way(self) -> None:
        import httpx

        from app.utils import embedding_server_client as server_client

        body = b'{"data":[],"usage": {"prompt_tokens": 5, "total_tokens": 5}}'
        transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=body))
        with captured_events("llm.call.completed") as events:
            async with httpx.AsyncClient(transport=transport, event_hooks={"response": [server_client._note_response_async]}) as http:
                with model_call_events.embedding_call("default", "m", 1):
                    assert (await http.post("http://embedding.invalid/v1/embeddings")).json()["data"] == []

        assert facts(events, "llm.call.completed")[0]["tokens_in"] == 5


class TestCallSite:
    def test_outside_indexing_a_call_says_what_it_is_for_when_a_call_context_is_bound(self) -> None:
        from app.services.model_identity.call_context import (
            ModelCallContext,
            bind_call_context,
        )
        from app.services.model_identity.principal import service_principal
        from app.services.model_identity.workload import Workload

        with captured_events("llm.call.completed") as events:
            _chat().invoke(SECRET)
            for workload in (Workload.CHAT, Workload.QUERY_EMBEDDING):
                with bind_call_context(ModelCallContext(principal=service_principal(), workload=workload)):
                    _chat().invoke(SECRET)

        assert [c["call_site"] for c in facts(events, "llm.call.completed")] == [None, "chat", "query_embedding"]

    def test_every_value_it_can_take_outside_indexing_is_a_workload_name(self) -> None:
        from app.services.model_identity.workload import Workload

        assert all(w.value.replace("_", "").isalpha() and w.value.islower() for w in Workload) and len(Workload) < 32
