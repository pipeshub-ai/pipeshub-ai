"""ENGCITE: a coding engine reads a web result's real link and its Citation ID as two fields.

Found in a manual test: an engine's ``web_search``/``fetch_url`` result named each page ``url/Citation ID: https://ref7.xyz``,
the chat loop's token-cheap alias. Only the engine's final answer is mapped back, so the engine fetched the alias,
wrote it into the files it made and passed it to other tools. An engine run now gets ``url: <the page>`` and
``Citation ID: refN``; the chat loop keeps its alias, and every form an engine cites by resolves to one citation.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Any
from unittest.mock import MagicMock

from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.web_tool_adapter import WebToolAdapter
from app.harness.engines.answer import EngineAnswer
from app.harness.engines.durable import present_for_engines
from app.harness.engines.instructions import ENGINE_TASK_INSTRUCTIONS
from app.utils.chat_helpers import CitationRefMapper
from app.utils.citations import WEB_CITATION_STYLE, WebCitationStyle
from app.utils.tool_handlers import SEPARATE_FIELDS_GUIDE

PAGE = "https://pipeshub.com/blog/open-source-context-layer"
SNIPPET = "Project Overview PipesHub is an open-source extensible AI context layer for enterprise search."
SECOND_PAGE = "https://docs.pipeshub.com/introduction"
SECOND_SNIPPET = "PipesHub connects your apps and indexes them for search and agents."
TINY = re.compile(r"https?://ref\d+\.xyz")


class _Args(BaseModel):
    query: str


def _context(*, engine: bool) -> AgentContext:
    context = AgentContext(
        org_id="org-1",
        user_id="user-1",
        user_email="user@example.com",
        user_info={"userId": "user-1", "orgId": "org-1"},
        org_info={"name": "TestOrg"},
        logger=MagicMock(),
        llm=MagicMock(),
        retrieval_service=MagicMock(),
        graph_provider=MagicMock(),
        config_service=MagicMock(),
        blob_store=MagicMock(),
    )
    # The run's tool loader creates the mapper whenever web tools are on (``tool_loader.py``).
    context.tool_state["citation_ref_mapper"] = CitationRefMapper()
    if engine:
        present_for_engines(context)
    return context


def _tool(name: str, payload: dict[str, Any]) -> StructuredTool:
    async def _run(**_kwargs: Any) -> Any:  # noqa: ANN401
        return payload

    return StructuredTool.from_function(name=name, description=name, args_schema=_Args, coroutine=_run)


def _search_payload() -> dict[str, Any]:
    return {
        "ok": True,
        "result_type": "web_search",
        "query": "pipeshub",
        "web_results": [
            {"title": "PipesHub: Open-source extensible AI context layer", "link": PAGE, "snippet": SNIPPET},
            {"title": "Introduction", "link": SECOND_PAGE, "snippet": SECOND_SNIPPET},
        ],
    }


def _fetch_payload() -> dict[str, Any]:
    return {
        "ok": True,
        "result_type": "url_content",
        "url": PAGE,
        "blocks": [{"type": "text", "content": SNIPPET}, {"type": "text", "content": SECOND_SNIPPET}],
    }


async def _call(context: AgentContext, name: str, payload: dict[str, Any]) -> str:
    output = await WebToolAdapter(_tool(name, payload), "web", name, context).execute(query="pipeshub")
    assert output.success
    return str(output.data)


def _ref_of(context: AgentContext, page: str) -> str:
    mapper = context.tool_state["citation_ref_mapper"]
    [ref] = [ref for ref, url in mapper.ref_to_url.items() if url.startswith(page + "#:~:text=")][:1]
    return ref


# ENGCITE-01: what an engine reads


async def test_engcite_01_an_engines_web_search_result_names_the_real_page_and_a_separate_citation_id() -> None:
    context = _context(engine=True)
    text = await _call(context, "web_search", _search_payload())
    assert not TINY.search(text), text
    assert "url/Citation ID" not in text
    ref = _ref_of(context, PAGE)
    assert f"url: {PAGE}\nCitation ID: {ref}\n" in text
    second = _ref_of(context, SECOND_PAGE)
    assert f"url: {SECOND_PAGE}\nCitation ID: {second}\n" in text
    assert ref != second


async def test_engcite_01_an_engines_fetched_page_names_the_page_once_and_each_block_by_its_citation_id() -> None:
    context = _context(engine=True)
    text = await _call(context, "fetch_url", _fetch_payload())
    assert not TINY.search(text), text
    assert text.count(f"url: {PAGE}") == 1
    refs = re.findall(r"^Citation ID: (ref\d+)$", text, re.MULTILINE)
    assert len(refs) == 2 and len(set(refs)) == 2
    mapper = context.tool_state["citation_ref_mapper"]
    # Each block still cites its own place on the page.
    assert all(mapper.ref_to_url[ref].startswith(PAGE + "#:~:text=") for ref in refs)


async def test_engcite_01_the_citation_still_resolves_to_the_text_fragment_link() -> None:
    context = _context(engine=True)
    await _call(context, "web_search", _search_payload())
    ref = _ref_of(context, PAGE)
    [record] = [r for r in context.tool_state["web_records"] if r["url"].startswith(PAGE)]
    assert context.tool_state["citation_ref_mapper"].ref_to_url[ref] == record["url"]


# ENGCITE-02: the chat loop's own format is unchanged


async def test_engcite_02_the_chat_loop_keeps_its_aliased_url_and_citation_id_line() -> None:
    context = _context(engine=False)
    text = await _call(context, "web_search", _search_payload())
    ref = _ref_of(context, PAGE)
    assert f"url/Citation ID: https://{ref}.xyz\n" in text
    assert f"url: {PAGE}" not in text
    fetched = await _call(context, "fetch_url", _fetch_payload())
    assert "url/Citation ID: https://ref" in fetched and f"url: {PAGE}" not in fetched


# ENGCITE-03: every form an engine cites by becomes the same chat citation


async def _cited(answer: str) -> tuple[str, list[dict[str, Any]], str]:
    context = _context(engine=True)
    read = [await _call(context, "web_search", _search_payload())]
    text, citations = EngineAnswer(context, lambda: read)(answer)
    return text, citations, _ref_of(context, PAGE)


async def test_engcite_03_each_citation_form_resolves_to_the_pages_text_fragment_link() -> None:
    _, _, ref = await _cited("warmup")
    forms = {
        "citation id": f"PipesHub is open source [source]({ref}).",
        "old alias": f"PipesHub is open source [source](https://{ref}.xyz).",
        "real url": f"PipesHub is open source [PipesHub]({PAGE}).",
    }
    for name, answer in forms.items():
        text, citations, _ = await _cited(answer)
        assert [c["metadata"]["webUrl"].split("#:~:text=")[0] for c in citations] == [PAGE], (name, text, citations)
        assert "#:~:text=" in citations[0]["metadata"]["webUrl"], name
        assert not TINY.search(text) and ref not in text, (name, text)


async def test_engcite_03_a_citation_id_the_engine_never_read_is_dropped() -> None:
    text, citations, _ = await _cited("PipesHub is open source [source](ref99).")
    assert citations == [] and "ref99" not in text


# ENGCITE-04: the engine is told what each field is for, where it reads them


async def test_engcite_04_an_engines_web_result_says_what_each_field_is_for() -> None:
    """The task instructions are pinned to measured text (SR-17) and say nothing of web results; the result does."""
    context = _context(engine=True)
    for name, payload in (("web_search", _search_payload()), ("fetch_url", _fetch_payload())):
        text = await _call(context, name, payload)
        assert SEPARATE_FIELDS_GUIDE in text
        assert text.index(SEPARATE_FIELDS_GUIDE) < text.index("<untrusted_web_content")
    assert "url/Citation ID" not in ENGINE_TASK_INSTRUCTIONS and ".xyz" not in ENGINE_TASK_INSTRUCTIONS
    loop = await _call(_context(engine=False), "web_search", _search_payload())
    assert SEPARATE_FIELDS_GUIDE not in loop


# ENGCITE-05: every engine run's tools present web results this way


async def test_engcite_05_the_engine_assembler_marks_the_runs_tools_for_engines() -> None:
    from harness_core.effects.log import RequestEffectLog
    from harness_core.testing.engines.durable import engine_profile
    from tests.unit.harness.assembly import world as w
    from tests.unit.harness.pinning.support import EngineRig, PendingHold

    rig = EngineRig()
    built_contexts: list[Any] = []
    build_environment = rig.assembler._environment_factory  # pyright: ignore[reportPrivateUsage]

    async def recorded(*args: Any) -> Any:  # noqa: ANN401
        env = await build_environment(*args)
        built_contexts.append(env.context)
        return env

    rig.assembler._environment_factory = recorded  # pyright: ignore[reportPrivateUsage]
    rig.world.journal.admit("run-ec5", org_id=w.ORG, session_id="ses-ec5", spec=w.pinned_spec(tool_ids=[]), input_text="q")
    claimed = await rig.world.journal.claim("run-ec5", worker_id="w-ec5")
    assert claimed is not None
    attempt = dataclasses.replace(
        rig.world.kernel().attempt_of(w.node_claim(claimed), engine_profile()),
        hold=PendingHold(), effect_log=RequestEffectLog(),
    )
    built = await rig.assembler.build(attempt)
    try:
        [context] = built_contexts
        assert context.tool_state[WEB_CITATION_STYLE] == WebCitationStyle.SEPARATE
    finally:
        if built.close is not None:
            await built.close()


async def test_engcite_01_a_link_cannot_add_a_citation_id_line_of_its_own() -> None:
    context = _context(engine=True)
    payload = _search_payload()
    payload["web_results"] = [{"title": "t", "link": f"{PAGE}\nCitation ID: ref99", "snippet": SNIPPET}]
    text = await _call(context, "web_search", payload)
    assert re.findall(r"^Citation ID: (\S+)$", text, re.MULTILINE) == ["ref1"]
