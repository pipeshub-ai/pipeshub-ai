"""How a tool's result restricts who may see the answer it reached.

`RECORDS` tools name what they showed through the writer hooks (record ids,
containers, artifacts, skills), so their own class adds nothing more. A tool read with
the asker's own credentials (MCP, actions, API toolsets, SQL toolsets) is
`PERSONAL`: the answer is then the asker's alone until shared. So is any tool
this table does not know, which fails closed. The web is `PUBLIC`.

Every tool the agent loop registers itself has a rule here (`explicit_class`), and
a test enumerates them, so a new one can't fall through to `PERSONAL` unnoticed.
A connector toolset's tools have none: they run on a credential.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping


class LabelClass(StrEnum):
    RECORDS = "records"
    PUBLIC = "public"
    PERSONAL = "personal"
    # Computed only from the model's own arguments, fixed text, or a child run
    # whose own model calls are already in the ledger.
    NONE = "none"


# Internal `/tools/<app>/` toolsets. Every other app is an external toolset on
# a credential, so personal.
_RECORDS_APPS: Final = frozenset({
    "knowledgegraph", "codegraph", "knowledgehub", "retrieval", "storage_pattern_match",
    # Artifact reads are named by the artifact registry; a record behind a
    # download link by the tool itself.
    "artifacts", "image_generator", "coding_sandbox",
})
_NONE_APPS: Final = frozenset({"calculator", "date_calculator", "internaltools", "agent_builder"})
# `/dynamic/<app>/` tools that name what they showed: SQL tables and live rows
# by connector, Slack threads by record and channels by record group. The
# `/tools/slack/` toolset is another thing: actions on the user's own Slack.
_RECORDS_DYNAMIC_APPS: Final = frozenset({"sql", "slack"})
_RECORDS_DYNAMIC_NAMES: Final = frozenset({
    "sql__execute_sql_query", "sql__discover_related_tables", "sql__get_table_relations",
    "slack__fetch_slack_thread", "slack__fetch_slack_nearby_messages",
})
_PUBLIC_DYNAMIC_TOOLS: Final = frozenset({"web_search", "fetch_url"})
# Library toolsets.
_NONE_TOOLSETS: Final = frozenset({"builtin", "agents"})
# The skill tools name each skill they showed, by where it came from, through
# the run's skill manager (`LabelledSkillManager`).
_RECORDS_TOOLSETS: Final = frozenset({"coding_sandbox", "skills"})
# Tools the agent loop registers whose path no rule above covers, each with the
# name the model calls it by.
_PATHS: Final[Mapping[str, tuple[str, LabelClass]]] = {
    # Granted once the model holds record ids; it names every record it renders
    # (`remember_record_ids`).
    "/dynamic/dynamic/fetch_full_record": ("knowledgegraph__fetch_record", LabelClass.RECORDS),
    # A tool result this run compacted, or one of an earlier run, which the
    # artifact registry names as that run's.
    "/tools/data/retrieve_artifact_content": ("retrieve_artifact_content", LabelClass.RECORDS),
    # A new, empty database, filled and queried by the model's own statements.
    "/tools/database_sandbox/execute_sqlite": ("database_sandbox__execute_sqlite", LabelClass.NONE),
    # A database the deployment keeps between runs: nothing names who wrote its
    # rows or who may read them.
    "/tools/database_sandbox/execute_postgresql": ("database_sandbox__execute_postgresql", LabelClass.PERSONAL),
}
# Built-in tools named without an app prefix. A call that never ran showed the
# model only the loop's own text, so none of these adds anything.
_NONE_NAMES: Final = frozenset({
    "final_answer", "task_complete", "create_plan", "critique_plan", "verify_result", "replan",
    "write_todos", "spawn_agent", "handoff", "clarify", "route_task", "best_of_n", "request_review",
    "list_toolsets", "fetch_tools", "search_tools", "parse_intent",
    "load_skill", "load_skill_resource", "skill_search", "skills_list", "skill_manage",
    "run_code", "install_packages", "read_sandbox_file",
    # The domain agents (`domain_agents.DOMAIN_AGENT_DEFINITIONS`).
    "web_agent", "coding_agent", "internal_exploration_agent",
})
_NAMES: Final[Mapping[str, LabelClass]] = dict(_PATHS.values())


def _by_app(app: str, tool: str, *, dynamic: bool, extra: Mapping[str, LabelClass]) -> LabelClass | None:
    if app in extra:
        return extra[app]
    if dynamic or app == "dynamic":
        if tool in _PUBLIC_DYNAMIC_TOOLS:
            return LabelClass.PUBLIC
        if app in _RECORDS_DYNAMIC_APPS:
            return LabelClass.RECORDS
        return None
    if app in _RECORDS_APPS:
        return LabelClass.RECORDS
    if app in _NONE_APPS:
        return LabelClass.NONE
    return None


def explicit_class(tool_path: str | None, *, extra: Mapping[str, LabelClass] | None = None) -> LabelClass | None:
    """The class a rule of this table gives a tool by its registered path, or None
    when none does: a connector toolset's tool, or a path nobody classified.
    `extra` maps an edition's own `/tools/<app>/` toolsets to their class."""
    path = "/" + (tool_path or "").strip("/")
    if path in _PATHS:
        return _PATHS[path][1]
    parts = path.strip("/").split("/")
    extra = extra or {}
    if len(parts) < 2:
        return None
    root, app = parts[0], parts[1]
    tool = parts[-1] if len(parts) > 2 else ""
    if root in ("mcp", "api-toolsets"):
        return LabelClass.PERSONAL
    if root == "tools":
        return _by_app(app, tool, dynamic=False, extra=extra)
    if root == "dynamic":
        return _by_app(app, tool, dynamic=True, extra=extra)
    if root == "toolsets":
        if app in _NONE_TOOLSETS:
            return LabelClass.NONE
        if app in _RECORDS_TOOLSETS:
            return LabelClass.RECORDS
    return None


def label_class(tool_path: str | None, *, extra: Mapping[str, LabelClass] | None = None) -> LabelClass:
    """The class of a tool by its registered path: its rule's (`explicit_class`),
    else `PERSONAL`. `extra` maps an edition's own `/tools/<app>/` toolsets to
    their class."""
    return explicit_class(tool_path, extra=extra) or LabelClass.PERSONAL


def label_class_for_name(name: str, *, extra: Mapping[str, LabelClass] | None = None) -> LabelClass:
    """The class of a call known only by the name the model used, for a call that
    never reached the executor (its result is the loop's own text)."""
    if name in _NONE_NAMES:
        return LabelClass.NONE
    if name in _NAMES:
        return _NAMES[name]
    if name in _RECORDS_DYNAMIC_NAMES:
        return LabelClass.RECORDS
    app, sep, tool = name.partition("__")
    if not sep:
        return LabelClass.PERSONAL
    return _by_app(app, tool, dynamic=False, extra=extra or {}) or LabelClass.PERSONAL


__all__ = ["LabelClass", "explicit_class", "label_class", "label_class_for_name"]
