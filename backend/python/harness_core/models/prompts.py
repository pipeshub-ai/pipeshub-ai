"""A versioned prompt registry (P4.7).

A prompt is addressed by ``(name, version)`` and a version never changes: the same
text again is idempotent, other text under it is refused. A run pins its version
(``RunSpec.model.promptVersion``), so a resumed or replayed run reads the text it
started with.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from harness_core.models.errors import PromptVersionConflict, PromptVersionUnknown

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.contracts.generated.run_spec import ModelPolicy as SpecModelPolicy


@dataclass(frozen=True)
class PromptVersion:
    name: str
    version: str
    text: str

    @property
    def digest(self) -> str:
        return "sha256:" + hashlib.sha256(self.text.encode("utf-8")).hexdigest()


class PromptRegistry(Protocol):
    async def put(self, prompt: PromptVersion) -> PromptVersion: ...

    async def get(self, name: str, version: str) -> PromptVersion:
        """Exactly that version, or ``PromptVersionUnknown``; never another version of the name."""
        ...

    async def versions(self, name: str) -> tuple[str, ...]: ...


class InMemoryPromptRegistry:
    def __init__(self) -> None:
        self._prompts: dict[tuple[str, str], PromptVersion] = {}

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> InMemoryPromptRegistry:
        """``{"prompts": [{name, version, text}]}``, checked by the same rules as the port's writes."""
        registry = cls()
        for item in document.get("prompts", []):
            _ = registry._put(PromptVersion(str(item["name"]), str(item["version"]), str(item["text"])))
        return registry

    def _put(self, prompt: PromptVersion) -> PromptVersion:
        held = self._prompts.get((prompt.name, prompt.version))
        if held is not None and held.digest != prompt.digest:
            raise PromptVersionConflict(f"{prompt.name}@{prompt.version} already holds other text")
        self._prompts[(prompt.name, prompt.version)] = prompt
        return prompt

    def _get(self, name: str, version: str) -> PromptVersion:
        held = self._prompts.get((name, version))
        if held is None:
            raise PromptVersionUnknown(f"no prompt {name}@{version}")
        return held

    async def put(self, prompt: PromptVersion) -> PromptVersion:
        return self._put(prompt)

    async def get(self, name: str, version: str) -> PromptVersion:
        return self._get(name, version)

    async def versions(self, name: str) -> tuple[str, ...]:
        return tuple(sorted(v for n, v in self._prompts if n == name))


async def pinned_prompt(registry: PromptRegistry, model: SpecModelPolicy, name: str) -> PromptVersion:
    """The version of ``name`` the run pinned."""
    return await registry.get(name, model.prompt_version)


__all__ = ["InMemoryPromptRegistry", "PromptRegistry", "PromptVersion", "pinned_prompt"]
