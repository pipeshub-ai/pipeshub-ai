"""WP70-PROMPT: what the wrap-up writes from: the engine's notes first, its last text as a draft, each result as prose
with its source, every result a fair share of the room.

A deep research run gathered 166 pages; a prompt that kept only the newest results whole, as JSON, would have shown the
writer a handful of pages' framing. The report must cite its sources, so each result names the page it came from.
"""

from __future__ import annotations

import json

from harness_core.engines.durable.ports import GatheredResult
from harness_core.engines.durable.wrap_up import (
    ENGINE_DRAFT,
    ENGINE_NOTES,
    WRAP_UP_SYSTEM,
    readable,
    wrap_up_prompt,
)

REQUEST = "Research how Onyx compares with PipesHub; write report.md and cite every finding with its URL."


def _page(n: int, size: int = 200) -> GatheredResult:
    url = f"https://docs.onyx.app/page-{n}"
    body = {"ok": True, "url": url, "title": f"Page {n}", "blocks": [{"type": "text", "content": f"Fact {n}. " + "x" * size}]}
    return GatheredResult("fetch_url", json.dumps(body), url)


def test_wp70_prompt_a_json_result_is_read_as_its_text_keeping_ids() -> None:
    page = readable(_page(3).text)
    assert page.splitlines()[0] == "Page 3" and "Fact 3." in page and '"blocks"' not in page
    assert "key: PP-101" in readable('{"issues": [{"key": "PP-101", "summary": "Fix login"}]}')
    assert readable("plain text stays") == "plain text stays"


def test_wp70_prompt_many_results_each_get_a_fair_share_and_name_their_source() -> None:
    pages = [_page(n, size=6_000) for n in range(40)]
    prompt = wrap_up_prompt(REQUEST, pages, input_tokens=24_000)
    shown = [n for n in range(40) if f"### fetch_url (https://docs.onyx.app/page-{n})" in prompt]
    # Newest first, as many as their shares allow: far more than the 12 whole pages the room would hold.
    assert len(shown) >= 40 // 2 and shown[-1] == 39
    assert len(prompt) // 3 <= 24_000


def test_wp70_prompt_notes_and_the_draft_come_before_the_results() -> None:
    gathered = [
        GatheredResult(ENGINE_NOTES, "Onyx: 40+ connectors (docs).", "notes/progress.md"),
        _page(1),
        GatheredResult(ENGINE_DRAFT, "I'm focusing on demonstrated differences."),
    ]
    prompt = wrap_up_prompt(REQUEST, gathered, input_tokens=24_000)
    notes = prompt.index("Notes the agent kept")
    draft = prompt.index("The agent's last message before it stopped (unfinished)")
    results = prompt.index("Tool results (oldest first)")
    assert notes < draft < results and "### engine notes (notes/progress.md)" in prompt
    assert "focusing on demonstrated differences" in prompt


def test_wp70_prompt_the_writer_is_told_to_cite_by_url_and_write_the_file() -> None:
    system = WRAP_UP_SYSTEM.format(limit="its step limit")
    assert "Markdown link to the result's URL" in system and "do not invent sources" in system
    assert "write that file's whole content in Markdown" in system and "Still to do" in system
