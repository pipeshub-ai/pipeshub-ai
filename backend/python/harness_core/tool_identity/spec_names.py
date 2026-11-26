"""OWD-7's model-facing names: ``pipeshub_<toolset>_<tool>``, while the internal id stays ``<source>@<instance>/<tool>``.

A name is only what a model sees. Journals, approvals, policies and audit keep the internal id (``ToolEffect.tool_id``),
so giving a run spec names rewrites no history: ``ModelNames`` shows each tool of a grant under its spec name and
answers both that name and today's (``jira__search_issues``), which tool results, skills and earlier turns still name.

- A connector or platform toolset's tool ``<group>__<tool>`` is ``pipeshub_<group>_<tool>``.
- The default surface (C3.29) has fixed names: ``search_tools`` is ``pipeshub_tools_search``, ``call_tool`` is
  ``pipeshub_tools_call``; ``ask_user`` is ``pipeshub_ask_user`` (``harness_core.gateway.asking``).
- An external MCP tool keeps its exported name, ``mcp_<server>[_<instance tag>]_<tool>``: it is already prefixed by
  its server instance and never takes PipesHub's prefix, so it cannot shadow a PipesHub tool (MX-05).
- A name that would pass ``SCOPED_NAME_MAX`` is cut and given a digest of today's name, as instance-scoped names are.

The mapping is a bijection over one grant, built in sorted order, so the same grant always shows the same names
(prompt caches keep the tools prefix) and two tools never share one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from harness_core.mcp_hub.names import name_digest
from harness_core.tool_identity.names import SCOPED_NAME_MAX

if TYPE_CHECKING:
    from collections.abc import Iterable

SPEC_PREFIX: Final = "pipeshub_"
SEARCH_TOOLS_SPEC: Final = "pipeshub_tools_search"
CALL_TOOL_SPEC: Final = "pipeshub_tools_call"
# The default surface's fixed names (C3.29).
SURFACE_NAMES: Final[dict[str, str]] = {"search_tools": SEARCH_TOOLS_SPEC, "call_tool": CALL_TOOL_SPEC}
_MCP_PREFIX: Final = "mcp_"


def _fit(name: str, legacy: str) -> str:
    if len(name) <= SCOPED_NAME_MAX:
        return name
    suffix = f"_{name_digest(legacy)}"
    return name[: SCOPED_NAME_MAX - len(suffix)].rstrip("_") + suffix


def spec_name(legacy: str) -> str:
    """The OWD-7 name of a tool today's grant names ``legacy``."""
    if legacy in SURFACE_NAMES:
        return SURFACE_NAMES[legacy]
    if legacy.startswith((SPEC_PREFIX, _MCP_PREFIX)):
        return legacy
    group, sep, tool = legacy.partition("__")
    flat = f"{group}_{tool}" if sep else legacy
    return _fit(f"{SPEC_PREFIX}{flat}", legacy)


@dataclass(frozen=True)
class ModelNames:
    """One grant's names: ``shown`` is what the model sees, ``internal`` the grant's own name for either form."""

    _shown: dict[str, str]
    _internal: dict[str, str]

    @classmethod
    def spec(cls, names: Iterable[str]) -> ModelNames:
        shown: dict[str, str] = {}
        taken: set[str] = set()
        legacy = sorted(set(names))
        own = set(legacy)
        for name in legacy:
            candidate = spec_name(name)
            # Another tool already shows that name, or the grant has a tool of that very name: keep the two apart.
            if candidate in taken or (candidate != name and candidate in own):
                candidate = _fit(f"{candidate}_{name_digest(name)}", name)
            shown[name] = candidate
            taken.add(candidate)
        internal = {spec: name for name, spec in shown.items()}
        # Today's names keep answering, unless a spec name already is one.
        for name in legacy:
            _ = internal.setdefault(name, name)
        return cls(shown, internal)

    @classmethod
    def legacy(cls) -> ModelNames:
        return cls({}, {})

    def shown(self, name: str) -> str:
        return self._shown.get(name, name)

    def internal(self, name: str) -> str:
        return self._internal.get(name, name)

    @property
    def renames(self) -> bool:
        return any(name != shown for name, shown in self._shown.items())


__all__ = [
    "CALL_TOOL_SPEC",
    "SEARCH_TOOLS_SPEC",
    "SPEC_PREFIX",
    "SURFACE_NAMES",
    "ModelNames",
    "spec_name",
]
