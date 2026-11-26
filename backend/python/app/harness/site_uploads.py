"""The site card (WP-63, SEC-A-01): a website held after the run read private data asks the person once per run and
site, without holding the turn.

The flow rule holds an engine run's websites after a private read: a write is refused ``site_upload_held`` and any
other read than a registry's own files ``site_request_held`` (``harness_core.sandbox.egress.sites``), since a URL
carries data as a body does. The worker journals an approval on the existing card, inbox and link paths: tool
``pipeshub@egress/site_upload``, rule ``site_upload``, the host as the destination and the flow rule's sources as every
flow ask shows them. A read-write site is asked about as the turn starts; a read site the first time the proxy holds a
request to it (its ``gateway.refused``), so a list of docs sites raises no cards nobody needs. The turn goes on
meanwhile. The card offers "allow for this conversation", as a ``fetch_url`` card does: a grant for the site answers
the other runs of the conversation too. When the person allows it, the worker releases the hold for the sandbox the
run holds now at the egress proxy (``POST /v1/site-grants``), and each later turn's sites carry the site unheld from
the start. A denial keeps it held for the rest of the run, without asking again.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Protocol

from app.core.org_context import spawn_in_request
from harness_core.contracts import canonical_digest, canonical_sha256
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects.flow import (
    AskDestination,
    ConversationGrants,
    destination_key,
    explained_ask,
    sensitivity_of,
)
from harness_core.effects.types import ToolEffect, tool_id_for
from harness_core.interactions.journal import requested_event
from harness_core.interactions.ports import InteractionRequest
from harness_core.journal.types import NewEvent
from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.engines.durable.ports import EngineRunPresenter, ProgressStamp
    from harness_core.journal.types import JournalEvent
    from harness_core.security.labels import SourceLedger

SITE_UPLOAD: Final = "site_upload"
SITE_UPLOAD_TOOL_ID: Final = tool_id_for(SITE_UPLOAD, source="pipeshub", instance="egress")
SITE_UPLOAD_RULE: Final = "site_upload"
EXPLAINED: Final = "interaction.explained"
REFUSED: Final = "gateway.refused"
# The proxy's refusals of a held site (``harness_core.sandbox.egress.errors``): each asks about its host on demand.
HELD_REASONS: Final = frozenset({"site_request_held", "site_upload_held"})
_LOG = HarnessLog.named("app.harness.site_uploads")
POLL_S: Final = 3.0


def site_upload_interaction(run_id: str, host: str) -> str:
    """One card per run and site: the id is theirs, so a later turn finds the ask it already made."""
    return "ix.approve.s" + canonical_sha256({"runId": run_id, "host": host.lower(), "tool": SITE_UPLOAD_TOOL_ID})[:40]


def site_upload_effect() -> ToolEffect:
    """What the card is about: requests to an outside host that may carry what the run read, the sends the flow rule
    holds after a private read."""
    return ToolEffect(
        tool_id=SITE_UPLOAD_TOOL_ID, effect="write", idempotent=False,
        sink=SinkDescriptor(egress="external", args_leave_boundary=True),
    )


class GrantsSites(Protocol):
    async def grant_site(self, *, sandbox_id: str, host: str, run_id: str) -> None: ...


class HoldsSandbox(Protocol):
    def sandbox_id(self) -> str | None: ...


@dataclass
class SiteUploads:
    """One engine run's upload cards: which held sites it asked about, what the person decided, and the release of an
    allowed one at the egress proxy for the sandbox that holds the run now."""

    run_id: str
    append: Callable[[Sequence[NewEvent]], Awaitable[None]]
    run_events: Callable[[], Awaitable[Sequence[JournalEvent]]]
    stamp: Callable[[], ProgressStamp]
    presenter: EngineRunPresenter
    ledger: SourceLedger
    control: GrantsSites | None
    sandbox: HoldsSandbox
    # Whether Node takes the card (its claim names ``site_upload_asks``): otherwise the site stays held, unasked.
    journal_asks: bool = True
    log: HarnessLog = field(default_factory=lambda: _LOG)
    poll_s: float = POLL_S
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    _asked: dict[str, str] = field(default_factory=dict[str, str], init=False)
    _decided: dict[str, str] = field(default_factory=dict[str, str], init=False)
    _released: set[tuple[str, str]] = field(default_factory=set[tuple[str, str]], init=False)
    _watch: asyncio.Task[None] | None = field(default=None, init=False)
    # The run's sites are held (the flow rule): its refusals are watched, and each held host is asked about once.
    _held: bool = field(default=False, init=False)
    _refused: list[str] = field(default_factory=list[str], init=False)

    def released(self) -> frozenset[str]:
        """The hosts the person allowed for this run, on its own card or for the whole conversation."""
        return frozenset(h for h, d in self._decided.items() if d == "approved")

    def released_for(self, hosts: Sequence[str]) -> frozenset[str]:
        """``released`` and, of ``hosts``, those the person allowed for the whole conversation in another run: a turn
        binds them unheld from the start, so their first request is not held only to be released."""
        return self.released() | frozenset(h for h in hosts if self._granted(h))

    def _granted(self, host: str) -> bool:
        """Whether the person allowed ``host`` for the conversation in another run (FLOWGRANT), for data as
        sensitive as this run's."""
        grants = getattr(self.ledger, "grants", None)
        if not isinstance(grants, ConversationGrants):
            return False
        sensitivity = sensitivity_of(self.ledger.source_records(self.run_id))
        return grants.allows(SITE_UPLOAD_TOOL_ID, destination_key(SITE_UPLOAD_TOOL_ID, host), sensitivity)

    def denied(self) -> frozenset[str]:
        return frozenset(h for h, d in self._decided.items() if d != "approved")

    async def refresh(self) -> None:
        """Reads what the run's journal says of its cards: asked, and decided."""
        events = await self.run_events()
        by_id: dict[str, str] = {}
        self._refused = [
            host for event in events
            if event.type == REFUSED and event.data.get("reason") in HELD_REASONS
            and isinstance(host := event.data.get("host"), str) and host
        ]
        for event in events:
            ix = event.data.get("interactionId")
            if not isinstance(ix, str) or not ix.startswith("ix.approve.s"):
                continue
            if event.type == EXPLAINED and event.data.get("toolId") == SITE_UPLOAD_TOOL_ID:
                destination = event.data.get("destination")
                label = destination.get("label") if isinstance(destination, Mapping) else None
                if isinstance(label, str) and ix == site_upload_interaction(self.run_id, label):
                    by_id[ix] = label
                    self._asked.setdefault(label, ix)
            elif event.type == "interaction.resolved" and ix in by_id:
                decision = event.data.get("decision")
                self._decided[by_id[ix]] = decision if isinstance(decision, str) else "closed"

    async def ask(self, hosts: Sequence[str]) -> int:
        """Journals a card for each held host not asked yet; how many. The turn does not wait on any."""
        if not self.journal_asks:
            return 0
        events: list[NewEvent] = []
        granted = False
        for host in hosts:
            if host in self._asked or host in self._decided:
                continue
            if self._granted(host):
                # Allowed for the conversation already: released now, never asked again.
                self._decided[host] = "approved"
                granted = True
                continue
            ix = site_upload_interaction(self.run_id, host)
            arguments: dict[str, JsonValue] = {"host": host}
            reason = explained_ask(
                SITE_UPLOAD_RULE, site_upload_effect(), self.ledger, self.run_id,
                AskDestination(host, destination_key(SITE_UPLOAD_TOOL_ID, host)), grantable=True,
            )
            events += self.presenter.approval(self.stamp(), ix, SITE_UPLOAD, arguments)
            events.append(NewEvent(EXPLAINED, {"interactionId": ix, **reason}, critical=False))
            events.append(requested_event(
                InteractionRequest(ix, "approval", SITE_UPLOAD_TOOL_ID, canonical_digest(arguments))
            ))
            self._asked[host] = ix
        if granted:
            _ = await self.release()
        if not events:
            return 0
        await self.append(events)
        asked = sum(1 for e in events if e.type == "interaction.requested")
        self.log.info("websites.upload_asked", sites=asked)
        self._watching()
        return asked

    def _watching(self) -> None:
        if self._watch is None or self._watch.done():
            self._watch = spawn_in_request(self._run_watch())

    def watch_held(self) -> None:
        """The run's sites are held now: each host the proxy holds a request to is asked about once."""
        if self.journal_asks:
            self._held = True
            self._watching()

    def pending(self) -> bool:
        return any(host not in self._decided for host in self._asked)

    async def ask_refused(self) -> int:
        """Asks about each held host the proxy refused a request to and nobody was asked about yet."""
        return await self.ask(list(dict.fromkeys(self._refused)))

    async def release(self) -> int:
        """Releases each allowed host for the sandbox that holds the run now (once per sandbox); how many."""
        sandbox_id = self.sandbox.sandbox_id()
        if sandbox_id is None or self.control is None:
            return 0
        done = 0
        for host in sorted(self.released()):
            if (sandbox_id, host) in self._released:
                continue
            try:
                await self.control.grant_site(sandbox_id=sandbox_id, host=host, run_id=self.run_id)
            except Exception as exc:  # the next turn's sandbox carries the site read-write anyway
                self.log.warning("websites.upload_release_failed", errorType=type(exc).__name__)
                continue
            self._released.add((sandbox_id, host))
            done += 1
        if done:
            self.log.info("websites.upload_released", sites=done)
        return done

    async def _run_watch(self) -> None:
        while self._held or self.pending():
            await self.sleep(self.poll_s)
            try:
                await self.refresh()
                _ = await self.ask_refused()
                _ = await self.release()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # a read that failed is read again at the next poll
                self.log.warning("websites.upload_watch_failed", errorType=type(exc).__name__)

    async def close(self) -> None:
        if self._watch is not None:
            self._watch.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._watch


__all__ = [
    "SITE_UPLOAD",
    "SITE_UPLOAD_RULE",
    "SITE_UPLOAD_TOOL_ID",
    "GrantsSites",
    "HoldsSandbox",
    "SiteUploads",
    "site_upload_effect",
    "site_upload_interaction",
]
