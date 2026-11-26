"""Knowledge publications in PipesHub (P2.13, D21): who may publish, and what a principal's publications admit.

Everything is read when asked (no cache): a revocation or an expiry acts at the next retrieval. Every graph read
goes through ``IGraphDBProvider`` (generic node/edge methods and ``get_records_by_record_group``), so Neo4j and
ArangoDB answer alike. A container is ``apps/<kb>`` (a knowledge base, or a whole connector instance, whose
records carry ``connectorId`` = the app) or ``recordGroups/<id>`` (its records belong to it or a nested group).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from app.config.constants.arangodb import CollectionNames
from app.core.org_context import org_of
from app.harness.knowledge.directory import GraphPublicationDirectory, split_container
from app.harness.knowledge.graph import Doc, doc, docs, graph_of, text
from app.services.featureflag.platform_settings import is_knowledge_publications_enabled
from app.services.graph_db.interface.graph_db_provider import (
    STRICT_SCOPE_FILTER_KEY,
    _unsupported_container_filters,  # pyright: ignore[reportPrivateUsage]
    requested_scope_ids,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from harness_core.knowledge.errors import (
    PublicationForbidden,
    PublicationNotFound,
    PublicationsDisabled,
)
from harness_core.knowledge.publications import (
    Principal,
    Publication,
    PublicationDraft,
    ReadDecision,
    ReadGrant,
    admitting,
    grant_read,
    validate_draft,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from collections.abc import Set as AbstractSet

    from app.config.configuration_service import ConfigurationService
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from harness_core.knowledge.ports import PublicationDirectory

logger = logging.getLogger(__name__)

ContainerKind = Literal["kb", "recordGroup"]
_KIND_COLLECTION: dict[str, str] = {"kb": CollectionNames.APPS.value, "recordGroup": CollectionNames.RECORD_GROUPS.value}
#: The most records one search admits through publications; beyond it fewer are admitted, never more.
MAX_PUBLISHED_RECORDS = 20_000
_PAGE = 1_000
_MAX_TEAMS = 1_000
_OWNER = "OWNER"

PublishedMap = dict[str, tuple[str, Publication]]


def container_id(kind: ContainerKind, key: str) -> str:
    return f"{_KIND_COLLECTION[kind]}/{key}"


@dataclass(frozen=True, slots=True)
class Caller:
    """Who asks, from the credential only: the token's user and org, and the live admin role from Node."""

    user_id: str
    org_id: str
    is_admin: bool


@dataclass(frozen=True, slots=True)
class Container:
    id: str
    collection: str
    key: str
    connector_id: str


class KnowledgePublications:
    def __init__(
        self,
        graph: IGraphDBProvider,
        config_service: ConfigurationService | None,
        *,
        directory: PublicationDirectory | None = None,
        now_ms: Callable[[], int] = get_epoch_timestamp_in_ms,
        new_id: Callable[[], str] = lambda: uuid.uuid4().hex,
    ) -> None:
        self._graph = graph_of(graph)
        self._config = config_service
        self.directory: PublicationDirectory = directory or GraphPublicationDirectory(graph)
        self._now = now_ms
        self._new_id = new_id

    async def enabled(self) -> bool:
        return await is_knowledge_publications_enabled(self._config)

    async def require_enabled(self) -> None:
        if not await self.enabled():
            raise PublicationsDisabled("knowledge publications are off for this org")

    # ---- principals and containers

    async def _user_key(self, user_id: str, org_id: str) -> str | None:
        """The user's graph key when they are a user of this org."""
        user = doc(await self._graph.get_user_by_user_id(user_id))
        if user is None or org_of(user) not in (org_id, None):
            return None
        return text(user, "id", "_key")

    async def principal(self, user_id: str | None, org_id: str) -> Principal | None:
        """The user as an audience is matched against; ``None`` when they are not a user of this org."""
        if not user_id:
            return None
        user = doc(await self._graph.get_user_by_user_id(user_id))
        if user is None or org_of(user) not in (org_id, None):
            return None
        key = text(user, "id", "_key")
        groups: frozenset[str] = frozenset()
        if key is not None:
            teams, _ = await self._graph.get_user_teams(key, page=1, limit=_MAX_TEAMS)
            groups = frozenset(t for t in (text(team, "id") for team in docs(teams)) if t is not None)
        return Principal(org_id=org_id, user_id=user_id, group_ids=groups)

    async def container(self, org_id: str, qualified: str) -> Container | None:
        """The container if it exists in this org; ``None`` otherwise (another org's reads as absent)."""
        try:
            collection, key = split_container(qualified)
        except ValueError:
            return None
        found = doc(await self._graph.get_document(key, collection, raise_on_error=True))
        if found is None:
            return None
        if collection == CollectionNames.APPS.value:
            edge = await self._graph.get_edge(
                org_id, CollectionNames.ORGS.value, key, collection, CollectionNames.ORG_APP_RELATION.value
            )
            return Container(qualified, collection, key, key) if edge else None
        connector = text(found, "connectorId")
        if org_of(found) != org_id or connector is None:
            return None
        return Container(qualified, collection, key, connector)

    async def _is_owner(self, caller: Caller, container: Container) -> bool:
        key = await self._user_key(caller.user_id, caller.org_id)
        if key is None:
            return False
        if container.collection == CollectionNames.APPS.value:
            return await self._graph.get_user_kb_permission(container.key, key) == _OWNER
        edge = await self._graph.get_edge(
            key, CollectionNames.USERS.value, container.key, container.collection, CollectionNames.PERMISSION.value
        )
        found = doc(edge)
        return found is not None and found.get("role") == _OWNER

    async def _authorized_container(self, caller: Caller, qualified: str) -> Container:
        found = await self.container(caller.org_id, qualified)
        if found is None:
            raise PublicationNotFound("no such container in this org")
        if not (caller.is_admin or await self._is_owner(caller, found)):
            raise PublicationForbidden("only an owner of the container or an org admin may do this")
        return found

    # ---- admin

    async def create(self, caller: Caller, draft: PublicationDraft) -> Publication:
        await self.require_enabled()
        await self._authorized_container(caller, draft.container_id)
        now = self._now()
        validate_draft(draft, now)
        publication = await self.directory.create(draft, publication_id=self._new_id(), now_ms=now)
        logger.info(
            "publications.created orgId=%s publicationId=%s containerId=%s mode=%s audienceUsers=%d audienceTeams=%d audienceOrg=%s",
            caller.org_id, publication.id, publication.container_id, publication.mode,
            len(publication.audience.user_ids), len(publication.audience.group_ids), publication.audience.org,
        )
        return publication

    async def list_for_container(self, caller: Caller, qualified: str) -> list[Publication]:
        await self.require_enabled()
        await self._authorized_container(caller, qualified)
        return await self.directory.list_for_container(caller.org_id, qualified)

    async def revoke(self, caller: Caller, publication_id: str) -> Publication:
        await self.require_enabled()
        found = await self.directory.get(caller.org_id, publication_id)
        if found is None:
            raise PublicationNotFound("no such publication in this org")
        await self._authorized_container(caller, found.container_id)
        revoked = await self.directory.revoke(caller.org_id, publication_id, by=caller.user_id, now_ms=self._now())
        if revoked is None:
            raise PublicationNotFound("no such publication in this org")
        logger.info("publications.revoked orgId=%s publicationId=%s", caller.org_id, publication_id)
        return revoked

    async def erase_container(self, org_id: str, qualified: str) -> int:
        """Erasure (C-14 rule): a deleted container takes its publications with it, flag on or off."""
        erased = await self.directory.erase_container(org_id, qualified)
        if erased:
            logger.info("publications.erased orgId=%s containerId=%s count=%d", org_id, qualified, erased)
        return erased

    async def erase_org(self, org_id: str) -> int:
        """Erasure (C-14 rule): a deleted org takes every publication it holds, flag on or off."""
        erased = await self.directory.erase_org(org_id)
        logger.info("publications.org_erased orgId=%s count=%d", org_id, erased)
        return erased

    # ---- retrieval

    async def admitting(self, principal: Principal) -> tuple[Publication, ...]:
        """The live publications whose audience holds ``principal`` and whose container still exists."""
        live = admitting(principal, await self.directory.list_for_org(principal.org_id), self._now())
        kept: list[Publication] = []
        for publication in live:
            if await self.container(principal.org_id, publication.container_id) is None:
                # Its container was deleted (a missed erasure, a connector removal): it grants nothing, and goes.
                logger.warning(
                    "publications.container_missing orgId=%s publicationId=%s choice=erase",
                    principal.org_id, publication.id,
                )
                await self.erase_container(principal.org_id, publication.container_id)
                continue
            kept.append(publication)
        return tuple(kept)

    async def container_records(self, org_id: str, container: Container, cap: int) -> dict[str, str]:
        """``virtualRecordId → recordId`` of the container's live records, at most ``cap``."""
        found: dict[str, str] = {}
        if container.collection == CollectionNames.APPS.value:
            skip = 0
            while len(found) < cap:
                page = docs(await self._graph.get_documents_paginated(
                    CollectionNames.RECORDS.value, skip=skip, limit=_PAGE,
                    filters={"connectorId": container.key, "orgId": org_id}, raise_on_error=True,
                ))
                for record in page:
                    vid, rid = text(record, "virtualRecordId"), text(record, "id", "_key")
                    if record.get("isDeleted") is not True and vid is not None and rid is not None:
                        found.setdefault(vid, rid)
                if len(page) < _PAGE:
                    break
                skip += _PAGE
        else:
            after: str | None = None
            while len(found) < cap:
                records = await self._graph.get_records_by_record_group(
                    container.key, container.connector_id, org_id, -1, limit=_PAGE, after_key=after,
                )
                # Typed records do not carry virtualRecordId (FileRecord drops it), so it is read by id.
                vids: Doc = (
                    doc(await self._graph.get_virtual_record_ids_for_record_ids([r.id for r in records])) or {}
                    if records
                    else {}
                )
                for record in records:
                    vid = vids.get(record.id)
                    if isinstance(vid, str) and vid:
                        found.setdefault(vid, record.id)
                if len(records) < _PAGE:
                    break
                after = records[-1].id
        if len(found) >= cap:
            logger.warning("publications.records_capped orgId=%s containerId=%s cap=%d", org_id, container.id, cap)
        return dict(list(found.items())[:cap])

    @staticmethod
    def _in_grant(container: Container, filters: Mapping[str, Any] | None) -> bool:
        scope = requested_scope_ids(dict(filters or {}))
        if scope is None:
            return not (filters or {}).get(STRICT_SCOPE_FILTER_KEY)
        return container.key in scope or container.connector_id in scope

    async def published_map(
        self, principal: Principal, filters: Mapping[str, Any] | None = None, time_range: Mapping[str, int] | None = None
    ) -> PublishedMap:
        """What ``principal``'s publications admit within the grant. A record-level filter (departments, a time
        range…) cannot be applied to a container, so such a search admits nothing published (narrower, never wider)."""
        if _unsupported_container_filters(dict(filters or {}), dict(time_range) if time_range else None):
            return {}
        published: PublishedMap = {}
        for publication in await self.admitting(principal):
            container = await self.container(principal.org_id, publication.container_id)
            if container is None or not self._in_grant(container, filters):
                continue
            room = MAX_PUBLISHED_RECORDS - len(published)
            if room <= 0:
                break
            for vid, rid in (await self.container_records(principal.org_id, container, room)).items():
                current = published.get(vid)
                # A record under two publications reads as the widest of them.
                if current is None or (current[1].mode == "answer_only" and publication.mode == "full"):
                    published[vid] = (rid, publication)
        return published

    async def read_decision(self, user_id: str | None, org_id: str, record_id: str, *, own: bool) -> ReadDecision:
        """How a user reads a record they may or may not read themselves (raw reads and record routes)."""
        return (await self.read_decisions(user_id, org_id, [record_id], own={record_id} if own else set()))[record_id]

    async def read_decisions(
        self, user_id: str | None, org_id: str, record_ids: Sequence[str], *, own: AbstractSet[str]
    ) -> dict[str, ReadDecision]:
        """``read_decision`` of each record (``own``: those the user may read themselves)."""
        grants = await self.read_grants(user_id, org_id, record_ids, own=own)
        return {rid: grant.decision for rid, grant in grants.items()}

    async def read_grants(
        self, user_id: str | None, org_id: str, record_ids: Sequence[str], *, own: AbstractSet[str]
    ) -> dict[str, ReadGrant]:
        """``read_decisions`` with the publication that decided each (H4: a record served through it carries it).
        The principal, its live publications and each one's container records are read once for all of them, not
        once per record."""
        grants: dict[str, ReadGrant] = {rid: ReadGrant("own") for rid in record_ids if rid in own}
        rest = [rid for rid in record_ids if rid not in own]
        if not rest:
            return grants
        principal = await self.principal(user_id, org_id)
        if principal is None:
            return grants | {rid: ReadGrant("denied") for rid in rest}
        held: list[tuple[Publication, frozenset[str]]] = []
        for publication in await self.admitting(principal):
            container = await self.container(org_id, publication.container_id)
            if container is not None:
                records = await self.container_records(org_id, container, MAX_PUBLISHED_RECORDS)
                held.append((publication, frozenset(records.values())))
        now = self._now()
        for rid in rest:
            covering = [publication for publication, records in held if rid in records]
            grants[rid] = grant_read(own=False, covering=covering, principal=principal, now_ms=now)
        return grants

    async def readable_through(self, principal: Principal, sources: list[tuple[str, str]]) -> set[tuple[str, str]]:
        """The ``(record_id, publication_id)`` pairs a label or citation names that the publication still discloses
        to ``principal``: live, its audience holds them, the record still in its container. Each publication's
        container is read once."""
        readable: set[tuple[str, str]] = set()
        by_publication: dict[str, set[str]] = {}
        for record_id, publication_id in sources:
            by_publication.setdefault(publication_id, set()).add(record_id)
        for publication_id, record_ids in by_publication.items():
            publication = await self.directory.get(principal.org_id, publication_id)
            if publication is None or not publication.admits(principal, self._now()):
                continue
            container = await self.container(principal.org_id, publication.container_id)
            if container is None:
                continue
            held = set((await self.container_records(principal.org_id, container, MAX_PUBLISHED_RECORDS)).values())
            readable |= {(rid, publication_id) for rid in record_ids if rid in held}
        return readable

__all__ = [
    "MAX_PUBLISHED_RECORDS",
    "Caller",
    "Container",
    "ContainerKind",
    "KnowledgePublications",
    "PublishedMap",
    "container_id",
]
