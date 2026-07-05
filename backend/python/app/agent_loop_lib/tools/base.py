"""Core Tool interface and supporting types.

A `Tool` is the fundamental unit of agent capability - e.g. "Create Jira
Issue" or "Web Search". Tools are addressed by a hierarchical path
(``/toolsets/{toolset}/{tool_name}``) which is what the middleware routing
layer (see `agent_loop.hooks.middleware.routing`) matches against, while the LLM's
function-calling interface still addresses them by their short, globally
unique `name` (see `ToolRegistry.resolve_by_name`) — paths are for
middleware scoping, not model-facing identity.

Design notes:
    - ``short_description`` vs ``description``: the short form is cheap enough
      to include for every registered tool during agent-side discovery
      (see `ToolRegistry.discover`); the full ``description`` (and the
      complete parameter schema) is only paid for once a caller resolves a
      specific tool via `ToolRegistry.resolve`. This is what enables lazy
      tool loading at scale.
    - ``tags`` are simple key/value pairs (not free-form strings) so they can
      be filtered on deterministically, e.g. ``Tag("provider", "atlassian")``.
      `Tool.risk_level` is a convenience bridge over ``Tag("risk", ...)`` for
      the existing approval system (see `agent_loop.approval`) and `ModeHook`
      — declare risk by tag, read it back either way.
    - ``sources``: tools that fetch external information (web_search,
      web_scrape, knowledge_query, ...) can attach `Source` provenance
      directly on the `ToolOutput` they return, instead of a separate
      wrapper type — see `agent_loop.core.types.Source`.

Note on naming: this module's result envelope is `ToolOutput`, distinct
from `agent_loop.core.types.ToolResult` (the `tool_call_id`/`name`/
`content`/`is_error` shape the agent loop and transport layer expect).
`ToolExecutor` is the one place that bridges the two (a `Tool.execute()`
call produces a `ToolOutput`; `ToolExecutor.call_tool()` converts it into a
`core.types.ToolResult` for the conversation) — see `tools/executor.py`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.core.types import Source
from app.agent_loop_lib.tools.errors import ToolValidationError

if TYPE_CHECKING:
    from app.agent_loop_lib.core.tool_schema import ToolSchema
    from app.agent_loop_lib.modules.stores.approval.base import RiskLevel

__all__ = [
    "ParameterType",
    "Tag",
    "ToolParameter",
    "ToolSummary",
    "ToolOutput",
    "Tool",
]


class ParameterType(str, Enum):
    """Supported JSON-schema-compatible parameter types."""

    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    ARRAY = "array"
    OBJECT = "object"


# Python types accepted for each ParameterType during validation.
# bool is intentionally excluded from INTEGER's accepted set because
# `isinstance(True, int)` is True in Python and would silently accept booleans
# as integers otherwise.
_PYTHON_TYPES: dict[ParameterType, tuple[type, ...]] = {
    ParameterType.STRING: (str,),
    ParameterType.INTEGER: (int,),
    ParameterType.FLOAT: (int, float),
    ParameterType.BOOLEAN: (bool,),
    ParameterType.ARRAY: (list, tuple),
    ParameterType.OBJECT: (dict,),
}

# JSON Schema type keywords for to_schema().
_JSON_SCHEMA_TYPES: dict[ParameterType, str] = {
    ParameterType.STRING: "string",
    ParameterType.INTEGER: "integer",
    ParameterType.FLOAT: "number",
    ParameterType.BOOLEAN: "boolean",
    ParameterType.ARRAY: "array",
    ParameterType.OBJECT: "object",
}


@dataclass(frozen=True)
class Tag:
    """A key/value pair used to categorize and filter Tools and Toolsets.

    Examples: ``Tag("provider", "atlassian")``, ``Tag("category", "write")``,
    ``Tag("risk", "high")``.
    """

    key: str
    value: str


@dataclass(frozen=True)
class ToolParameter:
    """Declarative description of a single tool argument.

    ``items`` describes the element schema for ``ParameterType.ARRAY``
    parameters (e.g. ``{"type": "string"}``); ignored for other types.
    """

    name: str
    type: ParameterType
    description: str
    required: bool = True
    default: Any = None
    enum: list[str] | None = None
    items: dict[str, Any] | None = None


@dataclass(frozen=True)
class ToolSummary:
    """Lightweight tool descriptor used for cheap, phase-1 discovery.

    Contains only what an agent needs to *decide whether* a tool is relevant
    (`name`, `short_description`, `tags`) plus the `path` needed to resolve
    the full tool afterwards. Deliberately excludes `parameters`/`description`.
    """

    name: str
    short_description: str
    path: str
    tags: tuple[Tag, ...] = ()


@dataclass
class ToolOutput:
    """Uniform result envelope returned by every tool execution.

    Denials, blocks, validation failures, and execution errors are all
    represented as ``ToolOutput(success=False, error=...)`` so callers (and
    LLMs) have exactly one shape to handle, rather than a mix of exceptions
    and return values. ``sources`` carries citation provenance (Phase 5) —
    tools with nothing to cite just leave it empty, as most do.
    """

    success: bool
    data: Any = None
    error: str | None = None
    sources: list[Source] = field(default_factory=list)


class Tool(ABC):
    """Abstract base class for a single agent-callable capability.

    Subclasses must implement the identity/metadata properties and
    `execute`. `validate`, `to_schema`, and `to_summary` have concrete
    default implementations derived from `parameters` and should only be
    overridden for advanced use cases (e.g. custom JSON schema generation).
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short, globally-unique identifier, e.g. 'create_issue' — this is
        what the LLM's function-calling interface addresses (`ToolCall.name`)."""

    @property
    @abstractmethod
    def short_description(self) -> str:
        """One-line summary shown to agents during cheap discovery."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Full description with usage details, loaded only on resolve."""

    @property
    @abstractmethod
    def path(self) -> str:
        """Full routing path, e.g. '/toolsets/jira/create_issue'."""

    @property
    def tags(self) -> list[Tag]:
        """Key/value metadata for categorization and filtering. Optional."""
        return []

    @property
    def risk_level(self) -> "RiskLevel":
        """Convenience bridge from `Tag("risk", ...)` to `approval.base.RiskLevel`.

        Existing consumers (`ApprovalHook`, `ModeHook`) read a tool's risk
        via this property; new code should prefer declaring risk with
        `Tag("risk", "high")` in `tags` and can still read it back either
        way. Defaults to LOW when no "risk" tag is present.
        """
        from app.agent_loop_lib.modules.stores.approval.base import RiskLevel

        for tag in self.tags:
            if tag.key == "risk":
                return RiskLevel(tag.value)
        return RiskLevel.LOW

    @property
    @abstractmethod
    def parameters(self) -> list[ToolParameter]:
        """Declarative parameter schema used for validation and to_schema()."""

    @abstractmethod
    async def execute(self, **kwargs: Any) -> ToolOutput:
        """Perform the tool's action. Called with validated, defaulted kwargs."""

    async def __call__(self, **kwargs: Any) -> ToolOutput:
        """Convenience path for direct invocation (bypasses the middleware pipeline).

        Prefer routing calls through `ToolExecutor.call_tool` in production so
        that hooks/middleware are applied; this is primarily useful for tests
        and standalone scripts.
        """
        normalized = dict(kwargs)
        self.validate(normalized)
        return await self.execute(**normalized)

    def validate(self, kwargs: dict[str, Any]) -> None:
        """Validate and normalize ``kwargs`` in place against ``self.parameters``.

        Order of checks (first failure wins):
            1. Unknown keys not declared in ``parameters``.
            2. Missing required parameters that have no default.
            3. Type mismatches against each parameter's declared ``type``.
            4. Enum membership, when ``enum`` is set.

        Missing *optional* parameters are filled in from their declared
        ``default`` by mutating ``kwargs`` in place, so that a subsequent
        ``self.execute(**kwargs)`` receives the fully normalized arguments.

        Raises:
            ToolValidationError: if any check fails.
        """
        params_by_name = {p.name: p for p in self.parameters}

        unknown = set(kwargs) - set(params_by_name)
        if unknown:
            raise ToolValidationError(
                f"{self.path}: unexpected argument(s): {sorted(unknown)}"
            )

        for param in self.parameters:
            if param.name not in kwargs:
                if param.required:
                    raise ToolValidationError(
                        f"{self.path}: missing required argument '{param.name}'"
                    )
                kwargs[param.name] = param.default
                continue

            value = kwargs[param.name]
            accepted_types = _PYTHON_TYPES[param.type]
            is_bool_leaking_into_numeric = isinstance(value, bool) and param.type in (
                ParameterType.INTEGER,
                ParameterType.FLOAT,
            )
            if not isinstance(value, accepted_types) or is_bool_leaking_into_numeric:
                raise ToolValidationError(
                    f"{self.path}: argument '{param.name}' expected type "
                    f"{param.type.value!r}, got {type(value).__name__!r}"
                )

            if param.enum is not None and value not in param.enum:
                raise ToolValidationError(
                    f"{self.path}: argument '{param.name}' must be one of "
                    f"{param.enum}, got {value!r}"
                )

    def to_schema(self) -> "ToolSchema":
        """Export as a provider-agnostic function-calling schema."""
        from app.agent_loop_lib.core.tool_schema import ToolSchema

        properties: dict[str, Any] = {}
        required: list[str] = []
        for param in self.parameters:
            prop: dict[str, Any] = {
                "type": _JSON_SCHEMA_TYPES[param.type],
                "description": param.description,
            }
            if param.items is not None:
                prop["items"] = param.items
            if param.enum is not None:
                prop["enum"] = param.enum
            properties[param.name] = prop
            if param.required:
                required.append(param.name)

        return ToolSchema(
            name=self.name,
            description=self.description,
            input_schema={
                "type": "object",
                "properties": properties,
                "required": required,
            },
        )

    def to_summary(self) -> ToolSummary:
        """Export the lightweight, phase-1 discovery descriptor."""
        return ToolSummary(
            name=self.name,
            short_description=self.short_description,
            path=self.path,
            tags=tuple(self.tags),
        )
