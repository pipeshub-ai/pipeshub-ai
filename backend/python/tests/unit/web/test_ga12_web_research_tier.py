"""GA-12 (G-23, C20.24): the safe web-research tier. A run reads the public web only through PipesHub's own fetch and
search tools, which run in the control plane: GET (and the size-check HEAD) only, no cookie, credential or proxy
authorization ever sent, every redirect hop re-checked against the public-address rule, a response-size cap, and what
comes back marked untrusted to the model and to the session's ``untrusted_content_read`` taint. An engine's sandbox
reaches only the two gateways, so the web arrives through the tool gateway or not at all (G-24 keeps engines' native
web tools off).

The sites are ``FakeWeb``'s in-memory ones, served over real HTTP on a Unix socket; every host name resolves to a
public address (``SITE_ADDRESS``) except ``INTRANET_HOST``, so the real address policy runs unpatched."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from web_behaviour_fakes import (
    INTRANET_HOST,
    FakeRequestsClient,
    FakeScraper,
    FakeWeb,
    Page,
)

from app.connectors.sources.web import fetch_strategy
from app.harness.citations import EVENT as CITATIONS_EVENT
from app.harness.citations import CitationJournal
from app.harness.gateway.citations import JournalCitationSink
from app.harness.labels import SessionSourceLedger
from app.utils.chat_helpers import CitationRefMapper
from app.web.deadline import Deadline
from app.web.errors import InvalidUrl, TooLarge, UnsafeUrl
from app.web.fetch.http import MAX_PAGE_MB, fetch_public
from app.web.tools.fetch_url import FetchUrlArgs, checked_url
from harness_core.effects import EffectRunner, RequestEffectLog
from harness_core.engines.controller import EngineControllerConfig, engine_sandbox_spec
from harness_core.gateway.endpoint import RunToolEndpoint
from harness_core.invoker import InvocationScope
from harness_core.journal.types import JournalEvent, NewEvent
from harness_core.kernel.durable import classify_tool_effect
from harness_core.sandbox.types import MountSpec, Resources, RuntimeMode
from harness_core.testing.contracts.tool_endpoint import BINDING, claims_for
from harness_core.testing.ids import SeededIds
from harness_core.tools.durable_port import RegistryToolPort
from harness_core.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from harness_core.effects import ApprovalDecision, ToolEffect
    from harness_core.kernel.core.messages import ToolCall

SITE = "http://site.test"
OTHER = "http://other.test"
TEXT = "Readable paragraph text about the public subject. " * 40
FETCH = "dynamic__fetch_url"
SEARCH = "dynamic__web_search"
_CREDENTIAL_HEADERS = ("cookie", "authorization", "proxy-authorization")


def _deadline() -> Deadline:
    return Deadline.after(15)


def _sent_credentials(site: FakeWeb) -> list[tuple[str, str]]:
    return [(url, name) for url, headers in site.request_headers for name in _CREDENTIAL_HEADERS if name in headers]


# ---- the fetch itself: what leaves, where it may go, how much comes back ----


@pytest.mark.parametrize("landing", [f"{SITE}/landing", f"{OTHER}/landing"], ids=["same-site", "cross-site"])
async def test_ga12_a_cookie_set_on_a_redirect_is_never_sent_back(fake_web: FakeWeb, landing: str) -> None:
    """A page the model asks for is read as a stranger would read it: a cookie a hop sets is not replayed on the
    next hop, so no session a site hands out (or a tracking id) follows the run's fetches."""
    fake_web.add(
        f"{SITE}/start",
        Page(status=302, location=landing, content_type=None, headers={"Set-Cookie": "sid=abc; Path=/"},
             head_status=405),
    ).html(landing, "Landing", text=TEXT)
    body = await fetch_public(f"{SITE}/start", _deadline())
    assert body.url == landing and body.status == 200
    assert fake_web.gets(landing) == 1
    assert _sent_credentials(fake_web) == []


@pytest.mark.parametrize("strategy", ["curl_cffi", "cloudscraper"])
async def test_ga12_the_impersonating_clients_drop_a_hops_cookie_too(
    fake_web: FakeWeb, monkeypatch: pytest.MonkeyPatch, strategy: str
) -> None:
    """curl_cffi and cloudscraper keep their own cookie jar across a walk; for a model's page read it is emptied
    before every hop. (The crawl keeps it: a site's login redirect needs it there.)"""
    if strategy == "curl_cffi":
        import curl_cffi.requests

        monkeypatch.setattr(fetch_strategy, "_CURL_PROFILES", ["chrome"])
        monkeypatch.setattr(curl_cffi.requests, "Session", lambda **_: FakeRequestsClient(fake_web, "curl_cffi"))
    else:
        fake = SimpleNamespace(create_scraper=lambda **_: FakeScraper(fake_web, "cloudscraper"))
        monkeypatch.setitem(sys.modules, "cloudscraper", fake)
    fake_web.add(
        f"{SITE}/start",
        Page(status=302, location="/landing", content_type=None, headers={"Set-Cookie": "sid=abc; Path=/"},
             head_status=405),
    ).html(f"{SITE}/landing", "Landing", text=TEXT)
    body = await fetch_public(f"{SITE}/start", _deadline())
    assert body.status == 200
    assert {via for via, method, url in fake_web.served if method == "GET" and url == f"{SITE}/landing"} == {strategy}
    assert _sent_credentials(fake_web) == []


async def test_ga12_no_credential_or_proxy_authorization_header_is_ever_sent(fake_web: FakeWeb) -> None:
    fake_web.redirect(f"{SITE}/old", f"{SITE}/new").html(f"{SITE}/new", "New", text=TEXT)
    _ = await fetch_public(f"{SITE}/old", _deadline())
    assert fake_web.request_headers, "nothing was requested"
    assert _sent_credentials(fake_web) == []


@pytest.mark.parametrize(
    "url", ["https://user:secret@site.test/doc", "http://token@site.test/doc", "https://:pw@site.test/"]
)
def test_ga12_a_url_carrying_credentials_is_refused_before_any_request(url: str) -> None:
    """A URL's user-info becomes an ``Authorization`` header in every HTTP client the chain uses."""
    with pytest.raises(InvalidUrl):
        checked_url(url)


async def test_ga12_a_redirect_to_a_url_carrying_credentials_is_refused(fake_web: FakeWeb) -> None:
    fake_web.redirect(f"{SITE}/go", "http://admin:hunter22@site.test/landing").html(f"{SITE}/landing", "L", text=TEXT)
    with pytest.raises(UnsafeUrl):
        await fetch_public(f"{SITE}/go", _deadline())
    assert fake_web.gets(f"{SITE}/landing") == 0
    assert _sent_credentials(fake_web) == []


@pytest.mark.parametrize(
    "target",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://[::ffff:169.254.169.254]/latest/meta-data/",
        "http://168.63.129.16/machine",
        "http://10.0.0.5/admin",
        "http://127.0.0.1:3000/api/v1/users",
        f"http://{INTRANET_HOST}/secret",
        "http://localhost:8091/",
    ],
)
async def test_ga12_every_redirect_hop_is_rechecked_against_the_public_address_rule(
    fake_web: FakeWeb, target: str
) -> None:
    fake_web.redirect(f"{SITE}/go", target)
    with pytest.raises(UnsafeUrl):
        await fetch_public(f"{SITE}/go", _deadline())
    assert [url for _, url in fake_web.requests if not url.startswith(f"{SITE}/go")] == []


async def test_ga12_a_body_over_the_cap_is_refused_before_it_is_downloaded(fake_web: FakeWeb) -> None:
    """A declared length over the cap is refused at the size-check HEAD: the GET is never sent."""
    fake_web.add(f"{SITE}/big", Page(body=b"a" * (MAX_PAGE_MB * 1024 * 1024 + 1), content_type="text/plain"))
    with pytest.raises(TooLarge):
        await fetch_public(f"{SITE}/big", _deadline())
    assert fake_web.gets(f"{SITE}/big") == 0


async def test_ga12_an_undeclared_body_over_the_cap_is_refused(fake_web: FakeWeb) -> None:
    fake_web.add(
        f"{SITE}/stream", Page(body=b"a" * (MAX_PAGE_MB * 1024 * 1024 + 1), content_type="text/plain", chunked=True)
    )
    with pytest.raises(TooLarge):
        await fetch_public(f"{SITE}/stream", _deadline())


async def test_ga12_the_tool_only_reads(fake_web: FakeWeb) -> None:
    """The model names a URL and nothing else (no method, body or header), and only GET and HEAD leave."""
    assert set(FetchUrlArgs.model_fields) == {"url"}
    fake_web.redirect(f"{SITE}/a", f"{SITE}/b").html(f"{SITE}/b", "B", text=TEXT)
    _ = await fetch_public(f"{SITE}/a", _deadline())
    assert {method for _, method, _ in fake_web.served} <= {"GET", "HEAD"}


# ---- through the run's tool gateway: untrusted to the model and to the session ----


def _web_tools(state: dict[str, Any]) -> list[Any]:
    from app.agents.agent_loop.tool_loader import (
        _build_dynamic_tools,  # pyright: ignore[reportPrivateUsage]
    )

    context = SimpleNamespace(
        tool_state=state,
        org_id="org-1",
        user_id="user-1",
        conversation_id=None,
        deep_research=False,
        config_service=None,
        is_multimodal_llm=False,
    )
    return list(_build_dynamic_tools(context))  # type: ignore[arg-type]


class _Sink(JournalCitationSink):
    """The engine's citation sink over the run's state, as ``app/harness/engines/durable.py`` builds it."""

    def __init__(self, state: dict[str, Any]) -> None:
        journal = CitationJournal(SimpleNamespace(tool_state=state, include_retrieval_context=False))

        def citations_for(invocation_id: str) -> NewEvent | None:
            delta = journal.delta(invocation_id)
            return NewEvent(CITATIONS_EVENT, delta, critical=False) if delta is not None else None

        super().__init__(state, citations_for)


def _gateway(state: dict[str, Any]) -> tuple[RunToolEndpoint, RequestEffectLog]:
    registry = ToolRegistry()
    tools = _web_tools(state)
    for tool in tools:
        registry.register_tool(tool)
    effects = {t.name: classify_tool_effect(t.name, t.tags) for t in tools}
    log = RequestEffectLog()

    def allow(call: ToolCall, effect: ToolEffect) -> ApprovalDecision:
        return "allow"

    endpoint = RunToolEndpoint(
        binding=BINDING,
        # No integrity table: the web's own evidence (the call's web records) must make the result external.
        catalog=RegistryToolPort(registry, effects),
        effects=EffectRunner(log, allow),
        scope=InvocationScope(BINDING.run_id, BINDING.tenant, BINDING.attempt_id),
        ids=SeededIds(),
        citations=_Sink(state),
    )
    return endpoint, log


def _journal(log: RequestEffectLog, run_id: str) -> list[JournalEvent]:
    """What the attempt journals for each outcome: the outcome's integrity label and its citation delta."""
    events: list[JournalEvent] = []

    def add(type_: str, data: dict[str, Any]) -> None:
        events.append(
            JournalEvent(
                org_id="org-1", session_id="s-1", stream_id="s-1", seq=len(events) + 1, run_id=run_id, type=type_,
                critical=False, ts="2026-10-31T00:00:00Z", writer="worker", data=data,
            )
        )

    add("run.admitted", {})
    for execution in log.outcomes():
        for event in execution.events:
            add(event.type, dict(event.data))
        add("tool.outcome", {"invocationId": "i", "status": execution.status, "labels": {"integrity": execution.integrity}})
    return events


async def test_ga12_a_fetched_page_reaches_an_engine_marked_untrusted_and_taints_the_session(fake_web: FakeWeb) -> None:
    state: dict[str, Any] = {
        "web_search_config": {"provider": "duckduckgo", "configuration": {}},
        "citation_ref_mapper": CitationRefMapper(),
    }
    fake_web.html(f"{SITE}/doc", "Public doc", text=TEXT)
    endpoint, log = _gateway(state)

    result = await endpoint.call_tool(claims_for(BINDING), FETCH, {"url": f"{SITE}/doc"})

    assert result.disposition == "executed", result.text
    assert "Readable paragraph text" in result.text
    assert "<untrusted_web_content" in result.text and "</untrusted_web_content>" in result.text
    assert result.text.startswith("[Untrusted content: some of these results come from the web")
    assert result.structured()["provenance"] == {"integrity": "external", "untrustedSources": ["the web"]}
    assert [o.integrity for o in log.outcomes()] == ["external"]
    ledger = SessionSourceLedger()
    ledger.apply(_journal(log, BINDING.run_id))
    assert ledger.untrusted_read(BINDING.run_id) is True


async def test_ga12_a_failed_fetch_returns_no_page_content(fake_web: FakeWeb) -> None:
    state: dict[str, Any] = {"web_search_config": {"provider": "duckduckgo", "configuration": {}}}
    fake_web.redirect(f"{SITE}/go", "http://169.254.169.254/latest/meta-data/")
    endpoint, _ = _gateway(state)
    result = await endpoint.call_tool(claims_for(BINDING), FETCH, {"url": f"{SITE}/go"})
    payload = json.loads(result.text) if result.text.startswith("{") else {}
    assert payload.get("ok") is False and payload.get("error_type") == "unsafe_url"
    assert fake_web.gets("http://169.254.169.254/latest/meta-data/") == 0


def test_ga12_an_engine_sandbox_reaches_only_the_gateways() -> None:
    """Sandbox egress stays denied for the web: the engine's allowlist names the model and tool gateways and nothing
    else, so a page reaches it only as a tool result from the control plane (above)."""
    config = EngineControllerConfig(images={}, resources=Resources(cpus=1.0, memory_mb=1024, pids=256, scratch_mb=256))
    spec = engine_sandbox_spec(
        config,
        image="pxh-opencode-tools:1.18.34",
        mode=RuntimeMode.DOCKER_HARDENED,
        workspace=MountSpec(config.workspace_root),
        labels={"runId": "run-1", "attemptId": "a1"},
        placeholders=("pxh-model-a1-0123456789", "pxh-tools-a1-0123456789"),
        tools_upstream="http://worker-a1:8098/x",
    )
    assert {d.host for d in spec.egress.destinations} == {"model-gateway", "tool-gateway"}
    assert spec.egress.needs_proxy
