"""A model client checks what its endpoint's name resolves to as it connects, and dials only
the checked address. The DNS answer can change after a config is saved (or the name may not
have resolved at all then), so the save-time check alone cannot keep a client off the cloud
metadata service.

Names are answered by a fake resolver and every TCP connect is recorded and refused, so
nothing leaves the host.
"""

from __future__ import annotations

import base64
import contextlib
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from unittest.mock import MagicMock

import httpcore
import httpx
import pytest

from app.services.embeddings.multimodal.config import MultimodalProviderConfig
from app.services.embeddings.multimodal.factory import MultimodalEmbeddingFactory
from app.utils import aimodels
from app.utils.aimodels import (
    get_embedding_model,
    get_generator_model,
    get_image_generation_model,
    get_stt_model,
    get_tts_model,
    require_public_endpoint,
)
from app.utils.model_egress import (
    EndpointRefused,
    VettedAsyncBackend,
    VettedSyncBackend,
    env_proxy_applies,
    guard_httpx_client,
    guarded_async_client,
)
from app.utils.url_fetcher import PRIVATE_ADDRESS_SWITCH_ENV

METADATA = "169.254.169.254"
PUBLIC = "93.184.216.34"
PRIVATE = "10.0.0.5"
_real_getaddrinfo = socket.getaddrinfo
_real_resolved_addresses = aimodels._resolved_addresses

Answer = list[str] | int | None


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("OLLAMA_API_URL", PRIVATE_ADDRESS_SWITCH_ENV, "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(var, raising=False)
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    # The suite-wide stub answers every save-time lookup; here the fake resolver does.
    monkeypatch.setattr(aimodels, "_resolved_addresses", _real_resolved_addresses)


@pytest.fixture
def dns(monkeypatch: pytest.MonkeyPatch) -> dict[str, Answer]:
    """Fake resolver for *.example names: a list of addresses, None for NXDOMAIN, or an
    EAI_* error number. Lookups are counted under "__lookups__"."""
    answers: dict[str, Answer] = {}
    lookups: list[str] = []

    def fake_getaddrinfo(host, port, *args, **kwargs) -> list[tuple]:
        if isinstance(host, bytes):
            host = host.decode()
        if host not in answers:
            return _real_getaddrinfo(host, port, *args, **kwargs)
        lookups.append(host)
        answer = answers[host]
        if answer is None:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        if isinstance(answer, int):
            raise socket.gaierror(answer, "lookup failed")
        infos = []
        for address in answer:
            if ipaddress.ip_address(address).version == 6:
                infos.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "", (address, port or 0, 0, 0)))
            else:
                infos.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port or 0)))
        return infos

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    answers["__lookups__"] = lookups  # type: ignore[assignment]
    return answers


@pytest.fixture
def connects(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Every TCP connect the process attempts; none succeeds."""
    attempted: list[tuple] = []

    def fake_connect(self, address) -> None:
        attempted.append(address[:2])
        raise ConnectionRefusedError("test: connect blocked")

    monkeypatch.setattr(socket.socket, "connect", fake_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", lambda self, address: (attempted.append(address[:2]), 111)[1])
    return attempted


def _config(endpoint: str, **configuration: object) -> dict:
    return {"isDefault": True, "configuration": {"model": "m", "apiKey": "k", "endpoint": endpoint, **configuration}}


ENDPOINT = "http://models.example:8000/v1"
AZURE = {"deploymentName": "d", "apiVersion": "2024-02-01"}
_IMAGE = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()

ModelCall = Callable[[], Awaitable[object]]
MODEL_CALLS: dict[str, ModelCall] = {
    "openAICompatible-llm": lambda: get_generator_model("openAICompatible", _config(ENDPOINT, maxRetries=0)).ainvoke("hi"),
    "ollama-llm": lambda: get_generator_model("ollama", _config(ENDPOINT)).ainvoke("hi"),
    "together-llm": lambda: get_generator_model("together", _config(ENDPOINT)).ainvoke("hi"),
    "ollama-embedding": lambda: get_embedding_model("ollama", _config(ENDPOINT)).aembed_query("hi"),
    "azureOpenAI-embedding": lambda: get_embedding_model("azureOpenAI", _config(ENDPOINT, **AZURE)).aembed_query("hi"),
    "tts": lambda: get_tts_model("litellmProxy", _config(ENDPOINT)).synthesize("hi"),
    "stt": lambda: get_stt_model("litellmProxy", _config(ENDPOINT)).transcribe(b"audio"),
    "image": lambda: get_image_generation_model("litellmProxy", _config(ENDPOINT)).generate("a cat"),
    "multimodal-ollama": lambda: MultimodalEmbeddingFactory.create(
        MultimodalProviderConfig(provider="ollama", model_name="m", base_url=ENDPOINT, logger=MagicMock())
    ).embed_images([_IMAGE]),
    "provider-image-download": lambda: aimodels._download_provider_image(f"{ENDPOINT}/x.png"),
}


async def _call(call: ModelCall) -> None:
    with contextlib.suppress(Exception):  # each SDK words a refused connection its own way
        await call()


@pytest.mark.parametrize("call", MODEL_CALLS.values(), ids=MODEL_CALLS.keys())
class TestEveryModelClient:
    async def test_name_rebound_to_metadata_is_never_dialled(self, dns: dict, connects: list, call: ModelCall) -> None:
        dns["models.example"] = [METADATA]
        await _call(call)
        assert connects == []

    async def test_public_name_is_dialled_by_the_vetted_ip(self, dns: dict, connects: list, call: ModelCall) -> None:
        dns["models.example"] = [PUBLIC]
        await _call(call)
        assert connects and set(connects) == {(PUBLIC, 8000)}


@pytest.mark.parametrize(
    "answer",
    [[METADATA], [PUBLIC, METADATA], ["fd00:ec2::254"], ["::ffff:169.254.169.254"], ["fe80::1"], ["100.100.100.200"], ["0.0.0.0"], ["::"]],
)
@pytest.mark.parametrize("switch", ["", "true"], ids=["default", "blocked"])
async def test_link_local_metadata_and_unspecified_answers_are_refused_in_every_mode(
    monkeypatch: pytest.MonkeyPatch, dns: dict, connects: list, answer: list[str], switch: str
) -> None:
    monkeypatch.setenv(PRIVATE_ADDRESS_SWITCH_ENV, switch)
    dns["models.example"] = answer
    await _call(MODEL_CALLS["ollama-embedding"])
    assert connects == []


@pytest.mark.parametrize(("switch", "dialled"), [("", [(PRIVATE, 8000)]), ("true", [])], ids=["default", "blocked"])
async def test_private_answer_refused_only_in_blocked_mode(
    monkeypatch: pytest.MonkeyPatch, dns: dict, connects: list, switch: str, dialled: list
) -> None:
    monkeypatch.setenv(PRIVATE_ADDRESS_SWITCH_ENV, switch)
    dns["models.example"] = [PRIVATE]
    await _call(MODEL_CALLS["ollama-embedding"])
    assert connects == dialled


@pytest.mark.parametrize(("answer", "dialled"), [([PRIVATE], [(PRIVATE, 11434)]), ([METADATA], [])])
async def test_platform_ollama_exempt_from_private_but_not_metadata(
    monkeypatch: pytest.MonkeyPatch, dns: dict, connects: list, answer: list[str], dialled: list
) -> None:
    monkeypatch.setenv(PRIVATE_ADDRESS_SWITCH_ENV, "true")
    monkeypatch.setenv("OLLAMA_API_URL", "http://ollama.example:11434")
    dns["ollama.example"] = answer
    await _call(lambda: get_embedding_model("ollama", _config("http://ollama.example:11434")).aembed_query("hi"))
    assert connects == dialled


async def test_name_is_looked_up_once_per_connection(dns: dict, connects: list) -> None:
    dns["models.example"] = [PUBLIC]
    await _call(MODEL_CALLS["ollama-embedding"])
    assert dns["__lookups__"] == ["models.example"]


async def test_every_vetted_address_is_tried_in_order(dns: dict, connects: list) -> None:
    dns["models.example"] = ["2606:2800:220:1::1", PUBLIC]
    await _call(MODEL_CALLS["ollama-embedding"])
    assert connects == [("2606:2800:220:1::1", 8000), (PUBLIC, 8000)]


def test_sync_clients_are_guarded_too(dns: dict, connects: list) -> None:
    dns["models.example"] = [METADATA]
    with pytest.raises(Exception):  # noqa: B017
        get_embedding_model("openAICompatible", _config(ENDPOINT, maxRetries=0)).embed_query("hi")
    assert connects == []
    dns["models.example"] = [PUBLIC]
    with pytest.raises(Exception):  # noqa: B017
        get_embedding_model("openAICompatible", _config(ENDPOINT, maxRetries=0)).embed_query("hi")
    assert set(connects) == {(PUBLIC, 8000)}


class TestSaveAndCallTogether:
    """The scenarios from the review: the save-time check alone does not protect the call."""

    @pytest.mark.parametrize("switch", ["", "true"], ids=["default", "blocked"])
    async def test_save_check_refuses_a_name_that_does_not_resolve(
        self, monkeypatch: pytest.MonkeyPatch, dns: dict, switch: str
    ) -> None:
        monkeypatch.setenv(PRIVATE_ADDRESS_SWITCH_ENV, switch)
        dns["typo.example"] = None
        with pytest.raises(ValueError, match="does not resolve"):
            await require_public_endpoint("https://typo.example/v1")

    async def test_save_check_tells_a_transient_failure_apart(self, dns: dict) -> None:
        dns["models.example"] = socket.EAI_AGAIN
        with pytest.raises(ValueError, match="Try again"):
            await require_public_endpoint("https://models.example/v1")

    async def test_model_call_never_connects_to_a_name_rebound_to_metadata_after_save(
        self, dns: dict, connects: list
    ) -> None:
        endpoint = "http://models.example:11434"
        dns["models.example"] = [PUBLIC]
        await require_public_endpoint(endpoint)

        dns["models.example"] = [METADATA]
        embeddings = get_embedding_model("ollama", _config(endpoint))
        with pytest.raises(Exception):  # noqa: B017
            await embeddings.aembed_query("hello")
        assert connects == []

    async def test_model_built_while_unresolvable_never_connects_once_it_points_at_metadata(
        self, dns: dict, connects: list
    ) -> None:
        endpoint = "http://models.example:11434"
        dns["models.example"] = None
        embeddings = get_embedding_model("ollama", _config(endpoint))
        dns["models.example"] = [METADATA]
        with pytest.raises(Exception):  # noqa: B017
            await embeddings.aembed_query("hello")
        assert connects == []


class TestBackend:
    async def test_refusal_is_a_connect_error_naming_the_reason_but_not_the_address(self, dns: dict) -> None:
        dns["models.example"] = [METADATA]
        backend = VettedAsyncBackend(httpcore.AnyIOBackend(), allow_private=True)
        with pytest.raises(EndpointRefused, match="never allowed") as refusal:
            await backend.connect_tcp("models.example", 443)
        assert isinstance(refusal.value, httpcore.ConnectError)
        assert METADATA not in str(refusal.value)

    @pytest.mark.parametrize(("answer", "message"), [(None, "does not resolve"), (socket.EAI_AGAIN, "Temporary DNS failure")])
    async def test_a_failed_lookup_is_a_connect_error(self, dns: dict, answer: Answer, message: str) -> None:
        dns["models.example"] = answer
        backend = VettedAsyncBackend(httpcore.AnyIOBackend(), allow_private=True)
        with pytest.raises(httpcore.ConnectError, match=message):
            await backend.connect_tcp("models.example", 443)

    def test_sync_backend_refuses_the_same_way(self, dns: dict) -> None:
        dns["models.example"] = [PUBLIC, PRIVATE]
        with pytest.raises(EndpointRefused, match=PRIVATE_ADDRESS_SWITCH_ENV):
            VettedSyncBackend(httpcore.SyncBackend(), allow_private=False).connect_tcp("models.example", 443)

    @pytest.mark.parametrize("host", [METADATA, "::ffff:169.254.169.254", "0.0.0.0"])
    async def test_an_address_literal_is_judged_without_a_lookup(self, dns: dict, connects: list, host: str) -> None:
        backend = VettedAsyncBackend(httpcore.AnyIOBackend(), allow_private=True)
        with pytest.raises(EndpointRefused):
            await backend.connect_tcp(host, 80)
        assert dns["__lookups__"] == [] and connects == []

    async def test_unix_sockets_are_refused(self) -> None:
        with pytest.raises(EndpointRefused):
            await VettedAsyncBackend(httpcore.AnyIOBackend(), allow_private=True).connect_unix_socket("/var/run/docker.sock")

    async def test_tls_keeps_the_hostname_for_sni_and_certificate_checks(self, dns: dict) -> None:
        dns["models.example"] = [PUBLIC]
        dialled: list[str] = []
        server_names: list[str | None] = []

        class Stream(httpcore.AsyncNetworkStream):
            async def start_tls(self, ssl_context, server_hostname=None, timeout=None) -> httpcore.AsyncNetworkStream:
                server_names.append(server_hostname)
                raise httpcore.ConnectError("test: stop after the handshake starts")

            async def aclose(self) -> None:
                pass

        class Inner(httpcore.AsyncNetworkBackend):
            async def connect_tcp(
                self, host, port, timeout=None, local_address=None, socket_options=None
            ) -> httpcore.AsyncNetworkStream:
                dialled.append(host)
                return Stream()

        client = guarded_async_client("https://models.example", timeout=5.0)
        client._transport._pool._network_backend._inner = Inner()
        with pytest.raises(httpx.ConnectError):
            await client.get("https://models.example/v1/models")
        assert dialled == [PUBLIC]
        assert server_names == ["models.example"]


class TestGuardInstallation:
    """The guard reaches into httpx/httpcore internals; an upgrade that moves them must fail here."""

    def test_the_private_httpx_and_httpcore_attributes_it_relies_on_exist(self) -> None:
        from httpx._utils import URLPattern, get_environment_proxies

        assert callable(URLPattern) and callable(get_environment_proxies)
        for client in (httpx.Client(), httpx.AsyncClient()):
            assert isinstance(client._transport._pool, (httpcore.ConnectionPool, httpcore.AsyncConnectionPool))
            assert client._transport._pool._network_backend is not None
            assert client._transport_for_url(httpx.URL("https://models.example")) is client._transport

    @pytest.mark.parametrize(
        "build",
        [
            lambda: get_embedding_model("azureOpenAI", _config(ENDPOINT, **AZURE)),
            lambda: get_embedding_model("ollama", _config(ENDPOINT)),
            lambda: get_embedding_model("openAICompatible", _config(ENDPOINT)),
            lambda: get_embedding_model("together", _config(ENDPOINT)),
            lambda: get_embedding_model("fireworks", _config(ENDPOINT)),
            lambda: get_generator_model("azureAI", _config(ENDPOINT)),
            lambda: get_generator_model("azureAI", _config(ENDPOINT, model="claude-sonnet-4-5")),
            lambda: get_generator_model("azureOpenAI", _config(ENDPOINT, **AZURE)),
            lambda: get_generator_model("ollama", _config(ENDPOINT)),
            lambda: get_generator_model("together", _config(ENDPOINT)),
            lambda: get_generator_model("litellmProxy", _config(ENDPOINT)),
        ],
    )
    def test_every_sdk_client_of_a_model_built_on_an_endpoint_is_guarded(self, build) -> None:
        from tests.unit.utils.test_aimodels_endpoint_policy import _http_clients_under

        clients = _http_clients_under(build())
        assert clients
        for client in clients:
            assert isinstance(client._transport._pool._network_backend, (VettedAsyncBackend, VettedSyncBackend))
            assert client.follow_redirects is False

    def test_a_client_it_cannot_guard_is_refused_rather_than_used(self) -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
        with pytest.raises(TypeError):
            guard_httpx_client(client, ENDPOINT)

    def test_guarding_twice_does_not_stack_backends(self) -> None:
        client = guarded_async_client(ENDPOINT, timeout=5.0)
        guard_httpx_client(client, ENDPOINT)
        assert not isinstance(client._transport._pool._network_backend._inner, VettedAsyncBackend)


class TestProxies:
    def test_env_proxy_mount_is_left_untouched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HTTPS_PROXY", "http://10.0.0.1:3128")
        client = guarded_async_client("https://models.example/v1", timeout=5.0)
        proxy = client._transport_for_url(httpx.URL("https://models.example/v1"))
        assert proxy is not client._transport
        assert not isinstance(proxy._pool._network_backend, VettedAsyncBackend)
        assert isinstance(client._transport._pool._network_backend, VettedAsyncBackend)

    @pytest.mark.parametrize(
        ("env", "endpoint", "expected"),
        [
            ({}, "https://models.example", False),
            ({"HTTPS_PROXY": "http://proxy:3128"}, "https://models.example", True),
            ({"HTTPS_PROXY": "http://proxy:3128"}, "http://models.example", False),
            ({"ALL_PROXY": "http://proxy:3128"}, "models.example:8000", True),
            ({"HTTPS_PROXY": "http://proxy:3128", "NO_PROXY": "models.example"}, "https://models.example", False),
            ({"HTTPS_PROXY": "http://proxy:3128", "NO_PROXY": ".example"}, "https://models.example", False),
            ({"HTTPS_PROXY": "http://proxy:3128", "NO_PROXY": "*"}, "https://models.example", False),
        ],
    )
    def test_env_proxy_applies_follows_httpx(
        self, monkeypatch: pytest.MonkeyPatch, env: dict, endpoint: str, expected: bool
    ) -> None:
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        assert env_proxy_applies(endpoint) is expected
        client = httpx.Client()
        url = httpx.URL(endpoint if "://" in endpoint else f"http://{endpoint}")
        assert (client._transport_for_url(url) is not client._transport) is expected
