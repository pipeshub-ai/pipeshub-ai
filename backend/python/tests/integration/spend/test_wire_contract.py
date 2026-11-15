"""The wire contract of every pinned model wrapper: each wrapper's requests are seen on the wire, and an unhookable one is named.

Real wrappers, real httpx or botocore clients, real local sockets answering 429, 503 and 529. No external network: the
black-holed address is never routed. ``instrument_model`` is the funnel every model passes through.
"""

import asyncio
import time

import pytest

from app.services.llm_gateway.fleet_breaker import (
    FleetBreaker,
    credential_of,
    provider_pk,
    size_class,
)
from app.services.llm_gateway.gateway import (
    LLMGateway,
    ProviderUnavailableError,
    provider_key,
)
from app.services.model_identity.transport.instrument import instrument_model
from app.utils.aimodels import EmbeddingProvider, LLMProvider
from tests.support.fake_llm_server import FakeLLMServer, Reply

pytestmark = pytest.mark.timeout(240)

CODES = (429, 503, 529)


def _bedrock(base: str, timeout: float = 5.0):  # noqa: ANN202
    import boto3
    from botocore.config import Config
    from langchain_aws import ChatBedrockConverse

    client = boto3.client(
        "bedrock-runtime", region_name="us-east-1", endpoint_url=base, aws_access_key_id="x", aws_secret_access_key="y",
        config=Config(connect_timeout=2, read_timeout=timeout, retries={"max_attempts": 1, "mode": "standard"}),
    )
    return ChatBedrockConverse(model="m", client=client, region_name="us-east-1")


def _openai(base: str, timeout: float = 5.0):  # noqa: ANN202
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model="m", api_key="x", base_url=base + "/v1", max_retries=0, timeout=timeout)


def _anthropic(base: str, timeout: float = 5.0):  # noqa: ANN202
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(model="m", api_key="x", base_url=base, max_retries=0, timeout=timeout)


def _gemini(base: str, timeout: float = 5.0):  # noqa: ANN202
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(model="gemini-x", api_key="x", base_url=base, max_retries=0, timeout=timeout)


def _mistral(base: str, timeout: float = 5.0):  # noqa: ANN202
    from langchain_mistralai import ChatMistralAI

    return ChatMistralAI(api_key="x", endpoint=base, max_retries=0, timeout=int(timeout))


def _groq(base: str, timeout: float = 5.0):  # noqa: ANN202
    from langchain_groq import ChatGroq

    return ChatGroq(model="m", api_key="x", base_url=base, max_retries=0, timeout=timeout)


def _ollama(base: str, timeout: float = 5.0):  # noqa: ANN202
    from langchain_ollama import ChatOllama

    return ChatOllama(model="m", base_url=base)


# wrapper name -> (builder, the provider string the factory funnels it under)
WRAPPERS = {
    "langchain-openai": (_openai, "openAI"),
    "langchain-anthropic": (_anthropic, "anthropic"),
    "langchain-google-genai": (_gemini, "gemini"),
    "langchain-mistralai": (_mistral, "mistral"),
    "langchain-groq": (_groq, "groq"),
    "langchain-ollama": (_ollama, "ollama"),
    "langchain-aws": (_bedrock, "bedrock"),
}


@pytest.mark.parametrize("code", CODES)
@pytest.mark.parametrize("wrapper", list(WRAPPERS))
async def test_every_wrapper_reports_429_5xx_529(fleet, monkeypatch, wrapper: str, code: int) -> None:
    """Driven through the gateway against a real socket that answers ``code``: ``ProviderUnavailableError`` is raised (the
    wrapper's exception type, which ``is_provider_unavailable`` misreads for Mistral and Gemini, decides nothing), the wire
    hook recorded the status, and five failures open the fleet breaker."""
    from app.services.llm_gateway import gateway as gateway_module

    # One attempt per call: a 429 is otherwise retried after a 1-2 s wait, which this test is not about.
    monkeypatch.setattr(gateway_module, "RATE_LIMIT_ATTEMPTS", 1)
    build, provider = WRAPPERS[wrapper]
    server = await FakeLLMServer(lambda body: Reply(status=code)).start()
    try:
        model = instrument_model(build(f"http://127.0.0.1:{server.port}"), provider, None)
        gateway = LLMGateway(4)
        key = provider_key(model)
        with pytest.raises(ProviderUnavailableError):
            await gateway.invoke(model, "DOC:x", provider=key)
        assert server.total >= 1, "the request reached the socket"

        # The hook recorded the status of that request (the wrapper's retries are off, so it is the one answer).
        from app.services.llm_gateway import wire

        with wire.wire_scope() as seen:
            with pytest.raises(Exception):  # noqa: B017
                await model.ainvoke("DOC:x")
        assert seen.sent and seen.status == code, (seen.sent, seen.status)

        # The breaker counted a failure each time: four more open it.
        for _ in range(4):
            with pytest.raises(ProviderUnavailableError):
                await LLMGateway(4).invoke(model, "DOC:x", provider=key)
        admission = await FleetBreaker(fleet).admit(provider_pk(key, credential_of(model)), size_class(1), "probe")
        assert admission.code == 1, "five failures must open the breaker"
    finally:
        await server.stop()


# ---- unhookable wrappers --------------------------------------------------------------------------------------------------

HTTPX_WRAPPERS = [w for w in WRAPPERS if w != "langchain-aws"]


async def test_a_blackholed_connect_fails_within_twelve_seconds() -> None:
    """The request hook caps the connect phase at 10 s whatever per-request timeout the SDK computed (the models are built
    with a 120 s float timeout, as the factories do), so an endpoint that never answers fails in about 10 s on every
    httpx-based wrapper. They run together, so the test takes about as long as one."""

    async def one(wrapper: str) -> tuple[str, float]:
        build, provider = WRAPPERS[wrapper]
        model = instrument_model(build("http://10.255.255.1:81", 120.0), provider, None)
        started = time.monotonic()
        with pytest.raises(Exception):  # noqa: B017
            await model.ainvoke("DOC:x")
        return wrapper, time.monotonic() - started

    results = await asyncio.gather(*(one(w) for w in HTTPX_WRAPPERS))
    slow = {w: round(t, 1) for w, t in results if t > 12.0}
    assert not slow, f"these took longer than 12 s to give up on a black-holed connect: {slow}"


def test_bedrock_is_built_with_a_ten_second_connect_timeout() -> None:
    from app.utils.aimodels import _bedrock_client_config, _create_bedrock_client

    assert _bedrock_client_config().connect_timeout == 10
    client = _create_bedrock_client({"region": "us-east-1", "awsAccessKeyId": "x", "awsAccessSecretKey": "y"})
    assert client.meta.config.connect_timeout == 10


def _service_account_json(email: str = "sa@p.iam.gserviceaccount.com") -> str:
    """A throw-away service account key (never used against Google: nothing here sends a request to it)."""
    import json

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    return json.dumps({
        "type": "service_account", "project_id": "p", "private_key_id": "k", "private_key": pem,
        "client_email": email, "client_id": "1", "token_uri": "https://oauth2.googleapis.com/token",
    })


BASE = {
    "model": "m", "apiKey": "k", "endpoint": "https://gateway.example.com/v1", "deploymentName": "d",
    "apiVersion": "2024-02-01", "region": "us-east-1", "awsAccessKeyId": "x", "awsAccessSecretKey": "y",
    "project": "p", "location": "us-central1",
}

# Embedding providers whose models make no call through the indexing LLM gateway, so there is no breaker to feed:
# local models, an embedding type the factory does not support, and SDKs that speak their own HTTP (not httpx).
# They are named here so that a provider that gains an httpx client, or a new one, is a decision and not a gap.
EMBEDDINGS_NOT_ON_THE_GATEWAY = {
    "default": "local model",
    "huggingFace": "local model",
    "sentenceTransformers": "local model",
    "anthropic": "the factory does not support Anthropic embeddings",
    "jinaAI": "the SDK uses its own HTTP client",
    "voyage": "the SDK uses its own HTTP client",
}


def _observable(model: object) -> bool:
    from app.services.llm_gateway import wire

    clients = wire.find_http_clients(model)
    if clients:
        return all(wire.has_wire_hook(c) for c in clients)
    boto = wire._boto_clients(model)
    return bool(boto) and all(getattr(c, wire.BOTO_MARKER, False) for c in boto)


@pytest.mark.parametrize("provider", sorted(p.value for p in LLMProvider))
def test_every_chat_provider_builds_with_the_wire_hooks(provider: str) -> None:
    """Every ``LLMProvider`` constructs through its real factory path with the hooks installed. A model that cannot be
    observed is refused at construction (``WireHookError``), so a new wrapper cannot slip through without a hook."""
    from app.utils.aimodels import get_generator_model

    configuration = {**BASE, "serviceAccountJson": _service_account_json()}
    try:
        model = get_generator_model(provider, {"configuration": configuration, "isDefault": True}, "m")
    except ValueError as exc:
        pytest.fail(f"{provider} cannot be built offline: {exc}")  # extend BASE, do not skip: a silent skip hides a gap
    assert _observable(model), f"{provider}: {type(model).__name__} has an unobserved client"


@pytest.mark.parametrize("provider", sorted(p.value for p in EmbeddingProvider))
def test_every_embedding_provider_builds_with_the_wire_hooks(provider: str) -> None:
    """Every embedding provider constructs through its real factory path and, when its SDK uses httpx or botocore, carries the
    hooks. The few that make no call through the LLM gateway are named in ``EMBEDDINGS_NOT_ON_THE_GATEWAY``."""
    from app.utils.aimodels import get_embedding_model

    configuration = {**BASE, "serviceAccountJson": _service_account_json()}
    try:
        model = get_embedding_model(provider, {"configuration": configuration, "isDefault": True}, "m")
    except Exception as exc:
        if provider in EMBEDDINGS_NOT_ON_THE_GATEWAY:
            assert EMBEDDINGS_NOT_ON_THE_GATEWAY[provider]
            return
        pytest.fail(f"{provider} cannot be built offline: {type(exc).__name__}: {exc}")
    if provider in EMBEDDINGS_NOT_ON_THE_GATEWAY:
        return
    assert _observable(model), f"{provider}: {type(model).__name__} has an unobserved client"


def test_a_model_that_cannot_be_observed_is_refused_only_on_paid_background_work() -> None:
    """An unhookable chat model is refused when built for indexing-style work, and elsewhere (a person's
    chat, an admin check, no call context) it is allowed, recorded, and reported by ``hook_status`` for ``/health``."""
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    from app.services.llm_gateway import wire
    from app.services.llm_gateway.wire import WireHookError, hook_status
    from app.services.model_identity.call_context import (
        ModelCallContext,
        bind_call_context,
    )
    from app.services.model_identity.principal import service_principal
    from app.services.model_identity.workload import Workload

    wire._unhooked.clear()
    background = ModelCallContext(principal=service_principal(), workload=Workload.INDEXING)
    with bind_call_context(background), pytest.raises(WireHookError):
        instrument_model(FakeListChatModel(responses=["x"]), "openAI", None)
    chat = ModelCallContext(principal=service_principal(), workload=Workload.CHAT)
    with bind_call_context(chat):
        assert instrument_model(FakeListChatModel(responses=["x"]), "openAI", None) is not None
    assert instrument_model(FakeListChatModel(responses=["x"]), "huggingFace", None) is not None  # local: nothing to observe
    status = hook_status()
    assert status["warning"] is True and status["unhooked"] == [{"provider": "openAI", "model": "FakeListChatModel", "count": 1}]
    wire._unhooked.clear()


# Providers that take no credential: the endpoint, already in the provider key, is the identity.
NO_CREDENTIAL = {"ollama": "Ollama takes no credential"}


def _build_with_credential(provider: str, tag: str) -> object:
    from app.utils.aimodels import get_generator_model

    configuration = {
        **BASE, "apiKey": f"key-{tag}", "awsAccessKeyId": f"AKIA{tag}", "awsAccessSecretKey": f"secret-{tag}",
        "serviceAccountJson": _service_account_json(f"sa-{tag}@p.iam.gserviceaccount.com"),
    }
    return get_generator_model(provider, {"configuration": configuration, "isDefault": True}, "m")


@pytest.mark.parametrize("provider", sorted(p.value for p in LLMProvider))
def test_every_provider_mixes_its_credential_into_the_breaker_key(provider: str) -> None:
    """Two credentials on one model and endpoint get two breakers, so one credential's quota or denial does not pause the
    other's jobs; the same credential built twice gets the same breaker. Bedrock's credentials live in the botocore
    client's signer and Vertex AI's in a google-auth credentials object, not in string attributes."""
    a, again, b = (_build_with_credential(provider, tag) for tag in ("A", "A", "B"))
    pk_a, pk_again, pk_b = (provider_pk(provider_key(m), credential_of(m)) for m in (a, again, b))
    assert pk_a == pk_again, f"{provider}: one credential gives two breakers"
    if provider in NO_CREDENTIAL:
        assert credential_of(a) == "", f"{provider}: {NO_CREDENTIAL[provider]}, yet a credential was found"
        return
    assert pk_a != pk_b, f"{provider}: two credentials share one breaker"


def test_a_bedrock_role_is_its_identity_without_refreshing_the_credentials() -> None:
    """Credentials from a role refresh themselves, and reading their keys can call STS. The role is the identity: two
    roles get two breakers, and building the key never triggers a refresh."""
    import boto3
    from botocore.credentials import RefreshableCredentials

    refreshes: list[str] = []

    class _RoleFetcher:
        def __init__(self, role_arn: str) -> None:
            self._role_arn = role_arn

        def fetch_credentials(self) -> dict[str, str]:
            refreshes.append(self._role_arn)
            return {"access_key": "AKIA", "secret_key": "s", "token": "t", "expiry_time": "2000-01-01T00:00:00Z"}

    def client_for(role_arn: str) -> object:
        client = boto3.Session(aws_access_key_id="x", aws_secret_access_key="y", region_name="us-east-1").client(
            "bedrock-runtime"
        )
        fetcher = _RoleFetcher(role_arn)
        client._request_signer._credentials = RefreshableCredentials.create_from_metadata(
            fetcher.fetch_credentials(), refresh_using=fetcher.fetch_credentials, method="assume-role"
        )
        refreshes.clear()
        return client

    one, other = client_for("arn:aws:iam::1:role/a"), client_for("arn:aws:iam::1:role/b")

    class _Model:
        def __init__(self, client: object) -> None:
            self.client = client

    assert credential_of(_Model(one)) != credential_of(_Model(other))
    assert credential_of(_Model(one)) == credential_of(_Model(one))
    assert refreshes == []
