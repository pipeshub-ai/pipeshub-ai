"""The stage DAG. Registration order is dependency order, so the graph is acyclic by construction."""

import re
from typing import Any

from app.modules.pipeline.stage import Stage

PIPELINE_TOPIC_PREFIX = "pipeline."

# Stage names become topic names and state-key segments (``vrid:rev:stage``).
_NAME = re.compile(r"^[a-z][a-z0-9-]*$")


class UnknownStageError(KeyError):
    pass


class StageRegistry:
    def __init__(self) -> None:
        super().__init__()
        self._stages: dict[str, Stage[Any]] = {}
        self._external: set[str] = set()

    def register_external(self, name: str) -> None:
        """A prerequisite whose state is produced outside the stage runtime (for example by the legacy indexer)."""
        self._check_new_name(name)
        self._external.add(name)

    def register(self, stage: Stage[Any]) -> None:
        self._check_new_name(stage.name)
        if stage.version < 1:
            raise ValueError(f"stage {stage.name!r} must have version >= 1")
        unknown = sorted(stage.requires - self._stages.keys() - self._external)
        if unknown:
            raise ValueError(f"stage {stage.name!r} requires unregistered stages {unknown}; register them first")
        self._stages[stage.name] = stage

    def get(self, name: str) -> Stage[Any]:
        try:
            return self._stages[name]
        except KeyError:
            raise UnknownStageError(name) from None

    def has(self, name: str) -> bool:
        return name in self._stages

    def names(self) -> tuple[str, ...]:
        """Runnable stages in dependency order."""
        return tuple(self._stages)

    def is_external(self, name: str) -> bool:
        return name in self._external

    def successors(self, name: str) -> tuple[Stage[Any], ...]:
        return tuple(stage for stage in self._stages.values() if name in stage.requires)

    def topic_for(self, name: str) -> str:
        return f"{PIPELINE_TOPIC_PREFIX}{self.get(name).name}"

    def _check_new_name(self, name: str) -> None:
        if not _NAME.match(name):
            raise ValueError(f"invalid stage name {name!r}: use lowercase letters, digits and hyphens")
        if name in self._stages or name in self._external:
            raise ValueError(f"stage {name!r} is already registered")
