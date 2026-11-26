"""Recommendation 5 (recs audit): once a session has read private data and untrusted content, an external action
waits for a person, on the native loop and on an engine run, for every untrusted source and every external sink.

The decision is QA-10's (``tests/unit/harness/policy/paths.py``): each path's production composition decides one
call against the session's journal as P2.5a's ledger reads it. QA-10's table holds one read per context; here the
session holds two, a private record and then one untrusted source, which is what the Rule of Two is about. The
product's rule is stricter than the Rule of Two (a private or an unknown read alone holds an external send), so the
pair must hold too; the controls show which half holds it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest

from tests.unit.harness.engines.test_source_ledger import PipesHubLedgerWorld
from tests.unit.harness.policy import paths as qa10
from tests.unit.harness.policy.paths import PATHS, RUN, Scenario, probe

if TYPE_CHECKING:
    from collections.abc import Callable

    from harness_core.journal.types import JournalEvent
    from harness_core.testing.contracts.source_ledger import Stream

pytestmark = pytest.mark.timeout(120)

# What a session can have read that nobody vouches for, as the ledger sees each one.
UNTRUSTED: Final[dict[str, Callable[[PipesHubLedgerWorld, Stream], None]]] = {
    # A web page (web_search / fetch_url): public content, untrusted.
    "web": lambda w, s: w.browsed(s, RUN, "inv-web"),
    # A live connector item (Slack message, Confluence page or Jira issue fetched live) or an MCP server's result:
    # opaque to the ledger.
    "live-connector-or-mcp": lambda w, s: w.unrecorded(s, RUN, "inv-live"),
    # An indexed Slack or Confluence record: the index holds it, so it is private and untrusted at once.
    "indexed-slack-or-confluence": lambda w, s: w.recorded(s, RUN, "inv-indexed", ["rec-slack-thread"]),
}

# Every external sink the product holds: connector writes (the replay world's toolsets as they declare their tags), a
# fetch whose request leaves the org, and an MCP server's tool with no hints or open-world hints. The product's own
# web tools are not among them: they always run (``tests/unit/harness/test_web_always_allowed.py``).
SINKS: Final = (
    "world:slack__send_message",
    "world:gmail__send_email",
    "world:jira__create_issue",
    "world:jira__add_comment",
    "world:drive__share_file",
    "world:web__fetch_url",
    "unclassified",
    "mcp:open",
)


def _events(reads: tuple[str, ...]) -> Callable[[Scenario, qa10.Probe], list[JournalEvent]]:
    def events(_scenario: Scenario, _subject: qa10.Probe) -> list[JournalEvent]:
        from harness_core.testing.contracts.source_ledger import Stream

        stream = Stream()
        world = PipesHubLedgerWorld()
        stream.admitted(RUN)
        for read in reads:
            if read == "private":
                world.recorded(stream, RUN, "inv-private", ["rec-q3-forecast"])
            else:
                UNTRUSTED[read](world, stream)
        return stream.events

    return events


async def _decide(path: str, sink: str, reads: tuple[str, ...], monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(qa10, "session_events", _events(reads))
    return await PATHS[path].decide(Scenario(tool=sink))  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize("sink", SINKS)
@pytest.mark.parametrize("untrusted", sorted(UNTRUSTED))
@pytest.mark.parametrize("path", ["durable", "engine"])
async def test_r5_private_and_untrusted_then_an_external_action_waits_for_a_person(
    path: str, untrusted: str, sink: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    verdict = await _decide(path, sink, ("private", untrusted), monkeypatch)
    assert verdict.startswith("ask/"), (path, untrusted, sink, verdict)


@pytest.mark.parametrize("sink", SINKS)
@pytest.mark.parametrize("untrusted", sorted(UNTRUSTED))
async def test_r5_the_legacy_loop_refuses_what_the_durable_paths_ask(
    untrusted: str, sink: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Today's loop has no ledger and no cards: after a read whose content nobody vouches for it refuses (UX3)."""
    scenario = Scenario(tool=sink, context="unknown" if untrusted != "web" else "private")
    verdict = await PATHS["legacy"].decide(scenario)
    assert verdict.startswith("deny/"), (untrusted, sink, verdict)


@pytest.mark.parametrize("path", ["durable", "engine"])
async def test_r5_controls_a_session_that_read_nothing_sends_without_the_flow_rule(
    path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ask above is the flow rule's: with nothing read, a fetch to an outside page is allowed on both paths."""
    assert await _decide(path, "world:web__fetch_url", (), monkeypatch) == "allow"
    assert await _decide(path, "world:web__fetch_url", ("private", "web"), monkeypatch) == "ask/flow_external_write"


@pytest.mark.parametrize("path", ["durable", "engine"])
async def test_r5_untrusted_alone_is_not_held_by_the_flow_rule(path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The Rule of Two needs both halves: a web page alone does not hold a fetch (FG2). The product is stricter on
    the other half: an unknown read alone holds it."""
    assert await _decide(path, "world:web__fetch_url", ("web",), monkeypatch) == "allow"
    held = await _decide(path, "world:web__fetch_url", ("live-connector-or-mcp",), monkeypatch)
    assert held == "ask/flow_external_write"


@pytest.mark.skip(
    reason="recs audit R5 gap: there is no git clone tool and no git push sink (engine sandboxes have egress none), so a "
    "cloned repository is neither a labelled source nor a gated sink; a repo reaches a run only as workspace files"
)
async def test_r5_a_cloned_repository_counts_as_untrusted_and_a_push_waits_for_a_person() -> None:
    raise AssertionError("unreachable")


def test_r5_the_probe_names_resolve() -> None:
    for sink in SINKS:
        assert probe(sink).effect is not None
