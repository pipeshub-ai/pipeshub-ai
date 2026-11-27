"""PA-5: every connector tool states its effect, from a reviewed list.

RO-1 scans the source (no imports, so a toolset whose SDK is missing from
this venv is still checked); RO-3 holds the docs to the code; RO-2 covers the
platform tools a durable run exposes besides the connector toolsets.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from harness_core.kernel.durable.effects import classify_tool_effect
from harness_core.tools.base import Tag
from harness_core.tools.tags import EFFECT_TAG_KEY

BACKEND = Path(__file__).resolve().parents[3]
ACTIONS = BACKEND / "app" / "agents" / "actions"
REVIEWED = BACKEND.parents[1] / "docs" / "agent-harness-v2" / "impl" / "tool-effects.md"
EFFECTS = ("read", "write")


def _effect_tags(decorator: ast.Call) -> list[str]:
    tags = next((k.value for k in decorator.keywords if k.arg == "tags"), None)
    found: list[str] = []
    for element in tags.elts if isinstance(tags, ast.List) else []:
        if not (isinstance(element, ast.Call) and getattr(element.func, "id", None) == "Tag"):
            continue
        args = {k.arg: k.value for k in element.keywords}
        positional = list(element.args)
        key = args.get("key", positional[0] if positional else None)
        value = args.get("value", positional[1] if len(positional) > 1 else None)
        if isinstance(key, ast.Constant) and key.value == EFFECT_TAG_KEY:
            found.append(value.value if isinstance(value, ast.Constant) else "<dynamic>")
    return found


def scan(source: str) -> list[tuple[str, str | None, list[str]]]:
    """``(method, @tool path, effect tags)`` for every ``@tool(...)`` decorator."""
    tools: list[tuple[str, str | None, list[str]]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Call) and getattr(decorator.func, "id", None) == "tool":
                path = next(
                    (k.value.value for k in decorator.keywords if k.arg == "path" and isinstance(k.value, ast.Constant)),
                    None,
                )
                tools.append((node.name, path, _effect_tags(decorator)))
    return tools


def _connector_tools() -> dict[str, str]:
    """``@tool`` path -> its effect, for every connector tool; asserts one tag each."""
    effects: dict[str, str] = {}
    problems: list[str] = []
    for file in sorted(ACTIONS.rglob("*.py")):
        for method, path, tags in scan(file.read_text(encoding="utf-8-sig")):
            where = f"{file.relative_to(BACKEND)}::{method}"
            if len(tags) != 1 or tags[0] not in EFFECTS:
                problems.append(f"{where} has effect tags {tags}")
                continue
            assert path is not None, where
            effects[path] = tags[0]
    assert not problems, (
        "every @tool must carry exactly one reviewed effect tag, "
        'Tag(key="effect", value="read"|"write") (see docs/agent-harness-v2/impl/tool-effects.md):\n'
        + "\n".join(problems)
    )
    return effects


def test_ro1_every_connector_tool_carries_one_reviewed_effect_tag() -> None:
    effects = _connector_tools()
    assert len(effects) > 500, "the scan must find the connector toolsets"


def test_ro1_the_scan_catches_an_untagged_tool() -> None:
    untagged = '''
class Toy:
    @tool(path="/tools/toy/a", tags=[Tag(key="type", value="read")])
    async def a(self): ...

    @tool(path="/tools/toy/b", tags=[Tag(key="effect", value="read")])
    async def b(self): ...

    @tool(path="/tools/toy/c")
    async def c(self): ...

    @tool(path="/tools/toy/d", tags=[Tag("effect", "write"), Tag("effect", "read")])
    async def d(self): ...
'''
    assert [(m, t) for m, _, t in scan(untagged)] == [
        ("a", []),
        ("b", ["read"]),
        ("c", []),
        ("d", ["write", "read"]),
    ]


def _reviewed() -> dict[str, str]:
    row = re.compile(r"^\| `(?P<path>/tools/[^`]+)` \| (?P<effect>read|write) \|")
    reviewed: dict[str, str] = {}
    for line in REVIEWED.read_text(encoding="utf-8").splitlines():
        match = row.match(line)
        if match:
            reviewed[match["path"]] = match["effect"]
    return reviewed


def test_ro3_the_reviewed_list_in_the_docs_is_what_the_code_carries() -> None:
    code = _connector_tools()
    docs = _reviewed()
    assert docs == code, {
        "only in code": sorted(set(code) - set(docs)),
        "only in docs": sorted(set(docs) - set(code)),
        "differ": sorted(p for p in set(code) & set(docs) if code[p] != docs[p]),
    }


def _platform_tools() -> dict[str, object]:
    from app.agent_loop_lib.tools.builtin.data.retrieve_artifact import (
        RetrieveArtifactContentTool,
    )
    from app.agent_loop_lib.tools.builtin.lazy_toolsets import (
        FetchToolsTool,
        ListToolsetsTool,
        SearchToolsTool,
    )
    from app.agent_loop_lib.tools.builtin.planning.final_answer import FinalAnswerTool
    from app.agent_loop_lib.tools.registry import ToolRegistry
    from app.agents.agent_loop.hooks.citations import (
        CitationCollector,
        _FetchFullRecordTool,
    )

    registry = ToolRegistry()
    context = type("Ctx", (), {"tool_state": {}})()
    return {
        "search_tools": SearchToolsTool(registry),
        "list_toolsets": ListToolsetsTool(registry),
        "fetch_tools": FetchToolsTool(registry),
        "final_answer": FinalAnswerTool(),
        "retrieve_artifact_content": RetrieveArtifactContentTool(store=object()),  # type: ignore[arg-type]
        "knowledgegraph__fetch_record": _FetchFullRecordTool(
            CitationCollector(context), context  # type: ignore[arg-type]
        ),
    }


@pytest.mark.parametrize(
    "name", ["search_tools", "list_toolsets", "fetch_tools", "final_answer", "retrieve_artifact_content", "knowledgegraph__fetch_record"]
)
def test_ro2_platform_tools_are_read_by_their_own_tag(name: str) -> None:
    tool = _platform_tools()[name]
    tags = list(tool.tags)  # type: ignore[attr-defined]
    assert Tag(EFFECT_TAG_KEY, "read") in tags
    assert classify_tool_effect(name, tags).effect == "read"


@pytest.mark.parametrize(
    ("name", "effect"),
    [
        ("skills_list", "read"),
        ("skill_search", "read"),
        ("load_skill", "read"),
        ("load_skill_resource", "read"),
        ("skill_manage", "write"),
    ],
)
def test_pa17_the_skill_tools_carry_their_reviewed_effect(name: str, effect: str) -> None:
    """The four reading skill tools only list, search and load (``load_skill``
    records no usage: it activates without a session); ``skill_manage`` writes."""
    from app.agent_loop_lib.tools.builtin.data import skills

    tools = {
        "skills_list": skills.SkillsListTool,
        "skill_search": skills.SkillSearchTool,
        "load_skill": skills.LoadSkillTool,
        "load_skill_resource": skills.LoadSkillResourceTool,
        "skill_manage": skills.SkillManageTool,
    }
    tool = tools[name].__new__(tools[name])
    assert tool.name == name
    assert classify_tool_effect(name, list(tool.tags)).effect == effect


def test_ro2_dynamic_tools_carry_the_effect_they_were_reviewed_for() -> None:
    from langchain_core.tools import StructuredTool

    from app.agents.agent_loop.tool_adapter import PipesHubStructuredToolAdapter
    from app.agents.agent_loop.web_tool_adapter import WebToolAdapter
    from harness_core.tools.tags import TAG_EFFECT_WRITE

    async def noop() -> str:
        return ""

    structured = StructuredTool.from_function(coroutine=noop, name="x", description="x")
    web = WebToolAdapter(structured, "dynamic", "web_search", context=object())  # type: ignore[arg-type]
    sql = PipesHubStructuredToolAdapter(structured, "sql", "execute_sql_query", effect=TAG_EFFECT_WRITE)
    untagged = PipesHubStructuredToolAdapter(structured, "new", "tool")
    assert classify_tool_effect(web.name, web.tags).effect == "read"
    assert classify_tool_effect(sql.name, sql.tags).effect == "write"
    # A dynamic tool nobody reviewed stays write (fail closed).
    assert untagged.tags == []
    assert classify_tool_effect(untagged.name, untagged.tags).effect == "write"


def test_ro2_the_effect_tag_decides_over_the_older_type_tag() -> None:
    tags = [Tag("type", "read"), Tag(EFFECT_TAG_KEY, "write")]
    assert classify_tool_effect("slack__get_dm_history", tags).effect == "write"
    tags = [Tag("type", "write"), Tag(EFFECT_TAG_KEY, "read")]
    assert classify_tool_effect("jira__get_create_issue_fields", tags).effect == "read"


def test_legacy_search_ranking_ignores_the_effect_tag() -> None:
    """Legacy stays identical: ``search_tools`` never ranks by the durable classification."""
    from app.agent_loop_lib.tools.base import ToolSummary
    from app.agent_loop_lib.tools.index import _tool_haystack

    plain = ToolSummary(name="post", short_description="Send a message", path="/t/a", tags=(Tag("type", "write"),))
    tagged = ToolSummary(
        name="post", short_description="Send a message", path="/t/a",
        tags=(Tag("type", "write"), Tag(EFFECT_TAG_KEY, "read")),
    )
    assert _tool_haystack(tagged) == _tool_haystack(plain)


async def test_pa17_load_skill_activates_without_a_session_so_it_records_no_usage() -> None:
    """The evidence for ``load_skill`` being ``read``: the usage tracker only
    records an activation that names a session, and the tool never names one."""
    from app.agent_loop_lib.tools.builtin.data.skills import LoadSkillTool

    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    class _Manager:
        async def activate_skill(self, *args: object, **kwargs: object) -> object:
            calls.append((args, kwargs))
            raise RuntimeError("stop here")

    tool = LoadSkillTool.__new__(LoadSkillTool)
    tool._manager = _Manager()  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError):
        _ = await tool.execute(name="triage")
    assert calls == [(("triage",), {})]
