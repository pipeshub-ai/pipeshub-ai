"""The run's model: the pinned choice, resolved and reached the way the agent route does."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, cast

from app.harness.placement import deployment_region, model_regions
from app.utils.env_flags import (
    AGENT_HARNESS_BUDGET_LEDGER,
    HARNESS_PROVIDER_STATE,
    env_flag,
)
from harness_core.budget.errors import BudgetRefused, BudgetUnavailable
from harness_core.budget.metered import (
    LedgerChildBudget,
    MeteredTransport,
    RunBudgetView,
)
from harness_core.clock import Clock, SystemClock
from harness_core.driver import RunRefused
from harness_core.gateway.priority_gate import upstream_key
from harness_core.kernel.durable.adapters import StreamingModelPort
from harness_core.kernel.durable.ports import ModelBackoff, ModelFailed
from harness_core.models.retry_after import provider_wait_s
from harness_core.models.transport_model import TransportModel
from harness_core.models.transports.admitted import AdmittedTransport
from harness_core.priority import class_for_run_kind

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from app.harness.assembly.context import RunContext
    from app.harness.classification import ModelGuard
    from harness_core.budget.ports import BudgetAccounts
    from harness_core.budget.types import RunAccountRef
    from harness_core.budget.wrap_up import WrapUpReserve
    from harness_core.gateway.priority_gate import ProviderAdmission
    from harness_core.ids import IdSource
    from harness_core.kernel.core.messages import AssistantMessage
    from harness_core.kernel.durable.ports import (
        DeltaKind,
        DurableModelPort,
        ModelCall,
        SampledTurn,
    )
    from harness_core.models.transports.base import LLMTransport
    from harness_core.priority import PriorityClass

# A provider that says "slow down" without saying for how long.
RATE_LIMIT_BACKOFF_S = 20.0
# Node (the ledger) unreachable: the run yields and asks again, it is not failed.
LEDGER_BACKOFF_S = 30.0
# "off" runs without charging the ledger (as before P6.5); anything else charges it.


class ClassifiedModelPort:
    """Provider errors the way the kernel acts on them: a rate limit is a
    backoff (the run yields and resumes, REPORT 6.17); anything else fails the
    call with the legacy error code, read from the whole exception chain (a
    server error inside a stream or a dropped connection is ``server_error``,
    FIX-R2 B4), which the kernel retries when it is transient and otherwise
    fails the run, the answer saying what happened as the legacy finalizer did.
    The provider's message never leaves this process."""

    def __init__(self, inner: DurableModelPort, clock: Clock | None = None) -> None:
        self._inner = inner
        self._clock = clock or SystemClock()

    async def sample(
        self, call: ModelCall, on_delta: Callable[[DeltaKind, str], Awaitable[None]]
    ) -> SampledTurn:
        from app.agents.agent_loop.error_classification import classify_model_exception

        try:
            return await self._inner.sample(call, on_delta)
        except (ModelBackoff, ModelFailed):
            raise
        except BudgetUnavailable as exc:
            raise ModelBackoff(LEDGER_BACKOFF_S, provider=False) from exc
        except BudgetRefused as exc:
            # The run (or its tree) cannot pay for another call: it ends, named for why.
            raise ModelFailed(exc.reason_code, budget_scope=exc.budget_scope) from exc
        except Exception as exc:
            code = classify_model_exception(exc)
            if code == "rate_limit":
                # The provider's own Retry-After when it sends one (ERR-UX2); the stream shows this wait.
                raise ModelBackoff(
                    provider_wait_s(exc, now=self._clock.now(), default_s=RATE_LIMIT_BACKOFF_S)
                ) from exc
            raise ModelFailed(code) from exc

    def restore(
        self, raw: bytes, *, provider: str, transport: str
    ) -> AssistantMessage | None:
        return self._inner.restore(raw, provider=provider, transport=transport)


@dataclass(frozen=True)
class ResolvedModel:
    # The provider's own chat model, only to build transports from and to read what it supports; never handed to a
    # tool or a helper, whose calls would then skip the run's ledger and provider slot (``chat_model`` is theirs).
    raw_llm: Any
    provider: str
    is_multimodal: bool
    context_length: int | None
    is_reasoning: bool
    port: DurableModelPort
    # The transport ``port`` samples through; the run's other model calls
    # (the intent pre-run, child agents, the tools' helpers) use it too.
    transport: LLMTransport | None = None
    transport_name: str = "direct"
    # Where the route's calls are served, from the configured entry it was built from (MG-15); ``None``: unknown.
    region: str | None = None
    # G-45: the provider limit the run's calls count against, derived as the model gateway derives it for the same
    # account (``upstream_key``), so loop and engine calls on one account share one limit; empty, the provider's.
    admission_key: str = ""
    # GA-15: the model the run's summaries call when its pin maps the compaction role; ``None``: this one.
    compaction: ResolvedModel | None = None

    def chat_model(self, owner: object = None) -> Any:  # noqa: ANN401 - a LangChain chat model
        """The LangChain model the run's tools and their helpers are handed (chat state, tool state, the router):
        each of its calls is a call of ``transport``, metered and admitted as the run's turns are (WP-49), and it
        must be the classification guard's (``ModelGuard.wrap``, WP-55). A test double without a
        transport has nothing to go through. ``owner``: the run scope a helper's model is handed out by, outside
        which it refuses to be called."""
        if self.transport is None:
            return self.raw_llm
        from app.harness.run_chat_model import RunChatModel

        return RunChatModel(
            transport=self.transport, model_name=self.transport.model_name, provider_name=self.provider, owner=owner
        )


def build_transport(
    llm: Any,  # noqa: ANN401 - LangChain model
    *,
    model_name: str,
    model_key: str | None,
    max_images: int | None = None,
    cache_scope: tuple[str, str | None] = ("", None),
    context_window: int | None = None,
) -> tuple[LLMTransport, str]:
    """The same arm the legacy factory takes: the provider SDK when the
    deployment chose ``direct`` and one exists, else LangChain; capped at
    ``max_images`` per request and at the per-turn output cap (what fits back
    sized by the model's ``context_window``), with the Opik project and the
    provider's prompt caching keyed by ``cache_scope`` (org, conversation), as
    the factory builds both arms."""
    from app.agent_loop_lib.transport.prompt_cache import (
        CacheScope,
        PromptCacheSettings,
    )
    from app.agents.agent_loop.factory import DIRECT_TRANSPORT, _transport_provider
    from app.agents.agent_loop.image_guard import with_image_cap
    from app.agents.agent_loop.output_cap_transport import (
        max_turn_output_chars,
        with_output_cap,
    )
    from app.harness.assembly.loop import opik_tracing

    cache_settings = PromptCacheSettings.from_env()
    scope = CacheScope(cache_scope[0], cache_scope[1])
    output_cap = max_turn_output_chars()
    if _transport_provider() == DIRECT_TRANSPORT:
        from app.agents.agent_loop.direct_transport import build_direct_transport

        direct = build_direct_transport(
            llm, model_name=model_name, model_key=model_key,
            cache_settings=cache_settings, cache_scope=scope,
        )
        if direct is not None:
            if provider_state_enabled():
                # P4.3: what a resumed tool turn must hand the provider back (signatures, reasoning items).
                _ = direct.keep_provider_state()
            capped = with_output_cap(with_image_cap(direct, max_images), output_cap, context_window=context_window)
            return capped, "direct"
    from app.agents.agent_loop.langchain_transport import LangChainTransport
    from app.agents.agent_loop.prompt_cache import cache_strategy_for

    _, project = opik_tracing()
    langchain = LangChainTransport(
        llm, model_name=model_name, model_key=model_key, opik_project_name=project, max_images_per_request=max_images,
        cache_strategy=cache_strategy_for(llm, model_name=model_name, settings=cache_settings, scope=scope),
    )
    return with_output_cap(langchain, output_cap, context_window=context_window), "langchain"


# The code legacy's `/chat/stream` answers a model it cannot start with.
LLM_INITIALIZATION_FAILED = "llm_initialization_failed"


def model_refusal(exc: Exception) -> RunRefused:
    """Why a run's model could not be started, as the journal keeps it: the user-facing message legacy shows,
    never the provider's or the config store's own text."""
    from app.agents.agent_loop.error_classification import classify_exception
    from app.api.routes.chatbot import CHAT_MODEL_START_FAILED
    from app.utils.llm import ModelNotAllowedError

    if isinstance(exc, ModelNotAllowedError) and exc.answer_code is not None:
        # The model funnel's placement check (MG-15) refused it first: the run ends with the code the rule names.
        return RunRefused(exc.answer_code, str(exc))
    code, message = classify_exception(exc)
    return RunRefused(LLM_INITIALIZATION_FAILED, CHAT_MODEL_START_FAILED if code == "unknown" else message)


def resolved_model(run: RunContext, llm: Any, config: Mapping[str, Any]) -> ResolvedModel:  # noqa: ANN401 - a chat model
    """The run's model from the chat model built for it and the configured entry it was built from."""
    from app.agents.agent_loop.context import AgentContext
    from app.harness.assembly.loop import opik_tracing
    from app.utils.image_policy import resolve_image_policy
    from harness_core.models.transports.opik_tracing import wrap_if_enabled

    provider = str(config.get("provider") or run.model.provider)
    model_name = run.model.model_name or str(getattr(llm, "model_name", "") or run.model.model)
    multimodal = bool(config.get("isMultimodal", False))
    # Enforced at the wire as legacy's factory does: the count admitted at the source was decided for
    # whichever model owned that state.
    max_images = resolve_image_policy(provider=provider, is_multimodal=multimodal).max_images_per_request
    transport, name = build_transport(
        llm, model_name=model_name, model_key=run.model.model_key, max_images=max_images,
        cache_scope=(run.org_id, run.session_id),
        # Read as the run's AgentContext reads it (`configured_context_window`).
        context_window=AgentContext._window_or_none(config.get("contextLength")),  # pyright: ignore[reportPrivateUsage]
    )
    traced, project = opik_tracing()
    transport = wrap_if_enabled(transport, enabled=traced, project_name=project)
    return ResolvedModel(
        raw_llm=llm,
        provider=provider,
        is_multimodal=multimodal,
        context_length=config.get("contextLength"),
        is_reasoning=bool(config.get("isReasoning", False)),
        port=_port(transport, provider, name),
        transport=transport,
        transport_name=name,
        region=model_regions().region_of(provider, _configuration(config), model_name, deployment=deployment_region()),
        admission_key=upstream_key(provider, _configuration(config)),
    )


def _configuration(config: Mapping[str, Any]) -> Mapping[str, object] | None:
    configuration = config.get("configuration")
    return cast("Mapping[str, object]", configuration) if isinstance(configuration, dict) else None


def _port(transport: LLMTransport, provider: str, name: str) -> DurableModelPort:
    return ClassifiedModelPort(
        StreamingModelPort(TransportModel(transport), provider=provider.lower() or "unknown", transport=name)
    )


def ledger_enabled() -> bool:
    return env_flag(AGENT_HARNESS_BUDGET_LEDGER)


def provider_state_enabled() -> bool:
    return env_flag(HARNESS_PROVIDER_STATE)


def admitted(
    model: ResolvedModel,
    admission: ProviderAdmission | None,
    priority_class: PriorityClass | None,
    run_kind: str | None,
    *,
    org: str | None = None,
) -> ResolvedModel:
    """The run's model with every call taking its provider slot first, at the run's class (G-45): the claim's, or
    as its run kind gives it under a Node that names none. Applied before ``metered``, so the slot sits next to the
    provider under the ledger's reservation. Unchanged without an admission or a transport (a test double).
    ``org``: the run's org, which the slot is counted for inside a shared upstream key (WP56-FAIR)."""
    if admission is None or model.transport is None:
        return model
    transport = AdmittedTransport(
        model.transport,
        admission=admission,
        priority_class=priority_class or class_for_run_kind(run_kind),
        provider=model.provider,
        key=model.admission_key or None,
        org=org,
    )
    return replace(model, transport=transport, port=_port(transport, model.provider, model.transport_name))


def metered(
    model: ResolvedModel,
    *,
    accounts: BudgetAccounts | None,
    account: RunAccountRef,
    pricing_model: str,
    ids: IdSource,
    wrap_up: WrapUpReserve | None = None,
) -> tuple[ResolvedModel, LedgerChildBudget | None]:
    """The run's model with every call charged to its ledger account (P6.5), and the
    child budget a deep run sizes its sub-agents' shares by. Unchanged when the claim
    has no ledger route, the model no transport (a test double), or the ledger is off.
    ``wrap_up``: the run's wrap-up reserve, which the answer-only turn draws on (BUDGETEND)."""
    if accounts is None or model.transport is None or not ledger_enabled():
        return model, None
    view = RunBudgetView()
    transport = MeteredTransport(
        model.transport, accounts=accounts, account=account, pricing_model=pricing_model, ids=ids, view=view,
        wrap_up=wrap_up,
    )
    return (
        replace(model, transport=transport, port=_port(transport, model.provider, model.transport_name)),
        LedgerChildBudget(view),
    )


@dataclass(frozen=True)
class HelperMeter:
    """How a model a helper builds for itself is made the run's: admitted at the run's class, then charged to the
    run's account at that model's own price. No wrap-up reserve: only the run's answer-only turn draws on that."""

    admission: ProviderAdmission | None
    priority_class: PriorityClass | None
    run_kind: str | None
    accounts: BudgetAccounts | None
    account: RunAccountRef
    ids: Callable[[], IdSource]

    def __call__(self, model: ResolvedModel, pricing_model: str) -> ResolvedModel:
        """``pricing_model``: the model's pricing name."""
        return metered(
            admitted(model, self.admission, self.priority_class, self.run_kind, org=self.account.tenant),
            accounts=self.accounts,
            account=self.account,
            pricing_model=pricing_model,
            ids=self.ids(),
        )[0]

    def image(self, adapter: Any, provider: str, guard: ModelGuard) -> Any:  # noqa: ANN401
        """An image-generation adapter as the run's: checked by ``guard``, priced per image on the run's account
        (when the ledger is on), admitted at the run's class (WP-55)."""
        from app.harness.run_image_model import RunImageModel

        if isinstance(adapter, RunImageModel):
            return adapter
        return RunImageModel(
            adapter,
            guard=guard,
            configured_provider=provider,
            admission=self.admission,
            priority_class=self.priority_class or class_for_run_kind(self.run_kind),
            accounts=self.accounts if ledger_enabled() else None,
            account=self.account,
            ids=self.ids(),
        )


def helper_meter(
    *,
    admission: ProviderAdmission | None,
    priority_class: PriorityClass | None,
    run_kind: str | None,
    accounts: BudgetAccounts | None,
    account: RunAccountRef,
    ids: Callable[[], IdSource],
) -> HelperMeter:
    return HelperMeter(admission, priority_class, run_kind, accounts, account, ids)


class RunHelperModels:
    """The run's scope for ``app.utils.run_model_scope``: a model a tool's helper builds from the org's configuration
    (a model role's chat model, the default's, the image-generation model) comes back as the run's: reached the way
    the run's own model is (``build_transport``, traced as it is), metered and admitted by ``meter``, and checked
    against the conversation's classification by ``guard`` (OWD-15, WP-55) before anything is sent."""

    def __init__(self, run: RunContext, meter: HelperMeter, guard: ModelGuard) -> None:
        self._run = run
        self._meter = meter
        self._guard = guard

    def __call__(self, llm: Any, config: Mapping[str, Any]) -> Any:  # noqa: ANN401 - LangChain chat models
        from app.harness.assembly.loop import opik_tracing
        from app.harness.run_chat_model import RunChatModel
        from harness_core.models.transports.opik_tracing import wrap_if_enabled

        if isinstance(llm, RunChatModel):
            return llm
        run = self._run
        configuration = config.get("configuration")
        configured = configuration.get("model", "") if isinstance(configuration, dict) else ""
        name = str(getattr(llm, "model_name", "") or getattr(llm, "model", "") or configured or "")
        provider = str(config.get("provider") or "")
        transport, transport_name = build_transport(
            llm, model_name=name, model_key=config.get("modelKey"), cache_scope=(run.org_id, run.session_id),
        )
        traced, project = opik_tracing()
        transport = wrap_if_enabled(transport, enabled=traced, project_name=project)
        resolved = ResolvedModel(
            raw_llm=llm,
            provider=provider,
            is_multimodal=bool(config.get("isMultimodal", False)),
            context_length=config.get("contextLength"),
            is_reasoning=bool(config.get("isReasoning", False)),
            port=_port(transport, provider, transport_name),
            transport=transport,
            transport_name=transport_name,
            # G-45 (WP-61): the helper's upstream shares one limit with every other call on that account.
            admission_key=upstream_key(provider, _configuration(config)),
        )
        return self._guard.wrap(self._meter(resolved, name), name).chat_model(owner=self)

    def image(self, adapter: Any, config: Mapping[str, Any]) -> Any:  # noqa: ANN401 - an ImageGenerationAdapter
        return self._meter.image(adapter, str(config.get("provider") or ""), self._guard)


def helper_model_scope(run: RunContext, meter: HelperMeter, guard: ModelGuard) -> RunHelperModels:
    return RunHelperModels(run, meter, guard)


__all__ = [
    "LLM_INITIALIZATION_FAILED",
    "ClassifiedModelPort",
    "HelperMeter",
    "ResolvedModel",
    "RunHelperModels",
    "admitted",
    "build_transport",
    "helper_meter",
    "helper_model_scope",
    "metered",
    "model_refusal",
    "resolved_model",
]
