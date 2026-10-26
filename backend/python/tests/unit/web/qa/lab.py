"""WebLab: the whole web platform end to end, offline.

A scenario sets up the outside world, then talks to the platform the way the agent loop does:

    lab.engines.serper(results=[...])            # a search API answers
    lab.site.html("http://docs.test/a", "Docs")  # a website serves a page
    agent = lab.agent(provider="serper", configuration={"apiKey": "k"})
    out = await agent.search("query")             # the real web_search tool, through WebToolAdapter
    page = await agent.fetch("http://docs.test/a")
    answer, citations = agent.cite("Answer [source](https://ref1.xyz).")

What is real: the tool loader, the tools, the services, the fallback chain, every provider and
fetcher, the guarded download (over HTTP on a Unix socket), the parsers, the handlers that write
what the model reads, the step-card views and the citation pass. What is fake: the search APIs and
self-hosted servers (an httpx router by host), the ddgs library and DuckDuckGo's results page
(scripted engines), and the headless browser (the web connector's fake).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

import httpx

from app.agent_loop_lib.core.types import ToolResult
from app.agents.agent_loop.tool_loader import _build_dynamic_tools
from app.utils.citations import normalize_citations_and_chunks_for_agent
from app.web.fetch.http import FetchedBody
from tests.unit.agents.adapter.conftest import make_context

if TYPE_CHECKING:
    from web_behaviour_fakes import FakeWeb

    from app.agent_loop_lib.tools.base import ToolOutput

Answer = Callable[[httpx.Request], httpx.Response]


def results(*items: tuple[str, str] | tuple[str, str, str]) -> list[dict[str, str]]:
    """``(url, title[, snippet])`` tuples as the neutral result shape the fake engines speak."""
    return [{"url": i[0], "title": i[1], "snippet": i[2] if len(i) > 2 else f"About {i[1]}"} for i in items]


@dataclass
class Engines:
    """Every search engine and remote server the platform can call, scripted per test."""

    answers: dict[str, Answer] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)
    ddgs: Callable[[str], list[dict[str, Any]]] | Exception = field(default_factory=lambda: lambda q: [])
    ddgs_queries: list[str] = field(default_factory=list)
    duckduckgo: Callable[[str], FetchedBody] | Exception | None = None
    duckduckgo_queries: list[str] = field(default_factory=list)

    # -- the httpx side: APIs and self-hosted servers ---------------------------------------

    def route(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.answers.get(request.url.host)
        if answer is None:
            raise httpx.ConnectError(f"no fake server for {request.url.host}", request=request)
        return answer(request)

    def requests_to(self, host: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host == host]

    def client(self, *args: object, **kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.route))

    @staticmethod
    def json(status: int, body: object = None, headers: dict[str, str] | None = None) -> Answer:
        return lambda request: httpx.Response(status, json=body if body is not None else {}, headers=headers)

    def serper(self, items: list[dict[str, str]] | None = None, *, status: int = 200, headers: dict[str, str] | None = None) -> None:
        body = {"organic": [{"link": i["url"], "title": i["title"], "snippet": i["snippet"]} for i in items or []]}
        self.answers["google.serper.dev"] = self.json(status, body, headers)

    def tavily(self, items: list[dict[str, str]] | None = None, *, status: int = 200) -> None:
        body = {"results": [{"url": i["url"], "title": i["title"], "content": i["snippet"]} for i in items or []]}
        self.answers["api.tavily.com"] = self.json(status, body)

    def brave(self, items: list[dict[str, str]] | None = None, *, status: int = 200) -> None:
        body = {"web": {"results": [{"url": i["url"], "title": i["title"], "description": i["snippet"]} for i in items or []]}}
        self.answers["api.search.brave.com"] = self.json(status, body)

    def searxng(self, host: str, items: list[dict[str, str]] | None = None, *, status: int = 200) -> None:
        body = {"results": [{"url": i["url"], "title": i["title"], "content": i["snippet"]} for i in items or []]}
        self.answers[host] = self.json(status, body)

    def crawl4ai(self, host: str, pages: dict[str, str], *, version: str = "0.9.4") -> None:
        """``pages``: URL -> markdown the server returns for it."""

        def answer(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/health":
                return httpx.Response(200, json={"status": "ok", "version": version})
            url = json.loads(request.content)["urls"][0]
            if url not in pages:
                result = {"url": url, "success": False, "error_message": "navigation failed"}
            else:
                result = {"url": url, "success": True, "status_code": 200, "markdown": {"raw_markdown": pages[url]}}
            return httpx.Response(200, json={"success": True, "results": [result]})

        self.answers[host] = answer

    def firecrawl(self, host: str = "api.firecrawl.dev", *, search: list[dict[str, str]] | None = None,
                  pages: dict[str, str] | None = None) -> None:
        def answer(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            if request.url.path == "/v2/search":
                web = [{"url": i["url"], "title": i["title"], "description": i["snippet"]} for i in search or []]
                return httpx.Response(200, json={"success": True, "data": {"web": web}})
            markdown = (pages or {}).get(body["url"])
            if markdown is None:
                return httpx.Response(200, json={"success": True, "data": {"markdown": "", "metadata": {"statusCode": 404}}})
            return httpx.Response(200, json={"success": True, "data": {
                "markdown": markdown, "metadata": {"title": "", "sourceURL": body["url"], "statusCode": 200},
            }})

        self.answers[host] = answer

    # -- the free engines -------------------------------------------------------------------

    def ddgs_text(self, query: str, timeout_s: int) -> list[dict[str, Any]]:
        self.ddgs_queries.append(query)
        if isinstance(self.ddgs, Exception):
            raise self.ddgs
        return self.ddgs(query)

    def set_ddgs(self, items: list[dict[str, str]] | Exception) -> None:
        self.ddgs = items if isinstance(items, Exception) else (lambda q: [{"href": i["url"], "title": i["title"], "body": i["snippet"]} for i in items])

    async def duckduckgo_page(self, url: str, deadline: object, **_: object) -> FetchedBody:
        self.duckduckgo_queries.append(parse_qs(urlsplit(url).query).get("q", [""])[0])
        if isinstance(self.duckduckgo, Exception):
            raise self.duckduckgo
        if self.duckduckgo is None:
            return FetchedBody(url, 200, "text/html", b'<html><body><div class="no-results">No results.</div></body></html>')
        return self.duckduckgo(url)

    def set_duckduckgo(self, items: list[dict[str, str]] | None = None, *, status: int = 200, html: str | None = None) -> None:
        if html is None:
            rows = "".join(
                f'<div class="web-result"><a class="result__a" href="{i["url"]}">{i["title"]}</a>'
                f'<span class="result__snippet">{i["snippet"]}</span></div>'
                for i in items or []
            )
            html = f"<html><body>{rows or '<div class=no-results>No results.</div>'}</body></html>"
        self.duckduckgo = lambda url: FetchedBody(url, status, "text/html", html.encode())


@dataclass
class Call:
    """One tool call as the agent loop sees it."""

    output: ToolOutput
    view: dict[str, Any] | None

    @property
    def text(self) -> str:
        return str(self.output.data or self.output.error or "")

    @property
    def payload(self) -> dict[str, Any]:
        """A failed call's JSON: ``{ok: false, error_type, error, retryable[, attempts]}``."""
        assert not self.output.success, "the call succeeded"
        return json.loads(self.output.error)


class Agent:
    """The web tools exactly as one agent run gets them from the tool loader."""

    def __init__(self, web_search_config: dict[str, Any] | None, *, deep_research: bool = False) -> None:
        state: dict[str, Any] = {"web_search_config": web_search_config, "deep_research": deep_research}
        self.context = make_context(tool_state=state, web_search_config=web_search_config, deep_research=deep_research)
        self.tools = {tool.name.split("__", 1)[-1]: tool for tool in _build_dynamic_tools(self.context)}

    @property
    def state(self) -> dict[str, Any]:
        return self.context.tool_state

    async def _call(self, name: str, **kwargs: str) -> Call:
        tool = self.tools[name]
        output = await tool.execute(**kwargs)
        result = ToolResult(tool_call_id="call", name=tool.name, content=output.data, sources=output.sources)
        return Call(output, tool.result_view(kwargs, result))

    async def search(self, query: str) -> Call:
        return await self._call("web_search", query=query)

    async def fetch(self, url: str) -> Call:
        return await self._call("fetch_url", url=url)

    def ref_for(self, url_prefix: str) -> str:
        """The ``https://refN.xyz`` alias the model was shown for a page."""
        mapper = self.state["citation_ref_mapper"]
        ref = next(ref for ref, url in mapper.ref_to_url.items() if url.startswith(url_prefix))
        return f"https://{ref}.xyz"

    def cite(self, answer: str) -> tuple[str, list[dict[str, Any]]]:
        mapper = self.state["citation_ref_mapper"]
        return normalize_citations_and_chunks_for_agent(
            answer, [], virtual_record_id_to_result={}, records=[],
            ref_to_url=mapper.ref_to_url, web_records=self.state.get("web_records", []),
        )


class WebLab:
    def __init__(self, site: FakeWeb, engines: Engines) -> None:
        self.site = site
        self.engines = engines

    def agent(self, provider: str = "duckduckgo", configuration: dict[str, Any] | None = None, *,
              others: list[dict[str, Any]] | None = None, deep_research: bool = False) -> Agent:
        config = {"provider": provider, "configuration": configuration or {}, "providers": others or []}
        return Agent(config, deep_research=deep_research)
