"""The authenticator link, decided by the real processor on a real graph store.

``link_authenticator_to_source_user`` links the user who authenticated a connector to the source account it signs in
as. Its evidence is the sync's ``SourceUserListing`` and the listed-authenticators marker: PRESENT or marked is never
linked, only ABSENT is, and a cut listing (UNKNOWN) makes no link. A userAppRelation in the store is no evidence: a
connector may have written it itself. A link left by a former authenticator goes on any presence. Shared by the
Neo4j/ArangoDB file and the PostgreSQL one, so every store answers the same cases.
"""

from __future__ import annotations

import contextlib
import copy
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

from app.config.constants.arangodb import CollectionNames, Connectors
from app.connectors.core.base.connector.source_user_listing import SourceUserListing
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    LISTED_AUTHENTICATORS_KEY,
    AuthenticatorLink,
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.models.entities import AppUser, Org
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

USERS, APPS = CollectionNames.USERS.value, CollectionNames.APPS.value
APP_NAME = Connectors.SLACK_WORKSPACE

logger = logging.getLogger("authenticator-link-it")


class MemoryConfig:
    """The config store the processor reads the listed-authenticators marker from."""

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    async def get_config(self, key: str, default: Any = None, **_: Any) -> Any:  # noqa: ANN401
        return copy.deepcopy(self.values.get(key, default))

    async def set_config(self, key: str, value: Any, **_: Any) -> bool:  # noqa: ANN401
        self.values[key] = copy.deepcopy(value)
        return True


@dataclass
class LinkWorld:
    graph: IGraphDBProvider
    org_id: str
    run: str
    config: MemoryConfig = field(default_factory=MemoryConfig)

    @property
    def connector_id(self) -> str:
        return f"slack-{self.run}"

    @property
    def other_connector_id(self) -> str:
        return f"slack-other-{self.run}"

    def user(self, name: str) -> tuple[str, str, str]:
        """(user id, graph key, email) of a seeded PipesHub user."""
        return f"uid-{name}-{self.run}", f"key-{name}-{self.run}", f"{name}-{self.run}@example.com"

    @property
    def creator(self) -> tuple[str, str, str]:
        return self.user("creator")

    @property
    def peer(self) -> tuple[str, str, str]:
        return self.user("peer")

    @property
    def service_email(self) -> str:
        """The source account the connector's credentials act as: nobody's PipesHub account."""
        return f"service-{self.run}@source.example"

    def keys(self) -> dict[str, list[str]]:
        return {USERS: [self.creator[1], self.peer[1]], APPS: [self.connector_id, self.other_connector_id]}

    def processor(self) -> DataSourceEntitiesProcessor:
        processor = DataSourceEntitiesProcessor(logger, GraphDataStore(logger, self.graph), self.config)
        processor.org_id = self.org_id
        processor.messaging_producer = MagicMock(
            send_message=AsyncMock(return_value=True),
            send_messages=AsyncMock(side_effect=lambda _topic, messages: [True] * len(messages)),
        )
        return processor

    @property
    def marker(self) -> list[str]:
        return self.values_of(LISTED_AUTHENTICATORS_KEY.format(connector_id=self.connector_id)) or []

    def values_of(self, key: str) -> Any:  # noqa: ANN401
        return self.config.values.get(key)


def plan_world(graph: IGraphDBProvider, org_id: str, run: str) -> LinkWorld:
    """The ids of the world, before anything is written: a fixture registers their clean-up from it first."""
    return LinkWorld(graph, org_id, run)


async def seed_world(w: LinkWorld, *, with_org: bool = True) -> None:
    """The org, the connector the creator authenticated (and a second one), the creator and one other user."""
    now = get_epoch_timestamp_in_ms()
    stamps = {"createdAtTimestamp": now, "updatedAtTimestamp": now}
    if with_org:
        assert await w.graph.batch_upsert_nodes([Org(id=w.org_id, name=w.org_id).to_arango_org()],
                                                CollectionNames.ORGS.value)
    assert await w.graph.batch_upsert_nodes(
        [{"_key": key, "orgId": w.org_id, "name": "Slack", "type": APP_NAME.value, "appGroup": "Slack",
          "scope": "team", "isActive": True, "createdBy": w.creator[0], **stamps}
         for key in (w.connector_id, w.other_connector_id)],
        APPS,
    )
    assert await w.graph.batch_upsert_nodes(
        [{"_key": key, "userId": uid, "orgId": w.org_id, "email": email, "fullName": email, "isActive": True, **stamps}
         for uid, key, email in (w.creator, w.peer)],
        USERS,
    )


# --- what a sync writes ------------------------------------------------------------------------------------------


def listing(w: LinkWorld, *names: str, cut: bool = False) -> SourceUserListing:
    """One sync's user listing, naming these users: whole, or cut before its end."""
    out = SourceUserListing(includes_inactive=True)
    out.add_row(f"someone-{w.run}@source.example")
    for name in names:
        out.add_row(w.user(name)[2])
    if cut:
        out.cut("a page failed")
    else:
        out.finish()
    return out


async def creator_with_source_account(w: LinkWorld) -> None:
    """A membership with a source account behind it, written for the creator without the source naming them: a
    stub a connector makes itself, say."""
    await w.processor().on_new_app_users([AppUser(
        app_name=APP_NAME, connector_id=w.connector_id, source_user_id=f"U-own-{w.run}",
        org_id=w.org_id, email=w.creator[2], full_name="Creator",
    )])


async def creator_as_external_collaborator(w: LinkWorld) -> None:
    """The creator holds a grant on some record as an outside collaborator: a flagged membership, no listing."""
    await w.processor().on_external_app_users([w.creator[2]], w.connector_id)


async def creator_with_gate_edge_only(w: LinkWorld) -> None:
    """A membership edge with no source account behind it (as a knowledge base or the connector gate writes)."""
    await w.graph.ensure_app_membership(w.creator[1], USERS, w.connector_id, is_external=False)


async def link(
    w: LinkWorld, listed: SourceUserListing | None = None, *, authenticator: str = "creator", connector_id: str | None = None,
) -> AuthenticatorLink:
    """What the connector does after its user sync: decide the link from what the listing proves (a whole listing
    that does not name the creator, unless one is given)."""
    listed = listed or listing(w)
    result = await w.processor().link_authenticator_to_source_user(
        connector_id or w.connector_id, w.creator[0], w.service_email, f"U-service-{w.run}", APP_NAME,
        presence=listed.presence(w.user(authenticator)[2]),
    )
    return result.outcome


async def authenticate_again(w: LinkWorld, name: str) -> None:
    """Another user supplies the connector's credentials."""
    assert await w.graph.update_node(w.connector_id, APPS, {"authenticatedBy": w.user(name)[0]})


# --- what the store says -----------------------------------------------------------------------------------------


async def membership_edges(w: LinkWorld) -> list[dict]:
    """The creator's userAppRelation edges to the connector."""
    edges = await w.graph.get_edges_from_node(f"{USERS}/{w.creator[1]}", CollectionNames.USER_APP_RELATION.value)
    return [e for e in edges or [] if e.get("_to") == f"{APPS}/{w.connector_id}"]


async def linked_to(w: LinkWorld) -> set[str]:
    """The keys of the source accounts the creator is linked to for the connector."""
    edges = await w.graph.get_edges_from_node(f"{USERS}/{w.creator[1]}", CollectionNames.AUTHENTICATED_AS.value)
    return {e["_to"].split("/", 1)[1] for e in edges or [] if e.get("connectorId") == w.connector_id}


async def service_account_key(w: LinkWorld) -> str:
    user = await w.graph.get_user_by_email(w.service_email, org_id=w.org_id)
    assert user is not None, "the source account the creator is linked to has no user node"
    return user.id


# --- a failed read of the connector's app ------------------------------------------------------------------------


@contextlib.contextmanager
def failing(target: object, attr: str, *, when: Callable[..., bool] = lambda *_a, **_k: True) -> Iterator[None]:
    """``target.attr`` raises for the calls ``when`` picks, as a store that went away mid-transaction would."""
    own = attr in vars(target)
    original = getattr(target, attr)

    async def call(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        if when(*args, **kwargs):
            raise RuntimeError("store down")
        return await original(*args, **kwargs)

    setattr(target, attr, call)
    try:
        yield
    finally:
        if own:
            setattr(target, attr, original)
        else:
            delattr(target, attr)


async def app_reads(w: LinkWorld, broken: Callable[[], contextlib.AbstractContextManager]) -> tuple[object, object]:
    """What ``get_app_by_id`` answers inside a transaction while the store fails: lenient, then strict."""
    async with GraphDataStore(logger, w.graph).transaction() as tx:
        with broken():
            lenient = await tx.get_app_by_id(w.connector_id)
            try:
                strict: object = await tx.get_app_by_id(w.connector_id, raise_on_error=True)
            except RuntimeError as e:
                strict = e
    return lenient, strict


# --- the cases every store answers -------------------------------------------------------------------------------
# Each checks the store first and the outcome the processor reports second, so a regression reads as the wrong link.


async def a_creator_listed_later_loses_the_link(w: LinkWorld) -> None:
    outcome = await link(w)
    assert await linked_to(w) == {await service_account_key(w)}, "an unlisted creator is linked after a whole listing"
    assert outcome is AuthenticatorLink.LINKED

    outcome = await link(w, listing(w, "creator"))
    assert await linked_to(w) == set(), "the listing names the creator: the link must go"
    assert outcome is AuthenticatorLink.UNLINKED
    assert w.marker == [w.creator[1]]


async def a_listed_creator_is_never_linked(w: LinkWorld) -> None:
    outcome = await link(w, listing(w, "creator"))
    assert await linked_to(w) == set()
    assert outcome is AuthenticatorLink.UNCHANGED
    assert w.marker == [w.creator[1]]

    # The source withdraws the creator's account: the next whole listing does not name them, and the marker still does.
    outcome = await link(w, listing(w))
    assert await linked_to(w) == set()
    assert outcome is AuthenticatorLink.UNCHANGED


async def a_cut_listing_makes_no_link_and_removes_none(w: LinkWorld) -> None:
    assert await link(w, listing(w, cut=True)) is AuthenticatorLink.UNCHANGED
    assert await linked_to(w) == set(), "a listing that stopped short proves nobody absent"

    assert await link(w) is AuthenticatorLink.LINKED
    source = await service_account_key(w)
    assert await link(w, listing(w, cut=True)) is AuthenticatorLink.UNCHANGED
    assert await linked_to(w) == {source}, "nor does it take the creator's own link"


async def a_cut_listing_that_names_the_creator_unlinks_them(w: LinkWorld) -> None:
    assert await link(w) is AuthenticatorLink.LINKED
    assert await link(w, listing(w, "creator", cut=True)) is AuthenticatorLink.UNLINKED
    assert await linked_to(w) == set()
    assert w.marker == [w.creator[1]]


async def a_source_account_membership_is_not_a_listing(w: LinkWorld) -> None:
    await creator_with_source_account(w)
    edges = await membership_edges(w)
    assert edges and all(e.get("sourceUserId") and not e.get("isExternalUser") for e in edges), edges

    outcome = await link(w)
    assert await linked_to(w) == {await service_account_key(w)}, "the listing does not name them"
    assert outcome is AuthenticatorLink.LINKED
    assert w.marker == []


async def an_external_collaborator_is_not_listed(w: LinkWorld) -> None:
    await creator_as_external_collaborator(w)
    edges = await membership_edges(w)
    assert edges and all(e.get("isExternalUser") is True for e in edges), edges

    outcome = await link(w)
    assert await linked_to(w) == {await service_account_key(w)}
    assert outcome is AuthenticatorLink.LINKED


async def a_membership_without_a_source_account_is_not_a_listing(w: LinkWorld) -> None:
    await creator_with_gate_edge_only(w)
    edges = await membership_edges(w)
    assert edges and not any(e.get("sourceUserId") for e in edges), edges

    outcome = await link(w)
    assert await linked_to(w) == {await service_account_key(w)}
    assert outcome is AuthenticatorLink.LINKED


async def a_listing_by_another_connector_does_not_count(w: LinkWorld) -> None:
    assert await link(w, listing(w, "creator"), connector_id=w.other_connector_id) is AuthenticatorLink.UNCHANGED
    assert w.values_of(LISTED_AUTHENTICATORS_KEY.format(connector_id=w.other_connector_id)) == [w.creator[1]]

    outcome = await link(w)
    assert await linked_to(w) == {await service_account_key(w)}, "the marker is the other connector's"
    assert outcome is AuthenticatorLink.LINKED


async def a_former_authenticators_link_goes_on_a_cut_listing(w: LinkWorld) -> None:
    assert await link(w) is AuthenticatorLink.LINKED
    await authenticate_again(w, "peer")

    assert await link(w, listing(w, cut=True), authenticator="peer") is AuthenticatorLink.UNLINKED
    assert await linked_to(w) == set(), "the creator's credentials no longer run the connector"
    assert await link(w, listing(w, cut=True), authenticator="peer") is AuthenticatorLink.UNCHANGED
    assert w.marker == [], "nobody was listed"


async def a_former_authenticators_link_to_another_connector_stays(w: LinkWorld) -> None:
    assert await link(w) is AuthenticatorLink.LINKED
    assert await link(w, connector_id=w.other_connector_id) is AuthenticatorLink.LINKED
    await authenticate_again(w, "peer")

    assert await link(w, listing(w, cut=True), authenticator="peer") is AuthenticatorLink.UNLINKED
    edges = await w.graph.get_edges_from_node(f"{USERS}/{w.creator[1]}", CollectionNames.AUTHENTICATED_AS.value)
    assert [e.get("connectorId") for e in edges or []] == [w.other_connector_id]


async def a_failed_app_read_raises_only_when_asked_to(
    w: LinkWorld, broken: Callable[[], contextlib.AbstractContextManager],
) -> None:
    lenient, strict = await app_reads(w, broken)
    assert lenient is None
    assert isinstance(strict, RuntimeError), strict


async def a_failed_app_read_stops_the_link_decision(
    w: LinkWorld, broken: Callable[[], contextlib.AbstractContextManager],
) -> None:
    """Read as "no app", the failure would make the creator the authenticator and decide for them."""
    assert await link(w) is AuthenticatorLink.LINKED
    source = await service_account_key(w)
    with broken():
        try:
            await link(w)
        except RuntimeError:
            pass
        else:
            raise AssertionError("the link decision went ahead without the connector's app")
    assert await linked_to(w) == {source}
