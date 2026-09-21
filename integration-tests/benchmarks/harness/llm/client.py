"""`LLMClient` protocol and its LiteLLM implementation.

No request ever carries tools, tool_choice or provider-side web grounding:
baselines and judges must answer from the prompt alone (anti-cheating).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_random_exponential

from benchmarks.harness.credentials import Credentials
from benchmarks.harness.llm.providers import provider_spec

logger = logging.getLogger(__name__)

# Reasoning models spend completion tokens on hidden reasoning.
_REASONING_MIN_MAX_TOKENS = 16_384
# High-effort reasoning over ~100k-token contexts can take minutes.
_DEFAULT_TIMEOUT_S = 600.0


class ResolvedModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    model_key: str
    provider: str
    model_name: str
    is_reasoning: bool = False
    context_length: int | None = None
    reasoning_effort: str | None = None
    # Azure routes by deployment name, which may differ from the model name.
    deployment: str | None = None

    @property
    def litellm_model(self) -> str:
        return f"{provider_spec(self.provider).litellm_prefix}/{self.deployment or self.model_name}"

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model_name}"


class ChatMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: Literal["system", "user", "assistant"]
    content: str


class LLMRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: ResolvedModel
    messages: tuple[ChatMessage, ...]
    temperature: float = 0.0
    max_tokens: int = 2048
    prompt_version: str
    cacheable: bool = False
    # Auxiliary steps (query rewrites) override these: the answerer's reasoning
    # effort and 16k output reservation make a three-line rewrite take minutes.
    effort: str | None = None
    reserve_reasoning_tokens: bool = True
    timeout_s: float | None = None


class LLMResponse(BaseModel):
    text: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cached_tokens: int | None = None
    cost_usd: float | None = None
    cached: bool = False
    latency_ms: int = 0


class EmbeddingResponse(BaseModel):
    vectors: list[list[float]]
    prompt_tokens: int | None = None


class LLMClient(Protocol):
    def complete(self, request: LLMRequest) -> LLMResponse: ...

    def embed(self, model: ResolvedModel, texts: list[str]) -> EmbeddingResponse: ...


# Claude models that reject sampling parameters (400); they are still run
# once per judgment and cached, which is what makes grading reproducible.
_NO_SAMPLING_PREFIXES = (
    "claude-fable-5", "claude-mythos-5", "claude-opus-5", "claude-opus-4-8", "claude-opus-4-7", "claude-sonnet-5",
)


def _accepts_temperature(model: ResolvedModel) -> bool:
    return not (model.provider == "anthropic" and model.model_name.startswith(_NO_SAMPLING_PREFIXES))


def build_completion_kwargs(
    request: LLMRequest, api_key: str, timeout_s: float, endpoint: dict[str, str] | None = None,
) -> dict[str, Any]:
    """The exact LiteLLM call. Reasoning models only accept temperature 1."""
    model = request.model
    kwargs: dict[str, Any] = {
        "model": model.litellm_model,
        "messages": [m.model_dump() for m in request.messages],
        "max_tokens": (
            max(request.max_tokens, _REASONING_MIN_MAX_TOKENS)
            if model.is_reasoning and request.reserve_reasoning_tokens
            else request.max_tokens
        ),
        "api_key": api_key,
        "timeout": timeout_s,
        "num_retries": 0,
        "drop_params": True,
        **(endpoint or {}),
    }
    if _accepts_temperature(model):
        kwargs["temperature"] = 1.0 if model.is_reasoning else request.temperature
    effort = request.effort or model.reasoning_effort
    if model.is_reasoning and effort:
        kwargs["reasoning_effort"] = effort
    return kwargs


def _is_retryable_llm_error(exc: BaseException) -> bool:
    import litellm

    return isinstance(exc, (
        litellm.RateLimitError, litellm.APIConnectionError, litellm.Timeout,
        litellm.InternalServerError, litellm.ServiceUnavailableError,
    ))


def _log_llm_retry(state: Any) -> None:  # noqa: ANN401 — tenacity RetryCallState
    exc = state.outcome.exception() if state.outcome else None
    logger.warning("LLM call retry %d: %s", state.attempt_number, str(exc)[:200])


_llm_retry = retry(
    retry=retry_if_exception(_is_retryable_llm_error),
    stop=stop_after_attempt(6),
    wait=wait_random_exponential(multiplier=2, max=90),
    before_sleep=_log_llm_retry,
    reraise=True,
)


def _completion_cost(response: Any) -> float | None:  # noqa: ANN401
    import litellm

    try:
        return float(litellm.completion_cost(completion_response=response))
    except Exception:  # noqa: BLE001 — pricing tables lag new models; cost is optional
        logger.debug("no LiteLLM price for %s", getattr(response, "model", "?"))
        return None


class LiteLLMClient:
    def __init__(self, credentials: Credentials, *, timeout_s: float = _DEFAULT_TIMEOUT_S) -> None:
        self._credentials = credentials
        self._timeout_s = timeout_s

    def _endpoint(self, provider: str) -> dict[str, str]:
        spec = provider_spec(provider)
        endpoint = {}
        if spec.api_base_envs:
            endpoint["api_base"] = self._credentials.first_of(spec.api_base_envs)
        if spec.api_version_envs:
            endpoint["api_version"] = self._credentials.first_of(spec.api_version_envs)
        return endpoint

    def complete(self, request: LLMRequest) -> LLMResponse:
        import litellm

        provider = request.model.provider
        api_key = self._credentials.first_of(provider_spec(provider).key_envs)
        kwargs = build_completion_kwargs(
            request, api_key, request.timeout_s or self._timeout_s, self._endpoint(provider),
        )
        started = time.monotonic()
        response = _llm_retry(litellm.completion)(**kwargs)
        usage = getattr(response, "usage", None)
        details = getattr(usage, "prompt_tokens_details", None)
        return LLMResponse(
            text=response.choices[0].message.content or "",
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
            cached_tokens=getattr(details, "cached_tokens", None),
            cost_usd=_completion_cost(response),
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    def embed(self, model: ResolvedModel, texts: list[str]) -> EmbeddingResponse:
        import litellm

        api_key = self._credentials.first_of(provider_spec(model.provider).key_envs)
        response = _llm_retry(litellm.embedding)(
            model=model.litellm_model, input=texts, api_key=api_key, timeout=self._timeout_s,
            num_retries=0, **self._endpoint(model.provider),
        )
        vectors = [list(item["embedding"]) for item in sorted(response.data, key=lambda d: d["index"])]
        return EmbeddingResponse(vectors=vectors, prompt_tokens=getattr(response.usage, "prompt_tokens", None))
