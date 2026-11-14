"""Drupal Wiki access on PostgreSQL: each user reads in PipesHub what the wiki lets them read.

The same scenarios as ``test_drupal_wiki_access_real_backends.py`` on Neo4j and ArangoDB (``drupal_wiki_world``).
Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from tests.integration.graph_db.drupal_wiki_world import (
    ALICE,
    BOB,
    CAROL,
    DrupalWorld,
    a_deleted_page_goes_on_the_next_sync,
    a_disabled_reader_loses_every_space_until_enabled_again,
    a_shared_page_reaches_the_other_spaces_readers_and_its_sub_page_does_not,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]


@pytest.fixture
async def world(pg_provider, pg_db, org_ctx) -> AsyncIterator[DrupalWorld]:
    org = uuid.uuid4().hex[:24]
    with org_ctx(org):
        built = DrupalWorld(pg_provider, org, f"drupal-wiki-{org[:10]}",
                            {email: f"user-{org[:10]}-{i}" for i, email in enumerate((ALICE, BOB, CAROL))})
        await built.seed()
        yield built
    with org_ctx(org):
        await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.edges WHERE org_id = $1", org), write=True)
        await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", org), write=True)


async def test_a_disabled_or_removed_reader_reads_nothing(world: DrupalWorld) -> None:
    await a_disabled_reader_loses_every_space_until_enabled_again(world)


async def test_a_deleted_page_is_removed_by_the_next_sync(world: DrupalWorld) -> None:
    await a_deleted_page_goes_on_the_next_sync(world)


async def test_a_shared_page_is_read_in_both_spaces_and_its_sub_page_only_in_its_own(world: DrupalWorld) -> None:
    await a_shared_page_reaches_the_other_spaces_readers_and_its_sub_page_does_not(world)
