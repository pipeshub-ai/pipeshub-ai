"""Live check of the agent's web tools against pipeshub.com and docs.pipeshub.com.

Opt-in: runs only with ``PIPESHUB_LIVE_WEB=1`` and outbound internet, otherwise every test skips.
The tools are built exactly as an agent run gets them (``tests/unit/web/qa/lab.py``), with nothing
faked: real DNS, the real guarded download, real parsing and the free search engines. Assertions
are about shape, not copy, so editing either site does not break them.
"""

from __future__ import annotations

import asyncio
import os
import re
import time

import pytest

from app.web import container
from app.web.chain import Cooldowns
from tests.unit.web.qa.lab import Agent

pytestmark = [
    pytest.mark.integration,
    pytest.mark.timeout(120),
    pytest.mark.skipif(os.getenv("PIPESHUB_LIVE_WEB") != "1", reason="live, needs internet: set PIPESHUB_LIVE_WEB=1"),
]

SITE = "https://pipeshub.com/"
DOCS = "https://docs.pipeshub.com/"
OUR_HOSTS = ("pipeshub.com", "docs.pipeshub.com")
_ENGINE_REFUSALS = {"blocked", "rate_limited", "timeout"}


@pytest.fixture(autouse=True)
def fresh_cooldowns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(container, "COOLDOWNS", Cooldowns())


@pytest.fixture
def agent() -> Agent:
    return Agent({"provider": "duckduckgo", "configuration": {}, "providers": []})


def _field(view: dict | None, label: str) -> str:
    assert view, "the call had no step card"
    return next(f["value"] for f in view["fields"] if f["label"] == label)


def _assert_clean_page_text(text: str) -> None:
    assert text.count("<untrusted_web_content") == 1 and text.count("</untrusted_web_content>") == 1
    assert "https://ref" in text, "the model got no citation id"
    for leak in ("<!--", "<script", "bodyStart", "self.__next_f"):
        assert leak not in text, f"markup leaked into the page text: {leak}"


class TestReadingPages:
    async def test_the_home_page(self, agent: Agent) -> None:
        call = await agent.fetch(SITE)
        assert call.output.success, call.text
        assert "PipesHub" in _field(call.view, "Page")
        assert _field(call.view, "Site") == "pipeshub.com"
        _assert_clean_page_text(call.text)
        assert len(call.text) > 2000

    async def test_the_docs_root_follows_its_redirect(self, agent: Agent) -> None:
        call = await agent.fetch(DOCS)
        assert call.output.success, call.text
        assert call.view["url"].startswith(DOCS)
        assert "PipesHub" in _field(call.view, "Page")
        _assert_clean_page_text(call.text)

    async def test_a_docs_page(self, agent: Agent) -> None:
        call = await agent.fetch(f"{DOCS}quickstart")
        assert call.output.success, call.text
        assert "Quickstart" in _field(call.view, "Page")
        assert "Docker" in call.text
        _assert_clean_page_text(call.text)

    async def test_llms_txt_as_plain_text(self, agent: Agent) -> None:
        call = await agent.fetch(f"{DOCS}llms.txt")
        assert call.output.success, call.text
        assert "# PipesHub" in call.text
        _assert_clean_page_text(call.text)

    async def test_pages_llms_txt_links_to(self, agent: Agent) -> None:
        index = await agent.fetch(f"{DOCS}llms.txt")
        links = [u for u in dict.fromkeys(re.findall(r"\((https://docs\.pipeshub\.com/[^)\s]+)\)", index.text))
                 if not u.endswith(".txt")][:3]
        assert links, "llms.txt lists no docs pages"
        for url in links:
            call = await agent.fetch(url)
            assert call.output.success, f"{url}: {call.text}"
            _assert_clean_page_text(call.text)

    async def test_a_missing_page_is_reported_quickly(self, agent: Agent) -> None:
        started = time.monotonic()
        call = await agent.fetch(f"{DOCS}no-such-page-for-pipeshub-live-test")
        assert call.payload["error_type"] == "upstream_error"
        assert "404" in call.payload["error"]
        assert call.payload["retryable"] is False
        assert time.monotonic() - started < 15


class TestSearching:
    async def test_the_free_engines_find_our_docs(self) -> None:
        # The free engines time out or call us a bot now and then; that is their limit, not a bug.
        refusals = []
        for attempt in range(3):
            call = await Agent({"provider": "duckduckgo", "configuration": {}, "providers": []}).search(
                "PipesHub documentation quickstart")
            if call.output.success and call.output.sources:
                break
            refusals.append(call.text if call.output.success else call.payload["error_type"])
            await asyncio.sleep(2 * (attempt + 1))
        else:
            if all(r in _ENGINE_REFUSALS for r in refusals):
                pytest.skip(f"the free search engines refused this machine: {refusals}")
            pytest.fail(f"searches failed: {refusals}")
        hosts = {re.sub(r"^www\.", "", u.split("/")[2]) for u in (s.url for s in call.output.sources)}
        assert hosts & set(OUR_HOSTS), f"no pipeshub result among {sorted(hosts)}"

    async def test_an_answer_cites_a_page_it_read(self, agent: Agent) -> None:
        await agent.fetch(f"{DOCS}quickstart")
        ref = agent.ref_for(f"{DOCS}quickstart")
        answer, citations = agent.cite(f"Run it with Docker Compose [quickstart]({ref}).")
        assert ref not in answer
        assert f"{DOCS}quickstart" in answer
        assert "Quickstart" in citations[0]["metadata"]["recordName"]
