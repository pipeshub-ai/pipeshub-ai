import asyncio
import logging
from typing import Any, Tuple, cast

from langchain_core.embeddings.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel

from app.config.configuration_service import ConfigurationService
from app.config.constants.service import config_node_constants
from app.exceptions.indexing_exceptions import DocumentProcessingError
from app.services.ai_models.decision import DecisionModel, get_decision_model
from app.services.spend_guard.retry_policy import clamp_retry_after
from app.utils.aimodels import (
    STTAdapter,
    TTSAdapter,
    get_default_embedding_model,
    get_embedding_model,
    get_generator_model,
    get_generator_model_async,
    get_stt_model,
    get_tts_model,
    is_local_cpu_embedding_provider,
)
from app.utils.model_placement import model_placement
from app.utils.run_model_scope import scoped_helper_model, scoped_image_model
from harness_core.access.errors import AccessPolicyUnavailable

# Users read these as a file's failure reason or a chat error, so each says what to do next.
LLM_MISSING_FOR_FILE = (
    "No AI model is set up for this workspace yet, so this file couldn't be processed. "
    "An admin can add one in Workspace → AI Models, then reindex the file."
)
LLM_MISSING_FOR_CHAT = (
    "No AI model is set up for this workspace yet. "
    "An admin can add one in Workspace → AI Models, then you can send your message again."
)


class LLMNotConfiguredError(DocumentProcessingError, ValueError):
    """No language model is configured for the org; permanent until an admin adds one, so never retried.

    A DocumentProcessingError so indexing passes the message through as the
    record's failure reason instead of prefixing it with "Failed to process document".
    """

    code = "LLM_NOT_CONFIGURED"

    def __init__(self, message: str = LLM_MISSING_FOR_FILE) -> None:
        super().__init__(message)


class ModelNotAllowedError(LLMNotConfiguredError):
    """A rule of the org an extension keeps (``ModelPlacementPort``) does not allow the model: nothing was sent to it.
    Permanent until an admin changes the model or the rule, so never retried; every surface that shows a missing
    model's message shows this. ``answer_code``: the error answer and run ending the rule names, which the web app
    words; None: the rule names none, and the refusal reads as a missing model."""

    code = "MODEL_NOT_ALLOWED"

    def __init__(self, message: str = LLM_MISSING_FOR_FILE, *, answer_code: str | None = None) -> None:
        super().__init__(message)
        self.answer_code = answer_code


class LLMUnavailableError(Exception):
    """The model provider could not serve the call (unreachable, timing out, overloaded or rate
    limiting). Transient: the caller waits and tries again without counting a failure."""

    code = "LLM_UNAVAILABLE"

    def __init__(
        self,
        message: str = "",
        *,
        retry_after: float | None = None,
        sent: bool = True,
        trial: bool = False,
        chargeable: bool = False,
        pk: str | None = None,
        size_class: str | None = None,
        requests: int | None = None,
        provider: str | None = None,
        charged: int | None = None,
    ) -> None:
        super().__init__(message)
        # The provider's readable key (class, model, endpoint host), for reasons and notices; never a credential.
        self.provider = provider
        # How many requests went on the wire for this outcome (SDK retries and the gateway's own 429 retries
        # included); the pause budget's "sends" are these. None: one if ``sent``, else none.
        self.requests = requests if requests is not None else (1 if sent else 0)
        # Seconds until the provider may serve again, when known (its open circuit's cooldown).
        # Untrusted when it came from a provider: finite, and never longer than a requeue may park a job.
        self.retry_after = clamp_retry_after(retry_after)
        # Whether a request reached the wire. A refusal by an open breaker is not a send, and an
        # error that does not say is read as one: a paused job must not get free sends.
        self.sent = sent
        # The send was a half-open trial (a real request carried for the fleet), never charged to its job.
        self.trial = trial
        # A failed send made with the breaker closed while the provider was evidently serving others,
        # so the job's document is the likely cause: it counts toward the job's charged cap.
        self.chargeable = chargeable
        # How many of this outcome's failed sends were charged (each failed attempt is judged on its own); None: one
        # when ``chargeable``.
        self.charged = charged if charged is not None else (1 if chargeable else 0)
        # The provider key digest and size class of the breaker involved, to read its hold ledger.
        self.pk = pk
        self.size_class = size_class


# Provider SDKs name the same failures differently (openai, anthropic, httpx, google, botocore).
_UNAVAILABLE_NAME_PARTS = ("Connect", "Timeout", "RateLimit", "Unavailable", "Overloaded", "Throttl", "InternalServer", "Disconnect")
_TOO_MANY_REQUESTS = 429
_SERVER_ERROR = 500


def is_provider_unavailable(exc: BaseException) -> bool:
    """Whether an LLM call failed because the provider could not serve it, not because of the request.

    Reads status codes and exception names, following the cause chain (LangChain re-raises SDK
    errors). A 4xx other than 429 is the request's fault.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (TimeoutError, ConnectionError)):
            return True
        status = getattr(current, "status_code", None)
        if isinstance(status, int):
            return status == _TOO_MANY_REQUESTS or status >= _SERVER_ERROR
        if any(part in type(current).__name__ for part in _UNAVAILABLE_NAME_PARTS):
            return True
        current = current.__cause__ or current.__context__
    return False


async def load_ai_models(config_service: ConfigurationService, *, raise_on_error: bool = False) -> dict[str, Any]:
    """Load the AI models config blob (OSS: org-scoped get_config); ``{}`` until AI settings are saved.

    ``raise_on_error=True`` raises when the store cannot be read, so ``{}`` then means
    only that no AI settings are saved.
    """
    config = cast(object, await config_service.get_config(
        config_node_constants.AI_MODELS.value, use_cache=False, raise_on_error=raise_on_error
    ))
    return cast(dict[str, Any], config) if isinstance(config, dict) else {}


def indexing_model_identity(ai_models: dict[str, Any], role: str = "indexing") -> str:
    """Which model serves ``role``, without instantiating it (for stage fingerprints).

    Mirrors ``get_llm_for_role``'s choice: the role's assigned model, else the default LLM,
    else the first; an assignment that does not resolve counts as none, as it does there.
    ``"none"`` when no LLM is configured.
    """
    roles = ai_models.get("modelRoles")
    assignment = cast(dict[str, Any], roles).get(role) if isinstance(roles, dict) else None
    if isinstance(assignment, dict):
        chosen = cast(dict[str, Any], assignment)
        model_type = chosen.get("modelType")
        bucket = ai_models.get(model_type) if isinstance(model_type, str) else None
        if chosen.get("modelKey") and isinstance(bucket, list):
            for candidate in cast(list[Any], bucket):
                entry = cast(dict[str, Any], candidate) if isinstance(candidate, dict) else None
                if entry is not None and entry.get("modelKey") == chosen["modelKey"]:
                    return _model_entry_identity(str(model_type), entry)
    configs = ai_models.get("llm")
    if not isinstance(configs, list) or not configs:
        return "none"
    entries = [cast(dict[str, Any], c) for c in cast(list[Any], configs) if isinstance(c, dict)]
    if not entries:
        return "none"
    default = next((c for c in entries if c.get("isDefault")), entries[0])
    return _model_entry_identity("llm", default)


def _model_entry_identity(model_type: str, entry: dict[str, Any]) -> str:
    configuration = entry.get("configuration")
    model = cast(dict[str, Any], configuration).get("model", "") if isinstance(configuration, dict) else ""
    return f"{model_type}:{entry.get('modelKey') or entry.get('provider', '')}:{model}"


PLACEMENT_UNREADABLE = (
    "Your organization's allowed model regions could not be read just now, so nothing was sent to the AI model. "
    "Try again in a moment."
)


def placement_refusal_message(why: str, what: str) -> str:
    """What a reader is told when a placement rule refuses ``what`` ("AI model", "embedding model")."""
    from app.services.model_identity.call_context import peek_call_context
    from app.services.model_identity.workload import WorkloadClass

    bound = peek_call_context()
    if bound is not None and bound.workload_class is WorkloadClass.INTERACTIVE:
        return f"Nothing was sent to the {what}, because {why}. Pick another model, or ask a workspace admin."
    return (
        f"This file wasn't processed, because {why} (the {what} set up for indexing). An admin can choose another "
        "in Workspace → AI Models, then reindex the file."
    )


async def require_placement(
    config_service: ConfigurationService,
    provider: str,
    config: dict[str, Any],
    model_name: str | None = None,
    *,
    what: str = "AI model",
) -> None:
    """The placement check every model client passes before it is built (MG-15).

    Raises ``ModelNotAllowedError`` when the org's rule does not allow the model, and ``LLMUnavailableError``
    when the rule cannot be read (nothing is sent until it can be).
    """
    try:
        verdict = await model_placement(config_service).verdict(provider, config, model_name)
    except AccessPolicyUnavailable as unreadable:
        raise LLMUnavailableError(PLACEMENT_UNREADABLE) from unreadable
    if verdict.refusal is not None:
        raise ModelNotAllowedError(placement_refusal_message(verdict.refusal, what), answer_code=verdict.code)


async def build_chat_model(
    config_service: ConfigurationService,
    provider: str,
    config: dict[str, Any],
    model_name: str | None = None,
    reasoning_effort: str | None = None,
    sdk_retries: int | None = None,
) -> BaseChatModel:
    """A chat model for ``config``, built only inside the org's region pin. Not handed through the run's scope:
    it is the model a run is built on (the harness wraps it in the run's guard, ``ResolvedModel``) or the indexing
    pipeline's (VLM OCR), which no tool reaches. ``sdk_retries`` as for ``get_generator_model``."""
    await require_placement(config_service, provider, config, model_name)
    return await get_generator_model_async(provider, config, model_name, reasoning_effort, sdk_retries)


async def require_embeddings_placement(config_service: ConfigurationService, config: dict[str, Any] | None) -> None:
    """``require_placement`` for the dense embedding entry ``config`` (``None``: the built-in embedding server). A
    holder of a built client asks it again before each use, so a pin saved since binds that client too."""
    if config is None:
        await require_placement(config_service, "default", {"configuration": {}}, what="embedding model")
    else:
        await require_placement(config_service, config["provider"], config, what="embedding model")


async def build_embeddings(
    config_service: ConfigurationService, config: dict[str, Any] | None, sdk_retries: int | None = None,
) -> Embeddings:
    """The dense embedding client for ``config`` (``None``: the built-in embedding server), built only inside the
    org's region pin. ``sdk_retries`` as for ``get_embedding_model``."""
    await require_embeddings_placement(config_service, config)
    if config is None:
        return await asyncio.to_thread(get_default_embedding_model)
    return await asyncio.to_thread(get_embedding_model, config["provider"], config, None, sdk_retries=sdk_retries)


async def build_multimodal_embeddings(config_service: ConfigurationService, provider_config: Any) -> Any:  # noqa: ANN401 - MultimodalProviderConfig and its provider
    """The image-embedding provider for ``provider_config``, built only inside the org's region pin."""
    from app.services.embeddings.multimodal.factory import MultimodalEmbeddingFactory

    configuration = {
        field: value
        for field, value in (("region", provider_config.region_name), ("endpoint", provider_config.base_url))
        if value
    }
    await require_placement(
        config_service,
        str(provider_config.provider or "default"),
        {"configuration": configuration},
        provider_config.model_name,
        what="embedding model",
    )
    return MultimodalEmbeddingFactory.create(provider_config)


async def build_image_generation_model(config_service: ConfigurationService, provider: str, config: dict[str, Any]) -> Any:  # noqa: ANN401 - an ImageGenerationAdapter
    """The image-generation adapter for ``config``, built only inside the org's region pin (MG-15), and handed
    through the run's scope inside a harness run: metered per image, admitted, classification-checked (WP-55)."""
    from app.utils.aimodels import (
        get_image_generation_model,  # provider SDKs load only when asked for
    )

    await require_placement(config_service, provider, config)
    return scoped_image_model(get_image_generation_model(provider, config), config)


def _select_default_config(configs: list) -> dict | None:
    """Prefer isDefault entry; else first entry; else None."""
    if not configs:
        return None
    return next((c for c in configs if c.get("isDefault")), configs[0])


async def _instantiate_llm_from_configs(
    config_service: ConfigurationService,
    llm_configs: list | None,
    *,
    reasoning_effort: str | None = None,
    sdk_retries: int | None = None,
) -> Tuple[BaseChatModel, dict[str, Any]]:
    """Pick and instantiate an LLM from a config list (default first, then any); a model outside the org's
    region pin is skipped, and when every one is outside it, the first refusal is raised."""
    if not llm_configs:
        raise LLMNotConfiguredError()

    ordered = [c for c in llm_configs if c.get("isDefault", False)] + list(llm_configs)
    refused: ModelNotAllowedError | None = None
    for config in ordered:
        try:
            await require_placement(config_service, config["provider"], config)
        except ModelNotAllowedError as outside:
            refused = refused or outside
            continue
        llm = await asyncio.to_thread(
            get_generator_model, config["provider"], config, None, reasoning_effort, sdk_retries,
        )
        if llm:
            return scoped_helper_model(llm, config), config

    if refused is not None:
        raise refused
    raise ValueError("No LLM found")


async def _try_resolve_role_llm(
    config_service: ConfigurationService,
    ai_models: dict | None,
    role: str,
    *,
    reasoning_effort: str | None = None,
    sdk_retries: int | None = None,
) -> Tuple[BaseChatModel, dict[str, Any]] | None:
    """Resolve modelRoles assignment to an instantiated LLM, or None."""
    model_roles: dict = (ai_models or {}).get("modelRoles") or {}
    assignment = model_roles.get(role)
    if not assignment:
        return None

    model_type: str = assignment.get("modelType", "")
    model_key: str = assignment.get("modelKey", "")
    bucket: list = (ai_models or {}).get(model_type, []) or []
    matched = next(
        (cfg for cfg in bucket if cfg.get("modelKey") == model_key), None
    )
    if not matched:
        return None

    await require_placement(config_service, matched["provider"], matched)
    llm = await asyncio.to_thread(
        get_generator_model, matched["provider"], matched, None, reasoning_effort, sdk_retries,
    )
    if llm:
        return scoped_helper_model(llm, matched), matched
    return None


async def get_llm(
    config_service: ConfigurationService,
    llm_configs: Any = None,
    *,
    reasoning_effort: str | None = None,
    sdk_retries: int | None = None,
) -> Tuple[BaseChatModel, dict[str, Any]]:
    if not llm_configs:
        ai_models = await load_ai_models(config_service)
        llm_configs = ai_models.get("llm")
    return await _instantiate_llm_from_configs(
        config_service, llm_configs, reasoning_effort=reasoning_effort, sdk_retries=sdk_retries,
    )


async def get_llm_for_role(
    config_service: ConfigurationService,
    role: str,
    *,
    reasoning_effort: str | None = None,
    sdk_retries: int | None = None,
) -> Tuple[BaseChatModel, dict[str, Any]]:
    """Return the LLM assigned to *role*, falling back to the default LLM.

    ``sdk_retries`` is how many times the model's SDK client repeats a failed request on its own (``None``: the SDK's
    default). Indexing call sites pass ``INDEXING_SDK_RETRIES``: their paid calls are repeated by the gateway alone.

    Reads ``modelRoles`` from the AI models config blob. If the role is
    assigned to a specific model (identified by ``modelKey`` within a
    ``modelType`` bucket), that model is instantiated and returned.

    Falls back to :func:`get_llm` when:
    - the role is not assigned, or
    - the assigned ``modelKey`` / ``modelType`` cannot be resolved.

    This ensures full backward compatibility: existing deployments without
    ``modelRoles`` in their config are unaffected.
    """
    try:
        ai_models = await load_ai_models(config_service)
        resolved = await _try_resolve_role_llm(
            config_service, ai_models, role, reasoning_effort=reasoning_effort, sdk_retries=sdk_retries
        )
        if resolved:
            return resolved
    except Exception:
        pass

    return await get_llm(config_service, reasoning_effort=reasoning_effort or "low", sdk_retries=sdk_retries)


async def get_embedding_model_config(config_service: ConfigurationService) -> dict | None:
    try:
        ai_models = await load_ai_models(config_service)
        embedding_configs = ai_models.get("embedding")
        if not embedding_configs:
            return None
        return embedding_configs[0]
    except Exception as e:
        raise e


async def is_local_cpu_embedding_configured(
    config_service: ConfigurationService,
    logger: logging.Logger,
) -> bool:
    """Whether records will be embedded on local CPU rather than a hosted API.

    Read once at startup by the services that hold CPU back from their
    heavy-parse ceiling for the embedding server (see
    ``resource_governor.policy.EMBEDDING_CPU_RESERVATION``). Mirrors the
    config selection ``VectorStore._get_embedding_model`` makes, so both
    reach the same verdict for a given deployment, and treats an
    unconfigured deployment as local because that path falls back to
    ``get_default_embedding_model()``.

    An unreadable config also resolves to ``True``: over-reserving two cores
    costs some parse throughput, while under-reserving them starves the
    embedding step that every record has to pass through.
    """
    try:
        ai_models = await config_service.get_config(
            config_node_constants.AI_MODELS.value, use_cache=False
        )
        embedding_configs = (ai_models or {}).get("embedding") or []
        if not embedding_configs:
            return True
        config = next(
            (c for c in embedding_configs if c.get("isDefault")), embedding_configs[0]
        )
        configuration = config.get("configuration") or {}
        return is_local_cpu_embedding_provider(
            config.get("provider"), configuration.get("endpoint")
        )
    except Exception:
        logger.warning(
            "Could not resolve the embedding provider; assuming a local CPU model",
            exc_info=True,
        )
        return True


async def get_image_generation_config(config_service: ConfigurationService) -> dict | None:
    """Return the active image-generation model config, or ``None`` if unset.

    Prefers the config marked ``isDefault``; falls back to the first entry.
    Mirrors the shape returned for other model types under the ``aiModels``
    namespace.
    """
    ai_models = await load_ai_models(config_service)
    return _select_default_config(ai_models.get("imageGeneration") or [])


async def _get_default_config(
    config_service: ConfigurationService,
    bucket: str,
) -> dict | None:
    """Shared helper: return the default entry under ``ai_models[bucket]``.

    Gracefully returns ``None`` when the whole ``aiModels`` blob is missing
    (e.g. on a brand-new install) so callers — and the chat UI fallback —
    can treat TTS/STT as simply unconfigured instead of erroring.
    """
    ai_models = await load_ai_models(config_service)
    if not ai_models:
        return None
    return _select_default_config(ai_models.get(bucket) or [])


async def get_tts_config(config_service: ConfigurationService) -> dict | None:
    """Return the active TTS model config, or ``None`` if unset."""
    return await _get_default_config(config_service, "tts")


async def get_stt_config(config_service: ConfigurationService) -> dict | None:
    """Return the active STT model config, or ``None`` if unset."""
    return await _get_default_config(config_service, "stt")


async def get_decision_config(config_service: ConfigurationService) -> dict | None:
    return await _get_default_config(config_service, "decision")


async def get_decision_model_instance(
    config_service: ConfigurationService,
) -> tuple[DecisionModel, dict] | None:
    config = await get_decision_config(config_service)
    if not config:
        return None
    await require_placement(config_service, config["provider"], config)
    return get_decision_model(config["provider"], config), config


async def get_tts_model_instance(
    config_service: ConfigurationService,
) -> Tuple[TTSAdapter, dict] | None:
    config = await get_tts_config(config_service)
    if not config:
        return None
    await require_placement(config_service, config["provider"], config)
    return get_tts_model(config["provider"], config), config


async def get_stt_model_instance(
    config_service: ConfigurationService,
) -> Tuple[STTAdapter, dict] | None:
    config = await get_stt_config(config_service)
    if not config:
        return None
    await require_placement(config_service, config["provider"], config)
    return get_stt_model(config["provider"], config), config
