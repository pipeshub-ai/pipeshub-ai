"""WP-83 (WP83-FLOW-1..7): a public repository the person let the engine clone is public content.

A research run's child: 23 approval cards for public web reads (22 ``fetch_url``, one ``web_search``), each
``flow_external_write`` with ``sensitivity=unknown``; runs 4 and 5 had none. With git in the worker image the two
clones succeeded for the first time, and a successful ``repository_fetch`` outcome counted as content nobody can name:
the run's context turned unknown at the first clone and the flow rule held every send after it, one card per host.
A repository fetched with no credential from a public address is what anyone may read, as a web page is: its outcome
now says so in the core item labels (``labels.confidentiality``: public readers, no sources), and the ledgers read that
as public. A mirror the deployment opened (``EGRESS_PRIVATE_ALLOW``) is not, an older outcome without the claim is
still unknown, and a private read after a public clone still holds a web read.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

import pytest

from app.harness.labels import SessionSourceLedger, own_work_label, public_result
from app.harness.repositories import TOOL_ID as REPOSITORY_ID
from harness_core.contracts import validate_contract
from harness_core.contracts.generated.events.tool_outcome import ToolOutcomeData
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects.flow import FlowGuardedPolicy, FlowRule, sensitivity_of
from harness_core.effects.journal import JournalEffectLog
from harness_core.effects.types import ToolEffect, ToolExecution
from harness_core.engines.durable.ledger import ItemLabelLedger
from harness_core.journal.memory import InMemoryContentStore
from harness_core.kernel.core.messages import ToolCall
from harness_core.provenance.types import ResultProvenance
from harness_core.repos.cache import RepositoryCache
from harness_core.repos.fetch import MIB, FetchConfig, RepositoryFetcher
from harness_core.sandbox.egress.upstream_proxy import parse_private_allow
from harness_core.testing.contracts.source_ledger import Stream
from tests.unit.harness.engines.test_source_ledger import (
    DIGEST,
    SEARCH_ID,
    TOOL_NAMES,
    WEB_ID,
)
from tests.unit.harness.test_gitf_repository_fetch import (  # noqa: F401 - the fixtures the tool tests share
    CTX,
    HOST,
    LOCATION,
    _call,
    _LoopbackFetcher,
    _Resolver,
    _Shelf,
    _tools,
    https_host,
    roomy_disk,
)

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic import JsonValue

    from tests.support.git_host import GitHttpHost

ENGINE = "run-engine"
NAMES = {**TOOL_NAMES, REPOSITORY_ID: "repository_fetch"}
PUBLIC_LABELS: dict[str, JsonValue] = {"integrity": "external", "confidentiality": {"readers": "public", "sources": []}}


def _call_journaled(stream: Stream, invocation: str, tool_id: str, labels: dict[str, JsonValue], *, egress: str = "external") -> None:
    _ = stream.add(
        "tool.intent",
        {
            "invocationId": invocation,
            "toolId": tool_id,
            "argsDigest": DIGEST,
            "effect": "read",
            "sink": cast("JsonValue", {"egress": egress, "argsLeaveBoundary": egress == "external"}),
        },
        run_id=ENGINE,
    )
    _ = stream.add("tool.outcome", {"invocationId": invocation, "status": "ok", "labels": labels}, run_id=ENGINE)


def _clone(stream: Stream, invocation: str, labels: dict[str, JsonValue]) -> None:
    _call_journaled(stream, invocation, REPOSITORY_ID, labels)


def _run7(labels: dict[str, JsonValue]) -> Stream:
    """Run 7's order: a web search, the two clones, then the web reads."""
    stream = Stream()
    stream.admitted(ENGINE)
    _call_journaled(stream, "web-0", WEB_ID, {"integrity": "external"})
    _clone(stream, "clone-1", labels)
    _clone(stream, "clone-2", labels)
    return stream


def _fetch_url_decision(stream: Stream) -> str:
    ledger = SessionSourceLedger(tool_names=NAMES)
    ledger.apply(stream.events)
    policy = FlowGuardedPolicy(lambda call, effect: "allow", FlowRule("ask"), ledger, ENGINE)
    effect = ToolEffect(WEB_ID, "read", True, SinkDescriptor(egress="external", args_leave_boundary=True))
    return policy(ToolCall(id="c1", name="dynamic__fetch_url", arguments={"url": "https://onyx.app/"}), effect)


def test_wp83_flow_1_public_clones_leave_the_context_public_and_web_reads_run() -> None:
    stream = _run7(PUBLIC_LABELS)
    label = own_work_label(stream.events, ENGINE, tool_names=NAMES)
    assert (label.complete, label.sources) == (True, frozenset())
    assert _fetch_url_decision(stream) == "allow"


def test_wp83_flow_2_a_clone_without_the_claim_is_still_unknown_and_web_reads_ask() -> None:
    """Run 7 as it was journaled, an older worker's outcome, and a mirror the deployment opened: nobody can name it."""
    stream = _run7({"integrity": "internal"})
    assert own_work_label(stream.events, ENGINE, tool_names=NAMES).complete is False
    assert _fetch_url_decision(stream) == "ask"


@pytest.mark.parametrize(
    "confidentiality",
    [
        {"readers": "public", "sources": ["rec-1"]},
        {"readers": "org", "sources": []},
        {"readers": ["p_1"]},
        "public",
    ],
)
def test_wp83_flow_3_only_public_readers_with_no_sources_is_public(confidentiality: JsonValue) -> None:
    data: dict[str, JsonValue] = {"labels": {"integrity": "external", "confidentiality": confidentiality}}
    assert public_result(data) is False
    stream = _run7({"integrity": "external", "confidentiality": confidentiality})
    assert own_work_label(stream.events, ENGINE, tool_names=NAMES).complete is False


def test_wp83_flow_4_a_private_read_after_a_public_clone_still_holds_a_web_read() -> None:
    """The exfiltration guard is unchanged: a fetch's URL can carry what the run read, so after a record it asks."""
    stream = _run7(PUBLIC_LABELS)
    _call_journaled(stream, "search-1", SEARCH_ID, {"integrity": "internal"}, egress="none")
    _ = stream.add(
        "x.pipeshub.citations.recorded", {"callId": "search-1", "records": {"vr-1": {"id": "rec-1"}}}, run_id=ENGINE
    )
    ledger = SessionSourceLedger(tool_names=NAMES)
    ledger.apply(stream.events)
    assert sensitivity_of(ledger.source_records(ENGINE)) == "private"
    assert _fetch_url_decision(stream) == "ask"


async def test_wp83_flow_5_the_outcome_journals_the_claim_in_the_core_labels_which_the_core_ledger_reads() -> None:
    log = JournalEffectLog(_no_commit, InMemoryContentStore(), _no_state)
    public = await log.outcome_event("i1", ToolExecution("ok", "{}", integrity="external", provenance=ResultProvenance("public", "external", ())))
    plain = await log.outcome_event("i2", ToolExecution("ok", "{}"))
    assert public.data["labels"] == PUBLIC_LABELS
    assert plain.data["labels"] == {"integrity": "internal"}
    _ = validate_contract(ToolOutcomeData, public.data)
    assert public_result(public.data) and not public_result(plain.data)
    stream = Stream()
    _ = stream.add("tool.outcome", public.data, run_id=ENGINE)
    core = ItemLabelLedger()
    core.apply(stream.events)
    assert core.source_records(ENGINE) == frozenset() and core.untrusted_read(ENGINE) is True


async def _no_commit(writer: str, events: list[Any]) -> None:
    del writer, events


def _no_state() -> Any:  # noqa: ANN401
    raise AssertionError("an outcome event reads no run state")


async def test_wp83_flow_6_only_a_public_address_makes_a_fetch_public_and_the_cache_keeps_it(tmp_path: Path) -> None:
    public = RepositoryFetcher(FetchConfig(cache_dir=tmp_path / "a"), _Resolver({HOST: ("140.82.112.3",)}))
    assert public.address_public(await public.checked_address(HOST)) is True
    mirror = RepositoryFetcher(
        FetchConfig(cache_dir=tmp_path / "b"), _Resolver({HOST: ("10.20.0.7",)}), private_allow=parse_private_allow(f"{HOST}:443")
    )
    assert mirror.address_public(await mirror.checked_address(HOST)) is False
    cache = RepositoryCache(tmp_path / "c", max_bytes=64 * MIB, max_age_s=3600)
    for key, flag in (("k-public", True), ("k-mirror", False)):
        staged = cache.temporary()
        (staged / "repo.tar").write_bytes(b"x")
        assert cache.put(key, staged, head="a" * 40, ref=None, label="l", public=flag).public is flag
        entry = cache.get(key)
        assert entry is not None and entry.public is flag
    # An entry from before WP-83 has no word on it: public only where the deployment opens no private address.
    meta = cache.root / "k-public" / "meta.json"
    old = json.loads(meta.read_text())
    del old["publicAddress"]
    meta.write_text(json.dumps(old))
    entry = cache.get("k-public")
    assert entry is not None and entry.public is None


class _PublicLoopback(_LoopbackFetcher):
    """The real fetch from the local test host, with its loopback address standing in for a public one."""

    def address_public(self, address: str) -> bool:
        return address == "127.0.0.1"


async def test_wp83_flow_7_the_tool_journals_an_outsider_written_public_result(
    https_host: tuple[GitHttpHost, Path, str, int],  # noqa: F811 - the shared fixture
    tmp_path: Path,
) -> None:
    _, _, ca, port = https_host
    config = FetchConfig(cache_dir=tmp_path / "cache", git_bin="git", port=port, ca_file=ca)
    call = _call(f"https://{HOST}/acme/widgets")
    # WP83-FU-3: the admin said anyone can read this hand-added host's public repositories.
    readable = [{"host": HOST, "publicReadable": True}]
    done = await _tools(_PublicLoopback(config, _Resolver({})), _Shelf(), hosts=readable).execute(call, CTX)
    assert (done.status, done.integrity, done.public) == ("ok", "external", True), done.content
    again = await _tools(_PublicLoopback(config, _Resolver({})), _Shelf(), hosts=readable).execute(call, CTX)
    assert json.loads(str(again.content))["cached"] is True and again.public is True
    # The loopback test host as itself: not a public address, so the result names nothing and stays unknown.
    other = FetchConfig(cache_dir=tmp_path / "other", git_bin="git", port=port, ca_file=ca)
    inside = await _tools(_LoopbackFetcher(other, _Resolver({})), _Shelf()).execute(call, CTX)
    assert (inside.status, inside.public) == ("ok", False)
    assert LOCATION.host == HOST
