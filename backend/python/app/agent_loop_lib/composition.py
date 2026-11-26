"""Composition root of the legacy agent loop.

`harness_core` never imports `app.*`. The seams it exposes in
`harness_core.bindings` are bound here, when `app.agent_loop_lib` is imported,
to this package's builtin tools, its AG-UI aliases (`ag_ui.py`) and to
PipesHub product modules. The two
product tools left in this package (`final_answer`, the skills validator)
read their PipesHub settings through the policy functions below, so this is
the only module here that reaches into `app.agents` / `app.utils`.

Every such import is deferred to call time: those modules import this
package, so a module-level import would be circular.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from harness_core import bindings

if TYPE_CHECKING:
    from harness_core.models.output_bound import OutputLimits


def _invalid_tool_call_recovery() -> Callable[[Mapping[str, Any]], Any]:
    from app.agents.agent_loop.converters import _recover_invalid_tool_call

    return _recover_invalid_tool_call


def _api_mode_store() -> bindings.ApiModeStore | None:
    from app.utils.llm_api_mode_store import get_llm_api_mode_store

    return get_llm_api_mode_store()


def _derive_ag_ui_events(event: Any) -> list[Any]:
    from app.agent_loop_lib.ag_ui import derive_aliases

    return derive_aliases(event)


def _streamed_answer_argument(tool_name: str) -> str | None:
    from app.agent_loop_lib.tools.builtin.planning import final_answer

    if final_answer.final_answer_enabled() and tool_name == final_answer.FinalAnswerTool().name:
        return "answer_markdown"
    return None


class _SpawnScheduler:
    # Resolved per call so tests that patch `spawn_scheduler` keep working.
    async def schedule(
        self,
        agent: Any,
        runtime: Any,
        calls: list[Any],
        run_scope: Any,
        *,
        goal: Any,
        turn_index: int,
        started_at: str | None,
    ) -> dict[str, asyncio.Task[Any]]:
        from app.agent_loop_lib.agent import spawn_scheduler

        return await spawn_scheduler.schedule_spawn_batch(
            agent, runtime, calls, run_scope,
            goal=goal, turn_index=turn_index, started_at=started_at,
        )

    async def cancel_pending(self, tasks: dict[str, asyncio.Task[Any]]) -> None:
        from app.agent_loop_lib.agent import spawn_scheduler

        await spawn_scheduler.cancel_pending_spawn_tasks(tasks)


def _special_route_fallback(name: str, tool_registry: object | None) -> Any:
    """Constructs a fresh instance of the framework's own builtin handler
    for `name`, if any. Every one of these tools is stateless (all real
    state lives on the `Agent`/`RouteContext`), so building a throwaway
    instance per lookup is cheap and side-effect-free."""
    if name == "fetch_tools":
        if tool_registry is None:
            return None
        from app.agent_loop_lib.tools.builtin.lazy_toolsets import FetchToolsTool
        return FetchToolsTool(tool_registry)

    if name == "search_tools":
        if tool_registry is None:
            return None
        from app.agent_loop_lib.tools.builtin.lazy_toolsets import SearchToolsTool
        return SearchToolsTool(tool_registry)

    from app.agent_loop_lib.tools.builtin.coordination.best_of_n import BestOfNTool
    from app.agent_loop_lib.tools.builtin.coordination.clarify import ClarifyTool
    from app.agent_loop_lib.tools.builtin.coordination.handoff import HandoffTool
    from app.agent_loop_lib.tools.builtin.coordination.route_task import RouteTaskTool
    from app.agent_loop_lib.tools.builtin.coordination.spawn_agent import SpawnAgentTool
    from app.agent_loop_lib.tools.builtin.planning.create_plan import CreatePlanTool
    from app.agent_loop_lib.tools.builtin.planning.critique_plan import CritiquePlanTool
    from app.agent_loop_lib.tools.builtin.planning.replan import ReplanTool
    from app.agent_loop_lib.tools.builtin.planning.request_review import (
        RequestReviewTool,
    )
    from app.agent_loop_lib.tools.builtin.planning.todos import WriteTodosTool
    from app.agent_loop_lib.tools.builtin.planning.verify_result import VerifyResultTool

    # Discovery-based, not a hand-maintained name -> class dict: dispatch
    # goes by each tool's OWN `.name`, so renaming a builtin's `name`
    # property can never drift out of sync with a separate string key.
    builtin_route_classes: tuple[type, ...] = (
        SpawnAgentTool, BestOfNTool, ClarifyTool, HandoffTool, ReplanTool,
        WriteTodosTool, RouteTaskTool, CreatePlanTool, CritiquePlanTool,
        VerifyResultTool, RequestReviewTool,
    )
    for cls in builtin_route_classes:
        instance = cls()
        if instance.name == name:
            return instance
    return None


def _output_limits() -> OutputLimits:
    from app.utils.output_limits import model_output_limits

    return model_output_limits()


class AppLogLines:
    """`harness_core.log_lines` on PipesHub's log contract (docs/logging.md): bound fields, error
    classes, redaction and the stack written once, from `app.utils.logging_fields` and `app.utils.error_class`."""

    def log_event(self, logger: logging.Logger, event: str, /, *, level: int = logging.INFO, **fields: object) -> None:
        from app.utils.logging_fields import event_fields

        # `log_event`'s body, with the kernel's caller as the line's `[file:line]` (past `harness_core.log_lines`).
        if logger.isEnabledFor(level):
            logger.log(level, event, extra=event_fields(event, fields), stacklevel=3)

    def log_failure(self, logger: logging.Logger, event: str, exc: BaseException, /, **fields: Any) -> None:
        from app.utils.error_class import log_failure

        # The kernel's caller is the line's `[file:line]`: past this method and `harness_core.log_lines`.
        log_failure(logger, event, exc, stacklevel=3, **fields)

    def log_fields(self, **ids: object) -> Any:
        from app.utils.logging_fields import log_fields

        return log_fields(**ids)

    def current_log_fields(self) -> Mapping[str, object]:
        from app.utils.logging_fields import current_log_fields

        return current_log_fields()

    def classify_error(self, exc: BaseException) -> str:
        from app.utils.error_class import classify_error

        return classify_error(exc)

    def level_for(self, error_class: str) -> int:
        from app.utils.error_class import level_for

        return level_for(error_class)

    def already_logged(self, exc: BaseException, level: int) -> bool:
        from app.utils.error_class import already_logged

        return already_logged(exc, level)

    def failure_reason(self, exc: BaseException, error_class: str | None = None) -> str:
        from app.utils.error_class import failure_reason

        return failure_reason(exc, error_class)

    def provider_code(self, exc: BaseException) -> str | None:
        from app.utils.error_class import provider_code

        return provider_code(exc)

    def stack_text_without_messages(self, exc: BaseException) -> str:
        from app.utils.error_class import stack_text_without_messages

        return stack_text_without_messages(exc)

    def redact_secrets_in_text(self, text: str) -> str:
        from app.utils.log_redaction import redact_secrets_in_text

        return redact_secrets_in_text(text)

    def credential_value_mask(self, name: str) -> str | None:
        from app.utils.log_redaction import REDACTED
        from app.utils.sensitive_names import is_credential_name

        return REDACTED if is_credential_name(name) else None


APP_LOG_LINES = AppLogLines()


def install() -> None:
    bindings.install(
        bindings.KernelBindings(
            invalid_tool_call_recovery=_invalid_tool_call_recovery,
            api_mode_store=_api_mode_store,
            event_deriver=_derive_ag_ui_events,
            streamed_answer_argument=_streamed_answer_argument,
            spawn_scheduler=_SpawnScheduler(),
            special_route_fallback=_special_route_fallback,
            output_limits=_output_limits,
            log_lines=APP_LOG_LINES,
        )
    )


def confidence_enabled() -> bool:
    from app.agents.agent_loop.confidence import confidence_enabled as enabled

    return enabled()


def confidence_levels() -> tuple[str, ...]:
    from app.agents.agent_loop.confidence import CONFIDENCE_LEVELS

    return CONFIDENCE_LEVELS


def confidence_rubric() -> str:
    from app.agents.agent_loop.confidence import rubric_text

    return rubric_text()


def normalize_confidence(level: str) -> str | None:
    from app.agents.agent_loop.confidence import normalize

    return normalize(level)


def final_answer_flag() -> bool:
    from app.agents.agent_loop.env_utils import env_bool

    return env_bool("PIPESHUB_ENABLE_FINAL_ANSWER", False)


def skills_resource_limits() -> tuple[int, int]:
    """(per-file, per-skill) byte caps for bundled skill resources."""
    from app.utils.env_config import env_int

    return (
        env_int("PIPESHUB_SKILLS_MAX_RESOURCE_FILE_BYTES", 1024 * 1024),
        env_int("PIPESHUB_SKILLS_MAX_RESOURCE_BYTES", 5 * 1024 * 1024),
    )
