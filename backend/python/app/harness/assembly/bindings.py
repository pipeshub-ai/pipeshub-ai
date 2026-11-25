"""The bindings a durable run is built with, stated explicitly (S4a requirement).

``harness_core.bindings`` seams are process-wide import side effects of the
legacy composition root. A durable run never inherits them: the driver runs
every attempt inside ``bindings.scoped(durable_bindings())``.

- ``event_deriver``: none. The durable kernel emits no AG-UI; Node projects.
- ``streamed_answer_argument``: the legacy resolver. ``final_answer``'s
  ``answer_markdown`` streams as the answer when the deployment enables the tool.
- ``spawn_scheduler`` / ``special_route_fallback``: none. Child runs and
  coordination tools arrive with P3; the durable tool set has no special routes.
- ``invalid_tool_call_recovery`` / ``api_mode_store`` / ``log_lines``: the same
  PipesHub functions the legacy path binds, passed by reference here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from harness_core.bindings import ApiModeStore, KernelBindings

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from harness_core.models.output_bound import OutputLimits


def _invalid_tool_call_recovery() -> Callable[[Mapping[str, Any]], Any]:
    from app.agents.agent_loop.converters import _recover_invalid_tool_call

    return _recover_invalid_tool_call


def _api_mode_store() -> ApiModeStore | None:
    from app.utils.llm_api_mode_store import get_llm_api_mode_store

    return get_llm_api_mode_store()


def _streamed_answer_argument(tool_name: str) -> str | None:
    from app.agent_loop_lib.composition import _streamed_answer_argument as legacy

    return legacy(tool_name)


def _output_limits() -> OutputLimits:
    from app.utils.output_limits import model_output_limits

    return model_output_limits()


def durable_bindings() -> KernelBindings:
    from app.agent_loop_lib.composition import APP_LOG_LINES

    return KernelBindings(
        invalid_tool_call_recovery=_invalid_tool_call_recovery,
        api_mode_store=_api_mode_store,
        event_deriver=None,
        streamed_answer_argument=_streamed_answer_argument,
        spawn_scheduler=None,
        special_route_fallback=None,
        output_limits=_output_limits,
        log_lines=APP_LOG_LINES,
    )


__all__ = ["durable_bindings"]
