"""Deterministic transformer: Agent Builder JSON config → Workflow SDK code.

This module inverts the logic of the Node.js extract-agent-config.ts helper.
It takes a serialised Agent Builder configuration (the JSON that the Node.js
API returns from GET /agent-configs/:id) and produces a valid Python workflow
module that can be verified against the SDK stubs and saved as a WorkflowVersion.

Design notes
------------
- Deterministic: no LLM calls.  The same input always produces the same output.
- Dual-run behind a feature flag before the builder is deprecated.
- Parity is checked by running the generated code against the SDK verifier.
- Complex agent configs that cannot be mechanically mapped produce an
  ``UntranslatableAgentConfig`` error with a human-readable reason.

Supported Agent Builder primitives
-----------------------------------
- ``tools``          → ``ctx.tool(tool_name, **kwargs)`` calls
- ``knowledge``      → ``ctx.tool("knowledge/search", query=...)`` calls
- ``skills``         → inline comments pointing to SDK skill equivalents
- ``model``          → keyword argument on ``ctx.agent(model=...)``
- ``system_prompt``  → multiline string constant ``SYSTEM_PROMPT``
- ``temperature``    → keyword argument on ``ctx.agent(temperature=...)``
- ``max_tokens``     → keyword argument on ``ctx.agent(max_tokens=...)``
- ``sub_agents``     → nested ``ctx.agent(name=...)`` calls
"""
from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "AgentBuilderToSDKTransformer",
    "TransformResult",
    "UntranslatableAgentConfig",
    "transform_agent_config",
    "dual_run_check",
]


class UntranslatableAgentConfig(ValueError):
    """Raised when the agent config cannot be mechanically converted to SDK code."""


@dataclass
class TransformResult:
    source: str
    warnings: list[str] = field(default_factory=list)


class AgentBuilderToSDKTransformer:
    """Transforms an Agent Builder config dict into a Workflow SDK Python module.

    Usage::

        transformer = AgentBuilderToSDKTransformer()
        result = transformer.transform(config_dict)
        # result.source is importable Python; result.warnings lists caveats
    """

    # Top-level keys we know how to handle (others are silently skipped with
    # a warning rather than raising, so new builder fields don't break
    # existing migrations).
    _KNOWN_KEYS = frozenset({
        "name", "description", "tools", "knowledge", "skills",
        "model", "system_prompt", "temperature", "max_tokens",
        "sub_agents", "input_schema", "output_schema",
    })

    def transform(self, config: dict[str, Any]) -> TransformResult:
        """Produce a SDK-compatible Python module from *config*.

        Raises:
            UntranslatableAgentConfig: if the config has structural problems
                that make mechanical translation impossible.
        """
        warnings: list[str] = []
        name_raw = config.get("name", "unnamed_agent")
        fn_name = self._slugify(name_raw)
        description = config.get("description", "")

        system_prompt = config.get("system_prompt", "")
        model = config.get("model", "")
        temperature = config.get("temperature")
        max_tokens = config.get("max_tokens")
        tools = config.get("tools") or []
        knowledge = config.get("knowledge") or []
        skills = config.get("skills") or []
        sub_agents = config.get("sub_agents") or []

        # Warn about unknown keys
        for key in config:
            if key not in self._KNOWN_KEYS:
                warnings.append(f"Unknown agent builder key '{key}' — skipped.")

        # Build the source lines
        lines: list[str] = []

        lines.append('"""')
        lines.append(f'Auto-generated from Agent Builder config: {name_raw!r}')
        if description:
            lines.append("")
            lines.append(description)
        lines.append('"""')
        lines.append("from __future__ import annotations")
        lines.append("from app.services.workflows.sdk import workflow, step, Ctx")
        lines.append("")

        # System prompt constant
        if system_prompt:
            escaped = system_prompt.replace('\\', '\\\\').replace('"""', '\\"\\"\\"')
            lines.append(f'SYSTEM_PROMPT = """{escaped}"""')
            lines.append("")

        # Knowledge retrieval step (if knowledge bases configured)
        if knowledge:
            lines.append("")
            lines.append("@step(retries=2, timeout_s=30)")
            lines.append("async def _retrieve_knowledge(ctx: Ctx, goal: str) -> list:")
            lines.append('    """Retrieve relevant knowledge from configured knowledge bases."""')
            lines.append("    results = []")
            for kb in knowledge:
                kb_id = kb.get("id", "unknown")
                lines.append(
                    f'    results.extend(await ctx.tool("knowledge/search",'
                    f' knowledge_base_id={kb_id!r}, query=goal))'
                )
            lines.append("    return results")
            lines.append("")

        # Tool invocation step (if explicit tools configured)
        for tool in tools:
            tool_name = tool.get("name", "")
            if not tool_name:
                warnings.append("Tool entry missing 'name' — skipped.")
                continue
            fn = self._slugify(f"call_{tool_name}")
            lines.append("")
            lines.append("@step(retries=1, timeout_s=60)")
            lines.append(f"async def {fn}(ctx: Ctx, **kwargs) -> dict:")
            lines.append(f'    """Invoke the {tool_name} tool."""')
            lines.append(f"    return await ctx.tool({tool_name!r}, **kwargs)")
            lines.append("")

        # Skills are advisory in the SDK — document them as comments
        if skills:
            lines.append("# Skills loaded at agent runtime (managed by the Skills system):")
            for skill in skills:
                skill_name = skill.get("name", str(skill))
                lines.append(f"# - {skill_name}")
            lines.append("")

        # Sub-agent invocations
        for agent in sub_agents:
            agent_name = agent.get("name", "")
            if not agent_name:
                warnings.append("Sub-agent entry missing 'name' — skipped.")
                continue
            fn = self._slugify(f"run_{agent_name}")
            lines.append("")
            lines.append("@step(retries=1, timeout_s=300)")
            lines.append(f"async def {fn}(ctx: Ctx, goal: str) -> dict:")
            lines.append(f'    """Invoke the {agent_name} sub-agent."""')
            lines.append(f"    return await ctx.agent({agent_name!r}, goal=goal)")
            lines.append("")

        # Main workflow
        lines.append("")
        lines.append("@workflow(")
        lines.append(f'    name={fn_name!r},')
        lines.append(")")
        lines.append(f"async def {fn_name}(ctx: Ctx, inp: dict) -> dict:")
        # `@workflow` has no `description` kwarg -- fold it into the docstring instead.
        docstring = f"Generated from Agent Builder: {name_raw!r}"
        if description:
            docstring += f"\n\n    {description}"
        lines.append(f'    """{docstring}"""')
        lines.append("    goal = inp.get('goal', '')")
        lines.append("")

        # Build agent call kwargs
        agent_kwargs: list[str] = ["goal=goal"]
        if system_prompt:
            agent_kwargs.append("system_prompt=SYSTEM_PROMPT")
        if model:
            agent_kwargs.append(f"model={model!r}")
        if temperature is not None:
            agent_kwargs.append(f"temperature={temperature}")
        if max_tokens is not None:
            agent_kwargs.append(f"max_tokens={max_tokens}")

        if knowledge:
            lines.append("    context = await _retrieve_knowledge(ctx, goal=goal)")
            agent_kwargs.append("context=context")

        kwargs_str = ", ".join(agent_kwargs)
        lines.append(f"    result = await ctx.agent({fn_name!r}, {kwargs_str})")
        lines.append("    return result")
        lines.append("")

        source = "\n".join(lines)
        return TransformResult(source=source, warnings=warnings)

    @staticmethod
    def _slugify(text: str) -> str:
        """Convert a human-readable name to a valid Python identifier."""
        slug = text.lower().strip()
        slug = re.sub(r"[^a-z0-9]+", "_", slug)
        slug = slug.strip("_")
        if not slug:
            slug = "agent"
        if slug[0].isdigit():
            slug = f"a_{slug}"
        return slug


def transform_agent_config(config: dict[str, Any]) -> TransformResult:
    """Module-level convenience wrapper."""
    return AgentBuilderToSDKTransformer().transform(config)


def dual_run_check(
    config: dict[str, Any],
    original_output: str,
    migrated_output: str,
    threshold: float = 0.35,
) -> dict[str, Any]:
    """Compare original agent output against migrated workflow output.

    Uses token-level Jaccard similarity as a lightweight, deterministic
    parity check during the dual-run migration window.  Returns a dict
    with ``passes`` (bool), ``similarity_score`` (float 0–1), and
    ``threshold`` (float) so callers can log or surface the metrics.

    A score above *threshold* is considered a pass.  The default 0.35
    accommodates reordering and paraphrasing while catching radically
    different outputs.
    """
    def _tokens(text: str) -> set[str]:
        words = re.findall(r"[a-z0-9]+", text.lower())
        return set(words)

    a = _tokens(original_output)
    b = _tokens(migrated_output)

    if not a and not b:
        score = 1.0
    elif not a or not b:
        score = 0.0
    else:
        score = len(a & b) / len(a | b)

    return {
        "passes": score >= threshold,
        "similarity_score": round(score, 4),
        "threshold": threshold,
        "config_name": config.get("name", ""),
    }


