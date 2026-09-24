"""Azure OpenAI through the official SDK, without LangChain.

`LangChainTransport` builds an `AIMessageChunk` per streamed token and pydantic
runs two model validators on each one; that measured 8.96% of query-service CPU
under load, and there is no upstream fix (langchain-core 1.5.3 still carries the
validators, marked with its own "TODO: remove this logic if possible").

Most of the request machinery is inherited from `OpenAITransport` -- Azure speaks
the same shapes -- so only the client and the configuration capture differ, plus
the two single-retry fallbacks `LangChainTransport` carries for request-shape
conflicts.

**Configuration is captured at construction, not passed per call.** That mirrors
`LangChainTransport`, which ignores per-call `effort`/`thinking_budget` because
`aimodels.get_generator_model` baked every knob into the model object. Reading
those knobs back off the same object is what keeps the two transports issuing
identical requests; reading only the credentials is what let them drift, sending
different endpoints and no reasoning at all.

Registered as provider "azure_direct"; "langchain" stays the default so this can
be switched per deployment and reverted without a code change.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.transport.openai import OpenAITransport, RequestDefaults

if TYPE_CHECKING:  # noqa: F401 - kept for the class docstring's references
    pass

logger = logging.getLogger(__name__)

# Re-exported: RequestDefaults moved to `openai.py` when the direct OpenAI
# transport started needing it too. Kept importable from here because that is
# where it was introduced and callers already import it from this module.
__all__ = ["AzureOpenAITransport", "RequestDefaults"]

class AzureOpenAITransport(OpenAITransport):
    """Azure OpenAI via AsyncAzureOpenAI.

    `deployment` is Azure's routing key: the model name in a request is the
    deployment name, not the underlying model. Callers pass whatever
    `aimodels.py` already resolved from configuration.
    """

    def __init__(
        self,
        api_key: str,
        azure_endpoint: str,
        api_version: str,
        deployment: str,
        model: str | None = None,
        defaults: RequestDefaults | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        model_key: str | None = None,
    ) -> None:
        # Skip OpenAITransport.__init__ (it builds AsyncOpenAI) but keep the
        # LLMTransport base contract and the cumulative counters its callers read.
        super(OpenAITransport, self).__init__()
        try:
            import openai as _openai
        except ImportError as exc:
            raise ImportError(
                "openai SDK is required for AzureOpenAITransport. "
                "Install it with: pip install 'agent-loop[openai]'"
            ) from exc
        self._openai = _openai
        self._deployment = deployment
        # Azure routes on the deployment name; the model field of a request must
        # carry it, so default the model to the deployment rather than a public
        # model id.
        self._model = model or deployment
        self._defaults = defaults or RequestDefaults()
        # Key the learned api-mode store is written under; None disables the
        # persistence half, exactly as it does for LangChainTransport.
        self._model_key = model_key
        client_kwargs: dict[str, Any] = {
            "api_key": api_key,
            "azure_endpoint": azure_endpoint,
            "api_version": api_version,
            "azure_deployment": deployment,
        }
        # Left unset the SDK applies its own (longer) default; LangChain pins
        # DEFAULT_LLM_TIMEOUT, so a slow turn must fail at the same point.
        if timeout is not None:
            client_kwargs["timeout"] = timeout
        if max_retries is not None:
            client_kwargs["max_retries"] = max_retries
        self._client = _openai.AsyncAzureOpenAI(**client_kwargs)
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.total_llm_calls: int = 0
        self.total_cache_read_tokens: int = 0
        self.total_cache_write_tokens: int = 0

    @classmethod
    def from_langchain_model(
        cls, llm: Any, model_name: str = "", model_key: str | None = None,
    ) -> "AzureOpenAITransport":
        """Build from an already-configured `AzureChatOpenAI`.

        Everything is read off the model the LangChain path already uses, rather
        than re-resolving it from configuration, so the two transports cannot
        drift apart. That includes the behaviour knobs, not just the
        credentials: `aimodels.get_generator_model` sets temperature, timeout and
        the reasoning configuration on the model rather than passing them per
        call, so copying only the credentials silently produced a different
        request -- Chat Completions with no reasoning, where LangChain used the
        Responses API at the configured effort.

        Raises ValueError when `llm` is not Azure-shaped -- this transport is
        Azure-only, and silently falling back would hide a misconfiguration
        behind a slower path.
        """
        def _val(name: str) -> str:
            raw = getattr(llm, name, None)
            # Credentials arrive as pydantic SecretStr on the LangChain model.
            secret = getattr(raw, "get_secret_value", None)
            return (secret() if callable(secret) else raw) or ""

        endpoint = _val("azure_endpoint")
        deployment = _val("deployment_name")
        api_key = _val("openai_api_key")
        api_version = _val("openai_api_version")
        missing = [
            n for n, v in (
                ("azure_endpoint", endpoint), ("deployment_name", deployment),
                ("openai_api_key", api_key), ("openai_api_version", api_version),
            ) if not v
        ]
        if missing:
            raise ValueError(
                f"{type(llm).__name__} is missing Azure settings {missing}; "
                "azure_direct only supports AzureChatOpenAI-configured models"
            )

        reasoning = getattr(llm, "reasoning", None)
        defaults = RequestDefaults(
            temperature=getattr(llm, "temperature", None),
            reasoning=reasoning if isinstance(reasoning, dict) else None,
            reasoning_effort=getattr(llm, "reasoning_effort", None),
            use_responses_api=bool(getattr(llm, "use_responses_api", False)),
            model=model_name or deployment,
        )
        timeout = getattr(llm, "request_timeout", None)
        if timeout is None:
            timeout = getattr(llm, "timeout", None)
        return cls(
            api_key=api_key, azure_endpoint=endpoint, api_version=api_version,
            deployment=deployment, model=model_name or deployment,
            defaults=defaults,
            timeout=timeout if isinstance(timeout, (int, float)) else None,
            max_retries=getattr(llm, "max_retries", None),
            model_key=model_key,
        )

    @property
    def provider(self) -> str:
        return "azure_direct"

    def _default_request_kwargs(self) -> dict[str, Any]:
        """Temperature and reasoning captured from the configured model.

        The Chat Completions path uses `reasoning_effort`; the Responses path
        uses `reasoning={"effort": ...}`. RequestDefaults emits whichever
        matches the endpoint this transport was configured for, so a request
        never carries the wrong spelling.
        """
        return self._defaults.request_kwargs()

    def _wants_responses(self) -> bool:
        """Taken from the configured model, never re-derived.

        `aimodels._reasoning_effort_kwargs` has already folded the model-name
        heuristic and the learned `LLMApiMode` fact into `use_responses_api`.
        Recomputing that here would be a second source of truth that drifts
        from the LangChain arm the first time either rule changes.
        """
        return self._defaults.use_responses_api

    def _responses_model_id(self) -> str:
        """The DEPLOYMENT name, not the model name.

        Azure puts `/deployments/{name}/` in the path for Chat Completions but
        not for `/openai/responses`, so on the Responses API the deployment has
        to travel in the body instead. Sending the model name there is a
        DeploymentNotFound 404.
        """
        return self._deployment
