"""WP-81: a code_task child hands its parent the sources its answer cites, however much it read (WP81-CITE).

In a manual research run, the Codex child had read 1,100+ web blocks (about 6.8 MB of page text). Its result
document's sources were the run's whole citation state bounded to the journal's 4 MiB delta first (the newest blocks
dropped), then narrowed to the refs its answer cited. The answer cited 32 refs; 21 of their pages were among the
dropped newest blocks, so the parent adopted 32 refs and only 11 web records, and its finalizer stripped the other 21
links to bare text ("Onyx changelog PipesHub code-graph route ..."): 11 citations reached the person.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from app.agents.agent_loop.context import AgentContext
from app.harness.citations import CHILD_SOURCES_KEY, DELTA_MAX_BYTES
from app.harness.engines.child_result import EngineChildResult
from app.utils.chat_helpers import CitationRefMapper
from harness_core.contracts import canonical_json
from harness_core.journal.children import child_result_document

OLD_PAGES = 2_600
BLOCK = "Detail of an early page the answer does not cite. " * 40
CITED = [f"https://docs.onyx.app/changelog#:~:text=entry%20{n}" for n in range(3)]


def _context() -> tuple[AgentContext, list[str]]:
    context = AgentContext(
        org_id="org-1", user_id="u", user_email="u@example.com", user_info={"userId": "u", "orgId": "org-1"},
        org_info={"name": "T"}, logger=MagicMock(), llm=MagicMock(), retrieval_service=MagicMock(),
        graph_provider=MagicMock(), config_service=MagicMock(), blob_store=MagicMock(),
    )
    mapper = CitationRefMapper()
    records: list[dict[str, Any]] = [
        {"url": f"https://github.com/early/page-{i}#:~:text=x", "content": BLOCK, "title": "early", "source_type": "web"}
        for i in range(OLD_PAGES)
    ]
    # The pages the answer cites were read last, as run 5's code and docs pages were.
    records += [{"url": url, "content": f"Changelog entry {n}.", "title": "Changelog", "source_type": "web"} for n, url in enumerate(CITED)]
    read = [f"url: {r['url']}\nCitation ID: {mapper.get_or_create_ref(r['url'])}\ncontent: {r['content']}" for r in records]
    context.tool_state["citation_ref_mapper"] = mapper
    context.tool_state["web_records"] = records
    return context, read


def test_wp81_cite_1_the_childs_cited_pages_survive_a_citation_state_past_the_journal_bound() -> None:
    context, read = _context()
    assert len(canonical_json(context.tool_state["web_records"])) > DELTA_MAX_BYTES  # run 5's size, uncut
    refs = [context.tool_state["citation_ref_mapper"].get_or_create_ref(url) for url in CITED]
    output = "Onyx documents its upgrades " + " ".join(f"[changelog {n}]({ref})" for n, ref in enumerate(refs)) + "."
    document = EngineChildResult(context, lambda: read)(child_result_document(outcome="answered", output=output), [])
    sources: dict[str, Any] = document[CHILD_SOURCES_KEY]  # type: ignore[assignment]
    assert [pair["url"] for pair in sources["refs"]] == CITED
    assert [record["url"] for record in sources["webRecords"]] == CITED, "every cited ref keeps the page it names"
    assert len(canonical_json(sources)) <= DELTA_MAX_BYTES


def test_wp81_cite_2_what_the_child_hands_on_is_still_bounded() -> None:
    """The narrowed sources keep the journal's bound: an answer that cites every page it read hands on no more."""
    context, read = _context()
    mapper = context.tool_state["citation_ref_mapper"]
    output = " ".join(f"[p]({mapper.get_or_create_ref(r['url'])})" for r in context.tool_state["web_records"])
    document = EngineChildResult(context, lambda: read)(child_result_document(outcome="answered", output=output), [])
    assert len(canonical_json(document[CHILD_SOURCES_KEY])) <= DELTA_MAX_BYTES
