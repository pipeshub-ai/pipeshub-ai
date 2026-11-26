"""LONGTASK-B4: the code-running guidance agrees with the chat's workspace (QAFIX4 B1).

``run_code``'s description says files under ``$WORKSPACE_DIR`` are kept for later turns, but the same description
then said "anything else your code writes stays inside the sandbox and is never shown to the user", and
``coding_agent``'s instructions said everything outside ``$OUTPUT_DIR`` stays in the sandbox. A live model asked to
keep a wiki "in your workspace" wrote it to ``$OUTPUT_DIR`` as attachments; the next turn could not find it in the
workspace (S3 research-wiki and planning-constraints, gpt-6-luna).
"""

from __future__ import annotations

from app.agents.agent_loop.domain_agents import DOMAIN_AGENT_DEFINITIONS

NEVER_SHOWN = "never shown to the user"


def _coding_agent() -> str:
    definition = next(d for d in DOMAIN_AGENT_DEFINITIONS if d.name == "coding_agent")
    return definition.extra_instructions or ""


def test_coding_agent_keeps_files_for_later_turns_in_the_workspace() -> None:
    text = _coding_agent()
    assert "$WORKSPACE_DIR" in text, text
    assert NEVER_SHOWN not in text and "Anything written outside $OUTPUT_DIR" not in text, text


def test_run_code_description_does_not_contradict_the_session_workspace() -> None:
    from app.agents.agent_loop.sandbox_bridge import PipesHubCodingSandboxTool

    tool = PipesHubCodingSandboxTool(object())  # type: ignore[arg-type]
    tool.set_session_workspace(True)
    description = tool.description
    assert "saved to the chat" in description, description
    assert "names by a path in the workspace" in description, description
    assert NEVER_SHOWN not in description, description


def test_fixp2_the_delegating_loop_is_told_where_a_named_file_goes() -> None:
    """FIX-P2 workspace paths: the delegating loop told ``coding_agent`` to put the files the person named
    (plan.json, wiki/plan.md, wiki/reconciliation.md) in $OUTPUT_DIR, so they were attached instead of kept. The
    parent-facing description now says where such a file goes."""
    description = next(d for d in DOMAIN_AGENT_DEFINITIONS if d.name == "coding_agent").description
    assert "A file the user names by a path (wiki/plan.md, data/clean.csv) belongs in this chat's workspace at that path" in description
    assert "write it under $WORKSPACE_DIR at exactly that path" in description
    assert "$OUTPUT_DIR only attaches a file to the reply" in description
