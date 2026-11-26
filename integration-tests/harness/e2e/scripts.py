"""The scripted model's turn for a row (04 §4.2): chosen by the tool family, typed into the question as directives.

The scripted model (``deployment/e2e/fake_llm/server.py``, ``ScriptedBrain``) reads ``tool <name> <json>`` and
``say <text>`` lines from the user's message: a tool directive calls the offered tool whose name is ``<name>`` or ends
in ``_<name>``, and after the last call the answer is the ``say`` text. Every answer carries the row's marker, so the
runner finds the turn's answer in what the user sees and in the saved conversation.

Sink scripts read first, then write: the run is tainted before the write, so the flow rule applies (04 §4.3). The loop
discloses connector and MCP tools lazily, so those scripts find the tools first, as a model would (QA5-F6).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass

SKILL_NAME = "qa6-release-checklist"
PRIVATE_ISSUE = "SEC-7"


@dataclass(frozen=True)
class Script:
    name: str
    lines: tuple[str, ...]
    writes: str | None = None
    """The tool whose call is the external write, if any."""

    def text(self) -> str:
        return "\n".join(self.lines)


def _tool(name: str, args: Mapping[str, object]) -> str:
    return f"tool {name} {json.dumps(args)}"


def _discover(*names: str) -> str:
    return _tool("search_tools", {"query": " ".join(names), "make_visible": True})


def answer(marker: str) -> Script:
    return Script("answer", ("Answer in one line.", f"say {marker}"))


def _either(*options: tuple[str, Mapping[str, object]]) -> str:
    """One capability under the tool this mode gives it (the scripted model takes the first one offered)."""
    return "tool " + " || ".join(f"{name} {json.dumps(args)}" for name, args in options)


def _delegated(task: str, *lines: str) -> Mapping[str, object]:
    """A composed sub-agent's goal: its own short script."""
    return {"goal": "\n".join((task, *lines))}


def read_then_answer(marker: str) -> Script:
    query = {"query": "onboarding guide"}
    return Script("read-then-answer", (
        "Look up the onboarding guide, then answer.",
        _either(
            ("search_internal_knowledge", query),
            ("internal_exploration_agent", _delegated("Find the onboarding guide.",
                                                      _tool("search_internal_knowledge", query), "say Found the guide.")),
            ("knowledgegraph__search", query),
        ),
        f"say {marker}",
    ))


def read_then_write_mcp(marker: str) -> Script:
    return Script("read-then-write-mcp", (
        "Read the team's note on the finance review, then file a ticket about it.",
        _discover("lookup_note", "create_ticket"),
        _tool("lookup_note", {"topic": "finance review"}),
        _tool("create_ticket", {"title": f"Finance review {marker}", "body": "From the team's note."}),
        f"say {marker}",
    ), writes="create_ticket")


def read_then_write_toolset(marker: str) -> Script:
    return Script("read-then-write-toolset", (
        f"Read issue {PRIVATE_ISSUE}, then open a follow-up issue for it.",
        _discover("get_issue", "create_issue"),
        _tool("get_issue", {"issue_key": PRIVATE_ISSUE}),
        _tool("create_issue", {"project_key": "SEC", "summary": f"Follow up {marker}", "issue_type_name": "Task"}),
        f"say {marker}",
    ), writes="create_issue")


def code_then_answer(marker: str) -> Script:
    code = {"language": "python",
            "code": "open('qa6-summary.csv', 'w').write('metric,value\\nrows,3\\n')\nprint('rows,3')"}
    return Script("code-then-answer", (
        "Compute the summary in code and save it as a file.",
        _either(
            ("run_code", code),
            ("coding_agent", _delegated("Compute the summary in code.", _tool("run_code", code), "say Saved the summary.")),
        ),
        f"say {marker}",
    ))


def skill_then_answer(marker: str) -> Script:
    return Script("skill-then-answer", (
        "Use the release checklist skill.",
        _tool("load_skill", {"name": SKILL_NAME}),
        f"say {marker}",
    ))


REMEMBERED = "Prefers answers as short bullet lists."


def remember_then_answer(marker: str) -> Script:
    return Script("remember-then-answer", (
        "From now on, I prefer answers as short bullet lists.",
        # remember's own schema (one item per fact): an engine's call is checked against it at the gateway (C3.30),
        # which does not take the one-memory shape the loop's tool still reads.
        _tool("remember", {"memories": [{"content": REMEMBERED, "kind": "preference", "scope": "user"}]}),
        f"say {marker}",
    ))


BY_FAMILY: Mapping[str, Callable[[str], Script]] = {
    "none": answer,
    "knowledge": read_then_answer,
    "mcp": read_then_write_mcp,
    "toolset": read_then_write_toolset,
    "run_code": code_then_answer,
    "skills": skill_then_answer,
    "memory": remember_then_answer,
}


# The script's name per tool family (04 §4.2): the one table every lane that scripts a matrix row reads
# (the engine matrix lane, tests/unit/harness/engines/matrix/rows.py, imports it).
SCRIPT_NAMES: Mapping[str, str] = {family: make("").name for family, make in BY_FAMILY.items()}

ENGINE_NOTES = "qa6-notes.md"
# An engine runs code with its own shell and loads a skill with its own skill tool, not the loop's tools.
_ENGINE_LINES = {
    "run_code": "shell printf 'metric,value\\nrows,3\\n' > qa6-summary.csv",
    "load_skill": f"skill {SKILL_NAME}",
}


def for_row(family: str, marker: str, *, engine: bool = False) -> Script:
    """An engine is given its tools through the tool gateway at once (no lazy disclosure to search), runs code in
    its own shell, and leaves a file in its workspace, so the run commits a workspace version (04 §4.4)."""
    script = BY_FAMILY[family](marker)
    if not engine:
        return script
    *work, say = (_ENGINE_LINES.get(line.split(" ", 2)[1] if line.startswith("tool ") else "", line)
                  for line in script.lines if not line.startswith("tool search_tools "))
    return Script(script.name, (*work, f"write {ENGINE_NOTES} {marker}", say), writes=script.writes)
