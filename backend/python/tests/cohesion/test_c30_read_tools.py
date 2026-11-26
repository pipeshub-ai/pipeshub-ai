"""C-30 (UX4): a tool whose name or kind says it reads is classified ``read`` (or ``workspace``), never ``write`` or
``execute``, unless it is a reviewed exception that names why. FG2: a read whose request carries what the model chose
to a host outside the organization declares ``sink=external``, so the flow guard holds it after a private read.

An untagged tool is a write (fail closed, P2.1), and the flow guard holds a write after a private read (UX1). So a read
classified as a write is a prompt nobody can explain: P4.17L's live lane held ``gmail/read_email`` and
``jira/get_issue`` because the replay world's read tools carried no ``effect`` tag. This scan covers every tool a durable
run can reach (P2.1's enumerators) and the replay world the deterministic and live lanes run on.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest

from harness_core.kernel.durable.effects import classify_tool_effect
from harness_core.tools.tags import EFFECT_TAG_KEY
from tests.unit.harness.test_effect_tags_complete import ENUMERATORS

if TYPE_CHECKING:
    from collections.abc import Iterable

    from harness_core.tools.base import Tag, Tool

READ_VERBS = frozenset(
    {"get", "list", "search", "read", "fetch", "query", "find", "lookup", "describe", "view", "download", "count", "retrieve", "show", "browse"}
)
READ_KINDS = frozenset({"read", "search", "list", "get", "fetch", "query"})

# Reviewed: each writes despite its name; the reason is its row in docs/agent-harness-v2/impl/tool-effects.md.
EXCEPTIONS: dict[str, str] = {
    "clickup__search_tasks": "creates and deletes a temporary workspace view",
    "confluence__download_attachment": "registers the download as an artifact in PipesHub storage",
    "salesforce__soql_query": "model-written SOQL; FOR VIEW / FOR REFERENCE update records (classified per call)",
    "slack__get_dm_history": "conversations.open creates the DM channel",
}


# FG2: the reads whose request leaves the organization (their rows in docs/agent-harness-v2/impl/tool-effects.md).
EXTERNAL_READS = frozenset({"dynamic__fetch_url", "dynamic__web_search"})
WORLD_EXTERNAL_READS = frozenset({"web__fetch_url"})
# An argument that names where a request goes or whom it reaches: a URL or host, a recipient, an email address.
# A read taking one is external or reviewed.
URL_ARG = re.compile(
    r"(^|_)(url|uri|link|href|endpoint|webhook|host|recipient|email|email_address|mail_to|to|cc|bcc|attendee)s?$"
)
# Reviewed: each takes a URL or an address that names an item in the org's own tenant; the request goes to the
# provider's API, never to that address.
INTERNAL_URL_READS: dict[str, str] = {
    "knowledgegraph__find_records_by_value": "date_to is a date range's end, not a recipient; the query reads the org's graph",
    "salesforce__search_contacts":"email filters a contact search in the org's Salesforce; nothing is sent to it",
    "onedrive__get_onenote_sections": "web_url names a notebook in the org's Microsoft 365 tenant; the request goes to Graph",
    "outlook__get_meeting_transcripts": "join_url names a meeting in the org's tenant; the request goes to Graph",
    "teams__get_my_meeting_transcripts": "join_url names a meeting in the org's tenant; the request goes to Graph",
    "teams__get_people_attended": "join_url names a meeting in the org's tenant; the request goes to Graph",
}


def implies_read(name: str, tags: Iterable[Tag]) -> bool:
    bare = re.split(r"__|\.", name)[-1]
    verb = bare.split("_", 1)[0].lower()
    kinds = {t.value.lower() for t in tags if t.key == "type"}
    return verb in READ_VERBS or bool(kinds & READ_KINDS)


def misclassified(tools: Iterable[Tool]) -> dict[str, str]:
    """Read-named tools classified as a write or execute, with their class."""
    found: dict[str, str] = {}
    for tool in tools:
        effect = classify_tool_effect(tool.name, tool.tags).effect
        if effect in ("write", "execute") and implies_read(tool.name, tool.tags):
            found[tool.name] = effect
    return found


def replay_world() -> list[Tool]:
    from app.agent_loop_lib.tools.toolset import ToolsetBuilder
    from tests.harness_replay.world import TOOLSETS

    tools: list[Tool] = []
    for name, (cls, _) in TOOLSETS.items():
        builder = ToolsetBuilder(cls.__new__(cls), name=name, description="", path_prefix=f"/tools/{name}")
        tools.extend(builder.tools)
    return tools


@pytest.fixture(scope="module")
def catalog() -> list[Tool]:
    return [tool for enumerate_ in ENUMERATORS.values() for tool in enumerate_()]


def test_c30_no_read_in_the_durable_catalog_is_a_write(catalog: list[Tool]) -> None:
    unexpected = {name: e for name, e in misclassified(catalog).items() if name not in EXCEPTIONS}
    assert unexpected == {}, f"read-named tools classified as writes (tag them `effect=read`, or review): {unexpected}"


def test_c30_every_exception_is_still_a_write(catalog: list[Tool]) -> None:
    flagged = misclassified(catalog)
    stale = sorted(name for name in EXCEPTIONS if name not in flagged)
    assert stale == [], f"no longer a read-named write; remove from EXCEPTIONS: {stale}"


def test_c30_the_replay_world_classifies_as_the_product_does() -> None:
    tools = replay_world()
    assert {t.name for t in tools} >= {"gmail__read_email", "jira__get_issue", "jira__search_issues", "drive__share_file"}
    assert [t.name for t in tools if not any(tag.key == EFFECT_TAG_KEY for tag in t.tags)] == []
    assert misclassified(tools) == {}
    effects = {t.name: classify_tool_effect(t.name, t.tags).effect for t in tools}
    assert effects["web__fetch_url"] == "read"
    assert {n for n, e in effects.items() if e == "write"} == {
        "jira__create_issue",
        "jira__add_comment",
        "slack__send_message",
        "gmail__send_email",
        "drive__share_file",
    }


def test_c30_a_planted_read_named_write_is_found() -> None:
    from harness_core.tools.base import Tag

    tools = [t for t in replay_world() if t.name == "gmail__read_email"]
    untagged = [type("Planted", (), {"name": "crm__get_account", "tags": [Tag("category", "crm")]})()]
    assert misclassified([*tools, *untagged]) == {"crm__get_account": "write"}  # type: ignore[list-item]


def loop_builtins() -> list[Tool]:
    """The agent loop's own tools under ``/toolsets/builtin/`` (planning, coordination, lifecycle, tool disclosure),
    which today's loop registers beside the connectors; instances built without running their constructors."""
    import importlib
    import inspect
    import pkgutil

    import app.agent_loop_lib.tools.builtin as builtin
    from app.agent_loop_lib.tools.base import Tool as LoopTool

    tools: list[Tool] = []
    for info in pkgutil.walk_packages(builtin.__path__, builtin.__name__ + "."):
        module = importlib.import_module(info.name)
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if cls.__module__ != module.__name__ or not issubclass(cls, LoopTool) or inspect.isabstract(cls):
                continue
            try:
                tool = cls.__new__(cls)
                if tool.path.startswith("/toolsets/builtin/"):
                    tools.append(tool)
            except AttributeError:
                continue  # needs its constructor's state to name itself: not a fixed builtin
    return tools


def test_c30_the_loops_own_tools_are_reads_or_machinery_to_both_guards() -> None:
    """One classification for both flow guards: a builtin is a read by its tags (the durable rule), or machinery by
    UX3's rule on today's loop; none is an external write to either."""
    from app.agents.agent_loop.hooks.flow_guard import is_loop_machinery
    from harness_core.effects.flow import external_write

    tools = loop_builtins()
    assert {"create_plan", "spawn_agent", "final_answer", "search_tools"} <= {t.name for t in tools}
    assert misclassified(tools) == {}
    held = [t.name for t in tools if external_write(classify_tool_effect(t.name, t.tags)) and not is_loop_machinery(t.path, t.tags)]
    assert held == []


def external_reads(tools: Iterable[Tool]) -> set[str]:
    return {
        t.name
        for t in tools
        if (effect := classify_tool_effect(t.name, t.tags)).effect == "read" and effect.sink.egress == "external"
    }


def unreviewed_url_reads(tools: Iterable[Tool]) -> set[str]:
    """Reads that take a URL-like argument, neither declared external nor a reviewed internal one."""
    return {
        t.name
        for t in tools
        if classify_tool_effect(t.name, t.tags).effect == "read"
        and any(URL_ARG.search(p.name) for p in t.parameters)
        and t.name not in external_reads([t])
        and t.name not in INTERNAL_URL_READS
    }


def test_c30_fg2_reads_that_leave_the_organization_are_external(catalog: list[Tool]) -> None:
    assert external_reads(catalog) == EXTERNAL_READS
    assert external_reads(replay_world()) == WORLD_EXTERNAL_READS
    assert unreviewed_url_reads(catalog) == set(), "a read takes a URL: tag it `sink=external`, or review it"
    assert unreviewed_url_reads(replay_world()) == set()


def test_c30_fg2_every_reviewed_url_read_is_still_one(catalog: list[Tool]) -> None:
    url_reads = {
        t.name
        for t in catalog
        if classify_tool_effect(t.name, t.tags).effect == "read" and any(URL_ARG.search(p.name) for p in t.parameters)
    }
    stale = sorted(set(INTERNAL_URL_READS) - (url_reads - EXTERNAL_READS))
    assert stale == [], f"no longer an internal URL-taking read; remove from INTERNAL_URL_READS: {stale}"


def test_c30_fg2_a_planted_url_read_without_the_tag_is_found() -> None:
    from harness_core.tools.base import Tag

    param = type("Param", (), {"name": "target_url"})()
    planted = type("Planted", (), {"name": "crm__fetch_page", "tags": [Tag("effect", "read")], "parameters": [param]})()
    assert unreviewed_url_reads([planted]) == {"crm__fetch_page"}  # type: ignore[list-item]
    for name in ("recipient", "to_recipients", "user_email", "cc"):
        addressed = type("Addressed", (), {"name": "crm__notify_check", "tags": [Tag("effect", "read")], "parameters": [type("P", (), {"name": name})()]})()
        assert unreviewed_url_reads([addressed]) == {"crm__notify_check"}, name  # type: ignore[list-item]
    tagged = type("Tagged", (), {"name": "crm__fetch_page", "tags": [Tag("effect", "read"), Tag("sink", "external")], "parameters": [param]})()
    assert unreviewed_url_reads([tagged]) == set()  # type: ignore[list-item]
