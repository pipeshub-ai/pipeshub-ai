"""Resolving configured model selectors against PipesHub's model registry.

"Use PipesHub's configured LLM" means the model identity comes from
`GET /api/v1/configurationManager/ai-models/llm`. That API never returns
secrets, so the provider key is read from the environment (`credentials.py`).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from benchmarks.harness.config import ModelSelector
from benchmarks.harness.errors import ModelNotRegisteredError
from benchmarks.harness.llm.client import ResolvedModel

EntriesProvider = Callable[[], list[Mapping[str, Any]]]


def _model_names(entry: Mapping[str, Any]) -> list[str]:
    configuration = entry.get("configuration") or {}
    raw = str(configuration.get("model") or entry.get("modelName") or "")
    return [name.strip() for name in raw.split(",") if name.strip()]


def _matches(entry: Mapping[str, Any], selector: ModelSelector) -> bool:
    if selector.model_key:
        return entry.get("modelKey") == selector.model_key
    if selector.provider and entry.get("provider") != selector.provider:
        return False
    return selector.model in _model_names(entry)


def _describe(entries: list[Mapping[str, Any]]) -> list[str]:
    return sorted(f"{e.get('provider')}:{name}" for e in entries for name in _model_names(e))


class ModelResolver:
    def __init__(self, entries_provider: EntriesProvider) -> None:
        self._entries_provider = entries_provider
        self._entries: list[Mapping[str, Any]] | None = None

    def _all(self) -> list[Mapping[str, Any]]:
        if self._entries is None:
            self._entries = list(self._entries_provider())
        return self._entries

    def refresh(self) -> None:
        self._entries = None

    def find(self, selector: ModelSelector) -> ResolvedModel | None:
        for entry in self._all():
            if _matches(entry, selector):
                return ResolvedModel(
                    model_key=str(entry["modelKey"]),
                    provider=selector.call_provider or str(entry.get("provider") or ""),
                    model_name=selector.model if selector.model in _model_names(entry) else _model_names(entry)[0],
                    is_reasoning=bool(entry.get("isReasoning")),
                    context_length=entry.get("contextLength") or None,
                    reasoning_effort=selector.reasoning_effort,
                    deployment=selector.deployment,
                )
        return None

    def resolve(self, selector: ModelSelector) -> ResolvedModel:
        found = self.find(selector)
        if found is None:
            raise ModelNotRegisteredError(
                f"{selector.provider or '*'}:{selector.model} is not registered in PipesHub; "
                f"available: {_describe(self._all())}. Run `seed-models` or configure it.",
            )
        return found
