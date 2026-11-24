"""Resolve ``modelKey: "auto"`` into a configured model and reasoning effort."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.config.constants.service import config_node_constants
from app.services.ai_models.auto.classifier import (
    CLASSIFIER_TIMEOUT_S,
    Classification,
    ClassifierError,
    classify,
    classify_with_decision,
    pick_classifier,
)
from app.services.ai_models.auto.pool import (
    Candidate,
    Need,
    apply_filters,
    build_pool,
    choose_effort,
    estimate_prompt_tokens,
    pick_for_need,
)
from app.utils.llm import get_decision_model_instance

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService

AUTO_MODEL_KEY = "auto"
_MIN_CHAT_CLASSIFIER_S = 1.5

_GREETING = re.compile(
    r"^\s*(hi|hello|hey|hiya|yo|good (morning|afternoon|evening)|thanks?( you)?|thank you|thx|ty|"
    r"cheers|ok(ay)? thanks?|great,? thanks?|bye|goodbye)[\s!.?,]*$",
    re.IGNORECASE,
)
# Short acknowledgements that ask nothing ("thanks, that helps!"); a question mark sends them to the classifier.
_ACKNOWLEDGEMENT = re.compile(
    r"^\s*(thanks|thank you|thx|ty|great|perfect|awesome|cool|nice|got it|understood)\b[^?]{0,40}$",
    re.IGNORECASE,
)
_CONTINUATION = re.compile(
    r"^\s*(ok(ay)?|yes|yep|yeah|sure|go on|go ahead|continue|proceed|do it|please do|sounds good|y)[\s!.?,]*$",
    re.IGNORECASE,
)


def is_auto_model_key(model_key: str | None) -> bool:
    return (model_key or "").strip().lower() == AUTO_MODEL_KEY


@dataclass
class AutoRequest:
    query: str
    previous_conversations: list[dict[str, Any]] = field(default_factory=list)
    attachments: list[dict[str, Any]] = field(default_factory=list)
    # The agent's own model list; None on chat routes.
    agent_models: list[Any] | None = None
    agent_route: bool = False
    # Agent default effort acts as a floor; an explicit request effort wins outright.
    effort_floor: str | None = None
    explicit_effort: str | None = None


@dataclass
class AutoDecision:
    model_key: str | None
    model_name: str | None
    reasoning_effort: str | None
    reason: str
    need: str | None = None
    needs_reasoning: bool = False
    fallback: bool = False
    classifier_ms: int | None = None
    classifier_model: str | None = None
    entry: dict[str, Any] | None = None

    def model_info(self) -> dict[str, Any]:
        """The ``modelInfo`` the final answer carries for this turn."""
        entry = self.entry or {}
        configuration = (
            entry.get("configuration")
            if isinstance(entry.get("configuration"), dict)
            else {}
        )
        friendly = entry.get("modelFriendlyName") or configuration.get(
            "modelFriendlyName"
        )
        info: dict[str, Any] = {
            "modelKey": self.model_key,
            "modelName": self.model_name,
            "modelProvider": entry.get("provider"),
            "autoRouted": True,
            "autoReason": self.reason,
        }
        if (
            friendly
            and len(
                [
                    n
                    for n in str(configuration.get("model") or "").split(",")
                    if n.strip()
                ]
            )
            <= 1
        ):
            info["modelFriendlyName"] = friendly
        if self.reasoning_effort:
            info["reasoningEffort"] = self.reasoning_effort
        return {key: value for key, value in info.items() if value is not None}


def _has_image(attachments: list[dict[str, Any]]) -> bool:
    return any(
        isinstance(att, dict)
        and str(att.get("mimeType") or "").lower().startswith("image/")
        for att in attachments or []
    )


def _pinned(
    pool: list[Candidate], previous_conversations: list[dict[str, Any]]
) -> tuple[Candidate | None, str | None]:
    """The model the last Auto-routed answer used, if it is still a candidate, and that turn's effort."""
    for turn in reversed(previous_conversations or []):
        if not isinstance(turn, dict) or turn.get("role") != "bot_response":
            continue
        if not turn.get("autoRouted"):
            return None, None
        key, name = turn.get("modelKey"), turn.get("modelName")
        for candidate in pool:
            if candidate.model_key == key and candidate.model_name == name:
                effort = turn.get("reasoningEffort")
                return candidate, effort if isinstance(effort, str) else None
        return None, None
    return None, None


def _default_candidate(pool: list[Candidate]) -> Candidate | None:
    if not pool:
        return None
    return next((c for c in pool if c.is_default), pool[0])


def _decide(
    candidate: Candidate, effort: str | None, reason: str, **extra: object
) -> AutoDecision:
    return AutoDecision(
        model_key=candidate.model_key,
        model_name=candidate.model_name,
        reasoning_effort=effort,
        reason=reason,
        entry=candidate.entry,
        **extra,
    )


def _fallback(
    full_pool: list[Candidate], request: AutoRequest, reason: str, **extra: object
) -> AutoDecision:
    # Agent routes leave the choice to the agent's own first model.
    if request.agent_route:
        return AutoDecision(None, None, None, reason=reason, fallback=True, **extra)
    default = _default_candidate(full_pool)
    if default is None:
        return AutoDecision(None, None, None, reason=reason, fallback=True, **extra)
    return _decide(default, None, reason, fallback=True, **extra)


async def resolve_auto_model(
    config_service: ConfigurationService,
    request: AutoRequest,
    logger: logging.Logger | None = None,
) -> AutoDecision:
    log = logger or logging.getLogger(__name__)
    started = time.monotonic()
    try:
        decision = await _resolve(config_service, request)
    # Auto must never fail the turn: any error falls back to the default model.
    except Exception as exc:
        log.warning(
            "auto_model_decision failed, using the default model: %s",
            exc,
            exc_info=True,
        )
        decision = AutoDecision(
            None, None, None, reason=f"error: {type(exc).__name__}", fallback=True
        )
        if not request.agent_route:
            try:
                ai_models = (
                    await config_service.get_config(
                        config_node_constants.AI_MODELS.value, use_cache=True
                    )
                    or {}
                )
                default = _default_candidate(build_pool(ai_models.get("llm") or []))
            # A key of None already means "the route's default model".
            except Exception:
                default = None
            if default is not None:
                decision = _decide(default, None, decision.reason, fallback=True)
    log.info(
        "auto_model_decision model=%s/%s effort=%s need=%s reasoning=%s reason=%s fallback=%s "
        "classifier=%s classifier_ms=%s total_ms=%d agent=%s",
        decision.model_key,
        decision.model_name,
        decision.reasoning_effort,
        decision.need,
        decision.needs_reasoning,
        decision.reason,
        decision.fallback,
        decision.classifier_model,
        decision.classifier_ms,
        int((time.monotonic() - started) * 1000),
        request.agent_route,
    )
    return decision


async def _decision_model_configured(config_service: ConfigurationService) -> bool:
    """Auto runs only when a decision model is saved.

    A saved model that cannot be built still counts: the classifier reports
    that failure. No saved model does not, and the turn uses the default model.
    """
    try:
        return await get_decision_model_instance(config_service) is not None
    except Exception:
        return True


async def _resolve(
    config_service: ConfigurationService, request: AutoRequest
) -> AutoDecision:
    ai_models = (
        await config_service.get_config(
            config_node_constants.AI_MODELS.value, use_cache=True
        )
        or {}
    )
    full_pool = build_pool(
        ai_models.get("llm") or [],
        request.agent_models if request.agent_models else None,
    )
    if not full_pool:
        return _fallback(full_pool, request, "no_candidates")
    if len(full_pool) == 1:
        # One model: nothing to route, and its configured effort applies as usual.
        return _decide(full_pool[0], None, "single_model")
    if not await _decision_model_configured(config_service):
        return _fallback(full_pool, request, "no_decision_model")

    pool = apply_filters(
        full_pool,
        needs_vision=_has_image(request.attachments),
        needs_tools=request.agent_route,
        prompt_tokens=estimate_prompt_tokens(
            request.query, request.previous_conversations
        ),
    )
    if not pool:
        return _fallback(full_pool, request, "no_candidates_after_filters")

    pinned, pinned_effort = _pinned(pool, request.previous_conversations)

    def effort_for(
        candidate: Candidate, need: Need, *, needs_reasoning: bool = False
    ) -> str | None:
        return choose_effort(
            candidate,
            need,
            needs_reasoning=needs_reasoning,
            floor=_agent_floor(request),
            explicit=request.explicit_effort,
        )

    if len(pool) == 1:
        only = pool[0]
        return _decide(
            only, effort_for(only, "standard"), "only_candidate", need="standard"
        )

    if pinned is not None and _CONTINUATION.match(request.query or ""):
        effort = (
            effort_for(pinned, "standard") if request.explicit_effort else pinned_effort
        )
        if pinned.is_reasoning and not effort:
            effort = effort_for(pinned, "standard")
        return _decide(
            pinned, effort if pinned.is_reasoning else None, "continuation_pinned"
        )

    query = request.query or ""
    if (
        _GREETING.match(query) or _ACKNOWLEDGEMENT.match(query)
    ) and not request.attachments:
        chosen = pick_for_need(pool, "light", pinned)
        return _decide(chosen, effort_for(chosen, "light"), "greeting", need="light")

    largest = max(c.size_rank for c in pool)
    if pinned is not None and pinned.size_rank == largest and not pinned.is_reasoning:
        return _decide(pinned, None, "pinned_largest", need="heavy")
    if len({c.size_rank for c in pool}) == 1 and not any(c.is_reasoning for c in pool):
        chosen = pick_for_need(pool, "standard", pinned)
        return _decide(chosen, None, "single_size", need="standard")

    started = time.monotonic()
    result, classifier_name, decision_error = await _classify_with_decision_model(
        config_service, request
    )
    chat_error: ClassifierError | None = None
    if result is None:
        chat_classifier = pick_classifier(pool, ai_models)
        classifier_name = chat_classifier.model_name
        # One budget for the whole step: a slow decision model leaves less for this call.
        remaining = CLASSIFIER_TIMEOUT_S - (time.monotonic() - started)
        try:
            result = await classify(
                chat_classifier,
                request.query,
                request.previous_conversations,
                timeout_s=max(remaining, _MIN_CHAT_CLASSIFIER_S),
            )
        except ClassifierError as exc:
            chat_error = exc
    if result is None:
        exc = chat_error
        elapsed = int((time.monotonic() - started) * 1000)
        if pinned is not None:
            # Keep the conversation where it is rather than moving it on a guess.
            return _decide(
                pinned,
                pinned_effort if pinned.is_reasoning else None,
                f"classifier_{exc}; kept pinned",
                fallback=True,
                classifier_ms=elapsed,
                classifier_model=classifier_name,
            )
        # The filtered pool, so an image turn still gets a model that can see it.
        return _fallback(
            pool,
            request,
            f"classifier_{exc}",
            classifier_ms=elapsed,
            classifier_model=classifier_name,
        )
    elapsed = int((time.monotonic() - started) * 1000)
    chosen = pick_for_need(pool, result.need, pinned)
    reason = f"classified_{result.need}" + (
        "+reasoning" if result.needs_reasoning else ""
    )
    if pinned is not None and chosen is pinned:
        reason += "; kept pinned"
    if decision_error is not None:
        reason += f"; decision_{decision_error}"
    return _decide(
        chosen,
        effort_for(chosen, result.need, needs_reasoning=result.needs_reasoning),
        reason,
        need=result.need,
        needs_reasoning=result.needs_reasoning,
        classifier_ms=elapsed,
        classifier_model=classifier_name,
    )


async def _classify_with_decision_model(
    config_service: ConfigurationService, request: AutoRequest
) -> tuple[Classification | None, str | None, ClassifierError | None]:
    """Rate the turn with the configured decision model, if any; on failure the chat classifier takes over."""
    try:
        decision_model = await get_decision_model_instance(config_service)
    except Exception as exc:
        return None, None, ClassifierError(f"error: {type(exc).__name__}")
    if decision_model is None:
        return None, None, None
    client, _config = decision_model
    try:
        result = await classify_with_decision(
            client, request.query, request.previous_conversations
        )
    except ClassifierError as exc:
        return None, client.model_name, exc
    return result, client.model_name, None


def _agent_floor(request: AutoRequest) -> str | None:
    if not request.agent_route:
        return None
    # Agent turns run tool loops; low effort makes them skip searches and stop early.
    return (
        request.effort_floor
        if request.effort_floor in ("medium", "high", "max")
        else "medium"
    )


async def resolve_requested_model(
    config_service: ConfigurationService,
    *,
    model_key: str | None,
    model_name: str | None,
    reasoning_effort: str | None,
    query: str,
    previous_conversations: list[dict[str, Any]] | None = None,
    attachments: list[dict[str, Any]] | None = None,
    agent_models: list[Any] | None = None,
    agent_route: bool = False,
    effort_floor: str | None = None,
    logger: logging.Logger | None = None,
) -> tuple[str | None, str | None, str | None, AutoDecision | None]:
    """The (model_key, model_name, reasoning_effort) a route should build, plus the Auto decision.

    Requests that name a real model pass through unchanged. For Auto, a
    ``None`` key means "use the route's usual default".
    """
    if not is_auto_model_key(model_key):
        return model_key, model_name, reasoning_effort, None
    decision = await resolve_auto_model(
        config_service,
        AutoRequest(
            query=query or "",
            previous_conversations=list(previous_conversations or []),
            attachments=list(attachments or []),
            agent_models=agent_models,
            agent_route=agent_route,
            effort_floor=effort_floor,
            explicit_effort=reasoning_effort,
        ),
        logger,
    )
    effort = (
        decision.reasoning_effort
        if decision.reasoning_effort is not None
        else reasoning_effort
    )
    return decision.model_key, decision.model_name, effort, decision
