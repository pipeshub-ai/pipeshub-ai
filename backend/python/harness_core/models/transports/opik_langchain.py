"""Opik's LangChain callback, recording a failed run's reason instead of its message.

Imported only once Opik is configured: defining the subclass imports Opik's SDK.
"""

from __future__ import annotations

from opik.integrations.langchain import OpikTracer

from harness_core.log_lines import failure_reason, stack_text_without_messages


class TextFreeOpikTracer(OpikTracer):
    """LangChain writes a failed run's `error` from `_get_stacktrace` (the message and the traceback), and Opik uploads
    that string. A provider's or a driver's message can quote the prompt, an email or a key, so the run's error is its
    reason and a frames-only stack instead."""

    @staticmethod
    def _get_stacktrace(error: BaseException) -> str:
        try:
            return f"{failure_reason(error)}\n{stack_text_without_messages(error)}"
        except Exception:  # LangChain calls this inside its callback: a raise would lose the error marker
            return failure_reason(error)
