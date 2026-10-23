from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Never

import httpx
import pytest

from app.services.model_identity import metrics
from app.services.model_identity.call_context import ModelCallContext, bind_call_context
from app.services.model_identity.forwarders.base import HeaderSet
from app.services.model_identity.forwarders.litellm_headers import (
    LiteLLMHeadersForwarder,
)
from app.services.model_identity.groups import GroupMembership, GroupsHandle
from app.services.model_identity.principal import service_principal
from app.services.model_identity.settings import (
    ConnectionForwardingSettings,
    IdentityGovernancePolicy,
)
from app.services.model_identity.transport import httpx_hook
from app.services.model_identity.transport.client_paths import iter_http_clients
from app.services.model_identity.transport.httpx_hook import (
    has_identity_hook,
    install_identity_hook,
)
from app.services.model_identity.transport.instrument import (
    identity_async_http_client,
    instrument_model,
    settings_from_config,
)
from app.services.model_identity.workload import Workload

if TYPE_CHECKING:
    from langchain_openai import ChatOpenAI

ENABLED = {"enabled": True}


def cfg(**raw) -> dict[str, object]:
    return {"configuration": {"identityForwarding": {"enabled": True, **raw}}}


def settings(**raw) -> ConnectionForwardingSettings:
    return ConnectionForwardingSettings.parse({"enabled": True, **raw})


class Recorder:
    """A MockTransport that remembers the headers of each request."""

    def __init__(self, body=None) -> None:
        self.seen = []
        self.body = body or {"ok": True}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(dict(request.headers))
        return httpx.Response(200, json=self.body)

    def async_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


def ctx_for(principal, workload=Workload.CHAT, **kw) -> ModelCallContext:
    return ModelCallContext(principal=principal, workload=workload, request_id="req-1", **kw)


class CountingForwarder:
    def __init__(self, expires_in=None, fail=False) -> None:
        self.renders = 0
        self.expires_in = expires_in
        self.fail = fail

    def requires_groups(self, s) -> bool:
        return True

    def render(self, identity, s) -> HeaderSet:
        self.renders += 1
        if self.fail and identity.subject_kind == "user":
            raise RuntimeError("render failed")
        expires = time.monotonic() + self.expires_in if self.expires_in is not None else None
        return HeaderSet(set=(("x-subject", identity.subject),), expires_at=expires)


class TestHook:
    async def test_async_client_gets_identity(self, alice) -> None:
        rec = Recorder()
        client = rec.async_client()
        assert install_identity_hook(client, settings(), LiteLLMHeadersForwarder())
        async with bind_call_context(ctx_for(alice)):
            await client.post("http://gw/v1/chat")
        assert rec.seen[0]["x-litellm-end-user-id"] == "u-alice"
        assert rec.seen[0]["x-pipeshub-request-id"] == "req-1"

    def test_sync_client_gets_identity(self, alice) -> None:
        rec = Recorder()
        client = rec.client()
        install_identity_hook(client, settings(), LiteLLMHeadersForwarder())
        with bind_call_context(ctx_for(alice)):
            client.post("http://gw/v1/chat")
        assert rec.seen[0]["x-litellm-end-user-id"] == "u-alice"

    async def test_unbound_call_is_the_service_and_counted(self) -> None:
        rec = Recorder()
        client = rec.async_client()
        install_identity_hook(client, settings(), LiteLLMHeadersForwarder())
        await client.post("http://gw/v1/chat")
        assert rec.seen[0]["x-litellm-end-user-id"] == "svc:pipeshub:system"
        assert metrics.counter_value(metrics.IDENTITY_MISSING, "system") >= 1

    async def test_a_client_spoofed_header_is_replaced(self, alice) -> None:
        rec = Recorder()
        client = rec.async_client()
        install_identity_hook(client, settings(), LiteLLMHeadersForwarder())
        async with bind_call_context(ctx_for(alice)):
            await client.post("http://gw/v1", headers={"x-litellm-end-user-id": "admin"})
        assert rec.seen[0]["x-litellm-end-user-id"] == "u-alice"

    async def test_install_is_idempotent(self, alice) -> None:
        rec = Recorder()
        client = rec.async_client()
        fwd = CountingForwarder()
        assert install_identity_hook(client, settings(), fwd)
        assert not install_identity_hook(client, settings(), fwd)
        async with bind_call_context(ctx_for(alice)):
            await client.post("http://gw/v1")
        assert fwd.renders == 1 and has_identity_hook(client)

    async def test_headers_are_rendered_once_per_context(self, alice) -> None:
        rec = Recorder()
        client = rec.async_client()
        fwd = CountingForwarder()
        install_identity_hook(client, settings(), fwd)
        async with bind_call_context(ctx_for(alice)):
            for _ in range(5):
                await client.post("http://gw/v1")
        assert fwd.renders == 1

    async def test_each_context_renders_its_own_headers(self, alice, bob) -> None:
        rec = Recorder()
        client = rec.async_client()
        fwd = CountingForwarder()
        install_identity_hook(client, settings(), fwd)
        for p in (alice, bob):
            async with bind_call_context(ctx_for(p)):
                await client.post("http://gw/v1")
        assert [h["x-subject"] for h in rec.seen] == ["u-alice", "u-bob"]

    async def test_sub_purpose_renders_separately(self, alice) -> None:
        from app.agent_loop_lib.core.usage_source import llm_usage_source

        rec = Recorder()
        client = rec.async_client()
        fwd = CountingForwarder()
        install_identity_hook(client, settings(), fwd)
        async with bind_call_context(ctx_for(alice)):
            await client.post("http://gw/v1")
            with llm_usage_source("compaction"):
                await client.post("http://gw/v1")
        assert fwd.renders == 2

    async def test_expiring_headers_are_rendered_again(self, alice, monkeypatch) -> None:
        rec = Recorder()
        client = rec.async_client()
        fwd = CountingForwarder(expires_in=100)
        install_identity_hook(client, settings(), fwd)
        now = [time.monotonic()]
        monkeypatch.setattr(httpx_hook.time, "monotonic", lambda: now[0])
        fwd.expires_in = 100
        async with bind_call_context(ctx_for(alice)):
            await client.post("http://gw/v1")
            await client.post("http://gw/v1")
            assert fwd.renders == 1
            now[0] += 200
            await client.post("http://gw/v1")
        assert fwd.renders == 2

    async def test_a_failing_forwarder_never_fails_the_request(self, alice) -> None:
        rec = Recorder()
        client = rec.async_client()
        install_identity_hook(client, settings(), CountingForwarder(fail=True))
        async with bind_call_context(ctx_for(alice)):
            response = await client.post("http://gw/v1")
        assert response.status_code == 200
        assert rec.seen[0]["x-subject"] == "svc:pipeshub:system"
        assert metrics.counter_value(metrics.IDENTITY_FORWARDED, "headers", "interactive", "error") == 1

    async def test_a_failed_render_never_moves_a_person_onto_the_service_key(self, alice) -> None:
        class ServiceKeyForwarder:
            def requires_groups(self, s) -> bool:
                return False

            def render(self, identity, s) -> HeaderSet:
                if identity.subject_kind == "user":
                    raise RuntimeError("signing key missing")
                return HeaderSet(
                    set=(("authorization", "Bearer service-key"), ("x-subject", identity.subject))
                )

        rec = Recorder()
        client = rec.async_client()
        client.headers["authorization"] = "Bearer connection-key"
        install_identity_hook(client, settings(), ServiceKeyForwarder())
        async with bind_call_context(ctx_for(alice)):
            await client.post("http://gw/v1")
        assert rec.seen[0]["authorization"] == "Bearer connection-key"
        assert rec.seen[0]["x-subject"] == "svc:pipeshub:system"

    async def test_a_service_call_that_fails_to_render_keeps_its_own_key(self) -> None:
        class Forwarder:
            def requires_groups(self, s) -> bool:
                return False

            def render(self, identity, s) -> Never:
                raise RuntimeError("down") if identity.workload == "indexing" else None

        # The fallback is the service identity, so its key is the one that belongs on the request.
        class WithKey(Forwarder):
            def render(self, identity, s) -> HeaderSet:
                if identity.workload == "indexing":
                    raise RuntimeError("down")
                return HeaderSet(set=(("authorization", "Bearer service-key"),))

        rec = Recorder()
        client = rec.async_client()
        install_identity_hook(client, settings(), WithKey())
        async with bind_call_context(ctx_for(service_principal("indexing", "org-1"), Workload.INDEXING)):
            await client.post("http://gw/v1")
        assert rec.seen[0]["authorization"] == "Bearer service-key"

    async def test_a_forwarder_that_always_fails_still_lets_the_request_through(self, alice) -> None:
        class Broken:
            def requires_groups(self, s) -> bool:
                return False

            def render(self, i, s) -> Never:
                raise RuntimeError("always")

        rec = Recorder()
        client = rec.async_client()
        install_identity_hook(client, settings(), Broken())
        async with bind_call_context(ctx_for(alice)):
            assert (await client.post("http://gw/v1")).status_code == 200

    async def test_async_hook_waits_for_groups(self, alice) -> None:
        class Resolver:
            async def resolve(self, org_id, subject) -> GroupMembership:
                await asyncio.sleep(0.02)
                return GroupMembership(ids=("g1",), source="t")

        rec = Recorder()
        client = rec.async_client()
        install_identity_hook(client, settings(), LiteLLMHeadersForwarder())
        handle = GroupsHandle(Resolver(), alice)
        handle.prefetch()
        async with bind_call_context(ctx_for(alice, groups=handle)):
            await client.post("http://gw/v1")
        assert "group:g1" in rec.seen[0]["x-litellm-tags"]

    async def test_a_slow_group_lookup_does_not_hold_the_request(self, alice) -> None:
        class Slow:
            async def resolve(self, org_id, subject) -> None:
                await asyncio.sleep(5)

        rec = Recorder()
        client = rec.async_client()
        install_identity_hook(client, settings(), LiteLLMHeadersForwarder())
        handle = GroupsHandle(Slow(), alice, budget_seconds=0.02)
        started = time.monotonic()
        async with bind_call_context(ctx_for(alice, groups=handle)):
            await client.post("http://gw/v1")
            await client.post("http://gw/v1")
        assert time.monotonic() - started < 1
        assert rec.seen[0]["x-pipeshub-groups-incomplete"] == "true"
        handle._task.cancel()

    def test_sync_hook_uses_groups_only_when_already_known(self, alice) -> None:
        rec = Recorder()
        client = rec.client()
        install_identity_hook(client, settings(), LiteLLMHeadersForwarder())
        handle = GroupsHandle.resolved(GroupMembership(ids=("g9",), source="t"))
        with bind_call_context(ctx_for(alice, groups=handle)):
            client.post("http://gw/v1")
        assert "group:g9" in rec.seen[0]["x-litellm-tags"]

    async def test_governance_on_the_context_is_applied(self, alice) -> None:
        rec = Recorder()
        client = rec.async_client()
        install_identity_hook(client, settings(), LiteLLMHeadersForwarder())
        gov = IdentityGovernancePolicy.parse({"pseudonymize": True})
        async with bind_call_context(ctx_for(alice, governance=gov, pseudonym_key=b"k")):
            await client.post("http://gw/v1")
        assert rec.seen[0]["x-litellm-end-user-id"].startswith("u_")
        assert "alice" not in json.dumps(rec.seen[0])

    async def test_no_cross_talk_between_concurrent_requests(self, alice, bob) -> None:
        rec = Recorder()
        client = rec.async_client()
        install_identity_hook(client, settings(), LiteLLMHeadersForwarder())

        async def call(p) -> httpx.Headers:
            async with bind_call_context(ctx_for(p)):
                await asyncio.sleep(0)
                r = await client.post("http://gw/v1", headers={"x-who": p.subject_id})
                return r.request.headers

        results = await asyncio.gather(*(call(p) for p in [alice, bob] * 25))
        for headers in results:
            assert headers["x-litellm-end-user-id"] == headers["x-who"]


class TestSettingsFromConfig:
    def test_missing_pieces_are_disabled(self) -> None:
        for c in (None, {}, {"configuration": None}, {"configuration": {}}, {"configuration": "x"}):
            assert not settings_from_config(c).enabled

    def test_enabled(self) -> None:
        assert settings_from_config(cfg()).enabled


class TestInstrumentModel:
    def _openai_model(self, rec, **extra) -> ChatOpenAI:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model="gpt-x",
            api_key="sk-test",
            base_url="http://gw/v1",
            http_client=rec.client(),
            http_async_client=rec.async_client(),
            max_retries=0,
            **extra,
        )

    def test_disabled_connection_is_untouched(self) -> None:
        rec = Recorder()
        model = self._openai_model(rec)
        assert instrument_model(model, "litellmProxy", {"configuration": {}}) is model
        assert not any(has_identity_hook(c) for c in iter_http_clients(model))

    def test_every_client_of_the_model_is_instrumented(self) -> None:
        rec = Recorder()
        model = self._openai_model(rec)
        assert len(iter_http_clients(model)) == 2  # sync and async
        instrument_model(model, "litellmProxy", cfg())
        clients = iter_http_clients(model)
        assert len(clients) == 2 and all(has_identity_hook(c) for c in clients)

    def test_instrumenting_twice_does_not_duplicate_the_hook(self) -> None:
        rec = Recorder()
        model = self._openai_model(rec)
        instrument_model(model, "litellmProxy", cfg())
        instrument_model(model, "litellmProxy", cfg())
        for c in iter_http_clients(model):
            assert len(c.event_hooks["request"]) == 1

    def test_langchain_invoke_carries_identity(self, alice) -> None:
        rec = Recorder(body=_chat_completion())
        model = instrument_model(self._openai_model(rec), "litellmProxy", cfg())
        with bind_call_context(ctx_for(alice)):
            assert model.invoke("hi").content == "hello"
        assert rec.seen[0]["x-litellm-end-user-id"] == "u-alice"

    async def test_langchain_ainvoke_carries_identity(self, alice) -> None:
        rec = Recorder(body=_chat_completion())
        model = instrument_model(self._openai_model(rec), "litellmProxy", cfg())
        async with bind_call_context(ctx_for(alice)):
            assert (await model.ainvoke("hi")).content == "hello"
        assert rec.seen[0]["x-litellm-end-user-id"] == "u-alice"

    async def test_shared_model_is_attributed_to_each_caller(self, alice, bob) -> None:
        rec = Recorder(body=_chat_completion())
        model = instrument_model(self._openai_model(rec), "litellmProxy", cfg())

        async def ask(p) -> None:
            async with bind_call_context(ctx_for(p)):
                await model.ainvoke("hi")

        await asyncio.gather(*(ask(p) for p in [alice, bob] * 10))
        assert sorted({h["x-litellm-end-user-id"] for h in rec.seen}) == ["u-alice", "u-bob"]
        assert len(rec.seen) == 20

    def test_unavailable_mode_installs_nothing(self) -> None:
        rec = Recorder()
        model = self._openai_model(rec)
        instrument_model(model, "litellmProxy", cfg(mode="signedJwt", jwtAudience="a"))
        assert not any(has_identity_hook(c) for c in iter_http_clients(model))

    def test_model_without_http_clients_is_counted_not_broken(self) -> None:
        class NoHttp:
            pass

        model = NoHttp()
        assert instrument_model(model, "bedrock", cfg()) is model
        assert metrics.counter_value(metrics.IDENTITY_FORWARDED, "headers", "-", "no_client") == 1

    def test_embeddings_are_instrumented_too(self, alice) -> None:
        from langchain_openai import OpenAIEmbeddings

        rec = Recorder(body={"data": [{"embedding": [0.1], "index": 0, "object": "embedding"}], "model": "m",
                             "object": "list", "usage": {"prompt_tokens": 1, "total_tokens": 1}})
        emb = OpenAIEmbeddings(
            model="m", api_key="sk", base_url="http://gw/v1", http_client=rec.client(),
            http_async_client=rec.async_client(), check_embedding_ctx_length=False,
        )
        instrument_model(emb, "litellmProxy", cfg())
        with bind_call_context(ctx_for(alice, Workload.QUERY_EMBEDDING)):
            emb.embed_query("x")
        assert rec.seen[0]["x-litellm-end-user-id"] == "u-alice"
        assert rec.seen[0]["x-pipeshub-workload"] == "query_embedding"

    def test_own_client_models_are_found(self) -> None:
        from langchain_anthropic import ChatAnthropic

        Recorder()
        model = ChatAnthropic(model="claude-x", api_key="k", base_url="http://gw", max_retries=0)
        clients = iter_http_clients(model)
        assert clients, "ChatAnthropic keeps its httpx clients in _client/_async_client"
        instrument_model(model, "anthropic", cfg())
        after = iter_http_clients(model)
        assert len(after) == len(clients)
        assert all(has_identity_hook(c) for c in after)


class TestSharedDefaultClients:
    """The SDKs share one default httpx client per base URL; the hook must not."""

    def _model(self, name) -> ChatOpenAI:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=name, api_key="k", base_url="http://shared.example/v1")

    def test_a_connection_with_forwarding_off_never_gets_anothers_hook(self) -> None:
        on, off = self._model("a"), self._model("b")
        shared_before = iter_http_clients(off)
        instrument_model(on, "litellmProxy", cfg())
        assert all(has_identity_hook(c) for c in iter_http_clients(on))
        assert not any(has_identity_hook(c) for c in iter_http_clients(off))
        assert iter_http_clients(off) == shared_before
        assert not any(has_identity_hook(c) for c in shared_before)

    def test_two_connections_with_different_settings_keep_their_own(self) -> None:
        a, b = self._model("a"), self._model("b")
        instrument_model(a, "litellmProxy", cfg(serviceApiKey="key-a"))
        instrument_model(b, "litellmProxy", cfg(serviceApiKey="key-b"))
        ha = iter_http_clients(a)[0]._pipeshub_identity_hook
        hb = iter_http_clients(b)[0]._pipeshub_identity_hook
        assert ha._settings.service_api_key == "key-a" and hb._settings.service_api_key == "key-b"

    def test_the_copy_keeps_the_original_configuration(self) -> None:
        import httpx

        from app.services.model_identity.transport.private_client import private_copy

        original = httpx.AsyncClient(timeout=7.0, follow_redirects=False, headers={"x-keep": "1"})
        copy = private_copy(original, None)
        assert copy is not original
        assert copy.timeout == original.timeout and copy.follow_redirects is False
        assert copy.headers["x-keep"] == "1"

    def test_the_egress_guard_survives_the_copy(self) -> None:
        import httpx

        from app.services.model_identity.transport.private_client import private_copy
        from app.utils.model_egress import VettedAsyncBackend, guard_httpx_client

        original = httpx.AsyncClient()
        guard_httpx_client(original, "https://gateway.example.com/v1")
        copy = private_copy(original, "https://gateway.example.com/v1")
        assert copy is not original and not copy.follow_redirects
        assert isinstance(copy._transport._pool._network_backend, VettedAsyncBackend)

    def test_a_client_with_custom_tls_is_instrumented_in_place(self) -> None:
        import ssl

        import httpx

        from app.services.model_identity.transport.private_client import private_copy

        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        original = httpx.AsyncClient(verify=context)
        assert private_copy(original, None) is original

    def test_a_client_with_proxy_mounts_is_instrumented_in_place(self) -> None:
        import httpx

        from app.services.model_identity.transport.private_client import private_copy

        original = httpx.AsyncClient(mounts={"https://gateway.example.com": httpx.AsyncHTTPTransport()})
        assert private_copy(original, None) is original

    def test_the_copy_keeps_the_connection_limits(self) -> None:
        import httpx

        from app.services.model_identity.transport.private_client import private_copy

        original = httpx.AsyncClient(limits=httpx.Limits(max_connections=7, max_keepalive_connections=3))
        copy = private_copy(original, None)
        assert copy is not original
        assert copy._transport._pool._max_connections == 7
        assert copy._transport._pool._max_keepalive_connections == 3

    def test_an_unguarded_client_stays_unguarded(self) -> None:
        import httpx

        from app.services.model_identity.transport.private_client import private_copy
        from app.utils.model_egress import VettedAsyncBackend

        copy = private_copy(httpx.AsyncClient(), None)
        assert not isinstance(copy._transport._pool._network_backend, VettedAsyncBackend)

    def test_a_custom_transport_is_instrumented_in_place(self) -> None:
        import httpx

        from app.services.model_identity.transport.private_client import private_copy

        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
        assert private_copy(client, None) is client

    def test_the_guard_of_a_real_model_survives(self) -> None:
        from app.utils.aimodels import get_generator_model
        from app.utils.model_egress import VettedAsyncBackend

        model = get_generator_model(
            "litellmProxy",
            {"configuration": {"model": "m", "apiKey": "k", "endpoint": "https://gateway.example.com/v1",
                               "identityForwarding": {"enabled": True}}, "isDefault": True},
            "m",
        )
        for client in iter_http_clients(model):
            if isinstance(client, httpx.AsyncClient):
                assert isinstance(client._transport._pool._network_backend, VettedAsyncBackend)
            assert client.follow_redirects is False


class TestDirectTransportParity:
    async def test_direct_transport_reuses_the_instrumented_client(self, alice) -> None:
        from langchain_openai import ChatOpenAI

        from app.agents.agent_loop.direct_transport import build_direct_transport

        rec = Recorder(body=_chat_completion())
        model = ChatOpenAI(
            model="gpt-x", api_key="sk", base_url="http://gw/v1",
            http_client=rec.client(), http_async_client=rec.async_client(), max_retries=0,
        )
        instrument_model(model, "litellmProxy", cfg())
        shared = identity_async_http_client(model)
        assert shared is not None

        transport = build_direct_transport(model, model_name="gpt-x")
        assert transport is not None and transport._client._client is shared

        async with bind_call_context(ctx_for(alice)):
            await transport._client.chat.completions.create(
                model="gpt-x", messages=[{"role": "user", "content": "hi"}]
            )
            await model.ainvoke("hi")
        langchain_headers, direct_headers = rec.seen[1], rec.seen[0]
        for name in ("x-litellm-end-user-id", "x-litellm-tags", "x-pipeshub-workload"):
            assert langchain_headers[name] == direct_headers[name]

    def test_uninstrumented_model_keeps_the_default_direct_client(self) -> None:
        from langchain_openai import ChatOpenAI

        from app.agents.agent_loop.direct_transport import build_direct_transport

        rec = Recorder()
        model = ChatOpenAI(model="gpt-x", api_key="sk", base_url="http://gw/v1",
                           http_client=rec.client(), http_async_client=rec.async_client())
        assert identity_async_http_client(model) is None
        transport = build_direct_transport(model, model_name="gpt-x")
        assert transport._client._client is not model.root_async_client._client


class TestMediaClients:
    def test_default_path_is_unchanged(self) -> None:
        from app.utils.aimodels import _media_client_kwargs

        assert _media_client_kwargs(None, None) == {}
        assert _media_client_kwargs(None, {"identityForwarding": {"enabled": False}}) == {}

    @pytest.mark.parametrize(
        "forwarding",
        [{"identityForwarding": {"enabled": True}}, {"identityForwardingEnabled": True}],
        ids=["nested", "flat-from-the-admin-dialog"],
    )
    def test_forwarding_without_an_endpoint_gets_an_instrumented_client(self, forwarding) -> None:
        from app.utils.aimodels import _media_client_kwargs

        kwargs = _media_client_kwargs(None, forwarding)
        assert has_identity_hook(kwargs["http_client"]) and "base_url" not in kwargs

    def test_endpoint_client_is_instrumented_when_enabled(self) -> None:
        from app.utils.aimodels import _media_client_kwargs

        kwargs = _media_client_kwargs("https://gateway.example.com/v1", {"identityForwardingEnabled": True})
        assert kwargs["base_url"] == "https://gateway.example.com/v1"
        assert has_identity_hook(kwargs["http_client"])
        plain = _media_client_kwargs("https://gateway.example.com/v1", None)
        assert not has_identity_hook(plain["http_client"])

    @pytest.mark.parametrize("getter,kind", [("get_image_generation_model", "image"), ("get_tts_model", "tts"), ("get_stt_model", "stt")])
    @pytest.mark.parametrize(
        "forwarding",
        [{"identityForwarding": {"enabled": True}}, {"identityForwardingEnabled": True, "identityForwardingMode": "headers"}],
        ids=["nested", "flat-from-the-admin-dialog"],
    )
    def test_adapters_receive_the_connection_settings(self, getter, kind, forwarding) -> None:
        from app.utils import aimodels

        config = {
            "configuration": {
                "model": "m", "apiKey": "k", "endpoint": "https://gateway.example.com/v1", **forwarding,
            }
        }
        adapter = getattr(aimodels, getter)("litellmProxy", config, "m")
        assert adapter.identity_forwarding == forwarding
        assert settings_from_config({"configuration": adapter.identity_forwarding}).enabled


def _chat_completion() -> dict[str, object]:
    return {
        "id": "c1", "object": "chat.completion", "created": 0, "model": "gpt-x",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "hello"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
