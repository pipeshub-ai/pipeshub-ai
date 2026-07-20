from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from app.agent_loop_lib.agent.loops import LoopStrategy, ReActLoop

if TYPE_CHECKING:
    from app.agent_loop_lib.models.base import Model
    from app.agent_loop_lib.transport.registry import TransportRegistry

"""The fundamental agent, made concrete.

At the most granular level an agent is just a system prompt, a set of
tools, and a model — `AgentSpec` below. Everything else in the framework
(roles, `AgentFactory`, `AgentBuilder`, agents-as-tools) exists to PRODUCE
an `AgentSpec`; nothing downstream of one needs to know how it was built.
See `agent/prompt.py` for the next layer up (prompt CONSTRUCTION — how
`system_prompt` gets turned into the text actually sent to the model).
"""

__all__ = ["ModelSpec", "AgentSpec"]


class ModelSpec(BaseModel):
    """Which model answers this agent's calls, and the transport knobs
    passed through per call. Immutable — swapping models means building a
    new `ModelSpec`, never mutating one shared across agents."""

    provider: str = "anthropic"
    model: str = "claude-sonnet-4-6"
    thinking_budget: int | None = None
    effort: str | None = None

    model_config = {"frozen": True}

    def resolve(self, registry: "TransportRegistry") -> "Model":
        """Resolve this spec against a `TransportRegistry` into the `Model`
        `Agent` actually depends on — the one place a `provider` string
        turns into a concrete, callable `Model` instance, keeping `Agent`
        itself free of any `TransportRegistry`/`LLMTransport` knowledge
        (Dependency Inversion: `Agent` depends on `Model`, never on how one
        gets built)."""
        from app.agent_loop_lib.models.transport import TransportModel

        transport = registry.resolve(self.provider)
        return TransportModel(transport)


class AgentSpec(BaseModel):
    """Layer 0: prompt + tools + model, plus the minimal structure needed
    to assemble a per-turn system prompt, pick a loop shape, and install
    per-agent deterministic middleware.

    `system_prompt` is either a plain string (the common case) or a
    `SystemPromptBuilder` (see `agent/prompt.py`) for callers who want a
    different prompt-CONSTRUCTION strategy than the default named-section
    template — that is Layer 1, deliberately separate from this class.

    `loop` is a `LoopStrategy` instance (default `ReActLoop()`) governing
    the turn loop's SHAPE — see `agent/loops.py`.

    `middleware` is a list of `Callable[[HookRegistry], None]` installers,
    each applied once when this spec is bound to an `Agent` — the
    per-agent equivalent of `ControlPlaneConfig.hooks`, for deterministic
    behavior that should travel WITH this spec wherever it's used (e.g. an
    agent exposed as a tool that always forces a critique step regardless
    of which parent composes it in). Installers are responsible for their
    own idempotency (see `hooks/middleware/builtin/turn_guards.py`'s
    pattern) since several agents can share one runtime's kernel.
    """

    name: str = "agent"
    description: str = ""  # shown as the tool description when exposed via agent_as_tool()
    capabilities: list[str] = Field(default_factory=list)

    system_prompt: Any = ""  # str | SystemPromptBuilder
    tool_names: list[str] = Field(default_factory=list)
    pinned_toolsets: list[str] = Field(default_factory=list)

    # "eager" (default, unchanged behavior): when `tool_names` is non-empty,
    # every named tool is fully visible from turn 0 — this is the pre-
    # existing, backward-compatible semantics ("explicit grant always fully
    # visible", see `agent/tool_loop.py::tool_schemas_for_turn`'s docstring).
    # "lazy": `tool_names` becomes a permission CEILING rather than an
    # eager grant — visibility starts at essentials/pinned toolsets
    # (intersected with the ceiling) and only grows via `fetch_tools`/
    # `search_tools`/preloading, never beyond `tool_names`. Only takes
    # effect when the registry actually has toolsets registered
    # (`ToolRegistry.has_toolsets()`); a flat registry is unaffected either
    # way. See `hooks/middleware/builtin/tool_preloading.py` for the
    # deterministic complement to this model-driven (probabilistic) growth.
    tool_disclosure: Literal["eager", "lazy"] = "eager"

    model: ModelSpec = Field(default_factory=ModelSpec)
    loop: LoopStrategy = Field(default_factory=ReActLoop)
    max_turns: int = 20
    mode: str = "act"

    output_style: str | None = None
    prompt_section_order: list[str] | None = None
    extra_prompt_sections: dict[str, str] = Field(default_factory=dict)

    middleware: list[Callable[[Any], None]] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}
