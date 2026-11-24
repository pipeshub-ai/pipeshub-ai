"""The connector's creator is linked to the account the connector signs in as only when its listing proves them
absent, on PostgreSQL: the twin of ../test_authenticator_link_real_backends.py, with the cases in
../authenticator_link_support.py.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none; without it each case skips.
"""
from __future__ import annotations

import uuid
from functools import partial
from typing import TYPE_CHECKING

import pytest

from tests.integration.graph_db import authenticator_link_support as cases

from .test_identity import w  # noqa: F401  (the fixture)

if TYPE_CHECKING:
    from .test_access import World

pytestmark = pytest.mark.integration


@pytest.fixture
async def world(w: World) -> cases.LinkWorld:  # noqa: F811
    link_world = cases.plan_world(w.p, w.org, uuid.uuid4().hex[:10])      # `w` deletes the whole org afterwards
    await cases.seed_world(link_world, with_org=False)
    return link_world


def _broken_app_read(world: cases.LinkWorld) -> object:
    """Every statement the provider runs fails; the app is the first thing the link decision reads."""
    return cases.failing(world.graph, "_q")


async def test_a_creator_the_listing_names_later_loses_the_link(world: cases.LinkWorld) -> None:
    await cases.a_creator_listed_later_loses_the_link(world)


async def test_a_listed_creator_is_never_linked_even_after_the_listing_drops_them(world: cases.LinkWorld) -> None:
    await cases.a_listed_creator_is_never_linked(world)


async def test_a_cut_listing_makes_no_link_and_removes_none(world: cases.LinkWorld) -> None:
    await cases.a_cut_listing_makes_no_link_and_removes_none(world)


async def test_a_cut_listing_that_names_the_creator_unlinks_them(world: cases.LinkWorld) -> None:
    await cases.a_cut_listing_that_names_the_creator_unlinks_them(world)


async def test_a_membership_with_a_source_account_is_not_a_listing(world: cases.LinkWorld) -> None:
    await cases.a_source_account_membership_is_not_a_listing(world)


async def test_an_external_collaborator_membership_is_not_a_listing(world: cases.LinkWorld) -> None:
    await cases.an_external_collaborator_is_not_listed(world)


async def test_a_membership_with_no_source_account_is_not_a_listing(world: cases.LinkWorld) -> None:
    await cases.a_membership_without_a_source_account_is_not_a_listing(world)


async def test_a_listing_by_another_connector_does_not_count(world: cases.LinkWorld) -> None:
    await cases.a_listing_by_another_connector_does_not_count(world)


async def test_a_former_authenticators_link_goes_on_a_cut_listing(world: cases.LinkWorld) -> None:
    await cases.a_former_authenticators_link_goes_on_a_cut_listing(world)


async def test_a_former_authenticators_link_to_another_connector_stays(world: cases.LinkWorld) -> None:
    await cases.a_former_authenticators_link_to_another_connector_stays(world)


async def test_an_authenticator_who_does_not_exist_leaves_no_link(world: cases.LinkWorld) -> None:
    await cases.an_authenticator_who_does_not_exist_leaves_no_link(world)


async def test_a_failed_app_read_raises_only_when_asked_to(world: cases.LinkWorld) -> None:
    await cases.a_failed_app_read_raises_only_when_asked_to(world, partial(_broken_app_read, world))


async def test_a_failed_app_read_stops_the_link_decision(world: cases.LinkWorld) -> None:
    await cases.a_failed_app_read_stops_the_link_decision(world, partial(_broken_app_read, world))
