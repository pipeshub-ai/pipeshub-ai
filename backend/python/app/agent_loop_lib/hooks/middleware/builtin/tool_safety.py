from __future__ import annotations

import re

from app.agent_loop_lib.hooks.middleware.context import ToolCallContext

"""`ToolSafetyHook` (new): a PRE_TOOL_USE middleware layer of static,
deterministic denylist checks for a small set of unambiguously destructive
command patterns — separate from `require_permission` (name-based
allow/deny), `enforce_mode` (risk-tag-based), and `require_approval`
(policy/HIL-based). This is a last-resort, defense-in-depth net for shell/
DB commands whose literal text is dangerous regardless of tool risk
classification or approval policy (e.g. a HIGH-risk tool an operator has
set to AUTO_APPROVE should still refuse `rm -rf /`).

Deliberately narrow and pattern-based rather than trying to be a general
sandboxing solution — the real confinement lives in `sandbox/os_sandbox.py`
(`ConfinedLocalSandbox`) and `sandbox/db_sandbox.py`'s table allowlist; this
is one more independent layer, not a replacement for either.
"""

_DANGEROUS_SHELL_PATTERNS = [
    re.compile(r"rm\s+-[a-z]*r[a-z]*f?\s+/(\s|$)"),   # rm -rf / (and permutations of -rf)
    re.compile(r"rm\s+-[a-z]*f[a-z]*r?\s+/(\s|$)"),
    re.compile(r":\(\)\s*\{\s*:\|\s*:\s*&\s*\}\s*;\s*:"),  # fork bomb
    re.compile(r"mkfs\."),
    re.compile(r">\s*/dev/sd[a-z]"),
    re.compile(r"dd\s+.*of=/dev/sd[a-z]"),
]

_DANGEROUS_SQL_PATTERNS = [
    re.compile(r"drop\s+database", re.IGNORECASE),
    re.compile(r"drop\s+table\s+\w+\s*;?\s*--", re.IGNORECASE),
]

_CHECKED_TOOL_NAMES = {"run_shell", "execute_code"}
_SQL_CHECKED_TOOL_NAMES = {"db_query"}


def enforce_tool_safety():
    """PRE_TOOL_USE middleware: deny calls whose input matches a known
    catastrophic command pattern, regardless of the tool's own risk/approval
    configuration."""

    async def _middleware(ctx: ToolCallContext, next_fn) -> None:
        name = ctx.tool_path.rsplit("/", 1)[-1]
        if name in _CHECKED_TOOL_NAMES:
            text = str(ctx.tool_input.get("command") or ctx.tool_input.get("code") or "")
            for pattern in _DANGEROUS_SHELL_PATTERNS:
                if pattern.search(text):
                    ctx.deny(f"Tool '{name}' input matched a denylisted destructive pattern")
                    return
        if name in _SQL_CHECKED_TOOL_NAMES:
            sql = str(ctx.tool_input.get("sql") or "")
            for pattern in _DANGEROUS_SQL_PATTERNS:
                if pattern.search(sql):
                    ctx.deny(f"Tool '{name}' SQL matched a denylisted destructive pattern")
                    return
        await next_fn()

    return _middleware
