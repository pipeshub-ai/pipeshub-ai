"""Unit tests for `AgentContext.project_instructions` wiring.

Additive per-conversation instructions from a linked Project (Node
`ProjectService.buildContext` -> `applyProjectContext` ->
`ChatQuery.projectInstructions`). Distinct from `instructions`
(agent-specific) and `custom_instructions` (org-level Chat Assistant /
Universal Agent Mode) — see `AgentContext.project_instructions` docstring.
These pin the field's default-off behavior and its round trip through
`AgentContext` construction, `from_chat_state()`, and `to_dict()`.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.agents.agent_loop.context import AgentContext


def _minimal_context(**overrides) -> AgentContext:
    defaults = {
        "org_id": "org-1", "user_id": "user-1", "user_email": "user@example.com",
        "logger": MagicMock(),
    }
    defaults.update(overrides)
    return AgentContext(**defaults)


class TestProjectInstructionsDefault:
    def test_direct_construction_defaults_to_none(self):
        context = _minimal_context()
        assert context.project_instructions is None

    def test_direct_construction_accepts_explicit_value(self):
        context = _minimal_context(project_instructions="Cite the Q3 report.")
        assert context.project_instructions == "Cite the Q3 report."


class TestFromChatState:
    def test_absent_in_chat_state_defaults_to_none(self):
        context = AgentContext.from_chat_state({})
        assert context.project_instructions is None

    def test_present_in_chat_state_is_threaded_through(self):
        state = {"project_instructions": "Cite the Q3 report."}
        context = AgentContext.from_chat_state(state)
        assert context.project_instructions == "Cite the Q3 report."

    def test_independent_of_custom_instructions(self):
        """The two fields must not clobber each other — a conversation can
        carry an org-level custom instruction AND a project instruction at
        once (see `prompt_builder.py`'s two independent sections)."""
        state = {
            "custom_instructions": "Always respond in Spanish.",
            "project_instructions": "Cite the Q3 report.",
        }
        context = AgentContext.from_chat_state(state)
        assert context.custom_instructions == "Always respond in Spanish."
        assert context.project_instructions == "Cite the Q3 report."


class TestSeedToolState:
    """`_seed_tool_state()` mirrors the field onto `tool_state` (via
    `model_post_init`'s `setdefault` loop) so tools/hooks reading `ChatState`
    directly can see it, same as `custom_instructions`."""

    def test_seed_tool_state_mirrors_the_field(self):
        context = _minimal_context(project_instructions="Cite the Q3 report.")
        assert context.tool_state["project_instructions"] == "Cite the Q3 report."

    def test_seed_tool_state_mirrors_none_by_default(self):
        context = _minimal_context()
        assert context.tool_state["project_instructions"] is None

    def test_from_chat_state_does_not_clobber_original_value(self):
        """The value `build_initial_state` set on `ChatState` survives into
        `tool_state` unchanged — `_seed_tool_state`'s `setdefault` never
        overwrites an already-present key."""
        state = {"project_instructions": "Cite the Q3 report."}
        context = AgentContext.from_chat_state(state)
        assert context.tool_state["project_instructions"] == "Cite the Q3 report."
