"""Type-keyed references (``jira@default/...``) resolved against what a definition binds now.

A run admitted before its agent's toolsets carried instances pinned ``type@default/tool``; its
journal and its grant keep that id for its whole life. When the definition is migrated to an
instance, the same run must find the same toolset again: the one binding of that type. When
two bindings of the type could answer, the reference is refused, never guessed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from harness_core.tool_identity.errors import ToolInstanceAmbiguous, ToolInstanceMissing
from harness_core.tool_identity.names import LEGACY_INSTANCE, type_key

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence


@dataclass(frozen=True, slots=True)
class BoundInstance:
    """One toolset a definition binds: its type and its instance (``None``: bound by type only)."""

    kind: str
    instance: str | None


@dataclass(frozen=True, slots=True)
class ToolBinding:
    """What a registered tool was built for: its toolset's type and instance, and its own (unscoped) name."""

    kind: str
    instance: str | None
    tool: str

    @property
    def bound(self) -> BoundInstance:
        return BoundInstance(self.kind, self.instance)


def granted_ref(binding: ToolBinding, granted: Iterable[tuple[str, str]], bound: Sequence[BoundInstance]) -> str | None:
    """The pinned id (``kind@instance/tool``) under which ``binding``'s tool is granted, if any.

    ``granted`` holds the ``(kind, instance)`` pairs of the run's grant. Each is resolved with
    ``match_grant`` against what the definition binds; the grant that resolves to this tool's own
    binding names it, so a type-keyed pin keeps its id after its definition was migrated."""
    for kind, instance in granted:
        if type_key(kind) != type_key(binding.kind):
            continue
        try:
            if match_grant(kind, instance, bound) == binding.bound:
                return f"{kind}@{instance}/{binding.tool}"
        except (ToolInstanceAmbiguous, ToolInstanceMissing):
            continue
    return None


def match_grant(kind: str, granted_instance: str, bound: Sequence[BoundInstance]) -> BoundInstance:
    """The binding a grant of ``kind@granted_instance`` refers to."""
    same = [b for b in bound if type_key(b.kind) == type_key(kind)]
    if granted_instance != LEGACY_INSTANCE:
        exact = [b for b in same if b.instance == granted_instance]
        if not exact:
            raise ToolInstanceMissing(f"no {kind} binding has instance {granted_instance}")
        return exact[0]
    unbound = [b for b in same if b.instance is None]
    candidates = unbound or same
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ToolInstanceMissing(f"no {kind} binding for a type-keyed grant")
    raise ToolInstanceAmbiguous(
        f"a type-keyed {kind} grant matches {len(candidates)} bindings; refusing to choose"
    )


__all__ = ["BoundInstance", "ToolBinding", "granted_ref", "match_grant"]
