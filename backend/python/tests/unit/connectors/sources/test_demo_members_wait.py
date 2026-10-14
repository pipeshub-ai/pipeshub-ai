"""The demo sync waits for the sign-in personas before it writes memberships.

Accounts land in the graph a second or two after the API call that creates
them, while enabling the connector reaches the sync straight away. Group
membership silently skips a member the graph does not have yet, so the wait is
what keeps a first-run demo from leaving Alice and Bob out of every group.

The personas are optional, so the wait reports who is missing rather than
failing the sync: a demo loaded from the connectors page never creates them,
and that instance still has to end up with its records.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from app.connectors.sources.demo.connector import _people_to_create, _wait_for_members

LOGGER = logging.getLogger(__name__)

ALICE = "alice@acme-demo.example"
BOB = "bob@acme-demo.example"


@pytest.mark.asyncio
async def test_returns_at_once_when_every_account_exists() -> None:
    present = {ALICE, BOB}

    async def lookup(email: str) -> object | None:
        return object() if email in present else None

    missing = await _wait_for_members(
        sorted(present), lookup, LOGGER, grace=1.0, timeout=2.0, poll=0.01
    )

    assert missing == []


@pytest.mark.asyncio
async def test_waits_for_an_account_that_arrives_late() -> None:
    calls: list[str] = []

    async def lookup(email: str) -> object | None:
        calls.append(email)
        return object() if calls.count(email) > 2 else None

    missing = await _wait_for_members(
        [BOB], lookup, LOGGER, grace=1.0, timeout=2.0, poll=0.01
    )

    assert missing == []
    assert calls.count(BOB) == 3


@pytest.mark.asyncio
async def test_keeps_waiting_past_the_grace_once_one_persona_has_arrived() -> None:
    """Alice being there means Bob is on his way, so he gets the longer wait."""
    calls: list[str] = []

    async def lookup(email: str) -> object | None:
        calls.append(email)
        if email == ALICE:
            return object()
        return object() if calls.count(BOB) > 4 else None

    missing = await _wait_for_members(
        [ALICE, BOB], lookup, LOGGER, grace=0.02, timeout=2.0, poll=0.01
    )

    assert missing == []
    assert calls.count(BOB) == 5


@pytest.mark.asyncio
async def test_gives_up_on_personas_this_instance_never_creates() -> None:
    """Demo data without personas: the sync goes on, for the installer alone."""

    async def lookup(_email: str) -> object | None:
        return None

    missing = await _wait_for_members(
        [ALICE, BOB], lookup, LOGGER, grace=0.02, timeout=5.0, poll=0.01
    )

    assert missing == [ALICE, BOB]


@pytest.mark.asyncio
async def test_an_empty_list_does_not_wait() -> None:
    async def lookup(_email: str) -> object | None:  # pragma: no cover - never called
        raise AssertionError("no account should be looked up")

    waited = asyncio.wait_for(_wait_for_members([], lookup, LOGGER), timeout=1.0)
    assert await waited == []


# ---------------------------------------------------------------- _people_to_create

PEOPLE = [
    {"id": "alice", "email": ALICE, "login": True},
    {"id": "bob", "email": BOB, "login": True},
    {"id": "dana", "email": "dana@acme-demo.example"},
]


def test_creates_everyone_when_both_personas_have_accounts() -> None:
    kept = _people_to_create(PEOPLE, set())

    assert [p["id"] for p in kept] == ["alice", "bob", "dana"]


def test_leaves_out_a_persona_with_no_account() -> None:
    """A row written here would race the account the outbox is about to create."""
    assert [p["id"] for p in _people_to_create(PEOPLE, {BOB})] == ["alice", "dana"]


def test_keeps_the_authors_whatever_the_personas_did() -> None:
    """Authors never have accounts, and nothing else ever writes their rows."""
    kept = _people_to_create(PEOPLE, {ALICE, BOB, "dana@acme-demo.example"})

    assert [p["id"] for p in kept] == ["dana"]
