"""A brand-new organisation's first knowledge-base create succeeds on PostgreSQL before its sign-up events are consumed.

The case itself, and why, is in tests/integration/graph_db/test_fresh_org_first_call_real_backends.py (Neo4j and
ArangoDB). Here it runs on a migrated database with the graph schema enforced, as the application writes it.
"""

from __future__ import annotations

import pytest

from tests.integration.graph_db.fresh_org_support import (
    a_claim_whose_collection_is_gone_is_replaced,
    a_first_request_after_add_and_delete_writes_nothing,
    a_new_users_writers_race,
    a_readded_user_keeps_one_node,
    a_removed_default_is_not_made_again_under_its_key,
    first_calls_after_signup,
)
from tests.integration.graph_db.postgres.pg_support import new_org

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]


async def test_the_first_creates_after_signup_succeed(pg_provider, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAPH_SCHEMA_ENFORCEMENT", "strict")  # after the folder's conftest, which turns it off
    await first_calls_after_signup(pg_provider, new_org())


async def test_any_number_of_writers_of_a_new_user_write_one_user_and_one_default(pg_provider, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAPH_SCHEMA_ENFORCEMENT", "strict")
    await a_new_users_writers_race(pg_provider, new_org())


@pytest.mark.parametrize("older_random_key", [True, False], ids=["older-random-key", "derived-key"])
async def test_a_user_readded_under_a_new_address_keeps_their_one_node(
    pg_provider, monkeypatch: pytest.MonkeyPatch, older_random_key: bool,
) -> None:
    monkeypatch.setenv("GRAPH_SCHEMA_ENFORCEMENT", "strict")
    await a_readded_user_keeps_one_node(pg_provider, new_org(), older_random_key=older_random_key)


async def test_a_first_request_after_add_and_delete_writes_nothing(pg_provider, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAPH_SCHEMA_ENFORCEMENT", "strict")
    await a_first_request_after_add_and_delete_writes_nothing(pg_provider, new_org())


async def test_a_removed_default_is_not_made_again_under_its_key(pg_provider, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAPH_SCHEMA_ENFORCEMENT", "strict")
    await a_removed_default_is_not_made_again_under_its_key(pg_provider, new_org())


@pytest.mark.parametrize("abandoned", [False, True], ids=["deleted-mid-claim", "abandoned-claim"])
async def test_a_claim_whose_collection_is_gone_is_replaced(pg_provider, monkeypatch: pytest.MonkeyPatch, abandoned: bool) -> None:
    monkeypatch.setenv("GRAPH_SCHEMA_ENFORCEMENT", "strict")
    await a_claim_whose_collection_is_gone_is_replaced(pg_provider, new_org(), abandoned=abandoned)
