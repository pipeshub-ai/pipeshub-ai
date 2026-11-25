"""One retry layer: every model an indexing call site builds repeats nothing on its own.

A paid indexing request that fails is repeated by the gateways alone (``LLMGateway``, ``EmbeddingGateway``). An SDK client
that also retried would multiply the sends of one logical call (3 gateway attempts x 3 SDK attempts = 9). The test builds
each model through the code path of its site, with a configured provider, and reads the retry count off the SDK client
objects (``tests/support/sdk_retries.py``). Nothing is sent.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path
from typing import Any

import pytest

from app.agent_loop_lib.transport.anthropic import AnthropicTransport
from app.agent_loop_lib.transport.azure_openai import AzureOpenAITransport
from app.agent_loop_lib.transport.gemini import GeminiTransport
from app.agent_loop_lib.transport.openai import OpenAITransport
from app.config.constants.service import config_node_constants
from app.utils import llm as llm_module
from app.utils.aimodels import (
    INDEXING_SDK_RETRIES,
    EmbeddingProvider,
    LLMProvider,
    get_embedding_model,
    get_generator_model,
)
from tests.support.app_tree import may_contain_name, sources
from tests.support.in_memory_config import InMemoryConfigService
from tests.support.provider_configs import BASE, service_account_json
from tests.support.sdk_retries import sdk_retries

APP = Path(__file__).resolve().parents[3] / "app"
CONFIGURATION = {**BASE, "serviceAccountJson": service_account_json()}

# Embedding providers with no remote request of their own to repeat: the local embedding server (its client retries,
# it is not paid) and a type the factory does not build.
NOT_AN_SDK_RETRY_SITE = {
    "default": "the local embedding server keeps its own client retry",
    "huggingFace": "the local embedding server keeps its own client retry",
    "sentenceTransformers": "the local embedding server keeps its own client retry",
    "anthropic": "the factory does not support Anthropic embeddings",
}


@pytest.fixture(autouse=True)
def _no_aws_lookup_leaves_the_process(monkeypatch: pytest.MonkeyPatch) -> Any:  # noqa: ANN401
    """Building a Bedrock client must not look credentials up: no instance-metadata request (the link-local address), no
    STS call. Static dummy credentials and a disabled metadata service are set, and any request botocore would send is
    recorded and refused; the test fails if one is attempted."""
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIADUMMY")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "dummy")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    attempted: list[str] = []

    def refuse(self: Any, request: Any) -> Any:  # noqa: ANN401
        attempted.append(str(getattr(request, "url", request)))
        raise ConnectionError("a unit test must not send a request")

    from botocore.httpsession import URLLib3Session

    monkeypatch.setattr(URLLib3Session, "send", refuse)
    yield
    assert not attempted, f"building models sent requests: {attempted}"


@pytest.fixture(autouse=True)
def _no_tokenizer_download(monkeypatch: pytest.MonkeyPatch) -> None:
    """Building Mistral embeddings downloads a tokenizer from Hugging Face unless one is given; the unit lane refuses
    the dial (tests/support/no_network.py). Refusing it here takes the client's own offline fallback (a len() tokenizer),
    which does not change the SDK client whose retries the test reads."""
    from tokenizers import Tokenizer

    def offline(*_args: Any, **_kwargs: Any) -> Any:  # noqa: ANN401
        raise OSError("no tokenizer download in a unit test")

    monkeypatch.setattr(Tokenizer, "from_pretrained", offline)


def test_the_indexing_constant_is_zero() -> None:
    assert INDEXING_SDK_RETRIES == 0


@pytest.mark.parametrize("provider", sorted(p.value for p in LLMProvider))
def test_every_chat_provider_has_sdk_retries_off(provider: str) -> None:
    """Built the way every indexing site builds it (``sdk_retries=INDEXING_SDK_RETRIES``), no provider's SDK repeats a
    request. The same provider built without the argument keeps the SDK's own retries (the measure is not a constant 0)."""
    config = {"configuration": CONFIGURATION, "isDefault": True}
    model = get_generator_model(provider, config, "m", sdk_retries=INDEXING_SDK_RETRIES)
    assert sdk_retries(model) == 0, f"{provider}: {type(model).__name__} repeats requests on its own"
    if provider not in {"ollama"}:
        default = get_generator_model(provider, config, "m")
        assert sdk_retries(default) > 0, f"{provider}: the default is expected to retry, or the measure reads nothing"


@pytest.mark.parametrize("provider", sorted(p.value for p in EmbeddingProvider if p.value not in NOT_AN_SDK_RETRY_SITE))
def test_every_embedding_provider_has_sdk_retries_off(provider: str) -> None:
    config = {"configuration": CONFIGURATION, "isDefault": True}
    model = get_embedding_model(provider, config, "m", sdk_retries=INDEXING_SDK_RETRIES)
    assert sdk_retries(model) == 0, f"{provider}: {type(model).__name__} repeats requests on its own"


# ---- the sites: the model comes out of each site's own code path --------------------------------------------------------


def _config_service(provider: str, *, multimodal: bool = True, role: bool = False, default: bool = True) -> Any:  # noqa: ANN401
    entry = {
        "provider": provider, "modelKey": "k1", "isDefault": default, "isMultimodal": multimodal,
        "configuration": {**CONFIGURATION, "model": "m"},
    }
    models: dict[str, Any] = {"llm": [entry]}
    if role:
        models["modelRoles"] = {"indexing": {"modelType": "llm", "modelKey": "k1"}}
    service = InMemoryConfigService()
    service.values[config_node_constants.AI_MODELS.value] = models
    return service


@pytest.fixture
def built(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """The models the real factory builds while a site runs (the factory is wrapped, not replaced)."""
    models: list[Any] = []
    real = llm_module.get_generator_model

    def record(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        model = real(*args, **kwargs)
        models.append(model)
        return model

    monkeypatch.setattr(llm_module, "get_generator_model", record)
    # The region-pinned builder (``llm.build_chat_model``, which VLM OCR goes through) reaches the factory through
    # ``get_generator_model_async``, which calls it by its name in ``app.utils.aimodels``.
    from app.utils import aimodels

    monkeypatch.setattr(aimodels, "get_generator_model", record)
    return models


@pytest.mark.parametrize("provider", ["openAI", "anthropic", "azureOpenAI", "groq", "mistral", "gemini", "bedrock"])
@pytest.mark.parametrize("selection", ["role", "default", "first_multimodal"])
async def test_vlm_ocr_builds_its_model_without_sdk_retries(built: list[Any], provider: str, selection: str) -> None:
    """VLM OCR has its own factory call (``_create_llm_from_config``) and three ways to choose the config; each one."""
    from app.modules.parsers.pdf.vlm_ocr_strategy import VLMOCRStrategy

    if selection == "role":
        service = _config_service(provider, role=True)
    elif selection == "default":
        service = _config_service(provider)
    else:
        service = _config_service(provider, default=False)
    strategy = VLMOCRStrategy(logging.getLogger("t"), service)
    model = await strategy._get_multimodal_llm()
    assert model in built, "the site did not build the model through the factory"
    assert sdk_retries(model) == 0, f"VLM OCR ({selection}) built {type(model).__name__} with SDK retries"


@pytest.mark.parametrize("provider", ["openAI", "anthropic", "groq"])
async def test_image_description_builds_its_model_without_sdk_retries(built: list[Any], provider: str) -> None:
    from app.modules.transformers.image_description import ImageDescriber

    model = await ImageDescriber(logging.getLogger("t"), _config_service(provider))._vision_model()
    assert model is not None and sdk_retries(model) == 0


@pytest.mark.parametrize("provider", ["openAI", "anthropic", "groq"])
async def test_table_summary_builds_its_model_without_sdk_retries(built: list[Any], provider: str) -> None:
    from app.utils.table_enrichment import indexing_llm_or_none

    model = await indexing_llm_or_none(_config_service(provider), logging.getLogger("t"))
    assert model is not None and sdk_retries(model) == 0


@pytest.mark.parametrize("provider", ["openAI", "anthropic", "groq"])
async def test_entity_resolution_builds_its_model_without_sdk_retries(built: list[Any], provider: str) -> None:
    from app.modules.entity_resolution.resolver import EntityResolver

    resolver = EntityResolver(logging.getLogger("t"), _config_service(provider), graph_provider=None)  # type: ignore[arg-type]
    model = await resolver._load_llm("org")
    assert sdk_retries(model) == 0


@pytest.mark.parametrize("provider", ["openAI", "anthropic", "groq"])
async def test_named_entities_build_their_model_without_sdk_retries(built: list[Any], provider: str) -> None:
    """The single-call strategy uses the model; the agent strategy wraps the same one in a transport."""
    from app.modules.named_entities.extractor import indexing_llm

    llm, _transport, _provider, _model = await indexing_llm(_config_service(provider))
    assert sdk_retries(llm) == 0


@pytest.mark.parametrize("provider", ["openAI", "anthropic", "groq"])
async def test_the_classify_and_extraction_server_path_builds_its_model_without_sdk_retries(
    built: list[Any], provider: str
) -> None:
    """Document classification (in the indexing process and in the Extraction service, one class) resolves its model with
    the indexing role through ``get_llm_for_role``; its call carries the argument (asserted for every site below)."""
    model, _config = await llm_module.get_llm_for_role(
        _config_service(provider), "indexing", reasoning_effort="low", sdk_retries=INDEXING_SDK_RETRIES,
    )
    assert sdk_retries(model) == 0


# ---- every call site in the source passes the argument -------------------------------------------------------------------

INDEXING_ROLE = "indexing"
# Calls that resolve a role for a person's request or an admin check, not for a record.
NOT_INDEXING = {
    "app/agents/actions/util/parse_file.py": "an agent action run for a person's request",
    "app/utils/llm.py": "the definition of the helpers",
}


ROLE_RESOLVERS = ("get_llm_for_role", "_get_llm_for_role")


def _role_arg(call: ast.Call) -> ast.expr | None:
    names = set(ROLE_RESOLVERS)
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    if name not in names:
        return None
    index = 1 if name == "get_llm_for_role" else 0
    if len(call.args) > index:
        return call.args[index]
    return next((k.value for k in call.keywords if k.arg == "role"), None)


def test_every_indexing_role_call_site_passes_sdk_retries() -> None:
    """A source scan of every call that resolves the ``indexing`` role (or the entity-resolution role): each passes
    ``sdk_retries=INDEXING_SDK_RETRIES``. A new site that forgets it fails here, not in production."""
    offenders: list[str] = []
    seen = 0
    # ``sources`` is the tree's one cached read and ``may_contain_name`` skips every file that holds neither function name
    # (a call can only match by its name), so only the few files that resolve a role are parsed; the rest of the 2,250
    # files were parsed for nothing (30 s on an idle core, over the 60 s timeout on a loaded runner).
    for path, text in sources(APP):
        relative = path.relative_to(APP.parent).as_posix()
        if relative in NOT_INDEXING or not may_contain_name(text, *ROLE_RESOLVERS):
            continue
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            role = _role_arg(node)
            if role is None:
                continue
            is_indexing = (isinstance(role, ast.Constant) and role.value == INDEXING_ROLE) or (
                isinstance(role, ast.Name) and role.id == "LLM_ROLE"
            )
            if not is_indexing:
                continue
            seen += 1
            passed = {k.arg: k.value for k in node.keywords}
            value = passed.get("sdk_retries")
            ok = isinstance(value, ast.Name) and value.id in {"INDEXING_SDK_RETRIES", "sdk_retries"}
            if not ok:
                offenders.append(f"{relative}:{node.lineno}")
    assert seen >= 12, f"the scan found only {seen} indexing call sites; it has stopped seeing them"
    assert not offenders, "pass sdk_retries=INDEXING_SDK_RETRIES at these indexing call sites:\n" + "\n".join(offenders)


# ---- the agent transports ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("provider", "transport"),
    [("anthropic", AnthropicTransport), ("openAI", OpenAITransport), ("azureOpenAI", AzureOpenAITransport),
     ("gemini", GeminiTransport)],
)
def test_the_agent_transports_send_each_request_once(provider: str, transport: Any) -> None:  # noqa: ANN401
    """An indexing model handed to a direct agent transport keeps the zero: the transport's SDK client makes one attempt."""
    config = {"configuration": CONFIGURATION, "isDefault": True}
    default = transport.from_langchain_model(get_generator_model(provider, config, "m"), "m")
    model = get_generator_model(provider, config, "m", sdk_retries=INDEXING_SDK_RETRIES)
    built = transport.from_langchain_model(model, "m")
    if provider == "gemini":
        options = built._client._api_client._http_options
        assert options.retry_options is not None and options.retry_options.attempts == 1
        assert default._client._api_client._http_options.retry_options is None, "only the indexing model is single-attempt"
    else:
        assert built._client.max_retries == 0
        assert default._client.max_retries > 0


# ---- multimodal embeddings and the Extraction client ---------------------------------------------------------------------


def test_the_multimodal_embedding_providers_are_built_without_sdk_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """The three providers that hold an SDK of their own (Cohere, Bedrock, Gemini) are told to retry nothing, through the
    config the vector store builds for them."""
    import sys
    import types
    from unittest.mock import MagicMock

    from app.services.embeddings.multimodal.config import MultimodalProviderConfig
    from app.services.embeddings.multimodal.factory import MultimodalEmbeddingFactory
    from tests.support.vectorstore_world import make_vectorstore

    # The config the vector store builds for its multimodal provider (not its source text): SDK retries are off.
    assert make_vectorstore()._multimodal_provider_config().sdk_retries == INDEXING_SDK_RETRIES

    def config(provider: str) -> MultimodalProviderConfig:
        return MultimodalProviderConfig(
            provider=provider, api_key="k", model_name="m", region_name="us-east-1", aws_access_key_id="a",
            aws_secret_access_key="s", sdk_retries=INDEXING_SDK_RETRIES, normalize_fn=lambda ref: ref,
        )

    # Cohere: every request carries the max_retries request option.
    cohere_client = MagicMock()
    cohere_client.embed.return_value = MagicMock(embeddings=MagicMock(float=[[0.1]]))
    fake_cohere = types.SimpleNamespace(ClientV2=lambda api_key: cohere_client)
    monkeypatch.setitem(sys.modules, "cohere", fake_cohere)
    import asyncio

    provider = MultimodalEmbeddingFactory.create(config("cohere"))
    results = asyncio.run(provider.embed_images(["data:image/png;base64,AAAA"]))  # type: ignore[union-attr]
    assert results[0].embedding == [0.1]
    assert cohere_client.embed.call_args.kwargs["request_options"] == {"max_retries": 0}

    # Bedrock: the client is built with one total attempt.
    captured: dict[str, Any] = {}

    def fake_boto_client(**kwargs: Any) -> Any:  # noqa: ANN401
        captured.update(kwargs)
        return MagicMock()

    monkeypatch.setattr("boto3.client", fake_boto_client)
    provider = MultimodalEmbeddingFactory.create(config("bedrock"))
    asyncio.run(provider.embed_images([]))  # type: ignore[union-attr]
    assert captured["config"].retries == {"mode": "standard", "total_max_attempts": 1}

    # Gemini: google-genai counts attempts and retries dropped connections unless given retry options.
    from google import genai

    seen: dict[str, Any] = {}

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:  # noqa: ANN401
            seen.update(kwargs)

    monkeypatch.setattr(genai, "Client", FakeClient)
    provider = MultimodalEmbeddingFactory.create(config("gemini"))
    asyncio.run(provider.embed_images([]))  # type: ignore[union-attr]
    assert seen["http_options"].retry_options.attempts == 1


def test_the_extraction_client_sends_each_request_once_until_the_service_single_flights() -> None:
    """A classify call the service ran and the client then lost is paid again by a retry; one attempt until the service
    attaches a repeat to the running computation."""
    from app.services.extraction.client import ExtractionClient

    assert ExtractionClient(service_url="http://extraction:8093").max_retries == 1


def test_the_google_embedding_reader_sees_a_retry_policy_not_only_the_transport() -> None:
    """A control: the reader reports the retries a Google embeddings client is configured with, so a regression that turns
    them on is seen (httpx transport alone does not mean no retries)."""
    from google.genai import types

    from app.services.llm_gateway import wire

    config = {"configuration": CONFIGURATION, "isDefault": True}
    model = get_embedding_model("gemini", config, "m", sdk_retries=INDEXING_SDK_RETRIES)
    assert sdk_retries(model) == 0
    for api in wire._genai_api_clients(model):
        api._http_options.retry_options = types.HttpRetryOptions(attempts=4)
    assert sdk_retries(model) == 3


async def test_the_gemini_agent_transport_sends_through_httpx_when_single_attempt() -> None:
    """With aiohttp installed google-genai repeats a request after a lost reply by itself; a single-attempt transport is
    routed through httpx, which does not."""
    from app.services.llm_gateway import wire

    config = {"configuration": CONFIGURATION, "isDefault": True}
    single = GeminiTransport.from_langchain_model(get_generator_model("gemini", config, "m", sdk_retries=0), "m")
    assert all(not api._use_aiohttp() for api in wire._genai_api_clients(single._client))


def test_the_scan_reads_the_role_keyword_too(tmp_path: Path) -> None:
    """``get_llm_for_role(config, role="indexing")`` is as much an indexing site as the positional form."""
    source = (
        "async def f(config):\n"
        "    return await get_llm_for_role(config, role=\"indexing\", reasoning_effort=\"low\")\n"
    )
    path = tmp_path / "site.py"
    path.write_text(source)
    call = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call) and _role_arg(n) is not None)
    role = _role_arg(call)
    assert isinstance(role, ast.Constant) and role.value == INDEXING_ROLE
