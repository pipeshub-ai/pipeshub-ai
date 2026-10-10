"""One discovery strategy per configured provider.

Strategies return raw rows. Classification (catalog, then ID rules) happens
after, so a provider that already knows the capability is not second-guessed.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from typing import Any
from urllib.parse import urlencode

from app.services.ai_models.discovery.http import MAX_MODELS, MAX_PAGES, DiscoveryHttp, bearer
from app.services.ai_models.discovery.types import DiscoveryErrorCode, DiscoveryHttpError, RawModel

_SHOW_TTL_S = 300
_show_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_show_sem: asyncio.Semaphore | None = None
_hf_calls: deque[float] = deque()
_HF_WINDOW_S = 300
_HF_MAX_CALLS = 500


def _show_semaphore() -> asyncio.Semaphore:
    global _show_sem
    if _show_sem is None:
        _show_sem = asyncio.Semaphore(4)
    return _show_sem


class DiscoveryStrategy:
    mode = "list"
    required_fields: tuple[str, ...] = ()

    async def list_models(
        self,
        configuration: dict[str, Any],
        http: DiscoveryHttp,
        query: str | None,
    ) -> tuple[list[RawModel], list[str]]:
        raise NotImplementedError


def _openai_v1_base(base: str) -> str:
    """Base URL the OpenAI models list is served under.

    A configured endpoint of ``https://host`` and ``https://host/v1`` both
    expose models at ``/v1/models``.
    """
    if base.endswith("/v1"):
        return base
    return f"{base}/v1"


def _endpoint(configuration: dict[str, Any], default: str | None = None) -> str:
    raw = str(configuration.get("endpoint") or "").strip().rstrip("/")
    if raw:
        return raw
    if default:
        return default.rstrip("/")
    raise DiscoveryHttpError(
        DiscoveryErrorCode.INVALID_RESPONSE, "An endpoint is required to list models."
    )


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("data", "models", "items"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
    raise DiscoveryHttpError(
        DiscoveryErrorCode.INVALID_RESPONSE, "The provider returned an unexpected model list."
    )


def _cap(rows: list[RawModel], warnings: list[str]) -> tuple[list[RawModel], list[str]]:
    if len(rows) > MAX_MODELS:
        warnings.append(f"Listing stopped after {MAX_MODELS} models.")
        return rows[:MAX_MODELS], warnings
    return rows, warnings


def _static(pairs: list[tuple[str, list[str]]], *, deprecated: set[str] | None = None) -> list[RawModel]:
    retired = deprecated or set()
    return [
        RawModel(id=model_id, capabilities=capabilities, deprecated=model_id in retired)
        for model_id, capabilities in pairs
    ]


class _OpenAIList(DiscoveryStrategy):
    """GET {base}/models. Capabilities are left for the catalog and ID rules."""

    def __init__(self, default_base: str, *, required: tuple[str, ...] = ("apiKey",)) -> None:
        self._default_base = default_base
        self.required_fields = required

    async def list_models(self, configuration, http, query):
        base = _openai_v1_base(_endpoint(configuration, self._default_base))
        headers = bearer(configuration.get("apiKey"))
        payload = await http.get_json(f"{base}/models", headers)
        warnings: list[str] = []
        models: list[RawModel] = []
        pages = 0
        while True:
            pages += 1
            for row in _rows(payload):
                model_id = str(row.get("id") or "").strip()
                if not model_id:
                    continue
                shutdown = row.get("shutdown_date")
                context = row.get("context_window") or row.get("max_model_len")
                task = row.get("task")
                capabilities = None
                if task in {"automatic-speech-recognition", "transcription"}:
                    capabilities = ["stt"]
                elif task in {"text-to-speech", "speech"}:
                    capabilities = ["tts"]
                models.append(
                    RawModel(
                        id=model_id,
                        capabilities=capabilities,
                        context_length=context if isinstance(context, int) else None,
                        deprecated=bool(shutdown) or row.get("active") is False,
                    )
                )
            if pages >= MAX_PAGES or len(models) >= MAX_MODELS:
                if pages >= MAX_PAGES:
                    warnings.append("Listing stopped after the page limit.")
                break
            if not (isinstance(payload, dict) and payload.get("has_more") and payload.get("last_id")):
                break
            payload = await http.get_json(
                f"{base}/models?after={payload['last_id']}", headers
            )
        return _cap(models, warnings)


class AnthropicStrategy(DiscoveryStrategy):
    required_fields = ("apiKey",)

    async def list_models(self, configuration, http, query):
        headers = {
            "x-api-key": str(configuration.get("apiKey") or ""),
            "anthropic-version": "2023-06-01",
        }
        after = ""
        models: list[RawModel] = []
        warnings: list[str] = []
        for page in range(MAX_PAGES):
            url = "https://api.anthropic.com/v1/models?limit=1000"
            if after:
                url += f"&after_id={after}"
            payload = await http.get_json(url, headers)
            for row in _rows(payload):
                model_id = str(row.get("id") or "").strip()
                if not model_id:
                    continue
                caps_obj = row.get("capabilities") if isinstance(row.get("capabilities"), dict) else {}
                models.append(
                    RawModel(
                        id=model_id,
                        display_name=row.get("display_name"),
                        capabilities=["llm"],
                        context_length=row.get("max_input_tokens")
                        if isinstance(row.get("max_input_tokens"), int)
                        else None,
                        is_multimodal=bool(caps_obj.get("image_input")) if "image_input" in caps_obj else None,
                        is_reasoning=bool(caps_obj.get("thinking")) if "thinking" in caps_obj else None,
                        supports_tools=True,
                        deprecated=bool(row.get("deprecated_at")) or row.get("lifecycle") == "deprecated",
                    )
                )
            if not (isinstance(payload, dict) and payload.get("has_more")):
                break
            after = str(payload.get("last_id") or "")
            if not after:
                break
            if page == MAX_PAGES - 1:
                warnings.append("Listing stopped after the page limit.")
        return _cap(models, warnings)


class GeminiStrategy(DiscoveryStrategy):
    required_fields = ("apiKey",)

    async def list_models(self, configuration, http, query):
        headers = {"x-goog-api-key": str(configuration.get("apiKey") or "")}
        token = ""
        models: list[RawModel] = []
        warnings: list[str] = []
        for page in range(MAX_PAGES):
            url = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000"
            if token:
                url += f"&pageToken={token}"
            payload = await http.get_json(url, headers)
            for row in _rows(payload):
                name = str(row.get("name") or "")
                model_id = name.removeprefix("models/").strip()
                if not model_id:
                    continue
                methods = set(row.get("supportedGenerationMethods") or [])
                if "predictLongRunning" in methods or methods == {"bidiGenerateContent"}:
                    continue
                lowered = model_id.lower()
                if lowered.endswith("-tts") or "-tts" in lowered:
                    capabilities = ["tts"]
                elif lowered.startswith("imagen") or "-image" in lowered:
                    capabilities = ["imageGeneration"]
                elif "embedContent" in methods:
                    capabilities = ["embedding"]
                elif "predict" in methods:
                    capabilities = ["imageGeneration"]
                elif "generateContent" in methods:
                    capabilities = ["llm"]
                else:
                    continue
                thinking = row.get("thinking")
                models.append(
                    RawModel(
                        id=model_id,
                        display_name=row.get("displayName"),
                        capabilities=capabilities,
                        context_length=row.get("inputTokenLimit")
                        if isinstance(row.get("inputTokenLimit"), int)
                        else None,
                        is_reasoning=bool(thinking) if thinking is not None else None,
                    )
                )
            token = str(payload.get("nextPageToken") or "") if isinstance(payload, dict) else ""
            if not token:
                break
            if page == MAX_PAGES - 1:
                warnings.append("Listing stopped after the page limit.")
        return _cap(models, warnings)


class XAIStrategy(DiscoveryStrategy):
    required_fields = ("apiKey",)
    _ENDPOINTS = (
        ("https://api.x.ai/v1/language-models", ["llm"]),
        ("https://api.x.ai/v1/image-generation-models", ["imageGeneration"]),
        ("https://api.x.ai/v1/embedding-models", ["embedding"]),
    )

    async def list_models(self, configuration, http, query):
        headers = bearer(configuration.get("apiKey"))
        models: list[RawModel] = []
        for url, capabilities in self._ENDPOINTS:
            payload = await http.get_json(url, headers)
            for row in _rows(payload):
                model_id = str(row.get("id") or "").strip()
                if not model_id:
                    continue
                modalities = row.get("input_modalities") or row.get("modalities") or []
                vision = None
                if isinstance(modalities, list) and modalities:
                    vision = "image" in modalities or "vision" in modalities
                models.append(
                    RawModel(id=model_id, capabilities=list(capabilities), is_multimodal=vision)
                )
        return _cap(models, [])


class OpenRouterStrategy(DiscoveryStrategy):
    required_fields = ("apiKey",)

    async def list_models(self, configuration, http, query):
        # The default list is text-only and hides embedding, speech, and transcription.
        url = "https://openrouter.ai/api/v1/models?output_modalities=all"
        payload = await http.get_json(url, bearer(configuration.get("apiKey")))
        models: list[RawModel] = []
        for row in _rows(payload):
            model_id = str(row.get("id") or "").strip()
            if not model_id:
                continue
            architecture = row.get("architecture") if isinstance(row.get("architecture"), dict) else {}
            output = set(architecture.get("output_modalities") or [])
            incoming = set(architecture.get("input_modalities") or [])
            if "embeddings" in output:
                capabilities = ["embedding"]
            elif "image" in output:
                capabilities = ["imageGeneration"]
            elif "speech" in output or "audio" in output:
                capabilities = ["tts"]
            elif "transcription" in output:
                capabilities = ["stt"]
            else:
                capabilities = ["llm"]
            params = set(row.get("supported_parameters") or [])
            models.append(
                RawModel(
                    id=model_id,
                    display_name=row.get("name"),
                    capabilities=capabilities,
                    context_length=row.get("context_length")
                    if isinstance(row.get("context_length"), int)
                    else None,
                    is_multimodal="image" in incoming if incoming else None,
                    is_reasoning="reasoning" in params if params else None,
                    supports_tools="tools" in params if params else None,
                    deprecated=bool(row.get("expiration_date")),
                )
            )
        return _cap(models, [])


class CohereStrategy(DiscoveryStrategy):
    required_fields = ("apiKey",)

    async def list_models(self, configuration, http, query):
        headers = bearer(configuration.get("apiKey"))
        models: list[RawModel] = []
        warnings: list[str] = []
        for endpoint_name, capabilities in (("chat", ["llm"]), ("embed", ["embedding"])):
            token = ""
            for page in range(MAX_PAGES):
                url = f"https://api.cohere.com/v1/models?endpoint={endpoint_name}&page_size=1000"
                if token:
                    url += f"&page_token={token}"
                payload = await http.get_json(url, headers)
                for row in _rows(payload):
                    model_id = str(row.get("name") or row.get("id") or "").strip()
                    if not model_id:
                        continue
                    models.append(
                        RawModel(
                            id=model_id,
                            capabilities=list(capabilities),
                            context_length=row.get("context_length")
                            if isinstance(row.get("context_length"), int)
                            else None,
                            deprecated=bool(row.get("is_deprecated")),
                        )
                    )
                token = str(payload.get("next_page_token") or "") if isinstance(payload, dict) else ""
                if not token:
                    break
                if page == MAX_PAGES - 1:
                    warnings.append("Listing stopped after the page limit.")
        return _cap(models, warnings)


class MistralStrategy(DiscoveryStrategy):
    required_fields = ("apiKey",)

    async def list_models(self, configuration, http, query):
        payload = await http.get_json(
            "https://api.mistral.ai/v1/models", bearer(configuration.get("apiKey"))
        )
        models: list[RawModel] = []
        for row in _rows(payload):
            model_id = str(row.get("id") or "").strip()
            if not model_id:
                continue
            flags = row.get("capabilities") if isinstance(row.get("capabilities"), dict) else {}
            capabilities = None
            if flags.get("completion_chat"):
                capabilities = ["llm"]
            elif flags.get("vision") and not flags.get("completion_chat"):
                capabilities = ["llm"]
            models.append(
                RawModel(
                    id=model_id,
                    capabilities=capabilities,
                    context_length=row.get("max_context_length")
                    if isinstance(row.get("max_context_length"), int)
                    else None,
                    is_multimodal=bool(flags.get("vision")) if "vision" in flags else None,
                    supports_tools=bool(flags.get("function_calling"))
                    if "function_calling" in flags
                    else None,
                    deprecated=bool(row.get("deprecation")),
                )
            )
        return _cap(models, [])


class TogetherStrategy(DiscoveryStrategy):
    required_fields = ("apiKey", "endpoint")

    async def list_models(self, configuration, http, query):
        base = _endpoint(configuration, "https://api.together.xyz/v1")
        payload = await http.get_json(f"{base}/models", bearer(configuration.get("apiKey")))
        type_map = {
            "chat": ["llm"],
            "language": ["llm"],
            "embedding": ["embedding"],
            "image": ["imageGeneration"],
            "audio": ["tts"],
            "transcribe": ["stt"],
        }
        models: list[RawModel] = []
        for row in _rows(payload):
            model_id = str(row.get("id") or "").strip()
            if not model_id:
                continue
            kind = str(row.get("type") or "")
            models.append(
                RawModel(
                    id=model_id,
                    display_name=row.get("display_name"),
                    capabilities=type_map.get(kind),
                    context_length=row.get("context_length")
                    if isinstance(row.get("context_length"), int)
                    else None,
                )
            )
        return _cap(models, [])


class FireworksStrategy(DiscoveryStrategy):
    required_fields = ("apiKey",)

    async def list_models(self, configuration, http, query):
        headers = bearer(configuration.get("apiKey"))
        warnings: list[str] = []
        try:
            return await self._account(http, headers, warnings)
        except DiscoveryHttpError as exc:
            if exc.code == DiscoveryErrorCode.AUTH_ERROR:
                raise
            warnings.append("Account model list was unavailable; used the inference catalog.")
            payload = await http.get_json("https://api.fireworks.ai/inference/v1/models", headers)
            models = [
                RawModel(id=str(row.get("id")))
                for row in _rows(payload)
                if row.get("id")
            ]
            return _cap(models, warnings)

    async def _account(self, http, headers, warnings):
        token = ""
        models: list[RawModel] = []
        kind_map = {
            "llm": ["llm"],
            "embedding": ["embedding"],
            "image": ["imageGeneration"],
        }
        for page in range(MAX_PAGES):
            url = "https://api.fireworks.ai/v1/accounts/fireworks/models"
            if token:
                url += f"?pageToken={token}"
            payload = await http.get_json(url, headers)
            for row in _rows(payload):
                if row.get("supportsServerless") is False:
                    continue
                model_id = str(row.get("name") or row.get("id") or "").strip()
                if not model_id:
                    continue
                models.append(
                    RawModel(
                        id=model_id,
                        capabilities=kind_map.get(str(row.get("kind") or "")),
                        context_length=row.get("contextLength")
                        if isinstance(row.get("contextLength"), int)
                        else None,
                        is_multimodal=bool(row.get("supportsImageInput"))
                        if "supportsImageInput" in row
                        else None,
                        supports_tools=bool(row.get("supportsTools")) if "supportsTools" in row else None,
                        deprecated=bool(row.get("deprecationDate")),
                    )
                )
            token = str(payload.get("nextPageToken") or "") if isinstance(payload, dict) else ""
            if not token:
                break
            if page == MAX_PAGES - 1:
                warnings.append("Listing stopped after the page limit.")
        return _cap(models, warnings)


class JinaStrategy(DiscoveryStrategy):
    required_fields = ("apiKey",)

    async def list_models(self, configuration, http, query):
        payload = await http.get_json(
            "https://api.jina.ai/v1/models", bearer(configuration.get("apiKey"))
        )
        models: list[RawModel] = []
        for row in _rows(payload):
            model_id = str(row.get("id") or "").strip()
            if not model_id or "reranker" in model_id.lower():
                continue
            model_id = model_id.removeprefix("jina-ai/")
            output = row.get("output_modalities") or []
            capabilities = ["embedding"] if "embeddings" in output else None
            models.append(RawModel(id=model_id, capabilities=capabilities))
        return _cap(models, [])


class MiniMaxStrategy(DiscoveryStrategy):
    required_fields = ("apiKey",)
    _STATIC = _static([
        ("speech-02-hd", ["tts"]),
        ("speech-02-turbo", ["tts"]),
    ])

    async def list_models(self, configuration, http, query):
        payload = await http.get_json(
            "https://api.minimax.io/v1/models", bearer(configuration.get("apiKey"))
        )
        models = [RawModel(id=str(row.get("id")), capabilities=["llm"]) for row in _rows(payload) if row.get("id")]
        seen = {model.id for model in models}
        for extra in self._STATIC:
            if extra.id not in seen:
                models.append(extra)
        return _cap(models, [])


class OllamaStrategy(DiscoveryStrategy):
    mode = "list"
    required_fields = ("endpoint",)

    async def list_models(self, configuration, http, query):
        base = _endpoint(configuration)
        payload = await http.get_json(f"{base}/api/tags")
        rows = _rows(payload)
        shown = await asyncio.gather(*(_show(http, base, row) for row in rows))
        models: list[RawModel] = []
        for row, details in zip(rows, shown):
            model_id = str(row.get("name") or row.get("model") or "").strip()
            if not model_id:
                continue
            capabilities = details.get("capabilities") or []
            mapped: list[str] = []
            if "embedding" in capabilities:
                mapped.append("embedding")
            if "completion" in capabilities or not mapped:
                mapped.append("llm")
            context = _ollama_context(details.get("model_info") or {})
            models.append(
                RawModel(
                    id=model_id,
                    capabilities=mapped,
                    context_length=context,
                    is_multimodal=True if "vision" in capabilities else None,
                    is_reasoning=True if "thinking" in capabilities else None,
                    supports_tools=True if "tools" in capabilities else None,
                )
            )
        return _cap(models, [])


def _ollama_context(model_info: dict[str, Any]) -> int | None:
    for key, value in model_info.items():
        if str(key).endswith(".context_length") and isinstance(value, int):
            return value
    return None


async def _show(http: DiscoveryHttp, base: str, row: dict[str, Any]) -> dict[str, Any]:
    digest = str(row.get("digest") or "")
    now = time.monotonic()
    if digest:
        cached = _show_cache.get(digest)
        if cached and cached[0] > now:
            return cached[1]
    name = str(row.get("name") or row.get("model") or "")
    if not name:
        return {}
    async with _show_semaphore():
        if digest:
            cached = _show_cache.get(digest)
            if cached and cached[0] > time.monotonic():
                return cached[1]
        try:
            details = await http.post_json(f"{base}/api/show", {"model": name})
        except DiscoveryHttpError:
            return {}
    if digest and isinstance(details, dict):
        _show_cache[digest] = (time.monotonic() + _SHOW_TTL_S, details)
    return details if isinstance(details, dict) else {}


class LMStudioStrategy(DiscoveryStrategy):
    required_fields = ("endpoint",)

    async def list_models(self, configuration, http, query):
        base = _endpoint(configuration)
        origin = base[:-3] if base.endswith("/v1") else base
        headers = bearer(configuration.get("apiKey"))
        warnings: list[str] = []
        for url, parser in (
            (f"{origin}/api/v1/models", _lmstudio_v1),
            (f"{origin}/api/v0/models", _lmstudio_v0),
            (f"{base}/models", _openai_bare),
        ):
            try:
                payload = await http.get_json(url, headers)
            except DiscoveryHttpError as exc:
                if exc.code == DiscoveryErrorCode.AUTH_ERROR:
                    raise
                continue
            models = parser(payload)
            if models:
                return _cap(models, warnings)
        raise DiscoveryHttpError(
            DiscoveryErrorCode.UNREACHABLE, "LM Studio did not return a model list."
        )


def _lmstudio_v1(payload: Any) -> list[RawModel]:
    models: list[RawModel] = []
    for row in _rows(payload):
        model_id = str(row.get("id") or "").strip()
        if not model_id:
            continue
        kind = str(row.get("type") or "")
        capabilities = ["embedding"] if kind == "embedding" else ["llm"]
        caps_obj = row.get("capabilities") if isinstance(row.get("capabilities"), dict) else {}
        models.append(
            RawModel(
                id=model_id,
                capabilities=capabilities,
                context_length=row.get("max_context_length")
                if isinstance(row.get("max_context_length"), int)
                else None,
                is_multimodal=bool(caps_obj.get("vision")) if "vision" in caps_obj else None,
                is_reasoning=bool(caps_obj.get("reasoning")) if "reasoning" in caps_obj else None,
                supports_tools=bool(caps_obj.get("trained_for_tool_use"))
                if "trained_for_tool_use" in caps_obj
                else None,
            )
        )
    return models


def _lmstudio_v0(payload: Any) -> list[RawModel]:
    models: list[RawModel] = []
    for row in _rows(payload):
        model_id = str(row.get("id") or "").strip()
        if not model_id:
            continue
        kind = str(row.get("type") or "")
        if kind == "embeddings":
            capabilities = ["embedding"]
        else:
            capabilities = ["llm"]
        models.append(
            RawModel(
                id=model_id,
                capabilities=capabilities,
                is_multimodal=True if kind == "vlm" else None,
            )
        )
    return models


def _openai_bare(payload: Any) -> list[RawModel]:
    return [RawModel(id=str(row.get("id"))) for row in _rows(payload) if row.get("id")]


class LiteLLMProxyStrategy(DiscoveryStrategy):
    required_fields = ("endpoint",)

    async def list_models(self, configuration, http, query):
        base = _endpoint(configuration)
        headers = bearer(configuration.get("apiKey"))
        info_url = f"{base}/model/info"
        try:
            payload = await http.get_json(info_url, headers)
        except DiscoveryHttpError as exc:
            if exc.code != DiscoveryErrorCode.AUTH_ERROR:
                raise
            payload = await http.get_json(f"{base}/models", headers)
            return _cap(_openai_bare(payload), ["The proxy refused model info; listed ids only."])
        models: list[RawModel] = []
        for row in _rows(payload):
            model_id = str(row.get("model_name") or row.get("id") or "").strip()
            info = row.get("model_info") if isinstance(row.get("model_info"), dict) else row
            mode = str(info.get("mode") or "")
            mode_map = {
                "chat": ["llm"],
                "completion": ["llm"],
                "embedding": ["embedding"],
                "image_generation": ["imageGeneration"],
                "audio_speech": ["tts"],
                "audio_transcription": ["stt"],
            }
            models.append(
                RawModel(
                    id=model_id,
                    capabilities=mode_map.get(mode),
                    context_length=info.get("max_input_tokens")
                    if isinstance(info.get("max_input_tokens"), int)
                    else None,
                    is_multimodal=bool(info.get("supports_vision")) if "supports_vision" in info else None,
                    is_reasoning=bool(info.get("supports_reasoning"))
                    if "supports_reasoning" in info
                    else None,
                    supports_tools=bool(info.get("supports_function_calling"))
                    if "supports_function_calling" in info
                    else None,
                )
            )
        return _cap([model for model in models if model.id], [])


class OpenAICompatibleStrategy(DiscoveryStrategy):
    required_fields = ("endpoint",)

    async def list_models(self, configuration, http, query):
        raw_base = _endpoint(configuration)
        base = _openai_v1_base(raw_base)
        headers = bearer(configuration.get("apiKey"))
        origin = raw_base[:-3] if raw_base.endswith("/v1") else raw_base
        warnings: list[str] = []
        try:
            payload = await http.get_json(f"{base}/models", headers)
        except DiscoveryHttpError as exc:
            if exc.code == DiscoveryErrorCode.AUTH_ERROR:
                raise
            try:
                voices = await http.get_json(f"{base}/audio/voices", headers)
            except DiscoveryHttpError:
                raise exc
            return _kokoro(voices), ["This server has no /models list; voices were used as TTS ids."]
        models = _openai_bare(payload)
        models = await _merge_localai(http, base, headers, models)
        models = await _merge_llama_props(http, origin, models)
        enriched: list[RawModel] = []
        by_id = {str(row.get("id")): row for row in _rows(payload)}
        for model in models:
            row = by_id.get(model.id) or {}
            context = row.get("max_model_len")
            task = row.get("task")
            capabilities = model.capabilities
            if task in {"automatic-speech-recognition", "transcription"}:
                capabilities = ["stt"]
            elif task in {"text-to-speech"}:
                capabilities = ["tts"]
            enriched.append(
                RawModel(
                    id=model.id,
                    capabilities=capabilities,
                    context_length=context if isinstance(context, int) else model.context_length,
                    is_multimodal=model.is_multimodal,
                    supports_tools=model.supports_tools,
                )
            )
        return _cap(enriched, warnings)


def _kokoro(payload: Any) -> list[RawModel]:
    voices = payload.get("voices") if isinstance(payload, dict) else payload
    if isinstance(voices, dict):
        ids = list(voices.keys())
    elif isinstance(voices, list):
        ids = [str(item.get("id") if isinstance(item, dict) else item) for item in voices]
    else:
        ids = []
    return [RawModel(id=voice_id, capabilities=["tts"]) for voice_id in ids if voice_id]


async def _merge_localai(http, base, headers, models: list[RawModel]) -> list[RawModel]:
    try:
        payload = await http.get_json(f"{base}/models/capabilities", headers)
    except DiscoveryHttpError:
        return models
    by_id = {model.id: model for model in models}
    for row in _rows(payload):
        model_id = str(row.get("id") or "")
        if model_id not in by_id:
            continue
        current = by_id[model_id]
        caps = row.get("capabilities")
        by_id[model_id] = RawModel(
            id=model_id,
            capabilities=list(caps) if isinstance(caps, list) and caps else current.capabilities,
            context_length=current.context_length,
            is_multimodal=current.is_multimodal,
        )
    return list(by_id.values())


async def _merge_llama_props(http, origin, models: list[RawModel]) -> list[RawModel]:
    try:
        payload = await http.get_json(f"{origin}/props")
    except DiscoveryHttpError:
        return models
    if not isinstance(payload, dict) or not models:
        return models
    modalities = payload.get("modalities") or {}
    context = payload.get("n_ctx") if isinstance(payload.get("n_ctx"), int) else None
    vision = None
    if isinstance(modalities, dict) and "vision" in modalities:
        vision = bool(modalities.get("vision"))
    first = models[0]
    models[0] = RawModel(
        id=first.id,
        capabilities=first.capabilities,
        context_length=context or first.context_length,
        is_multimodal=vision if vision is not None else first.is_multimodal,
    )
    return models


class AzureOpenAIStrategy(DiscoveryStrategy):
    required_fields = ("endpoint", "apiKey")

    async def list_models(self, configuration, http, query):
        base = _endpoint(configuration)
        url = f"{base}/openai/models?api-version=2024-10-21"
        headers = {"api-key": str(configuration.get("apiKey") or "")}
        payload = await http.get_json(url, headers)
        models: list[RawModel] = []
        for row in _rows(payload):
            model_id = str(row.get("id") or row.get("model") or "").strip()
            if not model_id:
                continue
            flags = row.get("capabilities") if isinstance(row.get("capabilities"), dict) else {}
            capabilities = None
            if flags.get("embeddings"):
                capabilities = ["embedding"]
            elif flags.get("chat_completion"):
                capabilities = ["llm"]
            status = str(row.get("status") or "")
            models.append(
                RawModel(
                    id=model_id,
                    capabilities=capabilities,
                    deprecated=status.lower() in {"deprecated", "deprecating"} or bool(row.get("deprecation")),
                )
            )
        return _cap(
            models,
            ["These are base models. Calls still use the deployment name, which Azure does not list here."],
        )


class BedrockStrategy(DiscoveryStrategy):
    mode = "list"
    required_fields = ("region",)

    async def list_models(self, configuration, http, query):
        from botocore.exceptions import ClientError, NoCredentialsError

        try:
            summaries, profiles = await asyncio.to_thread(list_bedrock_pages, configuration)
        except NoCredentialsError as exc:
            raise DiscoveryHttpError(
                DiscoveryErrorCode.AUTH_ERROR, "Bedrock credentials are missing."
            ) from exc
        except ClientError as exc:
            code = ""
            response = getattr(exc, "response", None)
            if isinstance(response, dict):
                error = response.get("Error")
                if isinstance(error, dict):
                    code = str(error.get("Code") or "")
            detail = f" ({code})" if code else ""
            raise DiscoveryHttpError(
                DiscoveryErrorCode.AUTH_ERROR,
                f"Bedrock rejected these credentials{detail}.",
            ) from exc
        profile_ids: dict[str, list[str]] = {}
        for profile in profiles:
            profile_id = str(profile.get("inferenceProfileId") or "")
            for model in profile.get("models") or []:
                arn = str(model.get("modelArn") or "")
                foundation_id = arn.split("/")[-1]
                if profile_id and foundation_id:
                    profile_ids.setdefault(foundation_id, []).append(profile_id)
        modality = {
            "TEXT": "llm",
            "EMBEDDING": "embedding",
            "IMAGE": "imageGeneration",
        }
        models: list[RawModel] = []
        for summary in summaries:
            model_id = str(summary.get("modelId") or "")
            if not model_id:
                continue
            outputs = [modality[item] for item in (summary.get("outputModalities") or []) if item in modality]
            if not outputs:
                continue
            inputs = set(summary.get("inputModalities") or [])
            inference = set(summary.get("inferenceTypesSupported") or [])
            lifecycle = summary.get("modelLifecycle") if isinstance(summary.get("modelLifecycle"), dict) else {}
            deprecated = str(lifecycle.get("status") or "") == "LEGACY"
            ids = profile_ids.get(model_id, []) if inference == {"INFERENCE_PROFILE"} else [model_id]
            if inference == {"INFERENCE_PROFILE"} and not ids:
                continue
            for listed_id in ids:
                models.append(
                    RawModel(
                        id=listed_id,
                        capabilities=outputs,
                        is_multimodal=True if "IMAGE" in inputs else None,
                        deprecated=deprecated,
                    )
                )
        return _cap(models, ["A listed Bedrock model is not a guarantee this account can invoke it."])


def list_bedrock_pages(configuration: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Sync control-plane reads. Patched in tests so discovery does not call AWS."""
    from app.utils.aimodels import _create_bedrock_client

    client = _create_bedrock_client(configuration, service_name="bedrock")
    summaries = list((client.list_foundation_models() or {}).get("modelSummaries") or [])
    profiles: list[dict[str, Any]] = []
    token = None
    for _ in range(MAX_PAGES):
        kwargs = {"nextToken": token} if token else {}
        page = client.list_inference_profiles(**kwargs) or {}
        profiles.extend(page.get("inferenceProfileSummaries") or [])
        token = page.get("nextToken")
        if not token:
            break
    return summaries, profiles


class HuggingFaceSearchStrategy(DiscoveryStrategy):
    mode = "search"
    required_fields = ()

    async def list_models(self, configuration, http, query):
        text = (query or "").strip()
        if len(text) < 2:
            return [], ["Type at least two characters to search the Hugging Face Hub."]
        _reserve_hf_call()
        params = urlencode({
            "library": "sentence-transformers",
            "pipeline_tag": "sentence-similarity",
            "sort": "downloads",
            "limit": "50",
            "search": text,
        })
        url = f"https://huggingface.co/api/models?{params}"
        models: list[RawModel] = []
        warnings: list[str] = []
        for page in range(MAX_PAGES):
            status, body, headers = await http.get_bytes(url)
            if status in (401, 403):
                raise DiscoveryHttpError(DiscoveryErrorCode.AUTH_ERROR, "The provider rejected these credentials.")
            if status == 429:
                raise DiscoveryHttpError(DiscoveryErrorCode.RATE_LIMITED, "The provider rate-limited model listing.")
            if status >= 400:
                raise DiscoveryHttpError(
                    DiscoveryErrorCode.UNREACHABLE,
                    f"The provider returned HTTP {status} while listing models.",
                )
            try:
                payload = json.loads(body)
            except ValueError as exc:
                raise DiscoveryHttpError(
                    DiscoveryErrorCode.INVALID_RESPONSE, "The provider did not return JSON."
                ) from exc
            if not isinstance(payload, list):
                raise DiscoveryHttpError(
                    DiscoveryErrorCode.INVALID_RESPONSE, "The provider returned an unexpected model list."
                )
            for row in payload:
                if isinstance(row, dict) and row.get("id"):
                    models.append(RawModel(id=str(row["id"]), capabilities=["embedding"]))
            url = _next_link(headers.get("link", ""))
            if not url:
                break
            if page == MAX_PAGES - 1:
                warnings.append("Listing stopped after the page limit.")
        return _cap(models, warnings)


def _reserve_hf_call() -> None:
    now = time.monotonic()
    while _hf_calls and now - _hf_calls[0] > _HF_WINDOW_S:
        _hf_calls.popleft()
    if len(_hf_calls) >= _HF_MAX_CALLS:
        raise DiscoveryHttpError(
            DiscoveryErrorCode.RATE_LIMITED,
            "Hugging Face search is paused so this server stays under the Hub rate limit.",
        )
    _hf_calls.append(now)


def _next_link(header: str) -> str:
    for part in header.split(","):
        if 'rel="next"' not in part:
            continue
        start = part.find("<")
        end = part.find(">")
        if start >= 0 and end > start:
            return part[start + 1:end]
    return ""


class _StaticStrategy(DiscoveryStrategy):
    mode = "static"
    required_fields = ()

    def __init__(self, models: list[RawModel]) -> None:
        self._models = models

    async def list_models(self, configuration, http, query):
        return list(self._models), []


class _ManualStrategy(DiscoveryStrategy):
    mode = "manual"


_WHISPER = _static([
    ("tiny", ["stt"]),
    ("base", ["stt"]),
    ("small", ["stt"]),
    ("medium", ["stt"]),
    ("large-v2", ["stt"]),
    ("large-v3", ["stt"]),
    ("distil-large-v3", ["stt"]),
])
_VOYAGE = _static([
    ("voyage-3-large", ["embedding"]),
    ("voyage-3.5", ["embedding"]),
    ("voyage-3-lite", ["embedding"]),
    ("voyage-code-3", ["embedding"]),
])
_VERTEX = _static([
    ("gemini-2.5-pro", ["llm"]),
    ("gemini-2.5-flash", ["llm"]),
    ("gemini-2.0-flash", ["llm"]),
    ("text-embedding-005", ["embedding"]),
    ("imagen-3.0-generate-002", ["imageGeneration"]),
])


def build_registry() -> dict[str, DiscoveryStrategy]:
    return {
        "openAI": _OpenAIList("https://api.openai.com/v1"),
        "groq": _OpenAIList("https://api.groq.com/openai/v1"),
        "anthropic": AnthropicStrategy(),
        "gemini": GeminiStrategy(),
        "xai": XAIStrategy(),
        "openRouter": OpenRouterStrategy(),
        "cohere": CohereStrategy(),
        "mistral": MistralStrategy(),
        "together": TogetherStrategy(),
        "fireworks": FireworksStrategy(),
        "jinaAI": JinaStrategy(),
        "minimax": MiniMaxStrategy(),
        "ollama": OllamaStrategy(),
        "lmStudio": LMStudioStrategy(),
        "litellmProxy": LiteLLMProxyStrategy(),
        "openAICompatible": OpenAICompatibleStrategy(),
        "azureOpenAI": AzureOpenAIStrategy(),
        "azureAI": _ManualStrategy(),
        "bedrock": BedrockStrategy(),
        "huggingFace": HuggingFaceSearchStrategy(),
        "sentenceTransformers": HuggingFaceSearchStrategy(),
        "voyage": _StaticStrategy(_VOYAGE),
        "whisper": _StaticStrategy(_WHISPER),
        "wispr": _StaticStrategy(_static([("flow-v1", ["stt"])])),
        "default": _StaticStrategy(_static([("BAAI/bge-large-en-v1.5", ["embedding"])])),
        "vertexAI": _StaticStrategy(_VERTEX),
    }
