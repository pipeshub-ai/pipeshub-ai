"""Which model endpoints a deployment will call.

By default any http(s) endpoint is accepted except link-local and cloud metadata
addresses, because a self-hosted install runs its models on localhost or a private
network. With PIPESHUB_BLOCK_PRIVATE_ADDRESSES on, private and internal endpoints are
refused as well. A name is looked up where a config is tested before it is saved (and one
that does not resolve is refused), and a client built on a configured endpoint does not
follow that server's redirects. What a name resolves to when the client connects is covered
in test_model_egress.py.
"""

from __future__ import annotations

import contextlib
import ipaddress
import json
import socket
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.embeddings.multimodal.config import MultimodalProviderConfig
from app.services.embeddings.multimodal.factory import MultimodalEmbeddingFactory
from app.utils import aimodels
from app.utils.aimodels import (
    _resolved_addresses,
    get_embedding_model,
    get_generator_model,
    get_image_generation_model,
    get_stt_model,
    get_tts_model,
    require_allowed_endpoint,
    require_public_endpoint,
)
from app.utils.url_fetcher import (
    PRIVATE_ADDRESS_SWITCH_ENV,
    is_never_allowed_address,
    literal_ip,
    private_addresses_blocked,
)

LOCAL_ENDPOINTS = [
    "http://localhost:11434",
    "http://127.0.0.1:1234/v1",
    "http://10.0.0.5:8000/v1",
    "http://192.168.1.20:4000",
    "http://host.docker.internal:11434",
    "http://ollama:11434",
    "host.docker.internal:11434",
    "http://models.corp.internal/v1",
    "http://[::1]:11434",
    "http://[64:ff9b::a00:5]/",
    "http://10.0。0.5:8000/v1",
    "http://localhost．:11434",
]
PUBLIC_ENDPOINTS = ["https://api.openai.com/v1", "https://my-resource.openai.azure.com", "http://8.8.8.8:8000"]
NEVER_ALLOWED = [
    "http://169.254.169.254/latest",
    "http://169.254.10.10:8080",
    "http://[fe80::1]:11434",
    "http://[::ffff:169.254.169.254]/",
    "http://[fd00:ec2::254]/",
    "http://[64:ff9b::a9fe:a9fe]/",
    "http://100.100.100.200/",
    "http://168.63.129.16/",
    "http://2852039166/",
    "http://0xA9FEA9FE/",
    "http://169.254.43518/",
    "http://metadata.google.internal/computeMetadata/v1/",
    "http://METADATA.google.internal./",
    "http://169.254．169.254/v1",
    "http://169。254。169。254/",
    "http://169.254｡169.254/",
    "169.254.169.254/latest//meta-data",
    "http://0.0.0.0:11434",
    "http://[::]:11434",
    "http://[::ffff:0.0.0.0]/",
]
NOT_HTTP = ["file:///etc/hosts", "ftp://models.example/x", "gopher://models.example/", "http://[bad"]
# Left over in a config whose provider never reads the endpoint; there is no host to judge.
NO_HOST = ["http://", "https://", ":11434", "/"]

PUBLIC_ADDRESS = ipaddress.ip_address("93.184.216.34")


@pytest.fixture
def default_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(PRIVATE_ADDRESS_SWITCH_ENV, raising=False)
    monkeypatch.delenv("OLLAMA_API_URL", raising=False)


@pytest.fixture
def blocked_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PRIVATE_ADDRESS_SWITCH_ENV, "true")
    monkeypatch.delenv("OLLAMA_API_URL", raising=False)


@pytest.fixture
def dns(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """What each name resolves to; a name not listed resolves to a public address. An
    exception as the answer is raised, as the real lookup raises when DNS fails."""
    answers: dict[str, list[str] | Exception] = {}
    looked_up: list[str] = []

    def resolve(host: str) -> list:
        looked_up.append(host)
        answer = answers.get(host, [str(PUBLIC_ADDRESS)])
        if isinstance(answer, Exception):
            raise answer
        return [ipaddress.ip_address(a) for a in answer]

    monkeypatch.setattr(aimodels, "_resolved_addresses", resolve)
    answers["__looked_up__"] = looked_up  # type: ignore[assignment]
    return answers


class TestSwitch:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("true", True), ("TRUE", True), (" true ", True), ("1", True), ("yes", True), ("on", True),
            ("false", False), ("0", False), ("no", False), ("off", False), ("", False), ("enabled", False),
        ],
    )
    def test_which_values_turn_it_on(self, monkeypatch: pytest.MonkeyPatch, value: str, expected: bool) -> None:
        monkeypatch.setenv(PRIVATE_ADDRESS_SWITCH_ENV, value)
        assert private_addresses_blocked() is expected

    def test_off_when_unset(self, default_mode: None) -> None:
        assert private_addresses_blocked() is False


class TestAddressHelpers:
    @pytest.mark.parametrize(
        ("host", "expected"),
        [
            ("169.254.169.254", "169.254.169.254"),
            ("2852039166", "169.254.169.254"),
            ("0xA9FEA9FE", "169.254.169.254"),
            ("169.254.43518", "169.254.169.254"),
            ("::1", "::1"),
            ("10.0.0.5", "10.0.0.5"),
        ],
    )
    def test_literal_ip_reads_every_spelling_of_an_address(self, host: str, expected: str) -> None:
        assert str(literal_ip(host)) == expected

    @pytest.mark.parametrize("host", ["localhost", "example.com", "ollama", "", "not an address"])
    def test_literal_ip_leaves_names_alone(self, host: str) -> None:
        assert literal_ip(host) is None

    @pytest.mark.parametrize(
        "address",
        [
            "169.254.169.254", "169.254.0.1", "fe80::1", "::ffff:169.254.169.254", "fd00:ec2::254",
            "64:ff9b::a9fe:a9fe", "100.100.100.200", "168.63.129.16", "0.0.0.0", "::", "::ffff:0.0.0.0",
        ],
    )
    def test_link_local_and_metadata_addresses_are_never_allowed(self, address: str) -> None:
        assert is_never_allowed_address(literal_ip(address)) is True

    @pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.5", "192.168.1.1", "::1", "fd12:3456::1", "8.8.8.8", "64:ff9b::808:808"])
    def test_other_addresses_are_left_to_the_deployments_policy(self, address: str) -> None:
        assert is_never_allowed_address(literal_ip(address)) is False


class TestRequireAllowedEndpoint:
    @pytest.mark.parametrize("endpoint", [*LOCAL_ENDPOINTS, *PUBLIC_ENDPOINTS, None, ""])
    def test_default_mode_accepts_local_and_public_endpoints(self, default_mode: None, endpoint: str | None) -> None:
        require_allowed_endpoint(endpoint)

    @pytest.mark.parametrize("endpoint", NEVER_ALLOWED)
    @pytest.mark.parametrize("switch", ["", "true"])
    def test_link_local_and_metadata_are_refused_in_every_mode(
        self, monkeypatch: pytest.MonkeyPatch, endpoint: str, switch: str
    ) -> None:
        monkeypatch.setenv(PRIVATE_ADDRESS_SWITCH_ENV, switch)
        with pytest.raises(ValueError, match="never allowed"):
            require_allowed_endpoint(endpoint)

    @pytest.mark.parametrize("endpoint", NEVER_ALLOWED)
    def test_the_host_it_judges_is_the_host_the_client_dials(self, endpoint: str) -> None:
        dialed = httpx.URL(endpoint if "://" in endpoint else f"http://{endpoint}").host
        assert aimodels._endpoint_host(endpoint).strip("[]") == dialed.removesuffix(".").lower()

    @pytest.mark.parametrize("endpoint", NOT_HTTP)
    def test_only_http_urls_are_endpoints(self, default_mode: None, endpoint: str) -> None:
        with pytest.raises(ValueError, match="http"):
            require_allowed_endpoint(endpoint)

    @pytest.mark.parametrize("endpoint", [123, True, ["http://models.example"], {"url": "http://models.example"}])
    def test_a_value_that_is_not_text_is_refused_with_a_reason(self, default_mode: None, endpoint: object) -> None:
        with pytest.raises(ValueError, match="must be a URL"):
            require_allowed_endpoint(endpoint)  # type: ignore[arg-type]

    @pytest.mark.parametrize("endpoint", NO_HOST)
    @pytest.mark.parametrize("switch", ["", "true"])
    def test_a_value_naming_no_host_is_left_to_the_provider(
        self, monkeypatch: pytest.MonkeyPatch, endpoint: str, switch: str
    ) -> None:
        monkeypatch.setenv(PRIVATE_ADDRESS_SWITCH_ENV, switch)
        require_allowed_endpoint(endpoint)

    @pytest.mark.parametrize("endpoint", LOCAL_ENDPOINTS)
    def test_blocked_mode_refuses_private_endpoints_and_says_why(self, blocked_mode: None, endpoint: str) -> None:
        with pytest.raises(ValueError, match=PRIVATE_ADDRESS_SWITCH_ENV):
            require_allowed_endpoint(endpoint)

    @pytest.mark.parametrize("endpoint", [*PUBLIC_ENDPOINTS, None, ""])
    def test_blocked_mode_accepts_public_endpoints(self, blocked_mode: None, endpoint: str | None) -> None:
        require_allowed_endpoint(endpoint)

    @pytest.mark.parametrize("configured", ["http://host.docker.internal:11434", "http://host.docker.internal:11434/"])
    def test_the_deployments_own_ollama_address_is_allowed_in_blocked_mode(
        self, blocked_mode: None, monkeypatch: pytest.MonkeyPatch, configured: str
    ) -> None:
        monkeypatch.setenv("OLLAMA_API_URL", "http://host.docker.internal:11434")
        require_allowed_endpoint(configured)
        with pytest.raises(ValueError):
            require_allowed_endpoint("http://host.docker.internal:11435")

    def test_it_never_resolves_names(self, blocked_mode: None) -> None:
        with patch.object(socket, "getaddrinfo", side_effect=AssertionError("resolved a name")):
            require_allowed_endpoint("https://api.openai.com/v1")
            with pytest.raises(ValueError):
                require_allowed_endpoint("http://ollama:11434")


class TestRequirePublicEndpoint:
    @pytest.mark.parametrize("answers", [["169.254.169.254"], ["93.184.216.34", "169.254.169.254"], ["fe80::1"], ["fd00:ec2::254"]])
    @pytest.mark.parametrize("switch", ["", "true"])
    async def test_a_name_on_a_metadata_address_is_refused_in_every_mode(
        self, monkeypatch: pytest.MonkeyPatch, dns: dict, answers: list[str], switch: str
    ) -> None:
        monkeypatch.setenv(PRIVATE_ADDRESS_SWITCH_ENV, switch)
        dns["models.example"] = answers
        with pytest.raises(ValueError, match="never allowed"):
            await require_public_endpoint("https://models.example/v1")

    async def test_default_mode_accepts_a_name_on_a_private_address(self, default_mode: None, dns: dict) -> None:
        dns["models.corp.example"] = ["10.0.0.5"]
        await require_public_endpoint("https://models.corp.example/v1")

    async def test_blocked_mode_refuses_a_name_on_a_private_address_without_saying_which(
        self, blocked_mode: None, dns: dict
    ) -> None:
        dns["models.example"] = ["93.184.216.34", "10.0.0.5"]
        with pytest.raises(ValueError, match=PRIVATE_ADDRESS_SWITCH_ENV) as refusal:
            await require_public_endpoint("https://models.example/v1")
        assert "10.0.0.5" not in str(refusal.value)

    @pytest.mark.parametrize("switch", ["", "true"])
    async def test_a_name_on_a_public_address_is_accepted(
        self, monkeypatch: pytest.MonkeyPatch, dns: dict, switch: str
    ) -> None:
        monkeypatch.setenv(PRIVATE_ADDRESS_SWITCH_ENV, switch)
        await require_public_endpoint("models.example:8443")
        assert dns["__looked_up__"] == ["models.example"]

    @pytest.mark.parametrize("answer", [[], socket.gaierror(socket.EAI_NONAME, "Name or service not known")], ids=["no_records", "nxdomain"])
    @pytest.mark.parametrize("switch", ["", "true"], ids=["default", "blocked"])
    async def test_a_name_that_does_not_resolve_is_refused(
        self, monkeypatch: pytest.MonkeyPatch, dns: dict, switch: str, answer: object
    ) -> None:
        monkeypatch.setenv(PRIVATE_ADDRESS_SWITCH_ENV, switch)
        dns["typo.example"] = answer
        with pytest.raises(ValueError, match="does not resolve"):
            await require_public_endpoint("https://typo.example/v1")

    async def test_a_transient_dns_failure_is_refused_with_a_retry_message(self, default_mode: None, dns: dict) -> None:
        dns["models.example"] = socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution")
        with pytest.raises(ValueError, match="temporary DNS failure. Try again") as refusal:
            await require_public_endpoint("https://models.example/v1")
        assert "does not resolve" not in str(refusal.value)

    async def test_unresolvable_name_is_accepted_when_an_env_proxy_applies(
        self, default_mode: None, monkeypatch: pytest.MonkeyPatch, dns: dict
    ) -> None:
        """The proxy resolves the name; the local resolver may not know it at all."""
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.corp:3128")
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)
        dns["models.internal.example"] = socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        await require_public_endpoint("https://models.internal.example/v1")

    async def test_a_name_excluded_by_no_proxy_must_still_resolve(
        self, default_mode: None, monkeypatch: pytest.MonkeyPatch, dns: dict
    ) -> None:
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.corp:3128")
        monkeypatch.setenv("NO_PROXY", "internal.example")
        dns["models.internal.example"] = socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        with pytest.raises(ValueError, match="does not resolve"):
            await require_public_endpoint("https://models.internal.example/v1")

    @pytest.mark.parametrize("endpoint", ["http://10.0.0.5:8000", "http://169.254.169.254/", "http://8.8.8.8/", None, "", "http://"])
    async def test_an_address_is_judged_by_its_text_without_a_lookup(
        self, blocked_mode: None, dns: dict, endpoint: str | None
    ) -> None:
        with contextlib.suppress(ValueError):
            await require_public_endpoint(endpoint)
        assert dns["__looked_up__"] == []

    async def test_the_deployments_own_ollama_address_is_not_resolved(
        self, blocked_mode: None, monkeypatch: pytest.MonkeyPatch, dns: dict
    ) -> None:
        monkeypatch.setenv("OLLAMA_API_URL", "http://ollama:11434")
        await require_public_endpoint("http://ollama:11434")
        assert dns["__looked_up__"] == []

    def test_the_lookup_reads_every_answer_and_lets_a_failure_through(self) -> None:
        answers = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fe80::1%eth0", 0, 0, 2)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
        ]
        with patch.object(socket, "getaddrinfo", return_value=answers):
            assert [str(ip) for ip in _resolved_addresses("models.example")] == ["93.184.216.34", "fe80::1%eth0"]
        with patch.object(socket, "getaddrinfo", side_effect=socket.gaierror("no such host")):
            with pytest.raises(socket.gaierror):
                _resolved_addresses("typo.example")


def _config(endpoint: str | None, **configuration: object) -> dict:
    return {"isDefault": True, "configuration": {"model": "m", "apiKey": "k", "endpoint": endpoint, **configuration}}


FACTORIES = {
    "embedding": lambda endpoint: get_embedding_model("ollama", _config(endpoint)),
    "llm": lambda endpoint: get_generator_model("ollama", _config(endpoint)),
    "image": lambda endpoint: get_image_generation_model("litellmProxy", _config(endpoint)),
    "tts": lambda endpoint: get_tts_model("litellmProxy", _config(endpoint)),
    "stt": lambda endpoint: get_stt_model("litellmProxy", _config(endpoint)),
    "multimodal": lambda endpoint: MultimodalEmbeddingFactory.create(
        MultimodalProviderConfig(provider="ollama", model_name="m", base_url=endpoint, logger=MagicMock())
    ),
}


@pytest.mark.parametrize("build", FACTORIES.values(), ids=FACTORIES.keys())
class TestEveryFactoryChecksItsEndpoint:
    def test_metadata_endpoint_is_refused_by_default(self, default_mode: None, build) -> None:
        with pytest.raises(ValueError, match="never allowed"):
            build("http://169.254.169.254/v1")

    def test_private_endpoint_is_refused_in_blocked_mode(self, blocked_mode: None, build) -> None:
        with pytest.raises(ValueError, match=PRIVATE_ADDRESS_SWITCH_ENV):
            build("http://10.0.0.5:11434")


class TestFactoriesStillBuildLocalModels:
    def test_ollama_on_localhost_by_default(self, default_mode: None) -> None:
        assert get_generator_model("ollama", _config("http://localhost:11434")) is not None

    def test_ollama_with_no_endpoint_in_blocked_mode(self, blocked_mode: None, monkeypatch: pytest.MonkeyPatch) -> None:
        """No endpoint means the deployment's own default, which is not the tenant's to choose."""
        monkeypatch.setenv("OLLAMA_API_URL", "http://ollama:11434")
        assert get_generator_model("ollama", _config(None)) is not None

    @pytest.mark.parametrize("endpoint", NO_HOST)
    def test_a_provider_that_ignores_the_endpoint_still_builds_with_a_leftover_value(
        self, default_mode: None, endpoint: str
    ) -> None:
        assert get_generator_model("openAI", _config(endpoint)) is not None


ENDPOINT = "https://models.example/v1"
AZURE = {"deploymentName": "d", "apiVersion": "2024-02-01"}
BUILT_ON_AN_ENDPOINT = {
    "embedding azureAI": lambda: get_embedding_model("azureAI", _config(ENDPOINT)),
    "embedding azureOpenAI": lambda: get_embedding_model("azureOpenAI", _config(ENDPOINT, **AZURE)),
    "embedding ollama": lambda: get_embedding_model("ollama", _config(ENDPOINT)),
    "embedding openAICompatible": lambda: get_embedding_model("openAICompatible", _config(ENDPOINT)),
    "embedding lmStudio": lambda: get_embedding_model("lmStudio", _config(ENDPOINT)),
    "embedding litellmProxy": lambda: get_embedding_model("litellmProxy", _config(ENDPOINT)),
    "embedding together": lambda: get_embedding_model("together", _config(ENDPOINT)),
    "llm azureAI": lambda: get_generator_model("azureAI", _config(ENDPOINT)),
    "llm azureAI claude": lambda: get_generator_model("azureAI", _config(ENDPOINT, model="claude-sonnet-4-5")),
    "llm azureOpenAI": lambda: get_generator_model("azureOpenAI", _config(ENDPOINT, **AZURE)),
    "llm ollama": lambda: get_generator_model("ollama", _config(ENDPOINT)),
    "llm openAICompatible": lambda: get_generator_model("openAICompatible", _config(ENDPOINT)),
    "llm lmStudio": lambda: get_generator_model("lmStudio", _config(ENDPOINT)),
    "llm litellmProxy": lambda: get_generator_model("litellmProxy", _config(ENDPOINT)),
    "llm together": lambda: get_generator_model("together", _config(ENDPOINT)),
}
_SDK_PACKAGES = ("openai", "anthropic", "ollama", "httpx", "langchain_openai", "langchain_anthropic", "langchain_ollama", "app")
_CLIENT_ATTRIBUTES = {"client", "async_client", "root_client", "root_async_client", "_client", "_async_client"}


def _http_clients_under(obj: object, seen: set[int] | None = None, depth: int = 0) -> list[httpx.Client | httpx.AsyncClient]:
    """Every httpx client reachable from a model, found without the names the code under test uses."""
    seen = set() if seen is None else seen
    if id(obj) in seen or depth > 5:
        return []
    seen.add(id(obj))
    if isinstance(obj, (httpx.Client, httpx.AsyncClient)):
        return [obj]
    names = set(getattr(obj, "__dict__", None) or {}) | set(getattr(obj, "__pydantic_private__", None) or {})
    found: list[httpx.Client | httpx.AsyncClient] = []
    for name in names | _CLIENT_ATTRIBUTES:
        child = getattr(obj, name, None)
        if child is not None and type(child).__module__.split(".")[0] in _SDK_PACKAGES:
            found += _http_clients_under(child, seen, depth + 1)
    return found


INTERNAL_HOST, METADATA_HOST = "10.0.0.5", "169.254.169.254"


def _hosts(requested: list[str]) -> set[str]:
    return {httpx.URL(url).host for url in requested}


def _redirecting_network(monkeypatch: pytest.MonkeyPatch, status: int, location: str) -> list[str]:
    """models.example answers every request with a redirect; anything else answers 200."""
    requested: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.host == "models.example":
            return httpx.Response(status, headers={"location": location})
        return httpx.Response(200, content=b'{"secret": "from the internal host"}', headers={"content-type": "application/json"})

    async def answer_async(self: object, request: httpx.Request) -> httpx.Response:
        return answer(request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", lambda self, request: answer(request))
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", answer_async)
    return requested


class TestClientsDoNotFollowRedirects:
    """The endpoint is checked; where that server then sends the client is not, so the
    client must not go there."""

    @pytest.mark.parametrize("build", BUILT_ON_AN_ENDPOINT.values(), ids=BUILT_ON_AN_ENDPOINT.keys())
    def test_no_http_client_of_a_model_built_on_an_endpoint_follows_redirects(self, default_mode: None, build) -> None:
        clients = _http_clients_under(build())
        assert clients, "no HTTP client found under the model"
        assert [type(c).__name__ for c in clients if c.follow_redirects] == []

    @pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
    def test_chat_does_not_go_where_the_endpoint_redirects(
        self, default_mode: None, monkeypatch: pytest.MonkeyPatch, status: int
    ) -> None:
        requested = _redirecting_network(monkeypatch, status, f"http://{METADATA_HOST}/latest/meta-data/")
        model = get_generator_model("openAICompatible", _config(ENDPOINT, maxRetries=0))
        with pytest.raises(Exception):  # noqa: B017 - the SDK's own error for an unexpected 3xx
            model.invoke("hello")
        assert _hosts(requested) & {"models.example", INTERNAL_HOST, METADATA_HOST} == {"models.example"}

    @pytest.mark.parametrize(
        "build",
        [
            lambda: get_generator_model("ollama", _config("https://models.example")).invoke("hello"),
            lambda: get_generator_model("azureAI", _config(ENDPOINT, model="claude-sonnet-4-5")).invoke("hello"),
            lambda: get_generator_model("together", _config(ENDPOINT)).invoke("hello"),
            lambda: get_embedding_model("openAICompatible", _config(ENDPOINT)).embed_query("hello"),
            lambda: get_embedding_model("ollama", _config("https://models.example")).embed_query("hello"),
        ],
        ids=["ollama chat", "claude on azure", "together chat", "openai-compatible embedding", "ollama embedding"],
    )
    def test_other_clients_do_not_go_there_either(self, blocked_mode: None, monkeypatch: pytest.MonkeyPatch, build) -> None:
        requested = _redirecting_network(monkeypatch, 307, f"http://{INTERNAL_HOST}:8000/internal")
        with pytest.raises(Exception):  # noqa: B017
            build()
        assert _hosts(requested) & {"models.example", INTERNAL_HOST, METADATA_HOST} == {"models.example"}

    async def test_async_chat_does_not_go_there_either(self, default_mode: None, monkeypatch: pytest.MonkeyPatch) -> None:
        requested = _redirecting_network(monkeypatch, 307, f"http://{METADATA_HOST}/latest/meta-data/")
        model = get_generator_model("openAICompatible", _config(ENDPOINT))
        with pytest.raises(Exception):  # noqa: B017
            await model.ainvoke("hello")
        assert _hosts(requested) & {"models.example", INTERNAL_HOST, METADATA_HOST} == {"models.example"}


class TestSpeechAndImageClients:
    """These build their client per request."""

    CALLS = {
        "tts": lambda: get_tts_model("litellmProxy", _config(ENDPOINT)).synthesize("hello"),
        "stt": lambda: get_stt_model("litellmProxy", _config(ENDPOINT)).transcribe(b"audio"),
        "image": lambda: get_image_generation_model("litellmProxy", _config(ENDPOINT)).generate("a cat"),
    }

    @pytest.mark.parametrize("call", CALLS.values(), ids=CALLS.keys())
    async def test_a_redirect_is_not_followed(
        self, default_mode: None, monkeypatch: pytest.MonkeyPatch, dns: dict, call
    ) -> None:
        requested = _redirecting_network(monkeypatch, 303, f"http://{METADATA_HOST}/latest/meta-data/")
        try:
            returned = await call()
        except Exception:  # the SDK's own error for an unexpected 3xx
            returned = b""
        assert _hosts(requested) & {"models.example", INTERNAL_HOST, METADATA_HOST} == {"models.example"}
        assert b"from the internal host" not in (returned if isinstance(returned, bytes) else str(returned).encode())

    @pytest.mark.parametrize("call", CALLS.values(), ids=CALLS.keys())
    async def test_the_name_is_not_looked_up_before_the_request(
        self, default_mode: None, monkeypatch: pytest.MonkeyPatch, dns: dict, call
    ) -> None:
        """The client checks the name as it connects; a lookup here would be a second one."""
        _redirecting_network(monkeypatch, 303, "https://models.example/elsewhere")
        with contextlib.suppress(Exception):
            await call()
        assert dns["__looked_up__"] == []


class TestVertexFieldsThatBecomeUrls:
    def _key_file(self, token_uri: str) -> str:
        return json.dumps({"type": "service_account", "client_email": "a@b.iam.gserviceaccount.com", "token_uri": token_uri})

    @pytest.mark.parametrize("token_uri", ["http://169.254.169.254/token", "file:///etc/hosts"])
    def test_a_key_file_whose_token_address_is_refused_is_not_used(self, default_mode: None, token_uri: str) -> None:
        with patch("google.oauth2.service_account.Credentials.from_service_account_info") as build:
            with pytest.raises(ValueError):
                aimodels._create_vertex_credentials(self._key_file(token_uri))
        build.assert_not_called()

    def test_a_private_token_address_is_refused_in_blocked_mode(self, blocked_mode: None) -> None:
        with patch("google.oauth2.service_account.Credentials.from_service_account_info") as build:
            with pytest.raises(ValueError, match=PRIVATE_ADDRESS_SWITCH_ENV):
                aimodels._create_vertex_credentials(self._key_file("http://10.0.0.5:8080/internal"))
        build.assert_not_called()

    def test_googles_token_address_is_used(self, blocked_mode: None) -> None:
        with patch("google.oauth2.service_account.Credentials.from_service_account_info") as build:
            aimodels._create_vertex_credentials(self._key_file("https://oauth2.googleapis.com/token"))
        build.assert_called_once()

    @pytest.mark.parametrize(("configured", "expected"), [("us-central1", "us-central1"), ("global", "global"), (None, "us-central1"), ("", "us-central1")])
    def test_a_region_name_is_a_location(self, configured: str | None, expected: str) -> None:
        assert aimodels._vertex_location({"location": configured}) == expected

    @pytest.mark.parametrize("location", ["models.attacker.example/x#", "us-central1.evil.example", "US-CENTRAL1", "us central1", 5])
    def test_anything_else_is_not(self, location: object) -> None:
        with pytest.raises(ValueError, match="region name"):
            aimodels._vertex_location({"location": location})


class TestDownloadProviderImage:
    @pytest.mark.parametrize("url", ["http://169.254.169.254/x.png", "file:///etc/hosts"])
    async def test_refused_urls_are_not_requested(self, default_mode: None, url: str) -> None:
        with patch("httpx.AsyncClient", side_effect=AssertionError("requested")):
            with pytest.raises(ValueError):
                await aimodels._download_provider_image(url)

    async def test_default_mode_downloads_directly(self, default_mode: None, monkeypatch: pytest.MonkeyPatch) -> None:
        async def answer(self: object, request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"png")

        monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", answer)
        assert await aimodels._download_provider_image("https://cdn.example/x.png") == b"png"

    async def test_default_mode_download_raises_on_an_error_status(
        self, default_mode: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def answer(self: object, request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, request=request)

        monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", answer)
        with pytest.raises(httpx.HTTPStatusError):
            await aimodels._download_provider_image("https://cdn.example/x.png")

    async def test_blocked_mode_uses_the_pinned_public_fetcher(self, blocked_mode: None) -> None:
        fetched = MagicMock(status_code=200, content=b"png")
        with patch("app.utils.public_http.PublicUrlFetcher.get", new_callable=AsyncMock, return_value=fetched) as get, patch(
            "httpx.AsyncClient", side_effect=AssertionError("unpinned request")
        ):
            assert await aimodels._download_provider_image("https://cdn.example/x.png") == b"png"
        assert get.await_args.args[0] == "https://cdn.example/x.png"

    async def test_blocked_mode_refuses_a_private_url_before_any_request(self, blocked_mode: None) -> None:
        with patch("app.utils.public_http.PublicUrlFetcher.get", new_callable=AsyncMock) as get:
            with pytest.raises(ValueError, match=PRIVATE_ADDRESS_SWITCH_ENV):
                await aimodels._download_provider_image("http://10.0.0.5/x.png")
        get.assert_not_awaited()

    async def test_blocked_mode_error_status_is_an_error(self, blocked_mode: None) -> None:
        fetched = MagicMock(status_code=404, content=b"")
        with patch("app.utils.public_http.PublicUrlFetcher.get", new_callable=AsyncMock, return_value=fetched):
            with pytest.raises(ValueError, match="404"):
                await aimodels._download_provider_image("https://cdn.example/x.png")
