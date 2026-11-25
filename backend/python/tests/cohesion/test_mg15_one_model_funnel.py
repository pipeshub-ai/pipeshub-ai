"""MG-15 (C3.68) guard: outside the agent harness, no model or embedding client is built around the placement check.

Every model PipesHub calls for an org outside a harness run is built by ``app/utils/llm.py``, which asks the org's
region pin first (``require_placement``). So a reference to a model builder, or a vendor client constructed,
anywhere else under ``app/`` is a path around the placement check. The exceptions, each named here with its reason:

- ``app/utils/aimodels.py`` and the factories the funnel calls: where the builders are defined;
- ``app/harness/...``: the harness's own funnels, guarded by ``test_mg15_one_placement_funnel.py``;
- ``app/api/routes/health.py``: an admin's test of a model they are configuring, with a fixed probe and no org
  content (a pin that stopped it would leave the admin unable to check a model before choosing it);
- the in-cluster embedding server (``app/embedding_main.py``) and the local reranker: the model runs in this process.
"""

from __future__ import annotations

import ast
from typing import Final

from tests.cohesion.support import PYTHON_ROOT
from tests.edition_overlay import extended

#: the builders a model client comes from.
BUILDERS: Final = frozenset(
    {
        "get_generator_model",
        "get_generator_model_async",
        "get_embedding_model",
        "get_default_embedding_model",
        "get_image_generation_model",
        "get_tts_model",
        "get_stt_model",
        "get_decision_model",
        "get_embedding_server_embeddings",
        "MultimodalEmbeddingFactory",
    }
)
#: vendor clients that send a call to a model.
VENDOR_CLIENTS: Final = frozenset(
    {
        "ChatOpenAI", "AzureChatOpenAI", "ChatAnthropic", "ChatBedrock", "ChatBedrockConverse", "ChatVertexAI",
        "ChatGoogleGenerativeAI", "ChatOllama", "ChatMistralAI", "ChatGroq", "ChatCohere", "ChatFireworks",
        "OpenAIEmbeddings", "AzureOpenAIEmbeddings", "CohereEmbeddings", "BedrockEmbeddings", "VertexAIEmbeddings",
        "GoogleGenerativeAIEmbeddings", "OllamaEmbeddings", "MistralAIEmbeddings", "JinaEmbeddings", "OpenAI",
        "AsyncOpenAI", "AzureOpenAI", "AsyncAzureOpenAI", "Anthropic", "AsyncAnthropic",
    }
)
#: where a builder may be referenced or a vendor client built.
ALLOWED: Final = (
    "app/utils/llm.py",
    "app/utils/aimodels.py",
    "app/utils/custom_chat_model.py",
    "app/utils/custom_embeddings.py",
    "app/utils/embedding_server_client.py",
    "app/services/ai_models/decision/",
    "app/services/embeddings/multimodal/",
    "app/harness/",
    "app/api/routes/health.py",
    "app/embedding_main.py",
)


def _allowed() -> tuple[str, ...]:
    """``ALLOWED``, with an edition overlay's ``"<path>": reason`` entries (``tests/edition_overlay.py``)."""
    return ALLOWED + tuple(extended(dict.fromkeys(ALLOWED, ""), "model_funnel", "allowed").keys() - set(ALLOWED))


def _found() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    allowed = _allowed()
    for path in sorted((PYTHON_ROOT / "app").rglob("*.py")):
        where = str(path.relative_to(PYTHON_ROOT))
        if where.startswith(allowed):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"), filename=where)):
            if isinstance(node, (ast.Name, ast.Attribute)):
                name = node.id if isinstance(node, ast.Name) else node.attr
                if name in BUILDERS:
                    found.setdefault(where, []).append(f"{name}:{node.lineno}")
            if isinstance(node, ast.Call):
                func = node.func
                called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                if called in VENDOR_CLIENTS:
                    found.setdefault(where, []).append(f"{called}():{node.lineno}")
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name in BUILDERS:
                        found.setdefault(where, []).append(f"import {alias.name}:{node.lineno}")
    return found


def test_mg15_no_model_or_embedding_client_is_built_outside_the_placement_funnel() -> None:
    assert _found() == {}, "a model client built around the placement check (MG-15): use app/utils/llm.py"


def test_mg15_the_funnel_asks_the_pin_before_every_builder_it_calls() -> None:
    """Each funnel function in ``app/utils/llm.py`` that calls a builder awaits ``require_placement`` (or the
    embedding form) first. A synchronous one cannot, so none may name a builder (round 12: WP-55's image adapter
    did, and the image tool reached the provider without asking the pin)."""
    tree = ast.parse((PYTHON_ROOT / "app" / "utils" / "llm.py").read_text())
    unchecked = []
    for function in ast.walk(tree):
        if not isinstance(function, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        names = [
            n.id if isinstance(n, ast.Name) else n.attr
            for n in ast.walk(function)
            if isinstance(n, (ast.Name, ast.Attribute))
        ]
        builds = [name for name in names if name in BUILDERS - {"MultimodalEmbeddingFactory"}]
        checked = {"require_placement", "require_embeddings_placement"} & set(names)
        if builds and not checked:
            unchecked.append(function.name)
    assert unchecked == [], f"funnel functions that build a model without asking the pin: {unchecked}"
