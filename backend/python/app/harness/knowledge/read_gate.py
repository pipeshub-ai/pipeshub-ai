"""Whether a reader may read a record now (H1): the check retrieval applies, made again at a raw read.

A request's record map (``virtual_record_id_to_result``) is filled by more than retrieval: a fetch by a
model-named id, the Slack thread tool, history attachments, a child run's records and the journal's citation
deltas restored on resume. An entry may also outlive a revocation, an expired publication or a removed share.
So a raw read of any record decides access when it happens, the way retrieval admits it (P2.13
``effective_scope``):

- publications off: the retrieval principal's own access;
- a principal reading as itself: its own access, else a live publication that discloses the record to it;
- a service-account agent (retrieving as its creator on a caller's behalf): the creator's own access, and then
  the caller's own access or a live publication to the caller. The creator's clearance never opens a record the
  caller could not see.

Many ids are decided together (H2): one call of main's access check (``check_access``, #4255) per principal, and
the principal's publications read once, whatever the number of ids.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from app.core.org_context import spawn_in_request
from app.harness.knowledge.service import KnowledgePublications
from harness_core.knowledge.publications import ReadGrant

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Sequence
    from collections.abc import Set as AbstractSet

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from harness_core.knowledge.publications import ReadDecision


class PublicationReads(Protocol):
    async def enabled(self) -> bool: ...

    async def read_grants(
        self, user_id: str | None, org_id: str, record_ids: Sequence[str], *, own: AbstractSet[str]
    ) -> dict[str, ReadGrant]: ...


class RecordReadGate:
    def __init__(
        self,
        graph: IGraphDBProvider,
        org_id: str,
        user_id: str,
        *,
        caller_user_id: str | None = None,
        publications: PublicationReads | None = None,
    ) -> None:
        # Every id is decided by main's one access check: retrieval cites by the same one (SYNC4 S7).
        self._graph = graph
        self._org_id = org_id
        self._user_id = user_id
        self._caller = caller_user_id if caller_user_id and caller_user_id != user_id else None
        self._publications: PublicationReads = publications or KnowledgePublications(
            graph, getattr(graph, "config_service", None)
        )
        self._on: asyncio.Future[bool] | None = None

    async def decide(self, record_id: str) -> ReadDecision:
        """``own``, ``published_full``, ``published_answer_only`` or ``denied``; a failed check raises."""
        return (await self.decide_many([record_id]))[record_id]

    async def decide_many(self, record_ids: Sequence[str]) -> dict[str, ReadDecision]:
        """``decide`` of every id at once; a failed check raises for all of them."""
        return {rid: grant.decision for rid, grant in (await self.decide_grants(record_ids)).items()}

    async def decide_grants(self, record_ids: Sequence[str]) -> dict[str, ReadGrant]:
        """``decide_many`` with the publication behind each published decision (the caller's, for a
        service-account agent): a record served through a publication carries it into the record map (H4)."""
        ids = list(dict.fromkeys(record_ids))
        if not ids:
            return {}
        own = await self._readable(self._user_id, ids)
        if not await self._publications_on():
            return {rid: ReadGrant("own" if rid in own else "denied") for rid in ids}
        if self._caller is None:
            return await self._publications.read_grants(self._user_id, self._org_id, ids, own=own)
        grants: dict[str, ReadGrant] = {rid: ReadGrant("denied") for rid in ids if rid not in own}
        opened = [rid for rid in ids if rid in own]
        if opened:
            caller_own = await self._readable(self._caller, opened)
            grants |= await self._publications.read_grants(self._caller, self._org_id, opened, own=caller_own)
        return grants

    async def _readable(self, user_id: str, record_ids: list[str]) -> set[str]:
        """The ids the user may access by main's one access check (#4255); a failed read raises, an unknown user
        reads nothing."""
        user = await self._graph.get_user_by_user_id(user_id, raise_on_error=True)
        user_key = (user or {}).get("_key") or (user or {}).get("id")
        if not user_key:
            return set()
        return set((await self._graph.check_access(user_key, self._org_id, node_ids=record_ids)).node_ids)

    async def _publications_on(self) -> bool:
        # Read once per gate: one fetch call decides every id under the same setting.
        if self._on is None:
            self._on = spawn_in_request(self._publications.enabled())
        return await self._on


__all__ = ["PublicationReads", "RecordReadGate"]
