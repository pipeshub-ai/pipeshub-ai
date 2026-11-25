"""Where a model a helper builds for itself is handed over to the run it is built for.

A tool helper that wants its own model (the ``query`` role for grep generation or a name check, the ``indexing``
role for a spreadsheet's tables, the image-generation model) builds it from the org's configuration through
``app.utils.llm``. Inside an agent harness run that model must cost the run, wait for its provider slot and answer to
the conversation's classification like every other call the run makes, so the harness binds a scope here and
``app.utils.llm`` passes every model it builds through it. Nothing binds one outside a harness run: a legacy chat's
and the indexing pipeline's models come back exactly as built.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping


class RunModelScope(Protocol):
    def __call__(self, llm: Any, config: Mapping[str, Any]) -> Any:  # noqa: ANN401 - LangChain chat models
        """The chat model as built, the configured entry it was built from -> the model the helper is handed."""
        ...

    def image(self, adapter: Any, config: Mapping[str, Any]) -> Any:  # noqa: ANN401 - an ImageGenerationAdapter
        """The image-generation adapter as built, its configured entry -> the adapter the tool is handed."""
        ...


_SCOPE: ContextVar[RunModelScope | None] = ContextVar("pipeshub_run_model_scope", default=None)


def scoped_helper_model(llm: Any, config: Mapping[str, Any]) -> Any:  # noqa: ANN401 - a LangChain chat model
    scope = _SCOPE.get()
    return llm if scope is None else scope(llm, config)


def scoped_image_model(adapter: Any, config: Mapping[str, Any]) -> Any:  # noqa: ANN401 - an ImageGenerationAdapter
    scope = _SCOPE.get()
    return adapter if scope is None else scope.image(adapter, config)


def run_model_scope_bound() -> bool:
    return _SCOPE.get() is not None


def current_run_model_scope() -> RunModelScope | None:
    return _SCOPE.get()


# The records one of a run's tool calls has read so far (set for the call by ``tool_call_reads``). The run's ledger
# names them only once the call's result is journaled; a model a helper calls during the call is checked at their
# level (the harness's classification guard reads this).
_READ: ContextVar[set[str] | None] = ContextVar("pipeshub_tool_call_records_read", default=None)


@contextmanager
def tool_call_reads() -> Iterator[None]:
    token = _READ.set(set())
    try:
        yield
    finally:
        _READ.reset(token)


def note_records_read(record_ids: Iterable[str]) -> None:
    """The current tool call has read (or is about to hand a helper) these records' content. Outside a harness
    run's tool call nothing is kept."""
    held = _READ.get()
    if held is not None:
        held.update(str(record_id) for record_id in record_ids if record_id)


def records_read_in_tool_call() -> frozenset[str]:
    held = _READ.get()
    return frozenset() if held is None else frozenset(held)


@contextmanager
def run_model_scope(scope: RunModelScope) -> Iterator[None]:
    token = _SCOPE.set(scope)
    try:
        yield
    finally:
        _SCOPE.reset(token)


def hold_run_model_scope(scope: RunModelScope) -> None:
    """For the rest of the current task: an attempt's task ends with the attempt, as its call identity's binding."""
    _ = _SCOPE.set(scope)


__all__ = [
    "RunModelScope",
    "current_run_model_scope",
    "hold_run_model_scope",
    "note_records_read",
    "records_read_in_tool_call",
    "run_model_scope",
    "run_model_scope_bound",
    "scoped_helper_model",
    "scoped_image_model",
    "tool_call_reads",
]
