from __future__ import annotations

from dataclasses import dataclass, field

# Agent modes are descriptive text only — they never bypass hooks. Actual
# tool-call enforcement stays with PermissionHook/ApprovalHook; this guidance
# just tells the model what regime it's operating under so it self-restricts
# before the deterministic layer would block it anyway.
MODE_GUIDANCE: dict[str, str] = {
    "plan": (
        "[Mode: plan] You are in planning mode. Describe your plan and reasoning; "
        "do not call tools that mutate state. Read-only/investigative tools are fine."
    ),
    "auto_approve": (
        "[Mode: auto-approve] Tool calls you make will execute immediately without "
        "a human approval prompt — be conservative and double-check risky actions yourself."
    ),
    "act": "",
}


@dataclass
class PromptTemplate:
    """Named, individually-overridable sections composed into one system
    prompt, rendered fresh per model call.

    Replaces `role.system_prompt` as a flat string with a structured
    composition unit — Claude Code / OpenHarness-style prompt assembly:
    identity, goal brief, toolset overview, todos state, mode, and output
    style are each their own section so any layer (a hook, a role default,
    a developer override) can set/replace one without touching the rest.
    """

    DEFAULT_ORDER: tuple[str, ...] = (
        "identity", "goal_brief", "toolset_overview", "skills_overview", "todos", "mode", "style",
    )

    sections: dict[str, str] = field(default_factory=dict)

    def set(self, name: str, content: str | None) -> "PromptTemplate":
        if content:
            self.sections[name] = content
        else:
            self.sections.pop(name, None)
        return self

    def get(self, name: str) -> str | None:
        return self.sections.get(name)

    def render(self, order: list[str] | None = None) -> str:
        ordered = list(order or self.DEFAULT_ORDER)
        seen: set[str] = set()
        parts: list[str] = []
        for name in ordered:
            if name in self.sections:
                parts.append(self.sections[name])
                seen.add(name)
        # Any section not named in `order` (e.g. a developer-added custom
        # one) still renders — appended after the ordered ones rather than
        # silently dropped.
        for name, content in self.sections.items():
            if name not in seen:
                parts.append(content)
        return "\n\n".join(p for p in parts if p)
