"""The policy resolver port (03-interfaces §4.1): CPD-5's cached resolver and Loom embeddings implement it; the
contract suite (``harness_core.testing.contracts.policy_resolver``) holds every implementation to the shared
vectors and the precedence properties."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pydantic import JsonValue

    from harness_core.policy.types import (
        FoldedPrefix,
        PolicyRefusal,
        Resolved,
        ScopedLayer,
    )


class PolicyResolver(Protocol):
    def resolve(self, layers: Sequence[ScopedLayer]) -> Resolved: ...

    def fold_prefix(self, layers: Sequence[ScopedLayer]) -> FoldedPrefix | PolicyRefusal: ...

    def resolve_from(self, prefix: FoldedPrefix, rest: Sequence[ScopedLayer]) -> Resolved: ...

    def assert_narrows(self, layer: ScopedLayer, above: Sequence[ScopedLayer]) -> PolicyRefusal | None: ...

    def live_fold(self, layers: Sequence[object], start: FoldedPrefix = ...) -> FoldedPrefix:
        """Live layers met into one; never refuses."""
        ...

    def narrowed_by_live(self, config: Mapping[str, JsonValue], live: FoldedPrefix) -> dict[str, JsonValue]:
        """``pin ⊓ live``: no ceiling of the result is above ``config``'s."""
        ...


__all__ = ["PolicyResolver"]
